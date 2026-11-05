"""Logika absensi QR: tanda tangan QR, pemeriksaan scan, dan rekap bulanan.

Semua aturan jam dan grup bisa diubah di settings.py lewat nama ATTENDANCE_* (lihat QR_INSTALL.md).
"""
import datetime
import hashlib
import hmac
import secrets
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from employee.models import Employee
from leave.models import Leaves
from settings_app.utils import getnewid
from attendance.models import Attendance, AttendanceDiv, AttendanceStatus, NacionalHoliday, Tolerance
from attendance.models_qr import AttendanceTime, QRCard, ScanDevice, ScanLog

QR_PREFIX = 'MDI'

_DEFAULTS = {
	'TIME_ZONE': 'Asia/Dili',
	'LATE_AFTER': '08:15',     # masuk setelah jam ini = terlambat
	'NOON': '12:00',           # scan sebelum jam ini = masuk (AM), sesudahnya = pulang (PM)
	'WORK_END': '16:00',       # pulang sebelum jam ini = pulang awal
	'DUPLICATE_SECONDS': 60,   # scan ganda dalam rentang ini diabaikan
	'ACTIVE_STATUS_ID': 1,     # Employee.status_id untuk pegawai aktif (sama seperti view lama)
	'PRESENT_CODE': 'P',       # AttendanceStatus.code untuk hadir
	'QR_SECRET': None,         # kunci tanda tangan QR; kosong = pakai SECRET_KEY
}


def conf(name):
	return getattr(settings, 'ATTENDANCE_' + name, _DEFAULTS[name])


def local_tz():
	return ZoneInfo(conf('TIME_ZONE'))


def conf_time(name):
	h, m = str(conf(name)).split(':')
	return datetime.time(int(h), int(m))


def to_local(dt):
	return dt.astimezone(local_tz())


def hhmm(dt):
	return to_local(dt).strftime('%H:%M') if dt else ''


# ---------------------------------------------------------------- QR dan kunci perangkat

def _secret():
	return (conf('QR_SECRET') or settings.SECRET_KEY).encode()


def _sign(emp_id, version):
	msg = '{}.{}'.format(emp_id, version).encode()
	return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:32]


def make_payload(card):
	return '{}.{}.{}.{}'.format(QR_PREFIX, card.employee_id, card.version, _sign(card.employee_id, card.version))


def parse_payload(raw):
	"""Mengembalikan (employee_id, version) kalau tanda tangan benar, selain itu None."""
	parts = (raw or '').strip().split('.')
	if len(parts) != 4 or parts[0] != QR_PREFIX:
		return None
	try:
		emp_id, version = int(parts[1]), int(parts[2])
	except ValueError:
		return None
	if not hmac.compare_digest(parts[3], _sign(emp_id, version)):
		return None
	return emp_id, version


def hash_key(key):
	return hashlib.sha256(key.encode()).hexdigest()


def new_device_key():
	return secrets.token_urlsafe(24)


def get_device(key):
	if not key:
		return None
	return ScanDevice.objects.filter(key_sha256=hash_key(key), is_active=True).first()


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


def employee_info(emp):
	pos = getattr(getattr(emp, 'employeeposition', None), 'position', None)
	div = employee_division(emp)
	nrf = getattr(getattr(emp, 'idfnumber', None), 'nrf', None)
	return {
		'id': emp.pk, 'name': str(emp).strip(), 'position': str(pos) if pos else '',
		'division': div.name if div else '', 'nrf': nrf or '', 'photo': photo_url(emp),
	}


def photo_url(emp):
	try:
		return emp.photo.image.url
	except Exception:
		return ''


# ---------------------------------------------------------------- hari libur dan cuti

def day_off_reason(d):
	"""Alasan hari ini bukan hari kerja, atau None. Libur dicocokkan lewat bulan dan tanggal,
	sama seperti view absensi yang sudah ada."""
	if d.weekday() >= 5:
		return 'Akhir pekan'
	h = NacionalHoliday.objects.filter(date__month=d.month, date__day=d.day).exclude(is_active=False).first()
	if h:
		return h.name or 'Libur nasional'
	t = Tolerance.objects.filter(date__month=d.month, date__day=d.day).exclude(is_active=False).first()
	if t:
		return t.name or 'Hari toleransi'
	return None


def active_leave(emp, d):
	return (Leaves.objects.filter(employee=emp, is_active=True, is_confirm=True,
			start_date__lte=d, end_date__gte=d).select_related('leave_type').first())


# ---------------------------------------------------------------- scan

def _result(ok, code, level, title, message, emp=None, **extra):
	res = {'ok': ok, 'code': code, 'level': level, 'title': title, 'message': message,
		   'employee': employee_info(emp) if emp else None, 'employee_id': emp.pk if emp else None}
	res.update(extra)
	return res


def process_scan(raw, device=None, now=None):
	"""Memproses satu scan dan mencatatnya di ScanLog. Mengembalikan dict untuk layar kiosk."""
	now = now or timezone.now()
	res = _process(raw, device, now)
	ScanLog.objects.create(employee_id=res['employee_id'], device=device, scanned_at=now,
						   code=res['code'], message=res['message'][:200])
	return res


def _process(raw, device, now):
	parsed = parse_payload(raw)
	if not parsed:
		return _result(False, 'invalid', 'error', 'QR tidak dikenal', 'Kartu ini bukan kartu absensi yang sah.')
	emp_id, version = parsed
	emp = (Employee.objects.select_related('employeeposition__position', 'employeedivision__division')
		   .filter(pk=emp_id).first())
	card = QRCard.objects.filter(employee_id=emp_id).first()
	if not emp or not card or not card.is_active or card.version != version:
		return _result(False, 'revoked', 'error', 'Kartu tidak berlaku',
					   'Kartu ini sudah diganti atau dinonaktifkan. Hubungi admin.', emp)
	if emp.status_id != conf('ACTIVE_STATUS_ID'):
		return _result(False, 'inactive', 'error', 'Pegawai tidak aktif', 'Hubungi admin.', emp)

	local = to_local(now)
	today = local.date()
	member = getattr(emp, 'employeecard', None)
	if member and (not member.is_active or (member.expiry_date and member.expiry_date < today)):
		return _result(False, 'card_expired', 'error', 'Kartu kedaluwarsa',
					   'Masa berlaku kartu pegawai sudah habis. Hubungi admin.', emp)

	reason = day_off_reason(today)
	if reason:
		return _result(False, 'holiday', 'error', 'Hari libur',
					   '{}. Absensi tidak dicatat hari ini.'.format(reason), emp)

	leave = active_leave(emp, today)
	if leave:
		kind = leave.leave_type.name if leave.leave_type and leave.leave_type.name else 'Cuti'
		return _result(False, 'leave', 'leave', 'Sedang cuti',
					   'Status Anda hari ini: {} (sampai {}). Absensi tidak diubah.'.format(
						   kind, leave.end_date.strftime('%d/%m/%Y')), emp)

	div = employee_division(emp)
	if div and AttendanceDiv.objects.filter(div=div, year=today.year, month=today.month, is_final=True).exists():
		return _result(False, 'month_final', 'error', 'Bulan sudah final',
					   'Absensi bulan ini sudah difinalkan. Hubungi admin.', emp)

	present = AttendanceStatus.objects.filter(code=conf('PRESENT_CODE')).first()
	if not present:
		return _result(False, 'config', 'error', 'Pengaturan belum lengkap',
					   'Status hadir (kode {}) belum ada di AttendanceStatus.'.format(conf('PRESENT_CODE')), emp)

	with transaction.atomic():
		Employee.objects.select_for_update().get(pk=emp.pk)  # satu scan per pegawai pada satu waktu
		att = Attendance.objects.filter(employee=emp, date=today).order_by('id').first()
		st = getattr(att, 'scantime', None) if att else None
		if att and not st:
			return _result(False, 'manual', 'error', 'Sudah diisi admin',
						   'Absensi hari ini sudah diisi manual oleh admin, scan tidak diterima.', emp)
		if not att:
			return _first_scan(emp, div, present, device, now, local, today)
		return _next_scan(emp, att, st, present, now, local)


def _first_scan(emp, div, present, device, now, local, today):
	after_noon = local.time() >= conf_time('NOON')
	late = after_noon or local.time() > conf_time('LATE_AFTER')
	att = None
	for _ in range(3):
		newid, hashed = getnewid(Attendance)
		try:
			with transaction.atomic():
				att = Attendance.objects.create(
					id=newid, dg=div.dg if div else None, div=div, employee=emp,
					status_am=None if after_noon else present, status_pm=present if after_noon else None,
					date=today, desc='Scan QR', datetime=now, hashed=hashed)
			break
		except IntegrityError:
			continue
	if att is None:
		return _result(False, 'busy', 'error', 'Coba lagi', 'Sistem sibuk, scan ulang kartu Anda.', emp)
	AttendanceTime.objects.create(attendance=att, check_in=now, check_out=now if after_noon else None,
								  is_late=late, device=device)
	if late:
		return _result(True, 'in_late', 'warn', 'Masuk tercatat (terlambat)',
					   'Masuk pukul {}. Batas masuk {}.'.format(hhmm(now), conf('LATE_AFTER')), emp, time=hhmm(now))
	return _result(True, 'in', 'success', 'Selamat datang', 'Masuk pukul {}.'.format(hhmm(now)), emp, time=hhmm(now))


def _next_scan(emp, att, st, present, now, local):
	last = st.check_out or st.check_in
	if (now - last).total_seconds() < conf('DUPLICATE_SECONDS'):
		return _result(True, 'duplicate', 'info', 'Sudah discan', 'Kartu Anda baru saja discan.', emp)
	if local.time() < conf_time('NOON'):
		return _result(True, 'already_in', 'info', 'Sudah tercatat masuk',
					   'Masuk pukul {}. Scan lagi saat pulang.'.format(hhmm(st.check_in)), emp, time=hhmm(st.check_in))
	st.check_out = now
	st.save(update_fields=['check_out'])
	if att.status_pm_id != present.pk:
		att.status_pm = present
		att.save(update_fields=['status_pm'])
	if local.time() < conf_time('WORK_END'):
		return _result(True, 'out_early', 'warn', 'Pulang awal tercatat',
					   'Pulang pukul {}. Jam pulang kantor {}.'.format(hhmm(now), conf('WORK_END')), emp, time=hhmm(now))
	return _result(True, 'out', 'success', 'Sampai jumpa', 'Pulang pukul {}.'.format(hhmm(now)), emp, time=hhmm(now))


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


def month_report(year, month, division=None, employee=None):
	"""Rekap per pegawai per hari. Kode sel: P hadir, L cuti, F alpha, W akhir pekan, H libur, '' belum/tidak ada."""
	last_day = datetime.date(year, month, 1).replace(day=28) + datetime.timedelta(days=4)
	last_day = last_day.replace(day=1) - datetime.timedelta(days=1)
	first_day = last_day.replace(day=1)
	today = to_local(timezone.now()).date()
	days = list(range(1, last_day.day + 1))

	off = {}
	for d in days:
		date = first_day.replace(day=d)
		reason = day_off_reason(date)
		if reason:
			off[d] = ('W' if date.weekday() >= 5 else 'H', reason)

	emps = (Employee.objects.filter(status_id=conf('ACTIVE_STATUS_ID'))
			.select_related('employeeposition__position', 'employeedivision__division', 'idfnumber'))
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
			cell = {'day': d, 'code': '', 'late': False, 'early': False, 'no_out': False, 'in': '', 'out': ''}
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
		info = employee_info(e)
		info['hashed'] = e.hashed
		rows.append({'employee': info, 'cells': cells, 'totals': tot})
	return {'year': year, 'month': month, 'days': days, 'off': off, 'rows': rows}
