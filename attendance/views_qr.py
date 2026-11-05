import csv
import datetime
import io
import json
from functools import wraps

import qrcode
import qrcode.constants
import qrcode.image.svg
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from custom.models import Division
from employee.models import Employee, EmployeeCard, EmployeeUser
from leave.models import Leaves
from settings_app.decorators import allowed_users
from attendance import qr_i18n as i18n
from attendance import qr_service as svc
from attendance.models import Attendance
from attendance.models_qr import QRCard, ScanDevice, ScanLog


def _groups(name, default):
	return getattr(settings, 'ATTENDANCE_' + name, default)


# Siapa boleh apa. Bisa diubah di settings.py (lihat QR_INSTALL.md).
MANAGERS = _groups('MANAGER_GROUPS', ['admin', 'staff'])
VIEWERS = _groups('VIEWER_GROUPS', ['admin', 'staff', 'president'])
DEVICE_ADMINS = _groups('DEVICE_GROUPS', ['admin'])
EVERYONE = _groups('SELF_GROUPS', ['admin', 'staff', 'president', 'funsionario', 'employee'])


def _group(request):
	return request.user.groups.all()[0].name if request.user.groups.exists() else ''


def _lang(request):
	return i18n.get_lang(request)


def _tr(request, key, **params):
	return i18n.tr(_lang(request), key, **params)


def _page(request, title_key, **extra):
	g = _group(request)
	title = _tr(request, title_key)
	ctx = {'group': g, 'title': title, 'legend': title, 'lang': _lang(request),
		   'can_manage': g in MANAGERS, 'can_view': g in VIEWERS, 'can_device': g in DEVICE_ADMINS,
		   'security_open': svc.security_is_open(), 'navbar_lang': bool(_groups('LANG_IN_NAVBAR', False))}
	ctx.update(extra)
	return ctx


def _local_today():
	return svc.to_local(timezone.now()).date()


def _months(lang):
	return [(i, i18n.month_name(i, lang)) for i in range(1, 13)]


# ============================================================ bahasa

def set_lang(request, code):
	"""Dipakai tombol bendera di halaman server: simpan pilihan di cookie lalu kembali ke halaman asal."""
	nxt = request.GET.get('next', '')
	if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
		nxt = reverse('qr-kiosk')
	resp = redirect(nxt)
	if code in i18n.LANGS:
		resp.set_cookie(i18n.COOKIE, code, max_age=i18n.COOKIE_AGE, samesite='Lax')
	return resp


# ============================================================ kiosk (halaman scan)

def kiosk(request):
	"""Halaman layar penuh untuk PC pintu kantor. Tanpa login dan tanpa setup: colok scanner USB, langsung jalan."""
	return render(request, 'attendance/qr/kiosk.html', {
		'tz': svc.conf('TIME_ZONE'), 'late_after': svc.conf('LATE_AFTER'), 'work_end': svc.conf('WORK_END'),
		'bundle': i18n.kiosk_bundle(), 'lang': _lang(request), 'langs': i18n.LANGS,
	})


def _device_from(request):
	return svc.get_device(request.headers.get('X-Kiosk-Key', ''))


def _error(request_lang, code, status):
	return JsonResponse({'ok': False, 'code': code, 'level': 'error', 'employee': None,
						 'title': i18n.tr(request_lang, 'res.%s.title' % code),
						 'message': i18n.tr(request_lang, 'res.%s.msg' % code)}, status=status)


@csrf_exempt
@require_POST
def kiosk_scan(request):
	try:
		body = json.loads(request.body.decode() or '{}')
		if not isinstance(body, dict):
			body = {}
	except (ValueError, UnicodeDecodeError):
		body = {}
	lang = i18n.norm(body.get('lang'))
	ip = svc.client_ip(request)
	if not svc.ip_allowed(ip):
		return _error(lang, 'net', 403)
	device = _device_from(request)
	if svc.conf('REQUIRE_DEVICE_KEY') and not device:
		return _error(lang, 'device', 401)
	code = body.get('code', '')
	if not isinstance(code, str) or len(code) > 200:
		code = ''
	if device:
		now = timezone.now()
		if not device.last_seen or (now - device.last_seen).total_seconds() > 60:
			ScanDevice.objects.filter(pk=device.pk).update(last_seen=now)
	res = svc.process_scan(code, device=device, lang=lang, ip=ip)
	res.pop('employee_id', None)
	return JsonResponse(res)


def kiosk_check(request):
	"""Dipanggil kiosk tiap beberapa detik: server hidup? jaringan boleh? perlu kunci?"""
	if not svc.ip_allowed(svc.client_ip(request)):
		return JsonResponse({'ok': False, 'state': 'net'}, status=403)
	if svc.conf('REQUIRE_DEVICE_KEY') and not _device_from(request):
		return JsonResponse({'ok': False, 'state': 'key'}, status=401)
	return JsonResponse({'ok': True, 'state': 'ok', 'require_key': bool(svc.conf('REQUIRE_DEVICE_KEY'))})


# ============================================================ papan hari ini

@login_required
@allowed_users(allowed_roles=VIEWERS)
def today(request):
	lang = _lang(request)
	d = _local_today()
	off = svc.day_off_reason(d)
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
		key = 'res.%s.title' % log.code
		log.label = i18n.tr(lang, key) if key in i18n.MESSAGES else log.code
	date_text = i18n.fmt_date(d, lang)
	return render(request, 'attendance/qr/today.html', _page(
		request, 'today.title', date=d, date_text=date_text, stats=stats, items=items, logs=logs, active='today',
		off_text=i18n.reason_text(off[0], off[1], lang) if off else ''))


# ============================================================ perangkat kiosk (opsional)

@login_required
@allowed_users(allowed_roles=DEVICE_ADMINS)
def devices(request):
	return render(request, 'attendance/qr/devices.html', _page(
		request, 'dev.title', devices=ScanDevice.objects.order_by('-id'), new_key=None, active='devices'))


@login_required
@allowed_users(allowed_roles=DEVICE_ADMINS)
@require_POST
def device_add(request):
	name = request.POST.get('name', '').strip()[:100]
	if not name:
		messages.warning(request, _tr(request, 'dev.need_name'))
		return redirect('qr-devices')
	key = svc.new_device_key()
	device = ScanDevice.objects.create(name=name, key_sha256=svc.hash_key(key),
									   datetime=timezone.now(), user=request.user)
	return render(request, 'attendance/qr/devices.html', _page(
		request, 'dev.title', devices=ScanDevice.objects.order_by('-id'), new_key=key, new_device=device, active='devices'))


@login_required
@allowed_users(allowed_roles=DEVICE_ADMINS)
@require_POST
def device_toggle(request, pk):
	device = get_object_or_404(ScanDevice, pk=pk)
	device.is_active = not device.is_active
	device.save(update_fields=['is_active'])
	messages.success(request, _tr(request, 'dev.msg_on' if device.is_active else 'dev.msg_off', name=device.name))
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
		request, 'cards.title', people=people, divisions=Division.objects.order_by('name'),
		div_id=div_id, q=q, limited=qs.count() > 500, active='cards'))


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
	"""Membuat kartu QR yang belum ada, lalu menampilkan halaman cetak (depan + belakang).
	Bahasa kartu bisa diganti langsung di halaman cetak lewat bendera."""
	ids = [int(i) for i in request.POST.getlist('ids') if i.isdigit()][:500]
	if not ids:
		messages.warning(request, _tr(request, 'msg.select'))
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
	cur = _lang(request)
	return render(request, 'attendance/qr/card_print.html', {
		'items': items, 'ids': [i['id'] for i in items], 'lang': cur, 'title': _tr(request, 'cp.title'),
		'notes': [{'code': c, 'hidden': c != cur, 'note': i18n.note_for(c), 'contact': i18n.contact_for(c)} for c in i18n.LANGS],
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
		messages.success(request, _tr(request, 'msg.printed', n=len(ids)))
	elif action == 'regen':
		for qc in qcs:
			qc.version += 1
			qc.is_active = True
			qc.printed_at = None
			qc.save()
		messages.success(request, _tr(request, 'msg.regen'))
	elif action == 'toggle':
		for qc in qcs:
			qc.is_active = not qc.is_active
			qc.save(update_fields=['is_active'])
		messages.success(request, _tr(request, 'msg.toggle'))
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
	lang = _lang(request)
	year, month = _period(request)
	div_id = request.GET.get('div', '')
	div = Division.objects.filter(pk=div_id).first() if div_id.isdigit() else None
	rep = svc.month_report(year, month, division=div)
	return render(request, 'attendance/qr/report.html', _page(
		request, 'rep.title', rep=rep, year=year, month=month, monthname=i18n.month_name(month, lang), div=div,
		div_id=div_id, divisions=Division.objects.order_by('name'), years=range(year - 3, year + 2),
		months=_months(lang), today=_local_today(), active='report', wd_short=i18n.weekday_short(lang)))


def _export_rows(rep, lang):
	t = lambda k: i18n.tr(lang, k)
	head = ['NRF', t('th.name'), t('th.position'), t('th.division')] + [str(d) for d in rep['days']] + \
		   [t('th.workdays'), t('th.present'), t('th.late'), t('th.leave'), t('th.absent'), t('csv.noout'), t('csv.early')]
	yield head
	for r in rep['rows']:
		e, tot = r['employee'], r['totals']
		cells = []
		for c in r['cells']:
			txt = c['code']
			if c['late']:
				txt += ' (T)'
			cells.append(txt)
		yield [e['nrf'], e['name'], e['position'], e['division']] + cells + \
			  [tot['workdays'], tot['present'], tot['late'], tot['leave'], tot['absent'], tot['no_out'], tot['early']]


@login_required
@allowed_users(allowed_roles=VIEWERS)
def report_export(request):
	lang = _lang(request)
	year, month = _period(request)
	div_id = request.GET.get('div', '')
	div = Division.objects.filter(pk=div_id).first() if div_id.isdigit() else None
	rep = svc.month_report(year, month, division=div)
	name = 'absensi_%d_%02d' % (year, month)
	if request.GET.get('fmt') == 'xlsx':
		try:
			from openpyxl import Workbook
		except ImportError:
			messages.warning(request, i18n.tr(lang, 'msg.noxlsx'))
			return redirect('qr-report')
		wb = Workbook()
		ws = wb.active
		ws.title = 'Absensi'
		for row in _export_rows(rep, lang):
			ws.append(row)
		resp = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
		resp['Content-Disposition'] = 'attachment; filename="%s.xlsx"' % name
		wb.save(resp)
		return resp
	resp = HttpResponse(content_type='text/csv; charset=utf-8')
	resp['Content-Disposition'] = 'attachment; filename="%s.csv"' % name
	resp.write('﻿')   # supaya Excel membaca UTF-8 dengan benar
	csv.writer(resp).writerows(_export_rows(rep, lang))
	return resp


# ============================================================ laporan: Tabular dan Grafiku

def _linked(request):
	link = EmployeeUser.objects.filter(user=request.user).select_related('employee').first()
	return link.employee if link else None


def _division(request):
	div_id = request.GET.get('div', '')
	return Division.objects.filter(pk=div_id).first() if div_id.isdigit() else None


def _qs(year, month, div=None):
	return 'year=%d&month=%d%s' % (year, month, '&div=%d' % div.pk if div else '')


def _api(groups):
	"""Endpoint JSON: 401/403 berupa JSON (bukan halaman login atau 403 HTML), hanya GET, tidak di-cache."""
	def deco(fn):
		@wraps(fn)
		@require_GET
		def inner(request, *a, **kw):
			if not request.user.is_authenticated:
				return JsonResponse({'error': 'login'}, status=401)
			if _group(request) not in groups:
				return JsonResponse({'error': 'forbidden'}, status=403)
			resp = fn(request, *a, **kw)
			resp['Cache-Control'] = 'no-store'
			return resp
		return inner
	return deco


def _series(lang, ontime, late, leave, absent):
	names = {k: i18n.tr(lang, 'legend.' + k) for k in ('present', 'late', 'leave', 'absent')}
	return [{'key': k, 'name': names[k], 'data': d} for k, d in
			(('present', ontime), ('late', late), ('leave', leave), ('absent', absent))]


@login_required
@allowed_users(allowed_roles=VIEWERS)
def dash(request):
	"""Dashboard Tabular untuk pimpinan: angka total, ringkasan per diresaun, dan paling sering terlambat."""
	lang = _lang(request)
	year, month = _period(request)
	div = _division(request)
	ov = svc.overview(svc.month_report(year, month, division=div))
	return render(request, 'attendance/qr/dash.html', _page(
		request, 'dash.title', ov=ov, year=year, month=month, monthname=i18n.month_name(month, lang), div=div,
		div_id=str(div.pk) if div else '', divisions=Division.objects.order_by('name'), years=range(year - 3, year + 2),
		months=_months(lang), active='dash', scope='dash', page_mode='tab', qs=_qs(year, month, div)))


@login_required
@allowed_users(allowed_roles=VIEWERS)
def dash_chart(request):
	lang = _lang(request)
	year, month = _period(request)
	div = _division(request)
	return render(request, 'attendance/qr/dash_chart.html', _page(
		request, 'dash.title', year=year, month=month, monthname=i18n.month_name(month, lang), div=div,
		div_id=str(div.pk) if div else '', divisions=Division.objects.order_by('name'), years=range(year - 3, year + 2),
		months=_months(lang), active='dash', scope='dash', page_mode='chart', qs=_qs(year, month, div),
		api_url=reverse('qr-api-dash') + '?' + _qs(year, month, div)))


@_api(VIEWERS)
def api_dash(request):
	lang = _lang(request)
	year, month = _period(request)
	ov = svc.overview(svc.month_report(year, month, division=_division(request)))
	divs, daily = ov['divisions'], ov['daily']
	minus = lambda a, b: [None if x is None else x - y for x, y in zip(a, b)]
	return JsonResponse({
		'divisions': {'label': [d['name'] for d in divs], 'series': _series(
			lang, [d['present'] - d['late'] for d in divs], [d['late'] for d in divs],
			[d['leave'] for d in divs], [d['absent'] for d in divs])},
		'daily': {'label': daily['label'], 'series': _series(
			lang, minus(daily['present'], daily['late']), daily['late'], daily['leave'], daily['absent'])},
		'late_top': {'label': [r['employee']['name'] for r in ov['late_top']], 'series': [
			{'key': 'late', 'name': i18n.tr(lang, 'legend.late'), 'data': [r['totals']['late'] for r in ov['late_top']]}]},
	})


_STATUS_FILTERS = {
	'present': lambda c: c['code'] == 'P', 'late': lambda c: c['code'] == 'P' and c['late'],
	'leave': lambda c: c['code'] == 'L', 'absent': lambda c: c['code'] == 'F',
	'early': lambda c: c['early'], 'noout': lambda c: c['no_out'],
}


def _day_rows(lang, rep_row, month, today, status):
	"""Baris harian untuk daftar: akhir pekan dan hari yang belum datang dilewati; libur tampil abu-abu.
	Hari ini tetap tampil (status "Belum") walau belum ada scan."""
	wd = i18n.weekday_short(lang)
	out = []
	for c in rep_row['cells']:
		if c['code'] == 'W' or c['date'] > today or (status and not _STATUS_FILTERS[status](c)):
			continue
		if c['code'] == 'P':
			kind, text = ('late' if c['late'] else 'present'), i18n.tr(lang, 'legend.late' if c['late'] else 'legend.present')
		elif c['code'] == 'L':
			kind, text = 'leave', i18n.tr(lang, 'legend.leave')
		elif c['code'] == 'F':
			kind, text = 'absent', i18n.tr(lang, 'legend.absent')
		elif c['code'] == 'H':
			kind, text = 'holiday', c['holiday'] or i18n.tr(lang, 'day.holiday')
		else:
			kind, text = 'pending', i18n.tr(lang, 'day.pending' if c['date'] >= today else 'day.none')
		out.append({'c': c, 'kind': kind, 'text': text, 'date_text': '%s %02d/%02d' % (wd[c['wd']], c['day'], month)})
	return out


@login_required
@allowed_users(allowed_roles=EVERYONE)
def my_attendance(request):
	"""Laporan Tabular pegawai: angka ringkas yang bisa diklik dan daftar harian."""
	lang = _lang(request)
	emp = _linked(request)
	year, month = _period(request)
	today = _local_today()
	rep = svc.month_report(year, month, employee=emp) if emp else None
	row = rep['rows'][0] if rep and rep['rows'] else None
	status = request.GET.get('status', '')
	status = status if status in _STATUS_FILTERS else ''
	return render(request, 'attendance/qr/me.html', _page(
		request, 'me.title', rep=rep, row=row, days=_day_rows(lang, row, month, today, status) if row else [],
		status=status, status_text=i18n.tr(lang, 'tile.' + status) if status else '',
		year=year, month=month, monthname=i18n.month_name(month, lang), today=today,
		years=range(year - 3, year + 2), months=_months(lang), active='me', scope='me', page_mode='tab',
		qs=_qs(year, month), wd_short=i18n.weekday_short(lang)))


@login_required
@allowed_users(allowed_roles=EVERYONE)
def my_chart(request):
	lang = _lang(request)
	emp = _linked(request)
	year, month = _period(request)
	return render(request, 'attendance/qr/me_chart.html', _page(
		request, 'me.title', linked=bool(emp), year=year, month=month, monthname=i18n.month_name(month, lang),
		years=range(year - 3, year + 2), months=_months(lang), active='me', scope='me', page_mode='chart',
		qs=_qs(year, month), api_url=reverse('qr-api-me') + '?' + _qs(year, month)))


@_api(EVERYONE)
def api_me(request):
	"""Data grafik pegawai: hanya milik pegawai yang tertaut ke akun ini (tidak menerima id dari klien)."""
	lang = _lang(request)
	emp = _linked(request)
	if not emp:
		return JsonResponse({'error': 'unlinked'}, status=404)
	year, month = _period(request)
	months = svc.year_summary(emp, year)
	col = lambda key: [t[key] for t in months]
	ontime = [t['present'] - t['late'] for t in months]
	cur = months[month - 1]
	keys = ['present', 'late', 'leave', 'absent']
	return JsonResponse({
		'year': {'label': i18n.month_short(lang), 'series': _series(lang, ontime, col('late'), col('leave'), col('absent'))},
		'status': {'label': [i18n.tr(lang, 'legend.' + k) for k in keys], 'keys': keys,
				   'obj': [cur['present'] - cur['late'], cur['late'], cur['leave'], cur['absent']]},
	})
