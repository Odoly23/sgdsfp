import csv
import datetime
import io
import ipaddress
import json
from functools import wraps

import qrcode
import qrcode.constants
import qrcode.image.svg
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.db.models import Q
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
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
from attendance import qr_i18n as i18n
from attendance import qr_service as svc
from attendance.models import Attendance
from attendance.models_qr import AttendanceTime, OfficeNetwork, QRCard, ScanDevice, ScanLog


# ============================================================ akses

def qr_access(groups_setting):
	"""Login + keanggotaan grup (salah satu grup pengguna, bukan hanya yang pertama). Tidak berhak = HTTP 403.
	`groups_setting` adalah nama setelan, mis. 'VIEWER_GROUPS', dibaca saat permintaan sehingga mudah diubah."""
	def deco(fn):
		@wraps(fn)
		def inner(request, *args, **kwargs):
			if not request.user.is_authenticated:
				return redirect_to_login(request.get_full_path())
			if not svc.in_groups(request.user, svc.conf(groups_setting)):
				return render(request, 'home/403.html', status=403)
			return fn(request, *args, **kwargs)
		return inner
	return deco


def _api(groups_setting):
	"""Endpoint JSON: 401/403 berupa JSON (bukan halaman login atau 403 HTML), hanya GET, tidak di-cache."""
	def deco(fn):
		@wraps(fn)
		@require_GET
		def inner(request, *a, **kw):
			if not request.user.is_authenticated:
				return JsonResponse({'error': 'login'}, status=401)
			if not svc.in_groups(request.user, svc.conf(groups_setting)):
				return JsonResponse({'error': 'forbidden'}, status=403)
			resp = fn(request, *a, **kw)
			resp['Cache-Control'] = 'no-store'
			return resp
		return inner
	return deco


def _group(request):
	return request.user.groups.all()[0].name if request.user.groups.exists() else ''


def _lang(request):
	return i18n.get_lang(request)


def _tr(request, key, **params):
	return i18n.tr(_lang(request), key, **params)


def _page(request, title_key, **extra):
	u = request.user
	title = _tr(request, title_key)
	ctx = {'group': _group(request), 'title': title, 'legend': title, 'lang': _lang(request),
		   'can_manage': svc.in_groups(u, svc.conf('MANAGER_GROUPS')), 'can_view': svc.in_groups(u, svc.conf('VIEWER_GROUPS')),
		   'can_device': svc.in_groups(u, svc.conf('DEVICE_GROUPS')),
		   'security_open': svc.security_is_open(), 'navbar_lang': bool(getattr(settings, 'ATTENDANCE_LANG_IN_NAVBAR', False))}
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
		resp.set_cookie(i18n.COOKIE, code, max_age=i18n.COOKIE_AGE, samesite='Lax', secure=request.is_secure())
	return resp


def _cookie_path():
	return reverse('qr-today').rsplit('today/', 1)[0]      # hanya terkirim ke alamat /attendance/qr/


def _set_kiosk_cookie(resp, request, key):
	"""Titipkan token PC kiosk ke browser ini: HttpOnly (tidak terbaca JavaScript), SameSite=Strict, Secure di HTTPS."""
	resp.set_cookie(svc.conf('KIOSK_COOKIE'), key, max_age=5 * 365 * 86400, httponly=True, secure=request.is_secure(),
					samesite='Strict', path=_cookie_path())


# ============================================================ kiosk (halaman scan)

def kiosk(request):
	"""Halaman layar penuh untuk PC pintu kantor. Halamannya sendiri tidak berisi data; yang dilindungi adalah
	endpoint scan (jaringan kantor + PC yang dipasangkan admin)."""
	resp = render(request, 'attendance/qr/kiosk.html', {
		'tz': svc.conf('TIME_ZONE'), 'late_after': svc.conf('LATE_AFTER'), 'work_end': svc.conf('WORK_END'),
		'bundle': i18n.kiosk_bundle(), 'lang': _lang(request), 'langs': i18n.LANGS,
		'allow_camera': bool(svc.conf('ALLOW_CAMERA')),
	})
	resp['Cache-Control'] = 'no-store'
	resp['Referrer-Policy'] = 'same-origin'
	resp['X-Frame-Options'] = 'DENY'
	return resp


def _scan_error(lang, code, status):
	resp = JsonResponse({'ok': False, 'code': code, 'level': 'error', 'employee': None,
						 'title': i18n.tr(lang, 'res.%s.title' % code), 'message': i18n.tr(lang, 'res.%s.msg' % code)},
						status=status)
	resp['Cache-Control'] = 'no-store'
	return resp


@csrf_exempt   # aman: tanpa token CSRF, tetapi wajib header khusus X-Kiosk (peramban tidak bisa mengirimnya lintas situs
@require_POST  # tanpa preflight CORS), cookie penanda SameSite=Strict, dan hanya berlaku dari jaringan kantor.
def kiosk_scan(request):
	if request.headers.get('X-Kiosk') != '1':
		return JsonResponse({'ok': False, 'code': 'bad_request'}, status=400)
	try:
		length = int(request.META.get('CONTENT_LENGTH') or 0)
	except ValueError:
		length = 0
	if length > svc.conf('MAX_BODY_BYTES'):
		return JsonResponse({'ok': False, 'code': 'too_large'}, status=413)
	if not svc.throttle(request, 'scan'):
		return _scan_error(i18n.default_lang(), 'throttle', 429)
	try:
		body = json.loads(request.body.decode() or '{}')
		if not isinstance(body, dict):
			body = {}
	except (ValueError, UnicodeDecodeError):
		body = {}
	lang = i18n.norm(body.get('lang'))
	if not svc.network_ok(request):
		return _scan_error(lang, 'net', 403)
	device, _status = svc.resolve_device(request)         # salinan yang terdeteksi langsung dicabut di sini
	if svc.conf('REQUIRE_DEVICE_KEY') and not device:
		return _scan_error(lang, 'device', 401)
	code = body.get('code', '')
	if not isinstance(code, str) or len(code) > 200:
		code = ''
	if device:
		now = timezone.now()
		if not device.last_seen or (now - device.last_seen).total_seconds() > 60:
			ScanDevice.objects.filter(pk=device.pk).update(last_seen=now)
	res = svc.process_scan(code, device=device, lang=lang, ip=svc.client_ip(request))
	res.pop('employee_id', None)
	if res['employee']:      # data minimum: layar kiosk hanya butuh yang ditampilkan (bukan id internal, NRF, dsb.)
		res['employee'] = {k: res['employee'][k] for k in ('name', 'position', 'division', 'photo')}
	resp = JsonResponse(res)
	resp['Cache-Control'] = 'no-store'
	return resp


@require_GET
def kiosk_check(request):
	"""Denyut kiosk tiap 30 detik: server hidup? jaringan boleh? PC sudah dipasangkan?
	Denyut ini juga memutar token PC kiosk secara berkala (Set-Cookie baru) sehingga salinan cookie cepat basi."""
	if not svc.throttle(request, 'check'):
		return JsonResponse({'ok': False, 'state': 'throttle'}, status=429)
	if not svc.network_ok(request):
		resp = JsonResponse({'ok': False, 'state': 'net'}, status=403)
	else:
		device, status = svc.resolve_device(request)
		if svc.conf('REQUIRE_DEVICE_KEY') and not device:
			resp = JsonResponse({'ok': False, 'state': 'key'}, status=401)
		else:
			resp = JsonResponse({'ok': True, 'state': 'ok'})
			if device and (status == 'prev' or svc.needs_rotation(device)):
				new_key = svc.rotate_device(device)
				if new_key:
					_set_kiosk_cookie(resp, request, new_key)
	resp['Cache-Control'] = 'no-store'
	return resp


# ============================================================ papan hari ini

@qr_access('VIEWER_GROUPS')
def today(request):
	lang = _lang(request)
	d = _local_today()
	off = svc.day_off_reason(d)
	active = Employee.objects.filter(status_id=svc.conf('ACTIVE_STATUS_ID'))
	rows = list(Attendance.objects.filter(date=d, scantime__isnull=False)
				.select_related('employee', 'div', 'scantime').order_by('-scantime__check_in'))
	items = [{'att': a, 'in': svc.hhmm(a.scantime.check_in), 'out': svc.hhmm(a.scantime.check_out),
			  'flagged': a.scantime.flagged, 'st_id': a.scantime.pk,
			  'early': bool(a.scantime.check_out) and svc.to_local(a.scantime.check_out).time() < svc.conf_time('WORK_END')}
			 for a in rows]
	total = active.count()
	on_leave = (Leaves.objects.filter(is_active=True, is_confirm=True, start_date__lte=d, end_date__gte=d,
									  employee__in=active).values('employee').distinct().count())
	have_row = Attendance.objects.filter(date=d, employee__in=active).values('employee').distinct().count()
	stats = {'total': total, 'scanned': len(items), 'late': sum(1 for a in rows if a.scantime.is_late),
			 'flagged': sum(1 for a in rows if a.scantime.flagged),
			 'leave': on_leave, 'waiting': max(total - have_row - on_leave, 0)}
	logs = ScanLog.objects.select_related('employee', 'device').exclude(code__in=['in', 'in_late', 'out', 'out_early'])[:15]
	for log in logs:
		log.local = svc.to_local(log.scanned_at).strftime('%H:%M:%S')
		key = 'res.%s.title' % log.code
		log.label = i18n.tr(lang, key) if key in i18n.MESSAGES else log.code
	week_ago = timezone.now() - datetime.timedelta(days=7)
	alerts = [{'text': i18n.tr(lang, 'today.reuse_alert', name=a.device.name if a.device else '?', ip=a.ip or '?',
							   time=svc.to_local(a.scanned_at).strftime('%d/%m %H:%M'))}
			  for a in ScanLog.objects.filter(code='device_reuse', scanned_at__gte=week_ago).select_related('device')[:3]]
	return render(request, 'attendance/qr/today.html', _page(
		request, 'today.title', date=d, date_text=i18n.fmt_date(d, lang), stats=stats, items=items, logs=logs, active='today',
		alerts=alerts, off_text=i18n.reason_text(off[0], off[1], lang) if off else ''))


@qr_access('MANAGER_GROUPS')
@require_POST
def scan_review(request, pk):
	"""Admin/staff menandai scan beruntun sebagai sudah dicek (menghapus tanda)."""
	st = get_object_or_404(AttendanceTime, pk=pk)
	st.flagged = False
	st.save(update_fields=['flagged'])
	messages.success(request, _tr(request, 'msg.reviewed'))
	return redirect('qr-today')


# ============================================================ PC kiosk (pemasangan)

@qr_access('DEVICE_GROUPS')
def devices(request):
	my_ip = svc.client_ip(request)
	settings_nets = svc.allowed_nets()
	return render(request, 'attendance/qr/devices.html', _page(
		request, 'dev.title', devices=ScanDevice.objects.order_by('-id'), this_device=svc.device_from_request(request),
		active='devices', my_ip=my_ip or '?', my_ip_ok=bool(my_ip) and svc.ip_allowed(my_ip),
		networks=OfficeNetwork.objects.order_by('-id'),
		settings_nets=', '.join(settings_nets) if settings_nets else '', network_open=settings_nets is None,
		ip_check=svc.network_enforced()))


@qr_access('DEVICE_GROUPS')
@require_POST
def device_pair(request):
	"""Admin menekan "Jadikan PC ini kiosk" di PC pintu: server membuat kunci acak, hanya sidik SHA-256 yang disimpan,
	dan kunci aslinya dititipkan ke browser ini sebagai cookie HttpOnly (tidak bisa dibaca JavaScript)."""
	name = request.POST.get('name', '').strip()[:100] or _tr(request, 'dev.pair_default')
	previous = svc.device_from_request(request)
	if previous:
		ScanDevice.objects.filter(pk=previous.pk).update(is_active=False)      # satu browser = satu pemasangan aktif
	key = svc.new_device_key()
	ScanDevice.objects.create(name=name, key_sha256=svc.hash_key(key), datetime=timezone.now(), user=request.user,
							  rotated_at=timezone.now())
	if request.POST.get('allow_ip') == '1' and svc.network_enforced():                     # satu klik: PC kiosk + IP kantor (perlu di VPS/internet)
		_allow_ip(request, svc.client_ip(request), name)
	resp = redirect('qr-devices')
	_set_kiosk_cookie(resp, request, key)
	messages.success(request, _tr(request, 'dev.paired', name=name))
	return resp


def _allow_ip(request, raw, note=''):
	"""Mendaftarkan IP/jaringan kantor. Mengembalikan 'invalid'/'broad' bila ditolak, selain itu cidr yang tersimpan."""
	cidr, err = svc.broad_net_error(raw)
	if err:
		return err
	if not svc.ip_allowed(raw.split('/')[0]):
		obj, created = OfficeNetwork.objects.get_or_create(
			cidr=cidr, defaults={'note': note[:100], 'datetime': timezone.now(), 'user': request.user})
		if not created and not obj.is_active:
			obj.is_active = True
			obj.save(update_fields=['is_active'])
		svc.forget_office_networks()
	return cidr


@qr_access('DEVICE_GROUPS')
@require_POST
def net_add(request):
	raw = request.POST.get('cidr', '').strip()[:64] or svc.client_ip(request)        # kosong = IP browser ini
	addr = svc._parse_ip(raw)
	if addr is not None and addr.version == 6:         # alamat IPv6 sementara berganti-ganti: daftarkan rentang /64-nya
		raw = str(ipaddress.ip_network((addr, 64), strict=False))
	result = _allow_ip(request, raw, request.POST.get('note', ''))
	if result in ('invalid', 'broad'):
		messages.warning(request, _tr(request, 'net.msg_invalid' if result == 'invalid' else 'net.msg_broad'))
	else:
		messages.success(request, _tr(request, 'net.msg_added', cidr=result))
	return redirect('qr-devices')


@qr_access('DEVICE_GROUPS')
@require_POST
def net_toggle(request, pk):
	net = get_object_or_404(OfficeNetwork, pk=pk)
	net.is_active = not net.is_active
	net.save(update_fields=['is_active'])
	svc.forget_office_networks()
	messages.success(request, _tr(request, 'net.msg_on' if net.is_active else 'net.msg_off', cidr=net.cidr))
	return redirect('qr-devices')


@qr_access('DEVICE_GROUPS')
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
	return mark_safe(svg[svg.index('<svg'):])     # aman: isinya dibuat pustaka dari data kita sendiri (angka dan hex)


def _filtered_employees(request):
	qs = (Employee.objects.filter(status_id=svc.conf('ACTIVE_STATUS_ID'))
		  .select_related('employeeposition__position', 'employeedivision__division', 'idfnumber', 'qrcard',
						  'employeecard').order_by('first_name', 'last_name'))
	div_id = request.GET.get('div', '')
	if div_id.isdigit():
		qs = qs.filter(svc.division_q(div_id))
	q = request.GET.get('q', '').strip()[:80]
	if q:
		qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(idfnumber__nrf__icontains=q))
	return qs.distinct(), div_id, q


@qr_access('MANAGER_GROUPS')
def cards(request):
	qs, div_id, q = _filtered_employees(request)
	people = []
	for e in qs[:500]:
		info = svc.employee_info(e, photo=False)          # daftar tidak butuh foto: hindari 1 query per pegawai
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


@qr_access('MANAGER_GROUPS')
@require_POST
def card_print(request):
	"""Membuat kartu QR yang belum ada (sekaligus, bukan satu per satu), lalu menampilkan halaman cetak.
	Bahasa kartu bisa diganti langsung di halaman cetak lewat bendera."""
	ids = [int(i) for i in request.POST.getlist('ids') if i.isdigit()][:500]
	if not ids:
		messages.warning(request, _tr(request, 'msg.select'))
		return redirect('qr-cards')
	emps = list(Employee.objects.filter(pk__in=ids, status_id=svc.conf('ACTIVE_STATUS_ID'))
				.select_related('employeeposition__position', 'employeedivision__division', 'idfnumber', 'employeecard',
								'photo').order_by('first_name', 'last_name'))
	cards_by_emp = {q.employee_id: q for q in QRCard.objects.filter(employee__in=emps)}
	missing = [QRCard(employee=e, datetime=timezone.now(), user=request.user) for e in emps if e.pk not in cards_by_emp]
	if missing:
		QRCard.objects.bulk_create(missing)
		cards_by_emp.update({q.employee_id: q for q in missing})
	items = [_card_context(e, cards_by_emp[e.pk]) for e in emps if cards_by_emp[e.pk].is_active]
	cur = _lang(request)
	resp = render(request, 'attendance/qr/card_print.html', {
		'items': items, 'ids': [i['id'] for i in items], 'lang': cur, 'title': _tr(request, 'cp.title'),
		'notes': [{'code': c, 'hidden': c != cur, 'note': i18n.note_for(c), 'contact': i18n.contact_for(c)} for c in i18n.LANGS],
	})
	resp['Cache-Control'] = 'no-store'      # halaman berisi QR pribadi: jangan disimpan di cache peramban/proxy
	return resp


@qr_access('MANAGER_GROUPS')
@require_POST
def card_action(request):
	action = request.POST.get('action')
	ids = [int(i) for i in request.POST.getlist('ids') if i.isdigit()][:500]
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


def _division(request):
	div_id = request.GET.get('div', '')
	return Division.objects.filter(pk=div_id).first() if div_id.isdigit() else None


def _org_report(year, month, div):
	"""Rekap seluruh pegawai: dihitung sekali per menit dan dipakai bersama oleh semua pimpinan."""
	return svc.cached(('rep', year, month, div.pk if div else None), lambda: svc.month_report(year, month, division=div))


def _org_overview(year, month, div):
	return svc.cached(('ov', year, month, div.pk if div else None),
					  lambda: svc.overview(svc.month_report(year, month, division=div)))


@qr_access('VIEWER_GROUPS')
def report(request):
	lang = _lang(request)
	year, month = _period(request)
	div = _division(request)
	rep = _org_report(year, month, div)
	return render(request, 'attendance/qr/report.html', _page(
		request, 'rep.title', rep=rep, year=year, month=month, monthname=i18n.month_name(month, lang), div=div,
		div_id=str(div.pk) if div else '', divisions=Division.objects.order_by('name'), years=range(year - 3, year + 2),
		months=_months(lang), today=_local_today(), active='report', wd_short=i18n.weekday_short(lang)))


def _safe_cell(v):
	"""Cegah injeksi rumus Excel: teks yang diawali = + - @ dianggap rumus saat file dibuka."""
	return "'" + v if isinstance(v, str) and v[:1] in ('=', '+', '-', '@', '\t', '\r') else v


def _export_rows(rep, lang):
	t = lambda k: i18n.tr(lang, k)
	yield ['NRF', t('th.name'), t('th.position'), t('th.division')] + [str(d) for d in rep['days']] + \
		  [t('th.workdays'), t('th.present'), t('th.late'), t('th.leave'), t('th.absent'), t('csv.noout'), t('csv.early'), t('csv.flag')]
	for r in rep['rows']:
		e, tot = r['employee'], r['totals']
		cells = [c['code'] + (' (T)' if c['late'] else '') for c in r['cells']]
		yield [_safe_cell(e['nrf']), _safe_cell(e['name']), _safe_cell(e['position']), _safe_cell(e['division'])] + cells + \
			  [tot['workdays'], tot['present'], tot['late'], tot['leave'], tot['absent'], tot['no_out'], tot['early'], tot['flagged']]


class _Echo:
	def write(self, value):
		return value


def _stream_csv(rows):
	"""CSV dialirkan baris demi baris: memori server tetap kecil walau pegawainya ribuan."""
	writer = csv.writer(_Echo())
	yield '﻿'            # supaya Excel membaca UTF-8 dengan benar
	for row in rows:
		yield writer.writerow(row)


@qr_access('VIEWER_GROUPS')
def report_export(request):
	lang = _lang(request)
	year, month = _period(request)
	rep = _org_report(year, month, _division(request))
	name = 'absensi_%d_%02d' % (year, month)
	if request.GET.get('fmt') == 'xlsx':
		try:
			from openpyxl import Workbook
		except ImportError:
			messages.warning(request, i18n.tr(lang, 'msg.noxlsx'))
			return redirect('qr-report')
		wb = Workbook(write_only=True)            # mode tulis-saja: tidak menyimpan seluruh lembar di memori
		ws = wb.create_sheet('Absensi')
		for row in _export_rows(rep, lang):
			ws.append(row)
		buf = io.BytesIO()
		wb.save(buf)
		resp = HttpResponse(buf.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
		resp['Content-Disposition'] = 'attachment; filename="%s.xlsx"' % name
		return resp
	resp = StreamingHttpResponse(_stream_csv(_export_rows(rep, lang)), content_type='text/csv; charset=utf-8')
	resp['Content-Disposition'] = 'attachment; filename="%s.csv"' % name
	return resp


# ============================================================ laporan: Tabular dan Grafiku

def _linked(request):
	link = EmployeeUser.objects.filter(user=request.user).select_related('employee').first()
	return link.employee if link else None


def _qs(year, month, div=None):
	return 'year=%d&month=%d%s' % (year, month, '&div=%d' % div.pk if div else '')


def _series(lang, ontime, late, leave, absent):
	names = {k: i18n.tr(lang, 'legend.' + k) for k in ('present', 'late', 'leave', 'absent')}
	return [{'key': k, 'name': names[k], 'data': d} for k, d in
			(('present', ontime), ('late', late), ('leave', leave), ('absent', absent))]


@qr_access('VIEWER_GROUPS')
def dash(request):
	"""Dashboard Tabular untuk pimpinan: angka total, ringkasan per diresaun, dan paling sering terlambat."""
	lang = _lang(request)
	year, month = _period(request)
	div = _division(request)
	return render(request, 'attendance/qr/dash.html', _page(
		request, 'dash.title', ov=_org_overview(year, month, div), year=year, month=month,
		monthname=i18n.month_name(month, lang), div=div, div_id=str(div.pk) if div else '',
		divisions=Division.objects.order_by('name'), years=range(year - 3, year + 2),
		months=_months(lang), active='dash', scope='dash', page_mode='tab', qs=_qs(year, month, div)))


@qr_access('VIEWER_GROUPS')
def dash_chart(request):
	lang = _lang(request)
	year, month = _period(request)
	div = _division(request)
	return render(request, 'attendance/qr/dash_chart.html', _page(
		request, 'dash.title', year=year, month=month, monthname=i18n.month_name(month, lang), div=div,
		div_id=str(div.pk) if div else '', divisions=Division.objects.order_by('name'), years=range(year - 3, year + 2),
		months=_months(lang), active='dash', scope='dash', page_mode='chart', qs=_qs(year, month, div),
		api_url=reverse('qr-api-dash') + '?' + _qs(year, month, div)))


@_api('VIEWER_GROUPS')
def api_dash(request):
	lang = _lang(request)
	year, month = _period(request)
	ov = _org_overview(year, month, _division(request))
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


@qr_access('SELF_GROUPS')
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


@qr_access('SELF_GROUPS')
def my_chart(request):
	lang = _lang(request)
	emp = _linked(request)
	year, month = _period(request)
	return render(request, 'attendance/qr/me_chart.html', _page(
		request, 'me.title', linked=bool(emp), year=year, month=month, monthname=i18n.month_name(month, lang),
		years=range(year - 3, year + 2), months=_months(lang), active='me', scope='me', page_mode='chart',
		qs=_qs(year, month), api_url=reverse('qr-api-me') + '?' + _qs(year, month)))


@_api('SELF_GROUPS')
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
