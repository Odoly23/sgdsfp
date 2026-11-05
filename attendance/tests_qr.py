import datetime
import io
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group, User
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
from attendance.models_qr import AttendanceTime, QRCard, ScanDevice, ScanLog

DILI = ZoneInfo('Asia/Dili')
MON = datetime.date(2026, 11, 9)      # Senin
SUN = datetime.date(2026, 11, 8)


def at(date, hh, mm=0):
	return datetime.datetime(date.year, date.month, date.day, hh, mm, tzinfo=DILI)


class QRBase(TestCase):
	def setUp(self):
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

	def api(self, code, key=None):
		headers = {'HTTP_X_KIOSK_KEY': key} if key else {}
		return self.client.post(reverse('qr-kiosk-scan'), data={'code': code}, content_type='application/json', **headers)

	def device(self):
		key = svc.new_device_key()
		ScanDevice.objects.create(name='Pintu', key_sha256=svc.hash_key(key))
		return key

	def test_kiosk_page_is_public(self):
		r = self.client.get(reverse('qr-kiosk'))
		self.assertEqual(r.status_code, 200)
		self.assertContains(r, 'id="clock"')
		self.assertContains(r, 'Asia/Dili')

	def test_scan_works_without_device_key_by_default(self):
		with mock.patch.object(timezone, 'now', lambda: at(MON, 8)):
			r = self.api(self.payload)
		self.assertEqual((r.status_code, r.json()['code']), (200, 'in'))
		self.assertIsNone(ScanLog.objects.get().device)

	@override_settings(ATTENDANCE_REQUIRE_DEVICE_KEY=True)
	def test_scan_api_requires_device_key_when_enabled(self):
		self.assertEqual(self.api(self.payload).status_code, 401)
		self.assertEqual(self.api(self.payload, key='nope').status_code, 401)
		self.assertEqual(ScanLog.objects.count(), 0)
		self.assertEqual(Attendance.objects.count(), 0)

	def test_scan_api_ok_and_hides_internal_id(self):
		key = self.device()
		with mock.patch.object(timezone, 'now', lambda: at(MON, 8)):
			r = self.api(self.payload, key)
		self.assertEqual(ScanLog.objects.get().ip, '127.0.0.1')
		data = r.json()
		self.assertEqual((r.status_code, data['ok'], data['code']), (200, True, 'in'))
		self.assertNotIn('employee_id', data)
		self.assertEqual(data['employee']['name'], 'Maria Silva')
		self.assertIsNotNone(ScanDevice.objects.get().last_seen)

	def test_scan_api_garbage_and_get(self):
		key = self.device()
		r = self.client.post(reverse('qr-kiosk-scan'), data='not json', content_type='application/json', HTTP_X_KIOSK_KEY=key)
		self.assertEqual(r.json()['code'], 'invalid')
		r = self.client.post(reverse('qr-kiosk-scan'), data={'code': 123}, content_type='application/json', HTTP_X_KIOSK_KEY=key)
		self.assertEqual(r.json()['code'], 'invalid')
		self.assertEqual(self.client.get(reverse('qr-kiosk-scan')).status_code, 405)

	@override_settings(ATTENDANCE_REQUIRE_DEVICE_KEY=True)
	def test_kiosk_check_with_required_key(self):
		key = self.device()
		self.assertEqual(self.client.get(reverse('qr-kiosk-check'), HTTP_X_KIOSK_KEY=key).status_code, 200)
		self.assertEqual(self.client.get(reverse('qr-kiosk-check'), HTTP_X_KIOSK_KEY='x').status_code, 401)

	def test_kiosk_check_open_by_default(self):
		r = self.client.get(reverse('qr-kiosk-check'))
		self.assertEqual((r.status_code, r.json()['state']), (200, 'ok'))

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
		self.assertContains(self.client.get(reverse('qr-cards')), '403')
		self.assertContains(self.client.get(reverse('qr-devices')), '403')
		self.client.logout()
		self.login('staff')
		self.assertContains(self.client.get(reverse('qr-cards')), 'Kartu QR pegawai')
		self.assertContains(self.client.get(reverse('qr-devices')), '403')
		self.client.logout()
		self.login('funsionario')
		self.assertContains(self.client.get(reverse('qr-report')), '403')
		self.assertContains(self.client.get(reverse('qr-today')), '403')
		self.assertContains(self.client.get(reverse('qr-me')), 'Absensi saya')

	def test_me_shows_only_own_rows(self):
		from employee.models import EmployeeUser
		u = self.login('funsionario')
		EmployeeUser.objects.create(employee=self.emp, user=u)
		other = self.make_employee('Jose', 'Costa')
		r = self.client.get(reverse('qr-me'), {'year': 2026, 'month': 11})
		self.assertContains(r, 'Maria Silva')
		self.assertNotContains(r, 'Jose Costa')

	def test_device_add_shows_key_once_and_stores_only_hash(self):
		self.login('admin')
		r = self.client.post(reverse('qr-device-add'), {'name': 'PC Pintu'})
		key = r.context['new_key']
		self.assertContains(r, key)
		dev = ScanDevice.objects.get()
		self.assertEqual(dev.key_sha256, svc.hash_key(key))
		self.assertNotContains(self.client.get(reverse('qr-devices')), key)
		self.client.post(reverse('qr-device-toggle', args=[dev.pk]))
		self.assertFalse(ScanDevice.objects.get().is_active)
		self.assertIsNone(svc.get_device(key))

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
		text = r.content.decode('utf-8-sig')
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



class NetworkTests(QRBase):
	def post(self, **extra):
		with mock.patch.object(timezone, 'now', lambda: at(MON, 8)):
			return self.client.post(reverse('qr-kiosk-scan'), data={'code': self.payload}, content_type='application/json', **extra)

	@override_settings(ATTENDANCE_KIOSK_ALLOWED_IPS=['10.0.0.0/8', '192.168.1.5'])
	def test_ip_allowlist(self):
		r = self.post(REMOTE_ADDR='203.0.113.9')
		self.assertEqual((r.status_code, r.json()['code']), (403, 'net'))
		self.assertEqual(Attendance.objects.count(), 0)
		self.assertEqual(self.post(REMOTE_ADDR='10.1.2.3').status_code, 200)
		self.assertEqual(self.client.get(reverse('qr-kiosk-check'), REMOTE_ADDR='203.0.113.9').status_code, 403)
		self.assertEqual(self.client.get(reverse('qr-kiosk-check'), REMOTE_ADDR='192.168.1.5').status_code, 200)

	@override_settings(ATTENDANCE_KIOSK_ALLOWED_IPS=['10.0.0.0/8'])
	def test_spoofed_forwarded_header_is_ignored_unless_trusted(self):
		r = self.post(REMOTE_ADDR='203.0.113.9', HTTP_X_FORWARDED_FOR='10.1.1.1')
		self.assertEqual(r.status_code, 403)

	@override_settings(ATTENDANCE_KIOSK_ALLOWED_IPS=['10.0.0.0/8'], ATTENDANCE_TRUST_PROXY_HEADER=True)
	def test_trusted_proxy_uses_rightmost_entry(self):
		# klien memalsukan 10.1.1.1 di kiri; proxy menambahkan alamat asli (luar kantor) di kanan
		self.assertEqual(self.post(REMOTE_ADDR='127.0.0.1', HTTP_X_FORWARDED_FOR='10.1.1.1, 203.0.113.9').status_code, 403)
		self.assertEqual(self.post(REMOTE_ADDR='127.0.0.1', HTTP_X_FORWARDED_FOR='198.51.100.7, 10.2.2.2').status_code, 200)

	@override_settings(ATTENDANCE_KIOSK_ALLOWED_IPS=['not-an-ip', '10.0.0.0/8'])
	def test_bad_config_entries_do_not_open_access(self):
		self.assertEqual(self.post(REMOTE_ADDR='203.0.113.9').status_code, 403)

	def test_security_banner_for_managers(self):
		u = User.objects.create_user('m', password='x')
		u.groups.add(Group.objects.create(name='admin'))
		self.client.force_login(u)
		self.client.cookies['mdi_lang'] = 'id'
		self.assertContains(self.client.get(reverse('qr-today')), 'ATTENDANCE_KIOSK_ALLOWED_IPS')
		with override_settings(ATTENDANCE_KIOSK_ALLOWED_IPS=['10.0.0.0/8']):
			self.assertNotContains(self.client.get(reverse('qr-today')), 'ATTENDANCE_KIOSK_ALLOWED_IPS')


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

	def test_flags_are_links_that_mark_current_language(self):
		self.login_admin()
		self.client.cookies['mdi_lang'] = 'pt'
		r = self.client.get(reverse('qr-today'))
		html = r.content.decode()
		for code in ('tet', 'en', 'id', 'pt'):
			self.assertIn('/attendance/qr/lang/%s/?next=' % code, html)
		self.assertEqual(html.count('qr-flag on'), 1)
		self.assertIn('qr-flag on" href="/attendance/qr/lang/pt/', html)

	@override_settings(ATTENDANCE_LANG_IN_NAVBAR=True)
	def test_flags_hidden_in_page_when_navbar_has_them(self):
		self.login_admin()
		self.assertNotContains(self.client.get(reverse('qr-today')), 'qr-flags')

	def test_navbar_snippet_renders_flags(self):
		from django.template.loader import render_to_string
		from django.test import RequestFactory
		req = RequestFactory().get('/x/')
		html = render_to_string('attendance/qr/_navbar_lang.html', {}, request=req)
		self.assertEqual(html.count('hreflang='), 4)
		self.assertIn('qr-navbar-lang', html)

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
		def api(lang):
			body = {'code': self.payload}
			if lang:
				body['lang'] = lang
			with mock.patch.object(timezone, 'now', lambda: at(MON, 8)):
				return self.client.post(reverse('qr-kiosk-scan'), data=body, content_type='application/json').json()
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
		text = self.client.get(reverse('qr-report-export'), {'year': 2026, 'month': 11}).content.decode('utf-8-sig')
		self.assertTrue(text.startswith('NRF,Name,Position,Division,1,2'))
		self.assertIn('Work days,Present,Late,Leave,Absent', text)
