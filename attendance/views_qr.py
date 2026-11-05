import csv
import datetime
import io
import json

import qrcode
import qrcode.constants
import qrcode.image.svg
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from custom.models import Division
from employee.models import Employee, EmployeeCard, EmployeeUser
from leave.models import Leaves
from settings_app.decorators import allowed_users
from settings_app.utils import f_monthname
from attendance import qr_service as svc
from attendance.models import Attendance
from attendance.models_qr import AttendanceTime, QRCard, ScanDevice, ScanLog


def _groups(name, default):
	return getattr(settings, 'ATTENDANCE_' + name, default)


# Siapa boleh apa. Bisa diubah di settings.py (lihat QR_INSTALL.md).
MANAGERS = _groups('MANAGER_GROUPS', ['admin', 'staff'])
VIEWERS = _groups('VIEWER_GROUPS', ['admin', 'staff', 'president'])
DEVICE_ADMINS = _groups('DEVICE_GROUPS', ['admin'])
EVERYONE = _groups('SELF_GROUPS', ['admin', 'staff', 'president', 'funsionario'])


def _group(request):
	return request.user.groups.all()[0].name if request.user.groups.exists() else ''


def _page(request, title, **extra):
	g = _group(request)
	ctx = {'group': g, 'title': title, 'legend': title,
		   'can_manage': g in MANAGERS, 'can_view': g in VIEWERS, 'can_device': g in DEVICE_ADMINS}
	ctx.update(extra)
	return ctx


def _local_today():
	return svc.to_local(timezone.now()).date()


# ============================================================ kiosk (halaman scan)

def kiosk(request):
	"""Halaman layar penuh untuk PC pintu kantor. Tanpa login: yang dilindungi adalah kunci perangkat."""
	return render(request, 'attendance/qr/kiosk.html', {
		'tz': svc.conf('TIME_ZONE'), 'late_after': svc.conf('LATE_AFTER'), 'work_end': svc.conf('WORK_END'),
	})


def _device_from(request):
	return svc.get_device(request.headers.get('X-Kiosk-Key', ''))


@csrf_exempt
@require_POST
def kiosk_scan(request):
	device = _device_from(request)
	if not device:
		return JsonResponse({'ok': False, 'code': 'device', 'level': 'error', 'title': 'Perangkat belum terdaftar',
							 'message': 'Kunci perangkat salah atau dinonaktifkan. Hubungi admin.'}, status=401)
	try:
		code = json.loads(request.body.decode() or '{}').get('code', '')
	except (ValueError, UnicodeDecodeError):
		code = ''
	if not isinstance(code, str) or len(code) > 200:
		code = ''
	timezone_now = timezone.now()
	if not device.last_seen or (timezone_now - device.last_seen).total_seconds() > 60:
		ScanDevice.objects.filter(pk=device.pk).update(last_seen=timezone_now)
	res = svc.process_scan(code, device=device)
	res.pop('employee_id', None)
	return JsonResponse(res)


def kiosk_check(request):
	"""Dipakai halaman kiosk untuk memeriksa kunci perangkat saat pertama kali dipasang."""
	return JsonResponse({'ok': bool(_device_from(request))}, status=200 if _device_from(request) else 401)


# ============================================================ papan hari ini

@login_required
@allowed_users(allowed_roles=VIEWERS)
def today(request):
	d = _local_today()
	reason = svc.day_off_reason(d)
	active = Employee.objects.filter(status_id=svc.conf('ACTIVE_STATUS_ID'))
	total = active.count()
	rows = (Attendance.objects.filter(date=d, scantime__isnull=False)
			.select_related('employee', 'div', 'scantime').order_by('-scantime__check_in'))
	on_leave = (Leaves.objects.filter(is_active=True, is_confirm=True, start_date__lte=d, end_date__gte=d,
									  employee__in=active).values('employee').distinct().count())
	have_row = Attendance.objects.filter(date=d, employee__in=active).values('employee').distinct().count()
	stats = {
		'total': total, 'scanned': rows.count(), 'late': rows.filter(scantime__is_late=True).count(),
		'leave': on_leave, 'waiting': max(total - have_row - on_leave, 0),
	}
	logs = ScanLog.objects.select_related('employee', 'device').exclude(code__in=['in', 'in_late', 'out', 'out_early'])[:15]
	items = [{'att': a, 'in': svc.hhmm(a.scantime.check_in), 'out': svc.hhmm(a.scantime.check_out),
			  'early': bool(a.scantime.check_out) and svc.to_local(a.scantime.check_out).time() < svc.conf_time('WORK_END')}
			 for a in rows]
	for log in logs:
		log.local = svc.to_local(log.scanned_at).strftime('%H:%M:%S')
	return render(request, 'attendance/qr/today.html', _page(
		request, 'Absensi Hari Ini', date=d, off_reason=reason, stats=stats, items=items, logs=logs))


# ============================================================ perangkat kiosk

@login_required
@allowed_users(allowed_roles=DEVICE_ADMINS)
def devices(request):
	return render(request, 'attendance/qr/devices.html', _page(
		request, 'Perangkat Kiosk', devices=ScanDevice.objects.order_by('-id'), new_key=None))


@login_required
@allowed_users(allowed_roles=DEVICE_ADMINS)
@require_POST
def device_add(request):
	name = request.POST.get('name', '').strip()[:100]
	if not name:
		messages.warning(request, 'Isi nama perangkat, misalnya "PC Pintu Depan".')
		return redirect('qr-devices')
	key = svc.new_device_key()
	device = ScanDevice.objects.create(name=name, key_sha256=svc.hash_key(key),
									   datetime=timezone.now(), user=request.user)
	return render(request, 'attendance/qr/devices.html', _page(
		request, 'Perangkat Kiosk', devices=ScanDevice.objects.order_by('-id'), new_key=key, new_device=device))


@login_required
@allowed_users(allowed_roles=DEVICE_ADMINS)
@require_POST
def device_toggle(request, pk):
	device = get_object_or_404(ScanDevice, pk=pk)
	device.is_active = not device.is_active
	device.save(update_fields=['is_active'])
	messages.success(request, 'Perangkat "%s" %s.' % (device.name, 'diaktifkan' if device.is_active else 'dinonaktifkan'))
	return redirect('qr-devices')


# ============================================================ kartu QR

def _qr_svg(text):
	img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, border=0, box_size=10,
					  error_correction=qrcode.constants.ERROR_CORRECT_M)
	buf = io.BytesIO()
	img.save(buf)
	svg = buf.getvalue().decode()
	return mark_safe(svg[svg.index('<svg'):])


def _filtered_employees(request):
	qs = (Employee.objects.filter(status_id=svc.conf('ACTIVE_STATUS_ID'))
		  .select_related('employeeposition__position', 'employeedivision__division', 'idfnumber', 'qrcard',
						  'employeecard').order_by('first_name', 'last_name'))
	div_id = request.GET.get('div', '')
	if div_id.isdigit():
		qs = qs.filter(svc.division_q(div_id))
	q = request.GET.get('q', '').strip()
	if q:
		from django.db.models import Q
		qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(idfnumber__nrf__icontains=q))
	return qs.distinct(), div_id, q


@login_required
@allowed_users(allowed_roles=MANAGERS)
def cards(request):
	qs, div_id, q = _filtered_employees(request)
	people = []
	for e in qs[:500]:
		info = svc.employee_info(e)
		qc = getattr(e, 'qrcard', None)
		info.update(qr=qc, printed=bool(qc and qc.printed_at))
		people.append(info)
	return render(request, 'attendance/qr/cards.html', _page(
		request, 'Kartu QR Pegawai', people=people, divisions=Division.objects.order_by('name'),
		div_id=div_id, q=q, limited=qs.count() > 500))


def _card_context(emp, qc):
	info = svc.employee_info(emp)
	member = getattr(emp, 'employeecard', None)
	info.update(qr=_qr_svg(svc.make_payload(qc)), expiry=member.expiry_date if member else None,
				number=info['nrf'] or 'MDI-%05d' % emp.pk)
	return info


@login_required
@allowed_users(allowed_roles=MANAGERS)
@require_POST
def card_print(request):
	"""Membuat kartu QR yang belum ada, lalu menampilkan halaman cetak (depan + belakang)."""
	ids = [int(i) for i in request.POST.getlist('ids') if i.isdigit()][:500]
	if not ids:
		messages.warning(request, 'Pilih minimal satu pegawai.')
		return redirect('qr-cards')
	emps = (Employee.objects.filter(pk__in=ids, status_id=svc.conf('ACTIVE_STATUS_ID'))
			.select_related('employeeposition__position', 'employeedivision__division', 'idfnumber', 'employeecard',
							'photo').order_by('first_name', 'last_name'))
	items = []
	for e in emps:
		qc, _ = QRCard.objects.get_or_create(employee=e, defaults={'datetime': timezone.now(), 'user': request.user})
		if not qc.is_active:
			continue
		items.append(_card_context(e, qc))
	return render(request, 'attendance/qr/card_print.html', {
		'items': items, 'ids': [i['id'] for i in items],
		'note': _groups('CARD_BACK_NOTE',
						'Kartu ini milik pegawai Mata Dalan Institute. Scan QR pada alat absensi saat masuk dan pulang. '
						'Kartu bersifat pribadi dan tidak boleh dipinjamkan. Jika menemukan kartu ini, '
						'harap kembalikan ke kantor Mata Dalan Institute.'),
		'contact': _groups('CARD_CONTACT', ''),
	})


@login_required
@allowed_users(allowed_roles=MANAGERS)
@require_POST
def card_action(request):
	action = request.POST.get('action')
	ids = [int(i) for i in request.POST.getlist('ids') if i.isdigit()]
	qcs = QRCard.objects.filter(employee_id__in=ids)
	if action == 'printed':
		now = timezone.now()
		qcs.update(printed_at=now)
		EmployeeCard.objects.filter(employee_id__in=ids).update(is_printed=True, issue_date=svc.to_local(now).date())
		messages.success(request, '%d kartu ditandai sudah dicetak.' % len(ids))
	elif action == 'regen':
		for qc in qcs:
			qc.version += 1
			qc.is_active = True
			qc.printed_at = None
			qc.save()
		messages.success(request, 'QR baru dibuat. Kartu lama tidak berlaku lagi, silakan cetak ulang.')
	elif action == 'toggle':
		for qc in qcs:
			qc.is_active = not qc.is_active
			qc.save(update_fields=['is_active'])
		messages.success(request, 'Status kartu diubah.')
	return redirect('qr-cards')


# ============================================================ rekap

def _period(request):
	d = _local_today()
	try:
		year, month = int(request.GET.get('year', d.year)), int(request.GET.get('month', d.month))
		datetime.date(year, month, 1)
	except ValueError:
		year, month = d.year, d.month
	return year, month


@login_required
@allowed_users(allowed_roles=VIEWERS)
def report(request):
	year, month = _period(request)
	div_id = request.GET.get('div', '')
	div = Division.objects.filter(pk=div_id).first() if div_id.isdigit() else None
	rep = svc.month_report(year, month, division=div)
	return render(request, 'attendance/qr/report.html', _page(
		request, 'Rekap Absensi', rep=rep, year=year, month=month, monthname=f_monthname(month), div=div,
		div_id=div_id, divisions=Division.objects.order_by('name'), years=range(year - 3, year + 2),
		months=[(i, f_monthname(i)) for i in range(1, 13)], today=_local_today()))


_CODE_LABEL = {'P': 'Hadir', 'L': 'Cuti', 'F': 'Alpha', 'W': 'Libur', 'H': 'Libur', '': ''}


def _export_rows(rep):
	head = ['NRF', 'Nama', 'Jabatan', 'Divisi'] + [str(d) for d in rep['days']] + \
		   ['Hari kerja', 'Hadir', 'Terlambat', 'Cuti', 'Alpha', 'Belum scan pulang', 'Pulang awal']
	yield head
	for r in rep['rows']:
		e, t = r['employee'], r['totals']
		cells = []
		for c in r['cells']:
			txt = c['code']
			if c['late']:
				txt += ' (T)'
			cells.append(txt)
		yield [e['nrf'], e['name'], e['position'], e['division']] + cells + \
			  [t['workdays'], t['present'], t['late'], t['leave'], t['absent'], t['no_out'], t['early']]


@login_required
@allowed_users(allowed_roles=VIEWERS)
def report_export(request):
	year, month = _period(request)
	div_id = request.GET.get('div', '')
	div = Division.objects.filter(pk=div_id).first() if div_id.isdigit() else None
	rep = svc.month_report(year, month, division=div)
	name = 'absensi_%d_%02d' % (year, month)
	if request.GET.get('fmt') == 'xlsx':
		try:
			from openpyxl import Workbook
		except ImportError:
			messages.warning(request, 'Excel (.xlsx) belum tersedia. Pasang openpyxl atau pakai CSV.')
			return redirect('qr-report')
		wb = Workbook()
		ws = wb.active
		ws.title = 'Absensi'
		for row in _export_rows(rep):
			ws.append(row)
		resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
		resp['Content-Disposition'] = 'attachment; filename="%s.xlsx"' % name
		wb.save(resp)
		return resp
	resp = HttpResponse(content_type='text/csv; charset=utf-8')
	resp['Content-Disposition'] = 'attachment; filename="%s.csv"' % name
	resp.write('﻿')   # supaya Excel membaca UTF-8 dengan benar
	csv.writer(resp).writerows(_export_rows(rep))
	return resp


@login_required
@allowed_users(allowed_roles=EVERYONE)
def my_attendance(request):
	link = EmployeeUser.objects.filter(user=request.user).select_related('employee').first()
	year, month = _period(request)
	rep = svc.month_report(year, month, employee=link.employee) if link else None
	return render(request, 'attendance/qr/me.html', _page(
		request, 'Absensi Saya', rep=rep, row=rep['rows'][0] if rep and rep['rows'] else None,
		year=year, month=month, monthname=f_monthname(month), today=_local_today(),
		years=range(year - 3, year + 2), months=[(i, f_monthname(i)) for i in range(1, 13)]))
