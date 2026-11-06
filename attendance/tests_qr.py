import datetime
import io
import json
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from custom.models import DG, Division
from employee.models import Employee, EmployeeCard, EmployeeDivision, EmployeePosition, IDFNumber
from custom.models import Position, Status
from leave.models import LeaveType, Leaves
from attendance import qr_service as svc
from attendance.models import Attendance, AttendanceDiv, AttendanceStatus, NacionalHoliday, Tolerance, Year
from attendance.models_qr import AttendanceTime, OfficeNetwork, QRCard, ScanDevice, ScanLog

DILI = ZoneInfo('Asia/Dili')
MON = datetime.date(2026, 11, 9)      # Senin
SUN = datetime.date(2026, 11, 8)


def at(date, hh, mm=0):
	return datetime.datetime(date.year, date.month, date.day, hh, mm, tzinfo=DILI)


class QRBase(TestCase):
	def pair(self, name='Pintu'):
		"""Memasangkan klien uji sebagai PC kiosk (cookie penanda), seperti yang dilakukan admin di PC pintu."""
		key = svc.new_device_key()
		dev = ScanDevice.objects.create(name=name, key_sha256=svc.hash_key(key), rotated_at=timezone.now())
		self.client.cookies['mdi_kiosk'] = key
		return dev, key

	def post_scan(self, body, **extra):
		return self.client.post(reverse('qr-kiosk-scan'), data=body, content_type='application/json', HTTP_X_KIOSK='1', **extra)

	def setUp(self):
		cache.clear()
		self.P = AttendanceStatus.objects.create(code='P', name='Presensa')
		AttendanceStatus.objects.create(code='F', name='Falta')
		AttendanceStatus.objects.create(code='L', name='Lisensa')
		Status.objects.create(id=1, name='Ativu')
		dg = DG.objects.create(code='DG1', name='DG Satu')
		self.div = Division.objects.create(dg=dg, code='D1', name='Divisi Satu')
		self.emp = self.make_employee('Maria', 'Silva')
		self.card = QRCard.objects.create(employee=self.emp)
		self.payload = svc.make_payload(self.card)

	def make_employee(self, first, last, pk=None):
		e = Employee.objects.create(first_name=first, last_name=last, status_id=1, hashed='h' + first)
		EmployeeDivision.objects.create(employee=e, division=self.div)
		EmployeePosition.objects.create(employee=e, position=Position.objects.get_or_create(name='Staf')[0])
		return e

	def scan(self, date, hh, mm=0, payload=None):
		return svc.process_scan(payload or self.payload, now=at(date, hh, mm))


class PayloadTests(QRBase):
	def test_roundtrip(self):
		self.assertEqual(svc.parse_payload(self.payload), (self.emp.pk, 1))
		self.assertEqual(svc.parse_payload('  ' + self.payload + '\n'), (self.emp.pk, 1))

	def test_tampered(self):
		p = self.payload.split('.')
		for bad in ['MDI.%d.1.%s' % (self.emp.pk + 1, p[3]), 'MDI.%d.2.%s' % (self.emp.pk, p[3]),
					'MDI.%d.1.%s' % (self.emp.pk, '0' * 32), 'XXX.1.1.abc', '', None, 'MDI.a.b.c']:
			self.assertIsNone(svc.parse_payload(bad), bad)

	def test_invalid_scan_is_logged(self):
		res = svc.process_scan('hello', now=at(MON, 8))
		self.assertEqual(res['code'], 'invalid')
		self.assertEqual(ScanLog.objects.get().code, 'invalid')

	@override_settings(ATTENDANCE_QR_SECRET='another-secret')
	def test_secret_change_invalidates(self):
		self.assertIsNone(svc.parse_payload(self.payload))


class ScanFlowTests(QRBase):
	def test_on_time_in_sets_am_present(self):
		res = self.scan(MON, 8, 10)
		self.assertEqual((res['ok'], res['code'], res['level']), (True, 'in', 'success'))
		a = Attendance.objects.get()
		self.assertEqual((a.status_am, a.status_pm, a.date, a.div, a.dg), (self.P, None, MON, self.div, self.div.dg))
		self.assertEqual(len(a.hashed), 32)
		self.assertFalse(a.scantime.is_late)
		self.assertEqual(res['employee']['name'], 'Maria Silva')

	def test_first_scan_creates_month_row_once_like_manual_flow(self):
		self.scan(MON, 8, 0)
		row = AttendanceDiv.objects.get()
		self.assertEqual((row.div, row.year, row.month, row.is_confirm, row.is_final), (self.div, 2026, 11, False, False))
		other = self.make_employee('Jose', 'Costa')
		self.scan(MON, 8, 5, payload=svc.make_payload(QRCard.objects.create(employee=other)))
		self.assertEqual(AttendanceDiv.objects.count(), 1)               # tidak ganda
		self.scan(MON + datetime.timedelta(days=1), 8, 0)
		self.assertEqual(AttendanceDiv.objects.count(), 1)

	def test_existing_month_row_is_left_untouched(self):
		AttendanceDiv.objects.create(id=7, div=self.div, year=2026, month=11, is_confirm=True)
		self.scan(MON, 8, 0)
		row = AttendanceDiv.objects.get()
		self.assertEqual((row.id, row.is_confirm), (7, True))

	def test_no_month_row_for_employee_without_division(self):
		EmployeeDivision.objects.filter(employee=self.emp).update(division=None)
		self.assertEqual(self.scan(MON, 8)['code'], 'in')
		self.assertEqual(AttendanceDiv.objects.count(), 0)

	def test_scan_ignores_active_year_and_month_settings(self):
		Year.objects.create(year=2026, is_active=True)      # bulan aktif manual tidak memengaruhi scan
		self.assertEqual(self.scan(MON, 8)['code'], 'in')
		self.assertEqual(Attendance.objects.get().date, MON)

	def test_boundary_0815_is_not_late_0816_is(self):
		self.assertEqual(self.scan(MON, 8, 15)['code'], 'in')
		Attendance.objects.all().delete()
		self.assertEqual(self.scan(MON, 8, 16)['code'], 'in_late')
		self.assertTrue(Attendance.objects.get().scantime.is_late)

	def test_second_scan_before_noon_is_ignored(self):
		self.scan(MON, 8, 0)
		res = self.scan(MON, 9, 30)
		self.assertEqual(res['code'], 'already_in')
		self.assertEqual(Attendance.objects.count(), 1)
		self.assertIsNone(Attendance.objects.get().scantime.check_out)

	def test_duplicate_within_a_minute(self):
		self.scan(MON, 8, 0)
		self.assertEqual(self.scan(MON, 8, 0)['code'], 'duplicate')

	def test_out_after_four(self):
		self.scan(MON, 8, 0)
		res = self.scan(MON, 16, 5)
		self.assertEqual((res['code'], res['level']), ('out', 'success'))
		a = Attendance.objects.get()
		self.assertEqual(a.status_pm, self.P)
		self.assertEqual(a.scantime.check_out, at(MON, 16, 5))

	def test_early_out_is_flagged_and_last_scan_wins(self):
		self.scan(MON, 8, 0)
		self.assertEqual(self.scan(MON, 13, 0)['code'], 'out_early')
		self.assertEqual(self.scan(MON, 16, 30)['code'], 'out')
		self.assertEqual(Attendance.objects.get().scantime.check_out, at(MON, 16, 30))

	def test_first_scan_after_noon_fills_pm_and_is_late(self):
		res = self.scan(MON, 13, 0)
		self.assertEqual(res['code'], 'in_late')
		a = Attendance.objects.get()
		self.assertEqual((a.status_am, a.status_pm), (None, self.P))
		self.assertTrue(a.scantime.is_late)

	def test_timezone_is_dili_not_utc(self):
		# 08:10 di Dili = 23:10 UTC hari sebelumnya; harus tetap dicatat sebagai Senin
		utc = at(MON, 8, 10).astimezone(datetime.timezone.utc)
		self.assertEqual(utc.date(), SUN)
		svc.process_scan(self.payload, now=utc)
		self.assertEqual(Attendance.objects.get().date, MON)


class RejectionTests(QRBase):
	def test_weekend(self):
		res = self.scan(SUN, 8)
		self.assertEqual(res['code'], 'holiday')
		self.assertEqual(Attendance.objects.count(), 0)

	def test_national_holiday_matches_month_and_day(self):
		NacionalHoliday.objects.create(name='Hari Test', date=datetime.date(2020, 11, 9))
		res = self.scan(MON, 8)
		self.assertEqual(res['code'], 'holiday')
		self.assertIn('Hari Test', res['message'])

	def test_inactive_holiday_is_ignored(self):
		NacionalHoliday.objects.create(name='X', date=MON, is_active=False)
		self.assertEqual(self.scan(MON, 8)['code'], 'in')

	def test_tolerance_day(self):
		y = Year.objects.create(year=2026, is_active=True)
		Tolerance.objects.create(year=y, name='Cuti Bersama', date=MON)
		res = self.scan(MON, 8)
		self.assertEqual(res['code'], 'holiday')
		self.assertIn('Cuti Bersama', res['message'])

	def test_confirmed_leave_is_read_but_not_recorded(self):
		lt = LeaveType.objects.create(code='AL', name='Cuti Tahunan')
		Leaves.objects.create(employee=self.emp, leave_type=lt, start_date=MON, end_date=MON + datetime.timedelta(days=2),
							  is_active=True, is_confirm=True)
		res = self.scan(MON, 8)
		self.assertEqual((res['code'], res['level'], res['ok']), ('leave', 'leave', False))
		self.assertIn('Cuti Tahunan', res['message'])
		self.assertEqual(Attendance.objects.count(), 0)

	def test_unconfirmed_or_inactive_leave_does_not_count(self):
		Leaves.objects.create(employee=self.emp, start_date=MON, end_date=MON, is_active=True, is_confirm=False)
		Leaves.objects.create(employee=self.emp, start_date=MON, end_date=MON, is_active=False, is_confirm=True)
		self.assertEqual(self.scan(MON, 8)['code'], 'in')

	def test_manual_row_blocks_scan(self):
		Attendance.objects.create(id=1, employee=self.emp, div=self.div, date=MON, status_am=self.P, hashed='x')
		res = self.scan(MON, 8)
		self.assertEqual(res['code'], 'manual')
		self.assertFalse(hasattr(Attendance.objects.get(), 'scantime'))

	def test_final_month_blocks_but_confirmed_does_not(self):
		AttendanceDiv.objects.create(div=self.div, year=2026, month=11, is_confirm=True, is_final=False)
		self.assertEqual(self.scan(MON, 8)['code'], 'in')
		Attendance.objects.all().delete()
		AttendanceDiv.objects.update(is_final=True)
		self.assertEqual(self.scan(MON, 8)['code'], 'month_final')

	def test_revoked_version(self):
		self.card.version = 2
		self.card.save()
		self.assertEqual(self.scan(MON, 8)['code'], 'revoked')
		self.assertEqual(self.scan(MON, 8, payload=svc.make_payload(self.card))['code'], 'in')

	def test_inactive_qr_card_and_expired_member_card(self):
		self.card.is_active = False
		self.card.save()
		self.assertEqual(self.scan(MON, 8)['code'], 'revoked')
		self.card.is_active = True
		self.card.save()
		EmployeeCard.objects.create(employee=self.emp, expiry_date=MON - datetime.timedelta(days=1), is_active=True)
		self.assertEqual(self.scan(MON, 8)['code'], 'card_expired')
		EmployeeCard.objects.update(expiry_date=MON, is_active=False)
		self.assertEqual(self.scan(MON, 8)['code'], 'card_expired')
		EmployeeCard.objects.update(expiry_date=MON, is_active=True)
		self.assertEqual(self.scan(MON, 8)['code'], 'in')

	def test_inactive_employee(self):
		Status.objects.create(id=2, name='Reforma')
		Employee.objects.filter(pk=self.emp.pk).update(status_id=2)
		self.assertEqual(self.scan(MON, 8)['code'], 'inactive')

	def test_missing_present_status_is_reported(self):
		AttendanceStatus.objects.filter(code='P').delete()
		self.assertEqual(self.scan(MON, 8)['code'], 'config')

	def test_every_scan_is_logged(self):
		self.scan(MON, 8)
		self.scan(SUN, 8)
		self.assertEqual(list(ScanLog.objects.order_by('id').values_list('code', flat=True)), ['in', 'holiday'])

	def test_new_ids_follow_getnewid_sequence(self):
		other = self.make_employee('Jose', 'Costa')
		other_card = QRCard.objects.create(employee=other)
		self.scan(MON, 8)
		self.scan(MON, 8, payload=svc.make_payload(other_card))
		self.assertEqual(list(Attendance.objects.order_by('id').values_list('id', flat=True)), [1, 2])


class DeviceTests(QRBase):
	def test_key_is_stored_hashed(self):
		key = svc.new_device_key()
		dev = ScanDevice.objects.create(name='Pintu', key_sha256=svc.hash_key(key))
		self.assertNotIn(key, dev.key_sha256)
		self.assertEqual(len(dev.key_sha256), 64)
		self.assertEqual(svc.get_device(key), dev)
		self.assertIsNone(svc.get_device('wrong'))
		self.assertIsNone(svc.get_device(''))
		dev.is_active = False
		dev.save()
		self.assertIsNone(svc.get_device(key))


class ReportTests(QRBase):
	def test_month_report_counts(self):
		# Nov 2026: 21 hari kerja (Senin-Jumat), tanpa libur
		self.scan(MON, 8, 0)
		self.scan(MON, 16, 0)
		tue = MON + datetime.timedelta(days=1)
		self.scan(tue, 9, 0)                     # terlambat, lupa pulang
		wed = MON + datetime.timedelta(days=2)
		lt = LeaveType.objects.create(code='AL', name='Cuti')
		Leaves.objects.create(employee=self.emp, leave_type=lt, start_date=wed, end_date=wed, is_active=True, is_confirm=True)
		thu = MON + datetime.timedelta(days=3)
		NacionalHoliday.objects.create(name='Libur', date=thu)

		with mock.patch.object(timezone, 'now', lambda: at(datetime.date(2026, 11, 13), 12)):   # Jumat 13 Nov
			rep = svc.month_report(2026, 11, division=self.div)
		self.assertEqual(len(rep['rows']), 1)
		row = rep['rows'][0]
		t = row['totals']
		self.assertEqual(t['workdays'], 20)                      # 21 hari kerja - 1 libur
		self.assertEqual((t['present'], t['late'], t['leave']), (2, 1, 1))
		self.assertEqual(t['no_out'], 1)                         # selasa lupa pulang
		self.assertEqual(t['absent'], 5)                         # 2-6 Nov tanpa data; Jumat 13 (hari ini) belum dihitung
		codes = {c['day']: c['code'] for c in row['cells']}
		self.assertEqual((codes[9], codes[10], codes[11], codes[12], codes[13], codes[14]),
						 ('P', 'P', 'L', 'H', '', 'W'))
		self.assertEqual(codes[2], 'F')

	def test_report_filters_by_division_and_employee(self):
		other_div = Division.objects.create(code='D2', name='Divisi Dua')
		e2 = self.make_employee('Ana', 'Lopes')
		EmployeeDivision.objects.filter(employee=e2).update(division=other_div)
		self.assertEqual(len(svc.month_report(2026, 11, division=self.div)['rows']), 1)
		self.assertEqual(len(svc.month_report(2026, 11)['rows']), 2)
		self.assertEqual(svc.month_report(2026, 11, employee=e2)['rows'][0]['employee']['name'], 'Ana Lopes')


class ViewTests(QRBase):
	def login(self, group):
		u = User.objects.create_user('u_' + group, password='x')
		u.groups.add(Group.objects.get_or_create(name=group)[0])
		self.client.force_login(u)
		self.client.cookies['mdi_lang'] = 'id'
		return u

	def api(self, code):
		return self.post_scan({'code': code})

	def test_kiosk_page_is_public_and_hardened(self):
		r = self.client.get(reverse('qr-kiosk'))
		self.assertEqual(r.status_code, 200)
		self.assertContains(r, 'id="clock"')
		self.assertContains(r, 'Asia/Dili')
		self.assertEqual(r['Cache-Control'], 'no-store')
		self.assertEqual(r['X-Frame-Options'], 'DENY')
		self.assertEqual(r['Referrer-Policy'], 'same-origin')
		self.assertNotContains(r, 'type="password"')                       # tidak ada lagi kolom kunci yang diketik
		self.assertContains(r, 'data-camera=""')                          # kamera mati secara bawaan

	@override_settings(ATTENDANCE_ALLOW_CAMERA=True)
	def test_camera_only_when_allowed(self):
		self.assertContains(self.client.get(reverse('qr-kiosk')), 'data-camera="1"')

	def test_scan_requires_paired_pc_by_default(self):
		r = self.api(self.payload)
		self.assertEqual((r.status_code, r.json()['code']), (401, 'device'))
		self.client.cookies['mdi_kiosk'] = 'cookie-asal-asalan'
		self.assertEqual(self.api(self.payload).status_code, 401)
		self.assertEqual(ScanLog.objects.count(), 0)
		self.assertEqual(Attendance.objects.count(), 0)

	def test_revoked_pc_is_rejected_immediately(self):
		dev, key = self.pair()
		with mock.patch.object(timezone, 'now', lambda: at(MON, 8)):
			self.assertEqual(self.api(self.payload).status_code, 200)
		ScanDevice.objects.update(is_active=False)
		self.assertEqual(self.api(self.payload).status_code, 401)

	def test_scan_ok_from_paired_pc_and_hides_internal_id(self):
		dev, key = self.pair()
		with mock.patch.object(timezone, 'now', lambda: at(MON, 8)):
			r = self.api(self.payload)
		self.assertEqual(ScanLog.objects.get().ip, '127.0.0.1')
		data = r.json()
		self.assertEqual((r.status_code, data['ok'], data['code']), (200, True, 'in'))
		self.assertNotIn('employee_id', data)
		self.assertEqual(data['employee']['name'], 'Maria Silva')
		self.assertIsNotNone(ScanDevice.objects.get().last_seen)
		self.assertEqual(r['Cache-Control'], 'no-store')

	@override_settings(ATTENDANCE_REQUIRE_DEVICE_KEY=False)
	def test_pairing_can_be_turned_off(self):
		with mock.patch.object(timezone, 'now', lambda: at(MON, 8)):
			r = self.api(self.payload)
		self.assertEqual((r.status_code, r.json()['code']), (200, 'in'))
		self.assertIsNone(ScanLog.objects.get().device)

	def test_scan_api_garbage_and_get(self):
		self.pair()
		r = self.client.post(reverse('qr-kiosk-scan'), data='not json', content_type='application/json', HTTP_X_KIOSK='1')
		self.assertEqual(r.json()['code'], 'invalid')
		self.assertEqual(self.post_scan({'code': 123}).json()['code'], 'invalid')
		self.assertEqual(self.post_scan([1, 2]).json()['code'], 'invalid')
		self.assertEqual(self.client.get(reverse('qr-kiosk-scan')).status_code, 405)

	def test_scan_needs_custom_header(self):
		self.pair()
		r = self.client.post(reverse('qr-kiosk-scan'), data={'code': self.payload}, content_type='application/json')
		self.assertEqual(r.status_code, 400)
		self.assertEqual(Attendance.objects.count(), 0)

	def test_scan_is_csrf_safe_without_token(self):
		from django.test import Client
		c = Client(enforce_csrf_checks=True)
		key = svc.new_device_key()
		ScanDevice.objects.create(name='X', key_sha256=svc.hash_key(key))
		c.cookies['mdi_kiosk'] = key
		with mock.patch.object(timezone, 'now', lambda: at(MON, 8)):
			ok = c.post(reverse('qr-kiosk-scan'), data={'code': self.payload}, content_type='application/json', HTTP_X_KIOSK='1')
			form = c.post(reverse('qr-kiosk-scan'), data={'code': self.payload})                  # form lintas situs tanpa header
		self.assertEqual(ok.status_code, 200)
		self.assertEqual(form.status_code, 400)

	def test_scan_rejects_oversized_body(self):
		self.pair()
		r = self.post_scan({'code': 'x' * 3000})
		self.assertEqual(r.status_code, 413)
		self.assertEqual(ScanLog.objects.count(), 0)

	@override_settings(ATTENDANCE_THROTTLE_PER_MINUTE=3)
	def test_scan_is_throttled_per_ip(self):
		self.pair()
		with mock.patch.object(svc.time, 'time', lambda: 1_000_000.0):
			codes = [self.post_scan({'code': 'junk'}).status_code for _ in range(5)]
			other = self.post_scan({'code': 'junk'}, REMOTE_ADDR='192.168.1.9').status_code
		self.assertEqual(codes, [200, 200, 200, 429, 429])
		self.assertEqual(other, 200)                                       # IP lain tidak ikut terblokir
		self.assertEqual(ScanLog.objects.count(), 3 + 1)                   # permintaan yang diblokir tidak menulis ke basis data
		with mock.patch.object(svc.time, 'time', lambda: 1_000_060.0):     # menit berikutnya: boleh lagi
			self.assertEqual(self.post_scan({'code': 'junk'}).status_code, 200)

	def test_throttle_response_is_json_in_default_language(self):
		self.pair()
		with override_settings(ATTENDANCE_THROTTLE_PER_MINUTE=1), mock.patch.object(svc.time, 'time', lambda: 5.0):
			self.post_scan({'code': 'a'})
			r = self.post_scan({'code': 'a'})
		self.assertEqual((r.status_code, r.json()['code'], r.json()['level']), (429, 'throttle', 'error'))

	def test_kiosk_check_states(self):
		r = self.client.get(reverse('qr-kiosk-check'))
		self.assertEqual((r.status_code, r.json()['state']), (401, 'key'))
		self.pair()
		r = self.client.get(reverse('qr-kiosk-check'))
		self.assertEqual((r.status_code, r.json()['state']), (200, 'ok'))
		self.assertEqual(r['Cache-Control'], 'no-store')
		self.assertEqual(self.client.post(reverse('qr-kiosk-check')).status_code, 405)

	def test_pages_require_login(self):
		for name in ['qr-today', 'qr-devices', 'qr-cards', 'qr-report', 'qr-me']:
			r = self.client.get(reverse(name))
			self.assertEqual(r.status_code, 302, name)

	def test_admin_pages_render(self):
		self.login('admin')
		self.scan(MON, 8)
		for name in ['qr-today', 'qr-devices', 'qr-cards', 'qr-report', 'qr-me']:
			self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
		r = self.client.get(reverse('qr-report'), {'year': 2026, 'month': 11, 'div': self.div.pk})
		self.assertContains(r, 'Maria Silva')
		self.assertContains(r, 'rp-P')

	def test_group_permissions(self):
		self.login('president')
		self.assertContains(self.client.get(reverse('qr-report')), 'Rekap absensi')
		self.assertContains(self.client.get(reverse('qr-cards')), '403', status_code=403)
		self.assertContains(self.client.get(reverse('qr-devices')), '403', status_code=403)
		self.client.logout()
		self.login('staff')
		self.assertContains(self.client.get(reverse('qr-cards')), 'Kartu QR pegawai')
		self.assertContains(self.client.get(reverse('qr-devices')), '403', status_code=403)
		self.client.logout()
		self.login('funsionario')
		self.assertContains(self.client.get(reverse('qr-report')), '403', status_code=403)
		self.assertContains(self.client.get(reverse('qr-today')), '403', status_code=403)
		self.assertContains(self.client.get(reverse('qr-me')), 'Absensi saya')

	def test_me_shows_only_own_rows(self):
		from employee.models import EmployeeUser
		u = self.login('funsionario')
		EmployeeUser.objects.create(employee=self.emp, user=u)
		other = self.make_employee('Jose', 'Costa')
		r = self.client.get(reverse('qr-me'), {'year': 2026, 'month': 11})
		self.assertContains(r, 'Maria Silva')
		self.assertNotContains(r, 'Jose Costa')

	def test_pairing_sets_secure_cookie_and_stores_only_hash(self):
		self.login('admin')
		r = self.client.post(reverse('qr-device-pair'), {'name': 'PC Pintu'})
		self.assertEqual(r.status_code, 302)
		c = r.cookies['mdi_kiosk']
		self.assertTrue(c['httponly'])
		self.assertEqual(c['samesite'], 'Strict')
		self.assertEqual(c['path'], '/attendance/qr/')
		self.assertEqual(c['max-age'], 5 * 365 * 86400)
		dev = ScanDevice.objects.get()
		self.assertEqual((dev.name, dev.key_sha256), ('PC Pintu', svc.hash_key(c.value)))
		self.assertNotIn(c.value, dev.key_sha256)
		self.assertNotContains(self.client.get(reverse('qr-devices')), c.value)       # kunci asli tidak pernah tampil di halaman
		self.assertContains(self.client.get(reverse('qr-devices')), 'sudah dipasangkan sebagai: PC Pintu')
		with mock.patch.object(timezone, 'now', lambda: at(MON, 8)):
			self.assertEqual(self.api(self.payload).json()['code'], 'in')               # PC itu langsung bisa scan

	def test_pairing_secure_flag_on_https(self):
		self.login('admin')
		r = self.client.post(reverse('qr-device-pair'), {}, secure=True)
		self.assertTrue(r.cookies['mdi_kiosk']['secure'])
		self.assertEqual(ScanDevice.objects.get().name, 'PC kiosk')          # nama bawaan

	def test_default_name_and_repairing_replaces_previous(self):
		self.login('admin')
		self.client.post(reverse('qr-device-pair'), {})
		first = ScanDevice.objects.get()
		self.assertEqual(first.name, 'PC kiosk')
		self.client.post(reverse('qr-device-pair'), {'name': 'Baru'})
		devs = {d.name: d.is_active for d in ScanDevice.objects.all()}
		self.assertEqual(sorted(devs.values()), [False, True])           # pemasangan lama di browser ini dimatikan
		self.assertTrue(devs['Baru'])

	def test_only_device_admins_can_pair_and_post_only(self):
		self.login('staff')
		r = self.client.post(reverse('qr-device-pair'), {})
		self.assertEqual(r.status_code, 403)
		self.assertNotIn('mdi_kiosk', r.cookies)
		self.assertEqual(ScanDevice.objects.count(), 0)
		self.client.logout()
		self.login('admin')
		self.assertEqual(self.client.get(reverse('qr-device-pair')).status_code, 405)

	def test_toggle_revokes_paired_pc(self):
		self.login('admin')
		self.client.post(reverse('qr-device-pair'), {})
		dev = ScanDevice.objects.get()
		self.client.post(reverse('qr-device-toggle', args=[dev.pk]))
		self.assertFalse(ScanDevice.objects.get().is_active)
		self.assertEqual(self.api(self.payload).status_code, 401)

	def test_card_print_creates_card_and_renders_qr_that_scans(self):
		self.login('admin')
		QRCard.objects.all().delete()
		IDFNumber.objects.create(employee=self.emp, nrf='NRF-001')
		r = self.client.post(reverse('qr-card-print'), {'ids': [self.emp.pk]})
		self.assertEqual(r.status_code, 200)
		self.assertContains(r, '<svg')
		self.assertContains(r, 'NRF-001')
		self.assertContains(r, 'Maria Silva')
		qc = QRCard.objects.get()
		self.assertEqual(self.scan(MON, 8, payload=svc.make_payload(qc))['code'], 'in')

	def test_card_print_without_selection(self):
		self.login('admin')
		r = self.client.post(reverse('qr-card-print'), {})
		self.assertEqual(r.status_code, 302)

	def test_card_regen_toggle_and_printed(self):
		self.login('admin')
		EmployeeCard.objects.create(employee=self.emp)
		self.client.post(reverse('qr-card-action'), {'action': 'printed', 'ids': [self.emp.pk]})
		self.assertIsNotNone(QRCard.objects.get().printed_at)
		self.assertTrue(EmployeeCard.objects.get().is_printed)
		self.client.post(reverse('qr-card-action'), {'action': 'regen', 'ids': [self.emp.pk]})
		qc = QRCard.objects.get()
		self.assertEqual((qc.version, qc.printed_at), (2, None))
		self.assertEqual(self.scan(MON, 8)['code'], 'revoked')                # kartu lama mati
		self.client.post(reverse('qr-card-action'), {'action': 'toggle', 'ids': [self.emp.pk]})
		self.assertFalse(QRCard.objects.get().is_active)

	def test_card_actions_require_post(self):
		self.login('admin')
		self.assertEqual(self.client.get(reverse('qr-card-action')).status_code, 405)
		self.assertEqual(self.client.get(reverse('qr-card-print')).status_code, 405)

	def test_export_csv_and_xlsx(self):
		self.login('admin')
		self.scan(MON, 8, 30)
		r = self.client.get(reverse('qr-report-export'), {'year': 2026, 'month': 11, 'fmt': 'csv'})
		self.assertEqual(r.status_code, 200)
		self.assertTrue(r.streaming)
		text = b''.join(r.streaming_content).decode('utf-8-sig')
		lines = text.strip().splitlines()
		self.assertTrue(lines[0].startswith('NRF,Nama,Jabatan,Divisi,1,2,3'))
		self.assertIn('Maria Silva', lines[1])
		self.assertIn('P (T)', lines[1])               # 9 Nov, terlambat
		r = self.client.get(reverse('qr-report-export'), {'year': 2026, 'month': 11, 'fmt': 'xlsx'})
		self.assertEqual(r['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
		from openpyxl import load_workbook
		ws = load_workbook(io.BytesIO(r.content)).active
		self.assertEqual(ws.cell(2, 2).value, 'Maria Silva')

	def test_bad_period_falls_back(self):
		self.login('admin')
		self.assertEqual(self.client.get(reverse('qr-report'), {'year': 'x', 'month': '99'}).status_code, 200)



@override_settings(ATTENDANCE_NETWORK_MODE='allowlist')
class NetworkTests(QRBase):
	"""Scan hanya dari jaringan kantor: dari rumah (alamat publik) harus gagal."""

	def setUp(self):
		super().setUp()
		self.pair()

	def post(self, **extra):
		with mock.patch.object(timezone, 'now', lambda: at(MON, 8)):
			return self.post_scan({'code': self.payload}, **extra)

	def test_default_accepts_only_private_networks(self):
		for ip, ok in [('127.0.0.1', True), ('192.168.1.20', True), ('10.5.5.5', True), ('172.16.3.3', True),
					   ('172.31.255.254', True), ('::1', True), ('fd12:3456::1', True),
					   ('203.0.113.9', False), ('8.8.8.8', False), ('172.32.0.1', False), ('192.169.0.1', False),
					   ('2001:db8::1', False), ('not-an-ip', False), ('', False)]:
			Attendance.objects.all().delete()
			r = self.post(REMOTE_ADDR=ip)
			self.assertEqual(r.status_code == 200, ok, ip)
			if not ok:
				self.assertEqual(r.json()['code'], 'net', ip)
				self.assertEqual(Attendance.objects.count(), 0, ip)

	def test_from_home_is_rejected_even_with_valid_card_and_paired_cookie(self):
		r = self.post(REMOTE_ADDR='114.4.20.10')                   # alamat publik pegawai di rumah
		self.assertEqual((r.status_code, r.json()['code']), (403, 'net'))
		self.assertEqual(Attendance.objects.count(), 0)
		self.assertIsNone(r.json()['employee'])                     # tidak membocorkan nama

	@override_settings(ATTENDANCE_KIOSK_ALLOWED_IPS=['10.0.0.0/8', '192.168.1.5'])
	def test_custom_allowlist(self):
		self.assertEqual(self.post(REMOTE_ADDR='203.0.113.9').status_code, 403)
		self.assertEqual(self.post(REMOTE_ADDR='10.1.2.3').status_code, 200)
		self.assertEqual(self.post(REMOTE_ADDR='192.168.1.5').status_code, 200)
		self.assertEqual(self.post(REMOTE_ADDR='192.168.1.6').status_code, 403)       # bukan di daftar sendiri
		self.assertEqual(self.client.get(reverse('qr-kiosk-check'), REMOTE_ADDR='203.0.113.9').status_code, 403)
		self.assertEqual(self.client.get(reverse('qr-kiosk-check'), REMOTE_ADDR='192.168.1.5').status_code, 200)

	def test_untrusted_proxy_header_fails_closed_and_warns_admin(self):
		with self.assertLogs('attendance.qr', 'WARNING') as logs:
			r = self.post(REMOTE_ADDR='127.0.0.1', HTTP_X_FORWARDED_FOR='10.1.1.1')
		self.assertEqual((r.status_code, r.json()['code']), (403, 'net'))
		self.assertIn('ATTENDANCE_TRUST_PROXY_HEADER', logs.output[0])
		for header in ('HTTP_FORWARDED', 'HTTP_X_REAL_IP'):
			self.assertEqual(self.post(REMOTE_ADDR='127.0.0.1', **{header: 'for=10.1.1.1'}).status_code, 403)

	@override_settings(ATTENDANCE_TRUST_PROXY_HEADER=True)
	def test_trusted_proxy_uses_rightmost_entry(self):
		# klien memalsukan 10.1.1.1 di kiri; proxy menambahkan alamat asli (luar kantor) di kanan
		self.assertEqual(self.post(REMOTE_ADDR='127.0.0.1', HTTP_X_FORWARDED_FOR='10.1.1.1, 203.0.113.9').status_code, 403)
		self.assertEqual(self.post(REMOTE_ADDR='127.0.0.1', HTTP_X_FORWARDED_FOR='198.51.100.7, 192.168.1.44').status_code, 200)

	@override_settings(ATTENDANCE_KIOSK_ALLOWED_IPS=['not-an-ip', '10.0.0.0/8'])
	def test_bad_config_entries_do_not_open_access(self):
		self.assertEqual(self.post(REMOTE_ADDR='203.0.113.9').status_code, 403)
		self.assertEqual(self.post(REMOTE_ADDR='10.0.0.7').status_code, 200)

	@override_settings(ATTENDANCE_KIOSK_ALLOWED_IPS=[])
	def test_explicitly_open_network_works_and_is_flagged(self):
		self.assertEqual(self.post(REMOTE_ADDR='203.0.113.9').status_code, 200)
		self.assertTrue(svc.security_is_open())

	def test_security_banner_only_when_network_is_open(self):
		u = User.objects.create_user('m', password='x')
		u.groups.add(Group.objects.create(name='admin'))
		self.client.force_login(u)
		self.client.cookies['mdi_lang'] = 'id'
		self.assertNotContains(self.client.get(reverse('qr-today')), 'ATTENDANCE_KIOSK_ALLOWED_IPS')
		with override_settings(ATTENDANCE_KIOSK_ALLOWED_IPS=[]):
			self.assertContains(self.client.get(reverse('qr-today')), 'ATTENDANCE_KIOSK_ALLOWED_IPS')


class I18nTests(QRBase):
	def login_admin(self):
		u = User.objects.create_user('adm', password='x')
		u.groups.add(Group.objects.get_or_create(name='admin')[0])
		self.client.force_login(u)

	def test_every_key_has_four_languages_and_same_placeholders(self):
		import re
		from attendance import qr_i18n as i18n
		ph = lambda text: sorted(re.findall(r'\{(\w+)\}', text))
		for key, row in i18n.MESSAGES.items():
			self.assertEqual(len(row), 4, key)
			for lang, text in zip(i18n.LANGS, row):
				self.assertTrue(text.strip(), '%s [%s] kosong' % (key, lang))
			for text in row[1:]:
				self.assertEqual(ph(text), ph(row[0]), 'placeholder berbeda: ' + key)
		for lang in i18n.LANGS:
			self.assertEqual(len(i18n.MONTHS[lang]), 12)
			self.assertEqual(len(i18n.WEEKDAYS[lang]), 7)

	def test_pages_follow_old_attendance_page_structure(self):
		self.login_admin()
		html = self.client.get(reverse('qr-today')).content.decode()
		for marker in ['breadcrumb-holder', 'card shadow-lg pt-1 qr-border rounded', 'card-header', 'btn btn-sm btn-mdi',
					   'table table-sm table-striped', reverse('attendance-dash')]:
			self.assertIn(marker, html)
		self.assertNotIn('btn-info', html)               # tombol memakai merah MDI, bukan teal

	def test_dash_button_snippet(self):
		from django.template.loader import render_to_string
		html = render_to_string('attendance/qr/_dash_button.html', {}, request=__import__('django.test', fromlist=['x']).RequestFactory().get('/'))
		self.assertIn(reverse('qr-today'), html)
		self.assertIn('btn btn-sm btn-mdi', html)
		self.assertIn('Absensia QR', html)
		self.assertIn('#e3171e', html)                   # tetap merah walau qr.css belum dimuat halaman itu

	def test_every_key_used_in_templates_and_code_exists(self):
		import pathlib, re
		from attendance import qr_i18n as i18n
		base = pathlib.Path(__file__).parent
		prefixes = sorted({k.split('.')[0] for k in i18n.MESSAGES}, key=len, reverse=True)
		literal = re.compile(r"""['"]((?:%s)(?:\.\w+)+)['"]""" % '|'.join(re.escape(p) for p in prefixes))
		used = set()
		for f in (base / 'templates' / 'attendance' / 'qr').glob('*.html'):
			text = f.read_text(encoding='utf-8')
			used |= set(re.findall(r"\{%\s*t4?\s+['\"](\w+(?:\.\w+)+)['\"]", text))
			used |= set(re.findall(r'data-i18n(?:-ph)?="(\w+(?:\.\w+)+)"', text))
		code = (base / 'views_qr.py').read_text(encoding='utf-8') + (base / 'qr_service.py').read_text(encoding='utf-8')
		js = (base / 'static' / 'attendance' / 'qr' / 'kiosk.js').read_text(encoding='utf-8')
		used |= set(literal.findall(code)) | set(literal.findall(js))
		for name in re.findall(r"_result\([^,]+,\s*'(\w+)'", code):
			used |= {'res.%s.title' % name, 'res.%s.msg' % name}
		used |= {'res.%s.%s' % (c, part) for c in ('net', 'device') for part in ('title', 'msg')}
		self.assertGreater(len(used), 60)             # pastikan regex benar-benar menemukan kunci
		self.assertEqual(sorted(k for k in used if k not in i18n.MESSAGES), [])

	def test_default_language_is_tetun_and_unknown_is_ignored(self):
		from attendance import qr_i18n as i18n
		self.assertEqual(i18n.tr(None, 'nav.today'), 'Ohin')
		self.assertEqual(i18n.tr('xx', 'nav.today'), 'Ohin')
		self.assertEqual(i18n.tr('pt', 'nav.today'), 'Hoje')
		self.assertEqual(i18n.tr('en', 'nope.key'), 'nope.key')
		self.assertEqual(i18n.tr('en', 'msg.printed', n=3), '3 cards marked as printed.')
		self.assertEqual(i18n.fmt_date(MON, 'pt'), 'Segunda-feira, 9 Novembro 2026')
		self.assertEqual(i18n.fmt_date(MON, 'tet'), 'Segunda, 9 Novembru 2026')

	@override_settings(ATTENDANCE_DEFAULT_LANG='en')
	def test_default_language_configurable(self):
		self.login_admin()
		self.assertContains(self.client.get(reverse('qr-today')), 'Attendance today')

	def test_lang_view_sets_cookie_and_redirects(self):
		url = reverse('qr-lang', args=['pt'])
		r = self.client.get(url, {'next': '/attendance/qr/today/'})
		self.assertEqual((r.status_code, r['Location']), (302, '/attendance/qr/today/'))
		self.assertEqual(r.cookies['mdi_lang'].value, 'pt')
		self.assertEqual(r.cookies['mdi_lang']['samesite'], 'Lax')

	def test_lang_view_rejects_open_redirect_and_bad_code(self):
		r = self.client.get(reverse('qr-lang', args=['en']), {'next': 'https://evil.example/x'})
		self.assertEqual(r['Location'], reverse('qr-kiosk'))
		r = self.client.get(reverse('qr-lang', args=['zz']), {'next': '/attendance/qr/today/'})
		self.assertNotIn('mdi_lang', r.cookies)

	@override_settings(ATTENDANCE_LANG_IN_NAVBAR=False)
	def test_admin_pages_render_in_each_language(self):
		from attendance import qr_i18n as i18n
		self.login_admin()
		self.scan(MON, 8)
		for lang in i18n.LANGS:
			self.client.cookies['mdi_lang'] = lang
			for name, key in [('qr-today', 'today.title'), ('qr-report', 'rep.title'), ('qr-cards', 'cards.title'),
							  ('qr-devices', 'dev.title'), ('qr-me', 'me.title')]:
				r = self.client.get(reverse(name))
				self.assertEqual(r.status_code, 200, (lang, name))
				self.assertContains(r, escape(i18n.tr(lang, key)))
				self.assertContains(r, 'class="qr-flag')
			self.assertContains(self.client.get(reverse('qr-report')), i18n.month_name(MON.month, lang))

	@override_settings(ATTENDANCE_LANG_IN_NAVBAR=False)
	def test_flags_are_links_that_mark_current_language(self):
		self.login_admin()
		self.client.cookies['mdi_lang'] = 'pt'
		r = self.client.get(reverse('qr-today'))
		html = r.content.decode()
		for code in ('tet', 'en', 'id', 'pt'):
			self.assertIn('/attendance/qr/lang/%s/?next=' % code, html)
		self.assertEqual(html.count('qr-flag on'), 1)
		self.assertIn('qr-flag on" href="/attendance/qr/lang/pt/', html)

	def _card_body(self):
		html = self.client.get(reverse('qr-today')).content.decode()
		return html[html.index('<div class="card-body">'):]

	@override_settings(ATTENDANCE_LANG_IN_NAVBAR=False)
	def test_flags_in_page_toolbar_until_navbar_snippet_is_installed(self):
		self.login_admin()
		self.assertIn('qr-flags', self._card_body())

	@override_settings(ATTENDANCE_LANG_IN_NAVBAR=True)
	def test_flags_hidden_in_page_when_navbar_has_them(self):
		self.login_admin()
		self.assertNotIn('qr-flags', self._card_body())

	def test_navbar_snippet_renders_flags(self):
		from django.template.loader import render_to_string
		from django.test import RequestFactory
		req = RequestFactory().get('/x/')
		html = render_to_string('attendance/qr/_navbar_lang.html', {}, request=req)
		self.assertEqual(html.count('hreflang='), 4)
		self.assertIn('qr-navbar-lang', html)
		self.assertIn('attendance/qr/qr.css', html)      # rapi juga di halaman non-absensi (Painel Absensia, dll.)

	def test_kiosk_has_flags_in_top_bar_and_all_languages(self):
		import json
		r = self.client.get(reverse('qr-kiosk'))
		html = r.content.decode()
		header = html[html.index('<header'):html.index('</header>')]
		self.assertEqual(header.count('data-lang='), 4)         # bendera ada di topbar kanan
		self.assertNotIn('data-lang=', html[html.index('<footer'):html.index('</footer>')])
		bundle = json.loads(r.context['bundle'] and json.dumps(r.context['bundle']))
		for lang in ('tet', 'en', 'id', 'pt'):
			self.assertIn('k.title', bundle[lang])
			self.assertEqual(len(bundle[lang]['_months']), 12)
		self.assertContains(r, 'id="i18n"')

	def test_kiosk_initial_language_follows_cookie(self):
		self.client.cookies['mdi_lang'] = 'pt'
		r = self.client.get(reverse('qr-kiosk'))
		self.assertContains(r, '<html lang="pt">')
		self.assertContains(r, 'ASSIDUIDADE DOS FUNCIONÁRIOS')

	def test_scan_result_language(self):
		self.pair()

		def api(lang):
			body = {'code': self.payload}
			if lang:
				body['lang'] = lang
			with mock.patch.object(timezone, 'now', lambda: at(MON, 8)):
				return self.post_scan(body).json()
		self.assertEqual(api('pt')['title'], 'Bem-vindo')
		Attendance.objects.all().delete()
		self.assertEqual(api('en')['message'], 'Checked in at 08:00.')
		Attendance.objects.all().delete()
		self.assertEqual(api('id')['title'], 'Selamat datang')
		Attendance.objects.all().delete()
		self.assertEqual(api('tet')['title'], 'Bem-vindu')
		Attendance.objects.all().delete()
		self.assertEqual(api('zz')['title'], 'Bem-vindu')       # bahasa tak dikenal -> bawaan (Tetun)
		Attendance.objects.all().delete()
		self.assertEqual(api(None)['title'], 'Bem-vindu')

	def test_rejection_messages_are_translated(self):
		NacionalHoliday.objects.create(name='', date=MON)
		self.assertIn('National holiday', svc.process_scan(self.payload, now=at(MON, 8), lang='en')['message'])
		self.assertIn('Feriado nacional', svc.process_scan(self.payload, now=at(MON, 8), lang='pt')['message'])
		self.assertIn('Libur nasional', svc.process_scan(self.payload, now=at(MON, 8), lang='id')['message'])
		self.assertIn('Feriadu nasionál', svc.process_scan(self.payload, now=at(MON, 8), lang='tet')['message'])
		self.assertIn('Weekend', svc.process_scan(self.payload, now=at(SUN, 8), lang='en')['message'])
		self.assertEqual(svc.process_scan('x', now=at(MON, 8), lang='pt')['title'], 'QR desconhecido')

	def test_today_log_shows_translated_result_label(self):
		self.login_admin()
		self.client.cookies['mdi_lang'] = 'pt'
		self.scan(SUN, 8)
		with mock.patch.object(timezone, 'now', lambda: at(MON, 12)):
			r = self.client.get(reverse('qr-today'))
		self.assertContains(r, 'Dia de folga')

	def test_leave_message_default_kind_translated(self):
		Leaves.objects.create(employee=self.emp, start_date=MON, end_date=MON, is_active=True, is_confirm=True)
		self.assertIn('Licença', svc.process_scan(self.payload, now=at(MON, 8), lang='pt')['message'])

	def test_card_print_has_all_four_languages_switchable(self):
		self.login_admin()
		self.client.cookies['mdi_lang'] = 'en'
		r = self.client.post(reverse('qr-card-print'), {'ids': [self.emp.pk]})
		html = r.content.decode()
		for code in ('tet', 'en', 'id', 'pt'):
			self.assertIn('data-l="%s"' % code, html)
			self.assertIn('data-lang="%s"' % code, html)       # bendera di toolbar
		self.assertIn('<span data-l="en">EMPLOYEE CARD</span>', html)
		self.assertIn('<span data-l="pt" hidden>CARTÃO DE FUNCIONÁRIO</span>', html)
		self.assertIn('KARTAUN FUNSIONÁRIU', html)
		self.assertIn('Scan saat masuk &amp; pulang', html)

	@override_settings(ATTENDANCE_CARD_BACK_NOTE={'en': 'English note', 'pt': 'Nota PT'}, ATTENDANCE_CARD_CONTACT='Tel 123')
	def test_card_note_and_contact_can_be_customised_per_language(self):
		self.login_admin()
		html = self.client.post(reverse('qr-card-print'), {'ids': [self.emp.pk]}).content.decode()
		self.assertIn('Nota PT', html)
		self.assertIn('English note', html)
		self.assertEqual(html.count('Tel 123'), 4)

	def test_csv_headers_follow_language(self):
		self.login_admin()
		self.client.cookies['mdi_lang'] = 'en'
		text = b''.join(self.client.get(reverse('qr-report-export'), {'year': 2026, 'month': 11}).streaming_content).decode('utf-8-sig')
		self.assertTrue(text.startswith('NRF,Name,Position,Division,1,2'))
		self.assertIn('Work days,Present,Late,Leave,Absent', text)


class ReportEngineTests(QRBase):
	def test_calendar_weeks_shape(self):
		rep = svc.month_report(2026, 11, employee=self.emp)       # 1 Nov 2026 = Minggu
		weeks = rep['rows'][0]['weeks']
		self.assertTrue(all(len(w) == 7 for w in weeks))
		self.assertEqual(weeks[0][:6], [None] * 6)
		self.assertEqual(weeks[0][6]['day'], 1)
		self.assertEqual(sum(1 for w in weeks for c in w if c), 30)
		self.assertEqual(len(weeks), 6)                       # 6 kotak kosong + 30 hari = 36 -> 6 minggu

	def test_cells_carry_date_weekday_and_holiday_name(self):
		NacionalHoliday.objects.create(name='Hari Kemerdekaan', date=datetime.date(2020, 11, 12))
		cells = svc.month_report(2026, 11, employee=self.emp)['rows'][0]['cells']
		self.assertEqual((cells[8]['date'], cells[8]['wd']), (MON, 0))
		self.assertEqual((cells[11]['code'], cells[11]['holiday']), ('H', 'Hari Kemerdekaan'))
		self.assertEqual(cells[0]['code'], 'W')

	def test_attendance_rate(self):
		self.scan(MON, 8)
		with mock.patch.object(timezone, 'now', lambda: at(MON + datetime.timedelta(days=2), 12)):   # Rabu 11
			t = svc.month_report(2026, 11, employee=self.emp)['rows'][0]['totals']
		# 2-6 Nov alpha (5), 9 Nov hadir, 10 Nov alpha; 11 Nov (hari ini) belum dihitung
		self.assertEqual((t['present'], t['absent'], t['rate']), (1, 6, 14))

	def test_holiday_map_precedence(self):
		y = Year.objects.create(year=2026, is_active=True)
		Tolerance.objects.create(year=y, name='Toleransi', date=MON)
		self.assertEqual(svc.holiday_map()[(11, 9)], ('tolerance', 'Toleransi'))
		NacionalHoliday.objects.create(name='Nasional', date=MON)
		self.assertEqual(svc.holiday_map()[(11, 9)], ('holiday', 'Nasional'))
		NacionalHoliday.objects.create(name='X', date=datetime.date(2026, 12, 25), is_active=False)
		self.assertNotIn((12, 25), svc.holiday_map())

	def test_month_report_query_count_does_not_grow_with_employees(self):
		for i in range(25):
			self.make_employee('Peg%d' % i, 'X')
		with CaptureQueriesContext(connection) as q:
			rep = svc.month_report(2026, 11)
		self.assertEqual(len(rep['rows']), 26)
		self.assertLess(len(q), 12)

	def test_year_summary(self):
		self.scan(MON, 8, 0)
		self.scan(datetime.date(2026, 3, 10), 9, 0)      # Selasa, terlambat
		with CaptureQueriesContext(connection) as q:
			months = svc.year_summary(self.emp, 2026)
		self.assertEqual(len(months), 12)
		self.assertEqual((months[10]['present'], months[2]['present'], months[2]['late']), (1, 1, 1))
		self.assertLess(len(q), 60)

	def test_overview(self):
		other_div = Division.objects.create(code='D2', name='Divisi Dua')
		e2 = self.make_employee('Ana', 'Lopes')
		EmployeeDivision.objects.filter(employee=e2).update(division=other_div)
		c2 = QRCard.objects.create(employee=e2)
		self.scan(MON, 8, 0)
		self.scan(MON, 9, 0, payload=svc.make_payload(c2))               # Ana terlambat
		self.scan(MON + datetime.timedelta(days=1), 9, 30, payload=svc.make_payload(c2))
		with mock.patch.object(timezone, 'now', lambda: at(datetime.date(2026, 11, 11), 12)):
			ov = svc.overview(svc.month_report(2026, 11))
		t = ov['totals']
		self.assertEqual((t['employees'], t['present'], t['late']), (2, 3, 2))
		names = [d['name'] for d in ov['divisions']]
		self.assertEqual(names, ['Divisi Dua', 'Divisi Satu'])
		self.assertEqual(ov['late_top'][0]['employee']['name'], 'Ana Lopes')
		self.assertEqual(ov['late_top'][0]['totals']['late'], 2)
		self.assertIsNone(ov['daily']['present'][0])                     # 1 Nov = Minggu
		self.assertEqual(ov['daily']['present'][8], 2)                   # 9 Nov
		self.assertEqual(ov['daily']['late'][8], 1)
		self.assertIsNotNone(ov['daily']['present'][10])                 # 11 Nov = hari ini
		self.assertIsNone(ov['daily']['present'][11])                    # 12 Nov belum terjadi: kosong, bukan 0
		self.assertIsNone(ov['daily']['absent'][20])

	def test_overview_empty(self):
		Employee.objects.all().delete()
		ov = svc.overview(svc.month_report(2026, 11))
		self.assertEqual((ov['totals']['employees'], ov['totals']['rate'], ov['divisions'], ov['late_top']), (0, None, [], []))


class ReportPageTests(QRBase):
	def login(self, group, link=False):
		u = User.objects.create_user('u_' + group, password='x')
		u.groups.add(Group.objects.get_or_create(name=group)[0])
		self.client.force_login(u)
		self.client.cookies['mdi_lang'] = 'id'
		if link:
			from employee.models import EmployeeUser
			EmployeeUser.objects.create(employee=self.emp, user=u)
		return u

	def test_dash_pages_for_viewers_only(self):
		for group, ok in [('admin', True), ('staff', True), ('president', True), ('funsionario', False), ('employee', False)]:
			self.client.logout()
			self.login(group)
			for name in ('qr-dash', 'qr-dash-chart'):
				r = self.client.get(reverse(name))
				self.assertEqual(r.status_code, 200 if ok else 403, (group, name))
				self.assertEqual('Dasbor laporan absensi' in r.content.decode(), ok, (group, name))
				if not ok:
					self.assertContains(r, '403', status_code=403)

	def test_dash_tabular_content(self):
		self.login('admin')
		self.scan(MON, 9, 0)
		with mock.patch.object(timezone, 'now', lambda: at(datetime.date(2026, 11, 13), 12)):
			r = self.client.get(reverse('qr-dash'), {'year': 2026, 'month': 11})
		self.assertContains(r, 'ABSENSI BULAN NOVEMBER TAHUN 2026')
		self.assertContains(r, 'Divisi Satu')
		self.assertContains(r, '?year=2026&month=11&div=%d' % self.div.pk)     # tautan ke rekap divisi
		self.assertContains(r, 'qr-stack')
		self.assertContains(r, 'qr-tiles')
		self.assertContains(r, 'qr-subtabs')

	def test_dash_filter_by_division(self):
		self.login('admin')
		other = Division.objects.create(code='D2', name='Divisi Dua')
		e2 = self.make_employee('Ana', 'Lopes')
		EmployeeDivision.objects.filter(employee=e2).update(division=other)
		r = self.client.get(reverse('qr-dash'), {'div': other.pk})
		self.assertContains(r, 'Divisi Dua')
		self.assertNotContains(r, '>Divisi Satu</a>')

	def test_api_dash_requires_login_and_group_with_json_errors(self):
		url = reverse('qr-api-dash')
		r = self.client.get(url)
		self.assertEqual((r.status_code, r.json()['error']), (401, 'login'))
		self.login('funsionario')
		r = self.client.get(url)
		self.assertEqual((r.status_code, r.json()['error']), (403, 'forbidden'))
		self.assertEqual(self.client.post(url).status_code, 405)

	def test_api_dash_data_shape_and_language(self):
		self.login('president')
		self.scan(MON, 9, 0)
		with mock.patch.object(timezone, 'now', lambda: at(datetime.date(2026, 11, 13), 12)):
			r = self.client.get(reverse('qr-api-dash'), {'year': 2026, 'month': 11})
		self.assertEqual(r['Cache-Control'], 'no-store')
		d = r.json()
		self.assertEqual(d['divisions']['label'], ['Divisi Satu'])
		self.assertEqual([s['key'] for s in d['divisions']['series']], ['present', 'late', 'leave', 'absent'])
		self.assertEqual([s['name'] for s in d['divisions']['series']], ['Hadir', 'Hadir, terlambat', 'Cuti', 'Alpha'])
		self.assertEqual([s['data'][0] for s in d['divisions']['series']], [0, 1, 0, 8])   # terlambat dipisah dari tepat waktu; alpha: 2-6 dan 10-12 Nov
		self.assertEqual(len(d['daily']['label']), 30)
		self.assertIsNone(d['daily']['series'][0]['data'][0])                              # akhir pekan = null
		self.assertEqual(d['late_top']['label'], ['Maria Silva'])
		self.client.cookies['mdi_lang'] = 'pt'
		d = self.client.get(reverse('qr-api-dash')).json()
		self.assertEqual(d['divisions']['series'][2]['name'], 'Licença')

	def test_me_tabular_requires_own_link_and_shows_tiles_and_days(self):
		self.login('funsionario', link=True)
		self.scan(MON, 9, 0)
		self.scan(MON + datetime.timedelta(days=1), 8, 0)
		self.scan(MON + datetime.timedelta(days=1), 15, 0)
		with mock.patch.object(timezone, 'now', lambda: at(datetime.date(2026, 11, 13), 12)):
			r = self.client.get(reverse('qr-me'), {'year': 2026, 'month': 11})
		html = r.content.decode()
		for status in ('present', 'late', 'leave', 'absent', 'early', 'noout'):
			self.assertIn('status=%s' % status, html)
		self.assertContains(r, 'qr-cal')
		self.assertIn('09/11', html)
		self.assertNotIn('07/11', html)                      # Sabtu tidak ada di daftar
		self.assertContains(r, 'Sen 09/11')

	def test_me_status_filter(self):
		self.login('employee', link=True)
		self.scan(MON, 9, 0)                                 # terlambat
		self.scan(MON + datetime.timedelta(days=1), 8, 0)    # tepat waktu
		with mock.patch.object(timezone, 'now', lambda: at(datetime.date(2026, 11, 13), 12)):
			html = self.client.get(reverse('qr-me'), {'year': 2026, 'month': 11, 'status': 'late'}).content.decode()
			self.assertIn('Sen 09/11', html)
			self.assertNotIn('Sel 10/11', html)
			self.assertIn('Filter: Terlambat', html)
			html = self.client.get(reverse('qr-me'), {'year': 2026, 'month': 11, 'status': 'bogus'}).content.decode()
			self.assertIn('Sel 10/11', html)
			self.assertNotIn('Filter:', html)
			html = self.client.get(reverse('qr-me'), {'year': 2026, 'month': 11, 'status': 'noout'}).content.decode()
			self.assertIn('Belum scan pulang', html)

	def test_me_shows_holiday_row_with_name(self):
		NacionalHoliday.objects.create(name='Hari Test', date=datetime.date(2020, 11, 12))
		self.login('funsionario', link=True)
		with mock.patch.object(timezone, 'now', lambda: at(datetime.date(2026, 11, 13), 12)):
			self.assertContains(self.client.get(reverse('qr-me'), {'year': 2026, 'month': 11}), 'Hari Test')

	def test_me_list_hides_future_days_but_keeps_today(self):
		self.login('funsionario', link=True)
		with mock.patch.object(timezone, 'now', lambda: at(datetime.date(2026, 11, 11), 10)):     # Rabu 11 Nov
			html = self.client.get(reverse('qr-me'), {'year': 2026, 'month': 11}).content.decode()
		self.assertIn('Rab 11/11', html)                  # hari ini, belum ada scan
		self.assertNotIn('Kam 12/11', html)
		self.assertNotIn('Sen 16/11', html)
		self.assertIn('qr-cal', html)                     # kalender mini tetap memuat seluruh bulan

	def test_me_unlinked_account(self):
		self.login('funsionario')
		self.assertContains(self.client.get(reverse('qr-me')), 'belum dihubungkan')
		self.assertContains(self.client.get(reverse('qr-me-chart')), 'belum dihubungkan')

	def test_me_chart_page_and_api(self):
		self.login('funsionario', link=True)
		self.scan(MON, 9, 0)
		r = self.client.get(reverse('qr-me-chart'), {'year': 2026, 'month': 11})
		self.assertContains(r, 'class="qr-charts"')
		self.assertContains(r, 'main/js_custom/highcharts.js')
		self.assertContains(r, 'attendance/qr/charts.js')
		self.assertContains(r, reverse('qr-api-me'))
		d = self.client.get(reverse('qr-api-me'), {'year': 2026, 'month': 11}).json()
		self.assertEqual(d['year']['label'][:3], ['Jan', 'Feb', 'Mar'])
		self.assertEqual([len(s['data']) for s in d['year']['series']], [12] * 4)
		self.assertEqual(d['year']['series'][1]['data'][10], 1)             # terlambat, November
		self.assertEqual(d['status']['obj'][1], 1)
		self.assertEqual(d['status']['keys'], ['present', 'late', 'leave', 'absent'])

	def test_api_me_only_returns_own_data_and_ignores_client_ids(self):
		other = self.make_employee('Jose', 'Costa')
		oc = QRCard.objects.create(employee=other)
		self.scan(MON, 8, 0, payload=svc.make_payload(oc))
		self.scan(MON + datetime.timedelta(days=1), 8, 0, payload=svc.make_payload(oc))
		self.login('funsionario', link=True)
		self.scan(MON, 8, 0)
		d = self.client.get(reverse('qr-api-me'), {'year': 2026, 'month': 11, 'employee': other.pk, 'id': other.pk}).json()
		self.assertEqual(d['year']['series'][0]['data'][10], 1)             # hanya milik sendiri, bukan 2

	def test_api_me_errors_are_json(self):
		url = reverse('qr-api-me')
		self.assertEqual(self.client.get(url).status_code, 401)
		self.login('funsionario')
		r = self.client.get(url)
		self.assertEqual((r.status_code, r.json()['error']), (404, 'unlinked'))
		self.client.logout()
		self.login('stranger', link=True)
		self.assertEqual(self.client.get(url).status_code, 403)

	def test_employee_group_is_allowed_by_default(self):
		self.login('employee', link=True)
		for name in ('qr-me', 'qr-me-chart', 'qr-api-me'):
			self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

	def test_subtabs_switch_between_tabular_and_charts(self):
		self.login('funsionario', link=True)
		html = self.client.get(reverse('qr-me'), {'year': 2026, 'month': 11}).content.decode()
		self.assertIn('href="%s?year=2026&amp;month=11"' % reverse('qr-me-chart'), html)
		self.assertIn('<a role="tab" class="on" href="%s' % reverse('qr-me'), html)
		self.login('admin')
		html = self.client.get(reverse('qr-dash-chart'), {'year': 2026, 'month': 11}).content.decode()
		self.assertIn(reverse('qr-dash') + '?year=2026', html)

	def test_menu_snippets_point_to_right_pages_per_group(self):
		from django.template.loader import render_to_string
		from django.test import RequestFactory
		for group, tab, chart in [('admin', 'qr-dash', 'qr-dash-chart'), ('president', 'qr-dash', 'qr-dash-chart'),
								  ('funsionario', 'qr-me', 'qr-me-chart'), ('employee', 'qr-me', 'qr-me-chart')]:
			u = User.objects.create_user('m_' + group, password='x')
			u.groups.add(Group.objects.get_or_create(name=group)[0])
			req = RequestFactory().get('/')
			req.user = u
			req.COOKIES['mdi_lang'] = 'tet'
			html = render_to_string('attendance/qr/_navbar_report.html', {}, request=req)
			self.assertIn('href="%s"' % reverse(tab), html, group)
			self.assertIn('href="%s"' % reverse(chart), html, group)
			self.assertIn('dropdown-menu', html)
			self.assertIn('RELATORIU', html)
			self.assertIn('Tabular', html)
			self.assertIn('Grafiku', html)
			home = render_to_string('attendance/qr/_home_button.html', {}, request=req)
			self.assertIn('href="%s"' % reverse(tab), home)

	def test_menu_snippets_in_each_language(self):
		from django.template.loader import render_to_string
		from django.test import RequestFactory
		u = User.objects.create_user('lang', password='x')
		u.groups.add(Group.objects.get_or_create(name='admin')[0])
		for lang, report, chart in [('en', 'REPORTS', 'Charts'), ('id', 'LAPORAN', 'Grafik'), ('pt', 'RELATÓRIOS', 'Gráficos')]:
			req = RequestFactory().get('/')
			req.user = u
			req.COOKIES['mdi_lang'] = lang
			html = render_to_string('attendance/qr/_navbar_report.html', {}, request=req)
			self.assertIn(report, html)
			self.assertIn(chart, html)


class ResponsiveTests(QRBase):
	def login_admin(self):
		u = User.objects.create_user('rsp', password='x')
		u.groups.add(Group.objects.get_or_create(name='admin')[0])
		self.client.force_login(u)
		self.client.cookies['mdi_lang'] = 'id'

	def test_standalone_pages_have_viewport_meta(self):
		self.login_admin()
		kiosk = self.client.get(reverse('qr-kiosk')).content.decode()
		cards = self.client.post(reverse('qr-card-print'), {'ids': [self.emp.pk]}).content.decode()
		for html in (kiosk, cards):
			self.assertIn('name="viewport" content="width=device-width, initial-scale=1', html)
		self.assertIn('viewport-fit=cover', kiosk)                       # layar berponi

	def test_report_has_card_view_for_phones_and_grid_for_wide_screens(self):
		self.login_admin()
		self.scan(MON, 8)
		html = self.client.get(reverse('qr-report'), {'year': 2026, 'month': 11}).content.decode()
		self.assertIn('class="d-md-none"', html)
		self.assertIn('class="d-none d-md-block"', html)
		self.assertIn('qr-empcards', html)
		self.assertIn('qr-cal', html)
		self.assertEqual(html.count('class="qr-cal-h"'), 7)
		self.assertIn('class="table-responsive"', html)

	def test_tables_collapse_to_cards_with_labels(self):
		self.login_admin()
		self.scan(MON, 8)
		with mock.patch.object(timezone, 'now', lambda: at(MON, 12)):
			html = self.client.get(reverse('qr-today')).content.decode()
		self.assertIn('qr-stack', html)
		self.assertIn('data-label="Nama"', html)
		html = self.client.get(reverse('qr-cards')).content.decode()
		self.assertIn('qr-stack', html)
		self.assertIn('data-label="Jabatan"', html)

	def test_forms_and_toolbar_use_touch_friendly_classes(self):
		self.login_admin()
		html = self.client.get(reverse('qr-report')).content.decode()
		self.assertIn('class="qr-form"', html)
		self.assertIn('qr-toolbar', html)

	def test_css_is_mobile_first(self):
		import pathlib, re
		css = (pathlib.Path(__file__).parent / 'static' / 'attendance' / 'qr' / 'qr.css').read_text(encoding='utf-8')
		self.assertNotIn('max-width: 900px', css)                       # tidak lagi desktop-first
		for width in (576, 768, 900, 992):
			self.assertIn('@media (min-width: %dpx)' % width, css)
		base = css[:css.index('@media (min-width: 576px)')]
		self.assertIn('grid-template-columns: repeat(2', base)          # dasar = 2 kolom (HP)
		self.assertIn('min-height: 46px', base)                         # sasaran sentuh >= 44px
		self.assertIn('.k-main { flex: 1; display: grid; grid-template-columns: minmax(0, 1fr)', base)
		self.assertRegex(css, r'\.k-top \{[^}]*flex-wrap: wrap')           # header kiosk membungkus di HP


class SecurityTests(QRBase):
	def login(self, *groups, name='sec'):
		u = User.objects.create_user(name, password='x')
		for g in groups:
			u.groups.add(Group.objects.get_or_create(name=g)[0])
		self.client.force_login(u)
		self.client.cookies['mdi_lang'] = 'id'
		return u

	# ---------- masukan tak tepercaya
	def test_payload_parsing_never_raises_on_hostile_input(self):
		sig = self.payload.split('.')[3]
		hostile = ['MDI.1.1.' + 'é' * 32, 'MDI.1.1.' + '١' * 32, 'MDI.١٢.1.' + sig,
				   'MDI.' + '9' * 5000 + '.1.' + sig, 'MDI.1.' + '9' * 40 + '.' + sig, 'MDI.1.1.' + sig + 'x', 'MDI.1.1.',
				   'MDI..1.' + sig, 'MDI.-1.1.' + sig, '\x00' * 50, 'MDI.1.1.' + sig[:-1], 'MDI.1.1.' + sig + 'a', 'MDI.1.1.' + sig + 'Z', 'M' * 5000, b'MDI', 12345, None, ['x']]
		for raw in hostile:
			self.assertIsNone(svc.parse_payload(raw), repr(raw)[:60])
		self.assertEqual(svc.parse_payload(self.payload), (self.emp.pk, 1))

	def test_scanner_quirks_are_tolerated(self):
		"""Alat pembaca USB: Caps Lock membalik huruf, awalan pengenal simbologi, akhiran Tab/CR/LF, karakter kontrol."""
		p = self.payload
		sig = p.split('.')[3]
		ok = [p.upper(), p.lower(), 'mdi.%d.1.%s' % (self.emp.pk, sig.upper()), p.swapcase(), ']Q1' + p, p + '\t', p + '\r\n',
			  '\x02' + p + '\x03', '  ' + p + '  ', ']Q1' + p.upper() + '\r', '\n' + p, p + '\x00']
		for raw in ok:
			self.assertEqual(svc.parse_payload(raw), (self.emp.pk, 1), repr(raw))
		bad = [p.replace(p[-1], '0' if p[-1] != '0' else '1'), p.upper().replace('.1.', '.2.', 1), 'MDI.%d.1.%s' % (self.emp.pk + 1, sig.upper()),
			   p + 'x', p[:-1], 'X' + p[1:], p.replace('MDI', 'MID')]
		for raw in bad:
			self.assertIsNone(svc.parse_payload(raw), repr(raw))

	def test_uppercase_scan_with_junk_is_recorded_like_a_normal_scan(self):
		r = self.scan(MON, 8, payload=']Q1' + self.payload.upper() + '\t')
		self.assertEqual((r['ok'], r['code']), (True, 'in'))
		self.assertEqual(Attendance.objects.get().employee_id, self.emp.pk)

	def test_kiosk_javascript_extracts_the_code_ignoring_case_and_junk(self):
		import pathlib
		js = (pathlib.Path(__file__).parent / 'static' / 'attendance' / 'qr' / 'kiosk.js').read_text(encoding='utf-8')
		self.assertIn('/i;', js)                                               # tanpa membedakan huruf besar/kecil
		self.assertIn('v.match(CODE_RE)', js)                                   # hanya isi QR yang dikirim, awalan/akhiran dibuang

	def test_non_ascii_qr_through_api_is_a_normal_rejection_not_a_500(self):
		self.pair()
		for code in ['MDI.1.1.' + 'é' * 32, '‮' * 20, 'MDI.' + '9' * 150]:
			r = self.post_scan({'code': code})
			self.assertEqual((r.status_code, r.json()['code']), (200, 'invalid'))

	def test_kiosk_response_exposes_only_what_the_screen_shows(self):
		self.pair()
		IDFNumber.objects.create(employee=self.emp, nrf='NRF-RAHASIA')
		with mock.patch.object(timezone, 'now', lambda: at(MON, 8)):
			d = self.post_scan({'code': self.payload}).json()
		self.assertEqual(sorted(d['employee']), ['division', 'name', 'photo', 'position'])
		self.assertNotIn('NRF-RAHASIA', json.dumps(d))
		self.assertNotIn(str(self.emp.pk) + '"', json.dumps(d['employee']))

	def test_revoked_card_does_not_reveal_owner(self):
		self.pair()
		old = self.payload
		self.card.version = 2
		self.card.save()
		r = self.post_scan({'code': old})
		d = r.json()
		self.assertEqual(d['code'], 'revoked')
		self.assertIsNone(d['employee'])                           # layar kiosk tidak menampilkan nama pemilik
		self.assertEqual(ScanLog.objects.get().employee_id, self.emp.pk)    # tetapi tetap tercatat untuk audit

	def test_csv_and_xlsx_formula_injection_is_neutralised(self):
		Employee.objects.filter(pk=self.emp.pk).update(first_name='=HYPERLINK("http://x")', last_name='+cmd')
		IDFNumber.objects.create(employee=self.emp, nrf='@SUM(1)')
		self.login('admin')
		text = b''.join(self.client.get(reverse('qr-report-export'), {'year': 2026, 'month': 11}).streaming_content).decode('utf-8-sig')
		self.assertIn('"\'=HYPERLINK(""http://x"") +cmd"', text)
		self.assertIn("'@SUM(1)", text)
		self.assertNotIn(',=HYPERLINK', text)
		from openpyxl import load_workbook
		ws = load_workbook(io.BytesIO(self.client.get(reverse('qr-report-export'), {'year': 2026, 'month': 11, 'fmt': 'xlsx'}).content)).active
		self.assertTrue(ws.cell(2, 2).value.startswith("'="))
		self.assertTrue(ws.cell(2, 1).value.startswith("'@"))

	def test_javascript_never_puts_server_text_into_innerhtml(self):
		import pathlib
		base = pathlib.Path(__file__).parent / 'static' / 'attendance' / 'qr'
		kiosk = (base / 'kiosk.js').read_text(encoding='utf-8')
		code_lines = [l for l in kiosk.splitlines() if '.innerHTML' in l and not l.strip().startswith(('/*', '*', '//'))]
		self.assertEqual(len(code_lines), 1)
		self.assertIn('<svg', code_lines[0])                                  # hanya ikon konstan
		self.assertNotIn('localStorage', kiosk)                               # tidak ada kunci yang disimpan di sisi klien
		self.assertNotIn('X-Kiosk-Key', kiosk)
		self.assertNotIn('innerHTML', (base / 'charts.js').read_text(encoding='utf-8'))

	# ---------- akses
	def test_group_membership_is_not_limited_to_first_group(self):
		self.login('funsionario', 'admin')                                    # grup yang berhak bukan yang pertama
		self.assertEqual(self.client.get(reverse('qr-dash')).status_code, 200)
		self.assertEqual(self.client.get(reverse('qr-cards')).status_code, 200)
		self.assertEqual(self.client.get(reverse('qr-api-dash')).status_code, 200)

	def test_forbidden_is_http_403_and_anonymous_is_redirected_to_login(self):
		self.login('funsionario')
		for name in ('qr-dash', 'qr-today', 'qr-report', 'qr-cards', 'qr-devices', 'qr-dash-chart'):
			self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
		self.client.logout()
		r = self.client.get(reverse('qr-cards'))
		self.assertEqual(r.status_code, 302)
		self.assertTrue(r['Location'].startswith('/login/?next=') or 'next=' in r['Location'])

	def test_state_changing_endpoints_need_post_and_csrf(self):
		from django.test import Client
		self.login('admin', name='csrfadmin')
		c = Client(enforce_csrf_checks=True)
		c.force_login(User.objects.get(username='csrfadmin'))
		for name, args in [('qr-device-pair', []), ('qr-card-print', []), ('qr-card-action', []), ('qr-device-toggle', [1])]:
			self.assertEqual(c.post(reverse(name, args=args), {}).status_code, 403, name)     # tanpa token CSRF ditolak
			self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 405, name)
		self.assertEqual(ScanDevice.objects.count(), 0)

	def test_language_cookie_secure_flag_and_validation(self):
		r = self.client.get(reverse('qr-lang', args=['pt']), {'next': '/attendance/qr/today/'}, secure=True)
		self.assertTrue(r.cookies['mdi_lang']['secure'])
		self.login('admin')
		self.client.cookies['mdi_lang'] = '<script>alert(1)</script>'
		self.assertContains(self.client.get(reverse('qr-today')), 'Absensia ohin')      # nilai asing -> bahasa bawaan (Tetun)

	def test_sensitive_responses_are_not_cacheable(self):
		self.login('admin')
		self.pair()
		self.assertEqual(self.client.post(reverse('qr-card-print'), {'ids': [self.emp.pk]})['Cache-Control'], 'no-store')
		self.assertEqual(self.client.get(reverse('qr-api-dash'))['Cache-Control'], 'no-store')
		self.assertEqual(self.client.get(reverse('qr-kiosk'))['Cache-Control'], 'no-store')

	# ---------- pemeliharaan dan konfigurasi
	def test_prune_scanlogs_command(self):
		from django.core.management import call_command
		now = timezone.now()
		old = [ScanLog(scanned_at=now - datetime.timedelta(days=400 + i), code='in') for i in range(7)]
		ScanLog.objects.bulk_create(old + [ScanLog(scanned_at=now - datetime.timedelta(days=3), code='in')])
		out = io.StringIO()
		call_command('prune_scanlogs', days=180, batch=3, stdout=out)
		self.assertEqual(ScanLog.objects.count(), 1)
		self.assertIn('7 baris', out.getvalue())

	def _checks(self):
		from django.core.checks import run_checks
		return {m.id: m for m in run_checks() if m.id.startswith('attendance.')}

	def test_system_checks_warn_about_risky_config(self):
		ids = self._checks()
		self.assertIn('attendance.W001', ids)                                   # QR_SECRET belum diisi
		self.assertNotIn('attendance.W002', ids)                                # jaringan privat bawaan = aman
		self.assertNotIn('attendance.W003', ids)
		with override_settings(ATTENDANCE_QR_SECRET='rahasia-panjang'):
			self.assertNotIn('attendance.W001', self._checks())
		with override_settings(ATTENDANCE_KIOSK_ALLOWED_IPS=[], ATTENDANCE_REQUIRE_DEVICE_KEY=False):
			ids = self._checks()
			self.assertIn('attendance.W002', ids)
			self.assertIn('attendance.W003', ids)

	def test_system_checks_catch_invalid_values(self):
		with override_settings(ATTENDANCE_TIME_ZONE='Mars/Olympus', ATTENDANCE_LATE_AFTER='25:99',
							   ATTENDANCE_KIOSK_ALLOWED_IPS=['300.1.1.1']):
			ids = self._checks()
		self.assertEqual({'attendance.E001', 'attendance.E002', 'attendance.E003'} - set(ids), set())

	def test_secret_isolated_from_secret_key(self):
		with override_settings(ATTENDANCE_QR_SECRET='A'):
			a = svc.make_payload(self.card)
		with override_settings(ATTENDANCE_QR_SECRET='A', SECRET_KEY='lain-lagi'):
			self.assertEqual(svc.parse_payload(a), (self.emp.pk, 1))             # SECRET_KEY diganti, kartu tetap berlaku
		with override_settings(ATTENDANCE_QR_SECRET='B'):
			self.assertIsNone(svc.parse_payload(a))


class PerformanceTests(QRBase):
	"""Anggaran query: tidak boleh bertambah seiring jumlah pegawai (tidak ada N+1)."""

	def many(self, n):
		out = []
		for i in range(n):
			e = self.make_employee('Peg%03d' % i, 'Uji')
			IDFNumber.objects.create(employee=e, nrf='NRF%03d' % i)
			out.append(e)
		return out

	def queries(self, fn):
		with CaptureQueriesContext(connection) as q:
			result = fn()
		return len(q), result

	def login_admin(self):
		u = User.objects.create_user('perf', password='x')
		u.groups.add(Group.objects.get_or_create(name='admin')[0])
		self.client.force_login(u)
		self.client.cookies['mdi_lang'] = 'id'

	def test_scan_query_budget(self):
		self.pair()
		svc.day_off_reason(MON)                                    # cache libur terisi (keadaan normal setelah scan pertama hari itu)
		n, r = self.queries(lambda: self.scan(MON, 8))
		self.assertEqual(r['code'], 'in')
		self.assertLessEqual(n, 19, 'scan pertama memakai %d query' % n)
		n, r = self.queries(lambda: self.scan(MON, 16, 5))
		self.assertEqual(r['code'], 'out')
		self.assertLessEqual(n, 14, 'scan pulang memakai %d query' % n)

	def test_holiday_list_is_cached_on_scan_path_and_can_be_disabled(self):
		self.scan(MON, 8)                                          # mengisi cache
		NacionalHoliday.objects.create(name='Baru', date=MON + datetime.timedelta(days=1))
		with CaptureQueriesContext(connection) as q:
			svc.day_off_reason(MON + datetime.timedelta(days=1))
		self.assertEqual(len(q), 0)                                # daftar libur dari cache, tanpa query
		with override_settings(ATTENDANCE_HOLIDAY_CACHE_SECONDS=0):
			self.assertEqual(svc.day_off_reason(MON + datetime.timedelta(days=1)), ('holiday', 'Baru'))

	def test_rejected_scans_are_cheap(self):
		n, _ = self.queries(lambda: svc.process_scan('sampah', now=at(MON, 8)))
		self.assertLessEqual(n, 2)                                              # hanya catatan log
		n, _ = self.queries(lambda: self.scan(SUN, 8))
		self.assertLessEqual(n, 8, 'scan hari libur memakai %d query' % n)

	def test_pages_do_not_grow_with_employee_count(self):
		self.login_admin()
		self.many(5)
		small = {}
		for name in ('qr-today', 'qr-cards', 'qr-report', 'qr-dash'):
			cache.clear()
			small[name] = self.queries(lambda: self.client.get(reverse(name), {'year': 2026, 'month': 11}))[0]
		self.many(60)
		for name in ('qr-today', 'qr-cards', 'qr-report', 'qr-dash'):
			cache.clear()
			big = self.queries(lambda: self.client.get(reverse(name), {'year': 2026, 'month': 11}))[0]
			self.assertLessEqual(big, small[name] + 1, '%s: %d query untuk 6 pegawai, %d untuk 66' % (name, small[name], big))

	def test_card_print_creates_cards_in_bulk(self):
		self.login_admin()
		emps = self.many(40)
		n, r = self.queries(lambda: self.client.post(reverse('qr-card-print'), {'ids': [e.pk for e in emps]}))
		self.assertEqual(r.status_code, 200)
		self.assertEqual(QRCard.objects.count(), 41)
		self.assertLessEqual(n, 14, 'cetak 40 kartu memakai %d query' % n)

	def test_org_reports_are_cached_and_shared(self):
		self.login_admin()
		self.scan(MON, 8)
		n1, _ = self.queries(lambda: self.client.get(reverse('qr-dash'), {'year': 2026, 'month': 11}))
		n2, r = self.queries(lambda: self.client.get(reverse('qr-dash'), {'year': 2026, 'month': 11}))
		self.assertLess(n2, n1 - 2, 'permintaan kedua (%d) harus jauh lebih ringan dari pertama (%d)' % (n2, n1))
		# api grafik memakai hasil hitung yang sama (tanpa menghitung ulang seluruh pegawai)
		n3, _ = self.queries(lambda: self.client.get(reverse('qr-api-dash'), {'year': 2026, 'month': 11}))
		self.assertLess(n3, n1 - 2)

	def test_cache_staleness_is_bounded_and_can_be_disabled(self):
		self.login_admin()
		url = reverse('qr-api-dash')
		first = self.client.get(url, {'year': 2026, 'month': 11}).json()['divisions']['series'][1]['data']
		with mock.patch.object(timezone, 'now', lambda: at(MON, 9)):
			self.scan(MON, 9, 0)                                              # terlambat
		stale = self.client.get(url, {'year': 2026, 'month': 11}).json()['divisions']['series'][1]['data']
		self.assertEqual(stale, first)                                         # masih hasil cache (maks. 60 detik)
		with override_settings(ATTENDANCE_REPORT_CACHE_SECONDS=0):
			fresh = self.client.get(url, {'year': 2026, 'month': 11}).json()['divisions']['series'][1]['data']
		self.assertEqual(fresh, [1])

	def test_cache_is_per_period_and_division(self):
		self.login_admin()
		other = Division.objects.create(code='D2', name='Divisi Dua')
		a = self.client.get(reverse('qr-api-dash'), {'year': 2026, 'month': 11}).json()['divisions']['label']
		b = self.client.get(reverse('qr-api-dash'), {'year': 2026, 'month': 11, 'div': other.pk}).json()['divisions']['label']
		c = self.client.get(reverse('qr-api-dash'), {'year': 2026, 'month': 10}).json()['divisions']['label']
		self.assertEqual((a, b, c), (['Divisi Satu'], [], ['Divisi Satu']))

	def test_streaming_csv_and_static_versioning(self):
		self.login_admin()
		r = self.client.get(reverse('qr-report-export'), {'year': 2026, 'month': 11})
		self.assertTrue(r.streaming)
		self.assertEqual(r['Content-Type'], 'text/csv; charset=utf-8')
		from django.template import Context, Template
		html = Template("{% load qr_i18n %}{% qr_static 'attendance/qr/qr.css' %}").render(Context())
		self.assertRegex(html, r'attendance/qr/qr\.css\?v=\d+$')

	def test_heavy_scripts_load_only_where_needed_and_deferred(self):
		self.login_admin()
		self.assertNotContains(self.client.get(reverse('qr-today')), 'highcharts')
		self.assertNotContains(self.client.get(reverse('qr-dash')), 'highcharts')
		html = self.client.get(reverse('qr-dash-chart')).content.decode()
		self.assertIn('<script defer src="/static/main/js_custom/highcharts.js">', html)
		self.assertIn('defer src="/static/attendance/qr/charts.js?v=', html)


@override_settings(ATTENDANCE_NETWORK_MODE='allowlist')
class OfficeNetworkTests(QRBase):
	"""VPS/cPanel: server hanya melihat IP publik kantor; admin mendaftarkannya sendiri dari halaman Perangkat kiosk."""
	HOME = '114.4.20.10'
	OFFICE = '203.0.113.9'

	def setUp(self):
		super().setUp()
		u = User.objects.create_user('netadmin', password='x')
		u.groups.add(Group.objects.get_or_create(name='admin')[0])
		self.admin = u
		self.client.force_login(u)
		self.client.cookies['mdi_lang'] = 'id'
		self.pair()

	def scan_from(self, ip, **extra):
		with mock.patch.object(timezone, 'now', lambda: at(MON, 8)):
			return self.post_scan({'code': self.payload}, REMOTE_ADDR=ip, **extra)

	def test_public_office_ip_is_rejected_until_admin_allows_it(self):
		self.assertEqual(self.scan_from(self.OFFICE).status_code, 403)
		r = self.client.post(reverse('qr-net-add'), {}, REMOTE_ADDR=self.OFFICE)          # kosong = IP browser ini
		self.assertEqual(r.status_code, 302)
		self.assertEqual(list(OfficeNetwork.objects.values_list('cidr', flat=True)), [self.OFFICE + '/32'])
		self.assertEqual(self.scan_from(self.OFFICE).status_code, 200)                    # langsung berlaku (cache dibersihkan)
		Attendance.objects.all().delete()
		self.assertEqual(self.scan_from(self.HOME).status_code, 403)                      # dari rumah tetap gagal
		self.assertEqual(self.scan_from('203.0.113.10').status_code, 403)                 # IP tetangga bukan kantor

	def test_ipv6_is_registered_as_its_64_prefix(self):
		self.client.post(reverse('qr-net-add'), {}, REMOTE_ADDR='2a0d:3344:1:2:aaaa:bbbb:cccc:1')
		self.assertEqual(OfficeNetwork.objects.get().cidr, '2a0d:3344:1:2::/64')
		self.assertEqual(self.scan_from('2a0d:3344:1:2:1111:2222:3333:4444').status_code, 200)    # alamat sementara lain
		Attendance.objects.all().delete()
		self.assertEqual(self.scan_from('2a0d:3344:1:3::1').status_code, 403)                  # /64 tetangga

	def test_range_and_note(self):
		self.client.post(reverse('qr-net-add'), {'cidr': '203.0.113.0/24', 'note': 'Kantor MDI'})
		n = OfficeNetwork.objects.get()
		self.assertEqual((n.cidr, n.note, n.user_id), ('203.0.113.0/24', 'Kantor MDI', self.admin.pk))
		self.assertEqual(self.scan_from('203.0.113.77').status_code, 200)

	def test_toggle_takes_effect_immediately_and_is_cached_briefly(self):
		self.client.post(reverse('qr-net-add'), {'cidr': self.OFFICE})
		self.assertEqual(self.scan_from(self.OFFICE).status_code, 200)
		n = OfficeNetwork.objects.get()
		self.client.post(reverse('qr-net-toggle', args=[n.pk]))
		self.assertFalse(OfficeNetwork.objects.get().is_active)
		self.assertEqual(self.scan_from(self.OFFICE).status_code, 403)
		self.client.post(reverse('qr-net-toggle', args=[n.pk]))
		self.assertEqual(self.scan_from(self.OFFICE).status_code, 200)
		with CaptureQueriesContext(connection) as q:                                     # daftar jaringan di-cache
			svc.ip_allowed(self.OFFICE)
			svc.ip_allowed(self.OFFICE)
		self.assertLessEqual(len(q), 0)

	def test_invalid_and_too_broad_networks_are_refused(self):
		for bad in ['0.0.0.0/0', '10.0.0.0/8', '::/0', '2001:db8::/32', 'abc', '1.2.3.4/33', '999.1.1.1', '1.2.3']:
			self.client.post(reverse('qr-net-add'), {'cidr': bad})
		self.assertEqual(OfficeNetwork.objects.count(), 0)
		self.assertEqual(self.client.get(reverse('qr-devices')).status_code, 200)
		cidr, err = svc.broad_net_error('192.168.0.0/16')
		self.assertEqual((cidr, err), ('192.168.0.0/16', None))
		self.assertEqual(svc.broad_net_error('192.168.1.1')[0], '192.168.1.1/32')
		self.assertEqual(svc.broad_net_error('0.0.0.0/0')[1], 'broad')
		self.assertEqual(svc.broad_net_error('x')[1], 'invalid')

	def test_pairing_can_allow_the_office_ip_in_one_click(self):
		self.client.post(reverse('qr-device-pair'), {'name': 'PC Pintu', 'allow_ip': '1'}, REMOTE_ADDR=self.OFFICE)
		self.assertEqual(OfficeNetwork.objects.get().cidr, self.OFFICE + '/32')
		self.assertEqual(self.scan_from(self.OFFICE).status_code, 200)

	def test_pairing_without_checkbox_or_from_private_ip_adds_nothing(self):
		self.client.post(reverse('qr-device-pair'), {'name': 'A'}, REMOTE_ADDR=self.OFFICE)
		self.client.post(reverse('qr-device-pair'), {'name': 'B', 'allow_ip': '1'})          # 127.0.0.1 sudah diterima
		self.assertEqual(OfficeNetwork.objects.count(), 0)

	def test_devices_page_shows_ip_seen_by_server_and_whether_accepted(self):
		html = self.client.get(reverse('qr-devices'), REMOTE_ADDR=self.OFFICE).content.decode()
		self.assertIn(self.OFFICE, html)
		self.assertIn('Belum diterima', html)
		self.assertIn('name="allow_ip"', html)                                # tawaran izinkan sekalian saat pasangkan
		self.client.post(reverse('qr-net-add'), {}, REMOTE_ADDR=self.OFFICE)
		html = self.client.get(reverse('qr-devices'), REMOTE_ADDR=self.OFFICE).content.decode()
		self.assertIn('Diterima', html)
		self.assertNotIn('name="allow_ip"', html)
		self.assertIn(self.OFFICE + '/32', html)
		self.assertIn('192.168.0.0/16', html)                                 # jaringan dari settings.py ikut ditampilkan

	def test_only_device_admins_manage_networks(self):
		self.client.logout()
		u = User.objects.create_user('staffer', password='x')
		u.groups.add(Group.objects.get_or_create(name='staff')[0])
		self.client.force_login(u)
		self.assertEqual(self.client.post(reverse('qr-net-add'), {'cidr': self.OFFICE}).status_code, 403)
		self.assertEqual(OfficeNetwork.objects.count(), 0)
		self.client.logout()
		self.client.force_login(self.admin)
		self.assertEqual(self.client.get(reverse('qr-net-add')).status_code, 405)

	@override_settings(ATTENDANCE_KIOSK_ALLOWED_IPS=['10.0.0.0/8'])
	def test_settings_and_database_networks_are_combined(self):
		OfficeNetwork.objects.create(cidr=self.OFFICE + '/32')
		OfficeNetwork.objects.create(cidr='198.51.100.0/24', is_active=False)
		self.assertEqual(self.scan_from('10.4.4.4').status_code, 200)
		Attendance.objects.all().delete()
		self.assertEqual(self.scan_from(self.OFFICE).status_code, 200)
		self.assertEqual(self.scan_from('198.51.100.5').status_code, 403)                  # nonaktif
		self.assertEqual(self.scan_from('192.168.1.1').status_code, 403)                   # bukan di daftar sendiri

	def test_public_remote_addr_ignores_client_supplied_proxy_headers(self):
		OfficeNetwork.objects.create(cidr=self.OFFICE + '/32')
		# koneksi langsung dari internet (Passenger/LiteSpeed): header palsu tidak berpengaruh, tidak juga membuat ditolak
		r = self.scan_from(self.HOME, HTTP_X_FORWARDED_FOR=self.OFFICE)
		self.assertEqual(r.status_code, 403)
		self.assertEqual(self.scan_from(self.OFFICE, HTTP_X_FORWARDED_FOR='1.1.1.1').status_code, 200)

	@override_settings(ATTENDANCE_CLIENT_IP_HEADER='HTTP_CF_CONNECTING_IP')
	def test_dedicated_client_ip_header_for_cloudflare(self):
		OfficeNetwork.objects.create(cidr=self.OFFICE + '/32')
		cf = '172.70.1.1'                                                    # alamat Cloudflare terlihat sebagai REMOTE_ADDR
		self.assertEqual(self.scan_from(cf, HTTP_CF_CONNECTING_IP=self.OFFICE).status_code, 200)
		Attendance.objects.all().delete()
		self.assertEqual(self.scan_from(cf, HTTP_CF_CONNECTING_IP=self.HOME).status_code, 403)
		self.assertEqual(self.scan_from(cf).status_code, 403)                                 # header hilang = tidak dikenal
		self.assertEqual(self.scan_from(cf, HTTP_CF_CONNECTING_IP='bukan-ip').status_code, 403)
		self.assertEqual(self.scan_from(cf, HTTP_CF_CONNECTING_IP='1.1.1.1, ' + self.OFFICE).status_code, 403)

	def test_reverse_proxy_on_same_server_needs_explicit_trust(self):
		OfficeNetwork.objects.create(cidr=self.OFFICE + '/32')
		kwargs = dict(REMOTE_ADDR='127.0.0.1', HTTP_X_FORWARDED_FOR=self.OFFICE)
		with self.assertLogs('attendance.qr', 'WARNING'):
			self.assertEqual(self.scan_from(kwargs.pop('REMOTE_ADDR'), **kwargs).status_code, 403)    # gagal-aman
		with override_settings(ATTENDANCE_TRUST_PROXY_HEADER=True):
			self.assertEqual(self.scan_from('127.0.0.1', HTTP_X_FORWARDED_FOR=self.OFFICE).status_code, 200)


class DeploymentTests(QRBase):
	def test_python_38_syntax_and_apis(self):
		"""cPanel sering hanya menyediakan Python 3.8/3.9: tidak boleh ada sintaks atau API yang lebih baru."""
		import ast, pathlib, re
		root = pathlib.Path(__file__).parent
		banned = re.compile(r'\.removeprefix\(|\.removesuffix\(|zip\([^)]*strict=|math\.lcm|functools\.cache\b|'
							r'\b(list|dict|set|tuple)\[[a-zA-Z]|\bmatch\s+\w+:|\|=|\)\s*\|\s*\{|:\s*\w+\s*\|\s*None')
		for f in root.rglob('*.py'):
			if 'migrations' in f.parts or f.name == 'tests_qr.py':
				continue
			src = f.read_text(encoding='utf-8')
			ast.parse(src, filename=str(f), feature_version=(3, 8))
			for n, line in enumerate(src.splitlines(), 1):
				code = line.split('#')[0]
				self.assertIsNone(banned.search(code), '%s:%d memakai fitur Python >= 3.9: %s' % (f.name, n, line.strip()))

	def test_zoneinfo_has_py38_fallback(self):
		import pathlib
		for name in ('qr_service.py', 'qr_checks.py'):
			src = (pathlib.Path(__file__).parent / name).read_text(encoding='utf-8')
			self.assertIn('backports.zoneinfo', src)
		req = (pathlib.Path(__file__).parent.parent / 'requirements-qr.txt').read_text(encoding='utf-8') if (pathlib.Path(__file__).parent.parent / 'requirements-qr.txt').exists() else ''
		self.assertTrue(not req or 'tzdata' in req)

	def test_check_warns_about_per_process_cache_in_production(self):
		from django.core.checks import run_checks
		locmem = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
		filebased = {'default': {'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache', 'LOCATION': '/tmp/mdi_cache_test'}}
		ids = lambda: {m.id for m in run_checks() if m.id.startswith('attendance.')}
		with override_settings(DEBUG=False, CACHES=locmem):
			self.assertIn('attendance.W004', ids())
		with override_settings(DEBUG=False, CACHES=filebased):
			self.assertNotIn('attendance.W004', ids())
		with override_settings(DEBUG=True, CACHES=locmem):
			self.assertNotIn('attendance.W004', ids())

	def test_file_based_cache_supports_what_we_need(self):
		"""Throttle (incr/add) dan cache laporan harus jalan di cache berbasis berkas, pilihan paling umum di cPanel."""
		import shutil, tempfile
		d = tempfile.mkdtemp()
		try:
			caches = {'default': {'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache', 'LOCATION': d}}
			with override_settings(CACHES=caches):
				from django.core.cache import cache as fb
				u = User.objects.create_user('fbc', password='x')
				u.groups.add(Group.objects.get_or_create(name='admin')[0])
				self.client.force_login(u)
				self.pair()
				with override_settings(ATTENDANCE_THROTTLE_PER_MINUTE=2), mock.patch.object(svc.time, 'time', lambda: 9_000.0):
					codes = [self.post_scan({'code': 'x'}).status_code for _ in range(3)]
				self.assertEqual(codes, [200, 200, 429])
				a = self.client.get(reverse('qr-api-dash'), {'year': 2026, 'month': 11}).json()
				b = self.client.get(reverse('qr-api-dash'), {'year': 2026, 'month': 11}).json()
				self.assertEqual(a, b)
				self.assertTrue(fb.get('attqr:proxywarn') is None)
		finally:
			shutil.rmtree(d, ignore_errors=True)


class DeviceTokenTests(QRBase):
	"""Mode bawaan 'device' (cocok untuk IP berganti seperti Starlink): penjaga utama PC kiosk dengan token berputar."""
	HOME = '114.4.20.10'
	STARLINK = ['100.64.12.1', '203.0.113.77', '2a0d:3344:1:2::5']

	def beat(self, **extra):
		return self.client.get(reverse('qr-kiosk-check'), **extra)

	def scan_as(self, ip='127.0.0.1'):
		with mock.patch.object(timezone, 'now', lambda: at(MON, 8)):
			return self.post_scan({'code': self.payload}, REMOTE_ADDR=ip)

	T0 = at(MON, 7)                                                              # jam palsu: denyut pertama yang memutar token

	def rotate_now(self):
		with mock.patch.object(timezone, 'now', lambda: self.T0):
			return self.beat(), self.T0

	def minutes_later(self, m):
		return mock.patch.object(timezone, 'now', lambda: self.T0 + datetime.timedelta(minutes=m))

	# ---------- IP boleh berganti
	def test_changing_office_ip_does_not_break_the_paired_pc(self):
		self.pair()
		for ip in self.STARLINK:
			Attendance.objects.all().delete()
			self.assertEqual(self.scan_as(ip).status_code, 200, ip)

	def test_home_is_still_impossible_without_the_pc_token(self):
		for ip in [self.HOME, '192.168.1.20', '127.0.0.1'] + self.STARLINK:
			r = self.scan_as(ip)
			self.assertEqual((r.status_code, r.json()['code']), (401, 'device'), ip)
		self.assertEqual(Attendance.objects.count(), 0)

	def test_other_devices_on_the_office_wifi_cannot_scan(self):
		self.pair()                                                              # PC kiosk memegang cookie
		from django.test import Client
		phone = Client()                                                         # HP pegawai di Wi-Fi kantor: IP sama, tanpa cookie
		with mock.patch.object(timezone, 'now', lambda: at(MON, 8)):
			r = phone.post(reverse('qr-kiosk-scan'), data={'code': self.payload}, content_type='application/json',
						   HTTP_X_KIOSK='1', REMOTE_ADDR='203.0.113.77')
		self.assertEqual(r.status_code, 401)

	@override_settings(ATTENDANCE_REQUIRE_DEVICE_KEY=False)
	def test_without_pairing_the_ip_check_comes_back_as_safety_net(self):
		self.assertEqual(self.scan_as('203.0.113.77').status_code, 403)           # tanpa penjaga PC, cek IP wajib
		self.assertEqual(self.scan_as('192.168.1.20').status_code, 200)

	def test_checks_flag_bad_mode_combinations(self):
		from django.core.checks import run_checks
		ids = lambda: {m.id for m in run_checks() if m.id.startswith('attendance.')}
		self.assertNotIn('attendance.W005', ids())
		with override_settings(ATTENDANCE_REQUIRE_DEVICE_KEY=False):
			self.assertIn('attendance.W005', ids())
		with override_settings(ATTENDANCE_NETWORK_MODE='bebas'):
			self.assertIn('attendance.E004', ids())

	# ---------- rotasi token
	def test_no_rotation_before_it_is_due(self):
		self.pair()
		r = self.beat()
		self.assertEqual(r.status_code, 200)
		self.assertNotIn('mdi_kiosk', r.cookies)

	def test_token_rotates_after_interval_with_secure_cookie(self):
		dev, old = self.pair()
		r, future = self.rotate_now()
		self.assertEqual(r.status_code, 200)
		c = r.cookies['mdi_kiosk']
		self.assertNotEqual(c.value, old)
		self.assertTrue(c['httponly'])
		self.assertEqual((c['samesite'], c['path']), ('Strict', '/attendance/qr/'))
		d = ScanDevice.objects.get()
		self.assertEqual((d.key_sha256, d.prev_sha256, d.rotated_at), (svc.hash_key(c.value), svc.hash_key(old), future))
		self.assertIsNone(d.current_used_at)
		self.assertEqual(self.scan_as().status_code, 200)                         # klien uji memakai cookie baru otomatis
		self.assertEqual(ScanDevice.objects.count(), 1)

	def test_rotation_happens_only_on_heartbeat_not_on_scan(self):
		dev, old = self.pair()
		future = timezone.now() + datetime.timedelta(hours=7)
		with mock.patch.object(timezone, 'now', lambda: future):
			r = self.post_scan({'code': 'x'})
		self.assertNotIn('mdi_kiosk', r.cookies)                                  # jalur scan tetap ringan

	def test_kiosk_that_missed_the_new_cookie_heals_without_being_revoked(self):
		dev, old = self.pair()
		self.rotate_now()
		self.client.cookies['mdi_kiosk'] = old                                    # Set-Cookie tidak pernah sampai ke browser
		r = self.beat()
		self.assertEqual(r.status_code, 200)
		self.assertIn('mdi_kiosk', r.cookies)                                      # token baru dikirim ulang
		self.assertTrue(ScanDevice.objects.get().is_active)
		self.assertEqual(self.scan_as().status_code, 200)

	def test_copied_cookie_is_detected_and_the_pc_is_revoked(self):
		dev, old = self.pair()
		self.rotate_now()                                                         # kiosk menerima token baru...
		with self.minutes_later(1):
			self.beat()                                                           # ...dan memakainya: pemegang sah terbukti
		self.assertIsNotNone(ScanDevice.objects.get().current_used_at)
		from django.test import Client
		thief = Client()
		thief.cookies['mdi_kiosk'] = old                                          # salinan token lama dibawa pulang
		with self.minutes_later(60):
			r = thief.post(reverse('qr-kiosk-scan'), data={'code': self.payload}, content_type='application/json',
						   HTTP_X_KIOSK='1', REMOTE_ADDR=self.HOME)
		self.assertEqual((r.status_code, r.json()['code']), (401, 'device'))      # pencuri tidak diberi tahu sebabnya
		self.assertEqual(Attendance.objects.count(), 0)
		d = ScanDevice.objects.get()
		self.assertFalse(d.is_active)
		log = ScanLog.objects.get(code='device_reuse')
		self.assertEqual((log.ip, log.device_id), (self.HOME, d.pk))
		with self.minutes_later(61):
			self.assertEqual(self.post_scan({'code': self.payload}).status_code, 401)   # PC sah ikut dicabut: pasangkan ulang

	def test_admin_sees_alert_after_a_copied_token_is_detected(self):
		dev, old = self.pair()
		self.rotate_now()
		with self.minutes_later(1):
			self.beat()
		ScanLog.objects.all().delete()
		from django.test import Client
		thief = Client()
		thief.cookies['mdi_kiosk'] = old
		with self.minutes_later(5):
			thief.post(reverse('qr-kiosk-scan'), data={'code': 'x'}, content_type='application/json', HTTP_X_KIOSK='1', REMOTE_ADDR=self.HOME)
		u = User.objects.create_user('alert', password='x')
		u.groups.add(Group.objects.get_or_create(name='admin')[0])
		self.client.force_login(u)
		self.client.cookies['mdi_lang'] = 'id'
		html = self.client.get(reverse('qr-today')).content.decode()
		self.assertIn('dicabut otomatis', html)
		self.assertIn(self.HOME, html)

	def test_old_token_after_grace_is_just_unknown(self):
		dev, old = self.pair()
		self.rotate_now()
		later = self.T0 + datetime.timedelta(hours=25)                           # lewat masa tenggang 24 jam
		self.client.cookies['mdi_kiosk'] = old
		with mock.patch.object(timezone, 'now', lambda: later):
			r = self.beat()
		self.assertEqual(r.status_code, 401)
		self.assertTrue(ScanDevice.objects.get().is_active)                        # tidak dicabut: kemungkinan PC sah yang lama mati

	def test_revoked_device_rejects_both_current_and_previous_tokens(self):
		dev, old = self.pair()
		self.rotate_now()
		ScanDevice.objects.update(is_active=False)
		self.assertEqual(self.beat().status_code, 401)
		self.client.cookies['mdi_kiosk'] = old
		self.assertEqual(self.beat().status_code, 401)

	def test_rotation_guard_against_concurrent_requests(self):
		dev, old = self.pair()
		stale = ScanDevice.objects.get()
		first = svc.rotate_device(ScanDevice.objects.get())
		self.assertIsNotNone(first)
		self.assertIsNone(svc.rotate_device(stale))                                # proses lain sudah memutar: tidak menimpa
		self.assertEqual(ScanDevice.objects.get().key_sha256, svc.hash_key(first))

	def test_current_used_at_is_updated_at_most_once_a_minute(self):
		dev, key = self.pair()
		svc.authenticate_device(key)
		with CaptureQueriesContext(connection) as q:
			svc.authenticate_device(key)
		self.assertEqual(sum(1 for x in q.captured_queries if x['sql'].startswith('UPDATE')), 0)

	def test_header_key_for_api_clients_still_works(self):
		key = svc.new_device_key()
		ScanDevice.objects.create(name='API', key_sha256=svc.hash_key(key), rotated_at=timezone.now())
		self.client.cookies.clear()
		self.assertEqual(self.client.get(reverse('qr-kiosk-check'), HTTP_X_KIOSK_KEY=key).status_code, 200)

	def test_devices_page_explains_ip_check_is_off(self):
		u = User.objects.create_user('dv', password='x')
		u.groups.add(Group.objects.get_or_create(name='admin')[0])
		self.client.force_login(u)
		self.client.cookies['mdi_lang'] = 'id'
		html = self.client.get(reverse('qr-devices')).content.decode()
		self.assertIn('Cek IP dimatikan karena IP kantor bisa berganti', html)
		self.assertNotIn('name="cidr"', html)                                      # formulir jaringan disembunyikan
		with override_settings(ATTENDANCE_NETWORK_MODE='allowlist'):
			html = self.client.get(reverse('qr-devices')).content.decode()
			self.assertIn('name="cidr"', html)
