"""Logika absensi QR: tanda tangan QR, pemeriksaan scan, dan rekap bulanan.

Semua aturan jam dan grup bisa diubah di settings.py lewat nama ATTENDANCE_* (lihat QR_INSTALL.md).
"""
import datetime
import hashlib
import hmac
import ipaddress
import logging
import secrets
import time
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from employee.models import Employee
from leave.models import Leaves
from settings_app.utils import getnewid
from attendance import qr_i18n
from attendance.models import Attendance, AttendanceDiv, AttendanceStatus, NacionalHoliday, Tolerance
from attendance.models_qr import AttendanceTime, QRCard, ScanDevice, ScanLog

QR_PREFIX = 'MDI'
log = logging.getLogger('attendance.qr')

# Jaringan privat/lokal: bawaan untuk kiosk (alamat 127.x, 10.x, 172.16-31.x, 192.168.x dan IPv6 lokal).
PRIVATE_NETS = ['127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '::1/128', 'fc00::/7', 'fe80::/10']

_DEFAULTS = {
	'TIME_ZONE': 'Asia/Dili',
	'LATE_AFTER': '08:15',     # masuk setelah jam ini = terlambat
	'NOON': '12:00',           # scan sebelum jam ini = masuk (AM), sesudahnya = pulang (PM)
	'WORK_END': '16:00',       # pulang sebelum jam ini = pulang awal
	'DUPLICATE_SECONDS': 60,   # scan ganda dalam rentang ini diabaikan
	'ACTIVE_STATUS_ID': 1,     # Employee.status_id untuk pegawai aktif (sama seperti view lama)
	'PRESENT_CODE': 'P',       # AttendanceStatus.code untuk hadir
	'QR_SECRET': None,         # kunci tanda tangan QR; kosong = pakai SECRET_KEY
	'KIOSK_ALLOWED_IPS': PRIVATE_NETS,   # bawaan: hanya jaringan privat kantor; contoh ['192.168.1.0/24']; [] = TERBUKA (tidak aman)
	'REQUIRE_DEVICE_KEY': True,    # scan hanya dari PC kiosk yang sudah dipasangkan admin (penanda di browser PC itu)
	'ALLOW_CAMERA': False,         # kamera cadangan di kiosk; nonaktif agar hanya alat pembaca QR yang dipakai
	'TRUST_PROXY_HEADER': False,   # True bila server di belakang proxy tepercaya (pakai X-Forwarded-For)
	'THROTTLE_PER_MINUTE': 60,     # batas permintaan scan per alamat IP per menit
	'REPORT_CACHE_SECONDS': 60,    # cache dashboard/rekap seluruh pegawai; 0 = mati
	'HOLIDAY_CACHE_SECONDS': 60,   # cache daftar libur untuk jalur scan; 0 = mati
	'MAX_BODY_BYTES': 1024,        # batas ukuran permintaan scan
	'KIOSK_COOKIE': 'mdi_kiosk',
	'MANAGER_GROUPS': ['admin', 'staff'],
	'VIEWER_GROUPS': ['admin', 'staff', 'president'],
	'DEVICE_GROUPS': ['admin'],
	'SELF_GROUPS': ['admin', 'staff', 'president', 'funsionario', 'employee'],
}


def conf(name):
	return getattr(settings, 'ATTENDANCE_' + name, _DEFAULTS[name])


def group_of(user):
	"""Nama grup pertama pengguna, sama seperti allowed_users di proyek."""
	if not getattr(user, 'is_authenticated', False) or not user.groups.exists():
		return ''
	return user.groups.all()[0].name


def local_tz():
	return ZoneInfo(conf('TIME_ZONE'))


def conf_time(name):
	h, m = str(conf(name)).split(':')
	return datetime.time(int(h), int(m))


def to_local(dt):
	return dt.astimezone(local_tz())


def hhmm(dt):
	return to_local(dt).strftime('%H:%M') if dt else ''


# ---------------------------------------------------------------- QR (tanda tangan)

def _secret():
	return (conf('QR_SECRET') or settings.SECRET_KEY).encode()


def _sign(emp_id, version):
	msg = '{}.{}'.format(emp_id, version).encode()
	return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:32]


def make_payload(card):
	return '{}.{}.{}.{}'.format(QR_PREFIX, card.employee_id, card.version, _sign(card.employee_id, card.version))


def parse_payload(raw):
	"""Mengembalikan (employee_id, version) kalau tanda tangan benar, selain itu None.
	Masukan berasal dari pihak tak tepercaya: dibatasi panjang dan karakternya sebelum dihitung."""
	if not isinstance(raw, str) or len(raw) > 200:
		return None
	parts = raw.strip().split('.')
	if len(parts) != 4 or parts[0] != QR_PREFIX:
		return None
	if not (parts[1].isascii() and parts[1].isdigit() and parts[2].isascii() and parts[2].isdigit()):
		return None
	if len(parts[1]) > 18 or len(parts[2]) > 9 or len(parts[3]) != 32 or not parts[3].isascii():
		return None
	emp_id, version = int(parts[1]), int(parts[2])
	if not hmac.compare_digest(parts[3].encode('ascii'), _sign(emp_id, version).encode('ascii')):
		return None
	return emp_id, version


# ---------------------------------------------------------------- jaringan, PC kiosk, pembatasan

_PROXY_HEADERS = ('HTTP_X_FORWARDED_FOR', 'HTTP_FORWARDED', 'HTTP_X_REAL_IP')


def client_ip(request):
	if conf('TRUST_PROXY_HEADER'):
		xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
		if xff:
			return xff.split(',')[-1].strip()   # entri paling kanan ditambahkan proxy tepercaya
	return request.META.get('REMOTE_ADDR', '')


def allowed_nets():
	"""Daftar jaringan yang boleh scan; None bila sengaja dibuka (setelan [])."""
	nets = conf('KIOSK_ALLOWED_IPS')
	return list(nets) if nets else None


def ip_allowed(ip):
	nets = allowed_nets()
	if nets is None:
		return True
	try:
		addr = ipaddress.ip_address(ip)
	except ValueError:
		return False
	for net in nets:
		try:
			if addr in ipaddress.ip_network(net, strict=False):
				return True
		except ValueError:
			continue
	return False


def network_ok(request):
	"""Jaringan kantor saja. Bila ada header proxy tetapi proxy tidak dipercaya, alamat asli tidak diketahui:
	ditolak (gagal-aman), dan admin diberi tahu lewat log."""
	if allowed_nets() is None:
		return True
	if not conf('TRUST_PROXY_HEADER') and any(h in request.META for h in _PROXY_HEADERS):
		if cache.add('attqr:proxywarn', 1, 600):
			log.warning('Scan ditolak: ada header proxy tetapi ATTENDANCE_TRUST_PROXY_HEADER=False. '
						'Set True bila server di belakang Nginx/proxy tepercaya.')
		return False
	return ip_allowed(client_ip(request))


def security_is_open():
	"""True bila jaringan sengaja dibuka lebar (perlu peringatan ke admin)."""
	return allowed_nets() is None


def throttle(request, scope='scan'):
	"""Pembatas per IP per menit (cache). True = boleh. Melindungi basis data dari banjir permintaan."""
	limit = conf('THROTTLE_PER_MINUTE')
	if not limit:
		return True
	key = 'attqr:thr:%s:%s:%d' % (scope, client_ip(request), int(time.time() // 60))
	try:
		n = cache.incr(key)
	except ValueError:
		cache.add(key, 1, 90)
		n = 1
	return n <= limit


def hash_key(key):
	return hashlib.sha256(key.encode()).hexdigest()


def new_device_key():
	return secrets.token_urlsafe(32)


def get_device(key):
	if not key or len(key) > 128:
		return None
	return ScanDevice.objects.filter(key_sha256=hash_key(key), is_active=True).first()


def device_from_request(request):
	"""PC kiosk dikenali dari penanda (cookie HttpOnly) yang dipasang admin; header X-Kiosk-Key untuk klien API."""
	return get_device(request.COOKIES.get(conf('KIOSK_COOKIE')) or request.headers.get('X-Kiosk-Key', ''))


def in_groups(user, names):
	"""Apakah pengguna masuk salah satu grup (bukan hanya grup pertama). Hasil disimpan per permintaan."""
	if not getattr(user, 'is_authenticated', False):
		return False
	mine = getattr(user, '_qr_group_names', None)
	if mine is None:
		mine = user._qr_group_names = set(user.groups.values_list('name', flat=True))
	return bool(mine & set(names))


def cached(parts, fn):
	"""Cache singkat untuk laporan seluruh pegawai (dipakai bersama semua pimpinan)."""
	ttl = conf('REPORT_CACHE_SECONDS')
	if not ttl:
		return fn()
	key = 'attqr:rep:' + hashlib.sha256(repr(parts).encode()).hexdigest()[:40]
	value = cache.get(key)
	if value is None:
		value = fn()
		cache.set(key, value, ttl)
	return value


# ---------------------------------------------------------------- data pegawai

def division_q(div):
	"""Filter pegawai per divisi: sama dengan yang dipakai view absensi yang sudah ada."""
	return (Q(employeedivision__division=div) | Q(employeedivision__department__division=div) |
			Q(employeedivision__section__department__division=div))


def employee_division(emp):
	ed = getattr(emp, 'employeedivision', None)
	if not ed:
		return None
	if ed.division_id:
		return ed.division
	if ed.department_id:
		return ed.department.division
	if ed.section_id:
		return ed.section.department.division
	return None


def employee_info(emp, photo=True):
	pos = getattr(getattr(emp, 'employeeposition', None), 'position', None)
	div = employee_division(emp)
	nrf = getattr(getattr(emp, 'idfnumber', None), 'nrf', None)
	return {
		'id': emp.pk, 'name': str(emp).strip(), 'position': str(pos) if pos else '',
		'division': div.name if div else '', 'division_id': div.pk if div else None,
		'nrf': nrf or '', 'photo': photo_url(emp) if photo else '',
	}


def photo_url(emp):
	try:
		return emp.photo.image.url
	except Exception:
		return ''


# ---------------------------------------------------------------- hari libur dan cuti

def day_off_reason(d):
	"""(jenis, nama) bila hari ini bukan hari kerja, selain itu None. Jenis: weekend, holiday, tolerance.
	Libur dicocokkan lewat bulan dan tanggal, sama seperti view absensi yang sudah ada. Daftar libur disimpan
	singkat di cache (jalur scan paling sering dipanggil, sedangkan libur jarang berubah)."""
	if d.weekday() >= 5:
		return 'weekend', ''
	return _holidays_for_scan().get((d.month, d.day))


def holiday_map():
	"""{(bulan, tanggal): (jenis, nama)} dari NacionalHoliday dan Tolerance yang aktif, hanya 2 query.
	Urutan prioritas sama dengan day_off_reason: libur nasional menang atas hari toleransi."""
	out = {}
	for t in Tolerance.objects.exclude(is_active=False):
		out[(t.date.month, t.date.day)] = ('tolerance', t.name or '')
	for h in NacionalHoliday.objects.exclude(is_active=False):
		out[(h.date.month, h.date.day)] = ('holiday', h.name or '')
	return out


def _holidays_for_scan():
	ttl = conf('HOLIDAY_CACHE_SECONDS')
	if not ttl:
		return holiday_map()
	value = cache.get('attqr:hol')
	if value is None:
		value = holiday_map()
		cache.set('attqr:hol', value, ttl)
	return value


def active_leave(emp, d):
	return (Leaves.objects.filter(employee=emp, is_active=True, is_confirm=True,
			start_date__lte=d, end_date__gte=d).select_related('leave_type').first())


# ---------------------------------------------------------------- scan

def _result(ok, result_code, level, emp, lang, hide=False, **params):
	"""Hasil scan untuk layar kiosk. Judul dan pesan diambil dari qr_i18n (res.<result_code>.title / .msg)."""
	res = {'ok': ok, 'code': result_code, 'level': level,
		   'title': qr_i18n.tr(lang, 'res.%s.title' % result_code, **params),
		   'message': qr_i18n.tr(lang, 'res.%s.msg' % result_code, **params),
		   'employee': employee_info(emp) if emp and not hide else None, 'employee_id': emp.pk if emp else None}
	if 'time' in params:
		res['time'] = params['time']
	return res


def process_scan(raw, device=None, now=None, lang=None, ip=None):
	"""Memproses satu scan dan mencatatnya di ScanLog. Mengembalikan dict untuk layar kiosk."""
	now = now or timezone.now()
	lang = qr_i18n.norm(lang)
	res = _process(raw, device, now, lang)
	ScanLog.objects.create(employee_id=res['employee_id'], device=device, scanned_at=now,
						   code=res['code'], message=res['message'][:200], ip=ip or None)
	return res


def _process(raw, device, now, lang):
	parsed = parse_payload(raw)
	if not parsed:
		return _result(False, 'invalid', 'error', None, lang)
	emp_id, version = parsed
	emp = (Employee.objects.select_related('employeeposition__position', 'employeedivision__division',
										  'qrcard', 'employeecard', 'idfnumber', 'photo').filter(pk=emp_id).first())
	card = getattr(emp, 'qrcard', None) if emp else None
	if not emp or not card or not card.is_active or card.version != version:
		# kartu lama/hilang: jangan tampilkan siapa pemiliknya di layar kiosk (privasi); tetap tercatat di log
		return _result(False, 'revoked', 'error', emp, lang, hide=True)
	if emp.status_id != conf('ACTIVE_STATUS_ID'):
		return _result(False, 'inactive', 'error', emp, lang)

	local = to_local(now)
	today = local.date()
	member = getattr(emp, 'employeecard', None)
	if member and (not member.is_active or (member.expiry_date and member.expiry_date < today)):
		return _result(False, 'card_expired', 'error', emp, lang)

	off = day_off_reason(today)
	if off:
		return _result(False, 'holiday', 'error', emp, lang, reason=qr_i18n.reason_text(off[0], off[1], lang))

	leave = active_leave(emp, today)
	if leave:
		kind = leave.leave_type.name if leave.leave_type and leave.leave_type.name else qr_i18n.tr(lang, 'leave.default')
		return _result(False, 'leave', 'leave', emp, lang, kind=kind, end=leave.end_date.strftime('%d/%m/%Y'))

	div = employee_division(emp)
	if div and AttendanceDiv.objects.filter(div=div, year=today.year, month=today.month, is_final=True).exists():
		return _result(False, 'month_final', 'error', emp, lang)

	present = AttendanceStatus.objects.filter(code=conf('PRESENT_CODE')).first()
	if not present:
		return _result(False, 'config', 'error', emp, lang, code=conf('PRESENT_CODE'))

	with transaction.atomic():
		Employee.objects.select_for_update().get(pk=emp.pk)  # satu scan per pegawai pada satu waktu
		att = Attendance.objects.filter(employee=emp, date=today).order_by('id').first()
		st = getattr(att, 'scantime', None) if att else None
		if att and not st:
			return _result(False, 'manual', 'error', emp, lang)
		if not att:
			return _first_scan(emp, div, present, device, now, local, today, lang)
		return _next_scan(emp, att, st, present, now, local, lang)


def _first_scan(emp, div, present, device, now, local, today, lang):
	after_noon = local.time() >= conf_time('NOON')
	late = after_noon or local.time() > conf_time('LATE_AFTER')
	att = None
	for _ in range(3):
		newid, hashed = getnewid(Attendance)
		try:
			with transaction.atomic():
				att = Attendance.objects.create(
					id=newid, dg_id=div.dg_id if div else None, div=div, employee=emp,
					status_am=None if after_noon else present, status_pm=present if after_noon else None,
					date=today, desc='Scan QR', datetime=now, hashed=hashed)
			break
		except IntegrityError:
			continue
	if att is None:
		return _result(False, 'busy', 'error', emp, lang)
	_ensure_month_row(div, today)
	AttendanceTime.objects.create(attendance=att, check_in=now, check_out=now if after_noon else None,
								  is_late=late, device=device)
	if late:
		return _result(True, 'in_late', 'warn', emp, lang, time=hhmm(now), limit=conf('LATE_AFTER'))
	return _result(True, 'in', 'success', emp, lang, time=hhmm(now))


def _ensure_month_row(div, today):
	"""Alur manual membuat AttendanceDiv (untuk unggah PDF, konfirmasi, final) saat hari pertama disimpan.
	Scan melakukan hal yang sama supaya bulan itu muncul lengkap di daftar absensi lama."""
	if not div or AttendanceDiv.objects.filter(div=div, year=today.year, month=today.month).exists():
		return
	for _ in range(3):
		newid, _hash = getnewid(AttendanceDiv)
		try:
			with transaction.atomic():
				AttendanceDiv.objects.create(id=newid, div=div, year=today.year, month=today.month)
			return
		except IntegrityError:
			continue


def _next_scan(emp, att, st, present, now, local, lang):
	last = st.check_out or st.check_in
	if (now - last).total_seconds() < conf('DUPLICATE_SECONDS'):
		return _result(True, 'duplicate', 'info', emp, lang)
	if local.time() < conf_time('NOON'):
		return _result(True, 'already_in', 'info', emp, lang, time=hhmm(st.check_in))
	st.check_out = now
	st.save(update_fields=['check_out'])
	if att.status_pm_id != present.pk:
		att.status_pm = present
		att.save(update_fields=['status_pm'])
	if local.time() < conf_time('WORK_END'):
		return _result(True, 'out_early', 'warn', emp, lang, time=hhmm(now), limit=conf('WORK_END'))
	return _result(True, 'out', 'success', emp, lang, time=hhmm(now))


# ---------------------------------------------------------------- rekap bulanan

def _row_status(att):
	codes = {att.status_am.code if att.status_am else None, att.status_pm.code if att.status_pm else None} - {None}
	if conf('PRESENT_CODE') in codes:
		return 'P'
	if 'L' in codes:
		return 'L'
	if 'F' in codes:
		return 'F'
	return ''


def month_report(year, month, division=None, employee=None, hol=None):
	"""Rekap per pegawai per hari. Kode sel: P hadir, L cuti, F alpha, W akhir pekan, H libur, '' belum/tidak ada."""
	last_day = datetime.date(year, month, 1).replace(day=28) + datetime.timedelta(days=4)
	last_day = last_day.replace(day=1) - datetime.timedelta(days=1)
	first_day = last_day.replace(day=1)
	today = to_local(timezone.now()).date()
	days = list(range(1, last_day.day + 1))

	hol = holiday_map() if hol is None else hol
	off = {}
	for d in days:
		date = first_day.replace(day=d)
		reason = ('weekend', '') if date.weekday() >= 5 else hol.get((month, d))
		if reason:
			off[d] = ('W' if reason[0] == 'weekend' else 'H', reason)

	emps = (Employee.objects.filter(status_id=conf('ACTIVE_STATUS_ID'))
			.select_related('employeeposition__position', 'employeedivision__division',
							'employeedivision__department__division', 'employeedivision__section__department__division',
							'idfnumber'))
	if employee is not None:
		emps = emps.filter(pk=employee.pk)
	elif division is not None:
		emps = emps.filter(division_q(division))
	emps = list(emps.distinct().order_by('first_name', 'last_name'))
	ids = [e.pk for e in emps]

	att_map = {}
	for a in (Attendance.objects.filter(employee_id__in=ids, date__gte=first_day, date__lte=last_day)
			  .select_related('status_am', 'status_pm', 'scantime').order_by('id')):
		att_map.setdefault((a.employee_id, a.date.day), a)

	leave_map = {}
	for lv in Leaves.objects.filter(employee_id__in=ids, is_active=True, is_confirm=True,
									start_date__lte=last_day, end_date__gte=first_day):
		leave_map.setdefault(lv.employee_id, []).append((lv.start_date, lv.end_date))

	rows = []
	for e in emps:
		cells = []
		tot = {'workdays': 0, 'present': 0, 'late': 0, 'leave': 0, 'absent': 0, 'no_out': 0, 'early': 0}
		for d in days:
			date = first_day.replace(day=d)
			cell = {'day': d, 'date': date, 'wd': date.weekday(), 'code': '', 'late': False, 'early': False,
					'no_out': False, 'in': '', 'out': '', 'holiday': off[d][1][1] if d in off else ''}
			if d in off:
				cell['code'] = off[d][0]
				cells.append(cell)
				continue
			tot['workdays'] += 1
			on_leave = any(s <= date <= en for s, en in leave_map.get(e.pk, []))
			a = att_map.get((e.pk, d))
			if on_leave:
				cell['code'] = 'L'
			elif a:
				cell['code'] = _row_status(a)
				st = getattr(a, 'scantime', None)
				if st:
					cell['in'], cell['out'] = hhmm(st.check_in), hhmm(st.check_out)
					cell['late'] = st.is_late
					cell['no_out'] = st.check_out is None and date < today
					cell['early'] = bool(st.check_out) and to_local(st.check_out).time() < conf_time('WORK_END')
			elif date < today:
				cell['code'] = 'F'
			if cell['code'] == 'P':
				tot['present'] += 1
				tot['late'] += cell['late']
				tot['no_out'] += cell['no_out']
				tot['early'] += cell['early']
			elif cell['code'] == 'L':
				tot['leave'] += 1
			elif cell['code'] == 'F':
				tot['absent'] += 1
			cells.append(cell)
		info = employee_info(e, photo=False)   # foto tidak dipakai di laporan: hindari 1 query per pegawai
		info['hashed'] = e.hashed
		seen = tot['present'] + tot['absent']
		tot['rate'] = round(100.0 * tot['present'] / seen) if seen else None
		rows.append({'employee': info, 'cells': cells, 'totals': tot, 'weeks': calendar_weeks(year, month, cells)})
	return {'year': year, 'month': month, 'days': days, 'off': off, 'rows': rows}


def calendar_weeks(year, month, cells):
	"""Kalender mini (Senin..Minggu): daftar minggu, tiap sel adalah dict hari atau None untuk kotak kosong."""
	padded = [None] * datetime.date(year, month, 1).weekday() + list(cells)
	padded += [None] * (-len(padded) % 7)
	return [padded[i:i + 7] for i in range(0, len(padded), 7)]


_ZERO = {'workdays': 0, 'present': 0, 'late': 0, 'leave': 0, 'absent': 0, 'no_out': 0, 'early': 0, 'rate': None}


def year_summary(employee, year):
	"""Total per bulan (12 elemen) untuk satu pegawai. Libur dimuat sekali untuk seluruh tahun."""
	hol = holiday_map()
	out = []
	for m in range(1, 13):
		rep = month_report(year, m, employee=employee, hol=hol)
		out.append(rep['rows'][0]['totals'] if rep['rows'] else dict(_ZERO))
	return out


def overview(rep):
	"""Ringkasan seluruh pegawai dari satu month_report: total, per divisi, tren harian, paling sering terlambat."""
	rows = rep['rows']
	keys = ('present', 'late', 'leave', 'absent', 'no_out', 'early')
	tot = {k: sum(r['totals'][k] for r in rows) for k in keys}
	seen = tot['present'] + tot['absent']
	tot['rate'] = round(100.0 * tot['present'] / seen) if seen else None
	tot['employees'] = len(rows)

	divs = {}
	for r in rows:
		e = r['employee']
		d = divs.setdefault(e['division'] or '-', {'name': e['division'] or '-', 'id': e['division_id'],
												 'employees': 0, 'present': 0, 'late': 0, 'leave': 0, 'absent': 0})
		d['employees'] += 1
		for k in ('present', 'late', 'leave', 'absent'):
			d[k] += r['totals'][k]
	division_rows = sorted(divs.values(), key=lambda d: d['name'])
	for d in division_rows:
		seen = d['present'] + d['absent']
		d['rate'] = round(100.0 * d['present'] / seen) if seen else None

	daily = {'label': rep['days'], 'present': [], 'late': [], 'leave': [], 'absent': []}
	today = to_local(timezone.now()).date()
	for i, day in enumerate(rep['days']):
		if day in rep['off'] or datetime.date(rep['year'], rep['month'], day) > today:   # libur dan hari yang belum terjadi: kosong
			for k in ('present', 'late', 'leave', 'absent'):
				daily[k].append(None)
			continue
		cs = [r['cells'][i] for r in rows]
		daily['present'].append(sum(1 for c in cs if c['code'] == 'P'))
		daily['late'].append(sum(1 for c in cs if c['code'] == 'P' and c['late']))
		daily['leave'].append(sum(1 for c in cs if c['code'] == 'L'))
		daily['absent'].append(sum(1 for c in cs if c['code'] == 'F'))
	late_top = sorted((r for r in rows if r['totals']['late']), key=lambda r: (-r['totals']['late'], r['employee']['name']))[:10]
	return {'totals': tot, 'divisions': division_rows, 'daily': daily, 'late_top': late_top}
