"""Pemeriksaan konfigurasi absensi QR, tampil di `python manage.py check` dan saat server dijalankan."""
import ipaddress
import re
try:
	from zoneinfo import ZoneInfo
except ImportError:      # Python 3.8
	from backports.zoneinfo import ZoneInfo

from django.conf import settings
from django.core.checks import Error, Warning, register

_TIME = re.compile(r'([01]\d|2[0-3]):[0-5]\d')


@register()
def check_attendance_qr(app_configs, **kwargs):
	from attendance import qr_service as svc
	out = []
	try:
		ZoneInfo(str(svc.conf('TIME_ZONE')))
	except Exception:
		out.append(Error('ATTENDANCE_TIME_ZONE "%s" bukan zona waktu yang dikenal.' % svc.conf('TIME_ZONE'),
						 hint='Contoh: "Asia/Dili".', id='attendance.E001'))
	for name in ('LATE_AFTER', 'NOON', 'WORK_END'):
		if not _TIME.fullmatch(str(svc.conf(name))):
			out.append(Error('ATTENDANCE_%s harus berformat "HH:MM", bukan "%s".' % (name, svc.conf(name)), id='attendance.E002'))
	for net in svc.conf('KIOSK_ALLOWED_IPS') or []:
		try:
			ipaddress.ip_network(net, strict=False)
		except ValueError:
			out.append(Error('ATTENDANCE_KIOSK_ALLOWED_IPS memuat "%s" yang bukan alamat/jaringan IP.' % net, id='attendance.E003'))
	if not getattr(settings, 'ATTENDANCE_QR_SECRET', None):
		out.append(Warning('ATTENDANCE_QR_SECRET belum diisi: tanda tangan QR memakai SECRET_KEY.',
						   hint='Isi dengan string acak panjang dari environment variable; mengganti SECRET_KEY nanti '
								'tidak akan membuat kartu hangus.', id='attendance.W001'))
	if svc.allowed_nets() is None:
		out.append(Warning('ATTENDANCE_KIOSK_ALLOWED_IPS = [] membuka scan dari seluruh internet.',
						   hint='Hapus setelan itu agar hanya jaringan privat kantor yang diterima.', id='attendance.W002'))
	if not svc.conf('REQUIRE_DEVICE_KEY'):
		out.append(Warning('ATTENDANCE_REQUIRE_DEVICE_KEY = False: scan tidak dikunci ke PC kiosk tertentu.',
						   id='attendance.W003'))
	backend = str(getattr(settings, 'CACHES', {}).get('default', {}).get('BACKEND', ''))
	if not settings.DEBUG and 'locmem' in backend.lower():
		out.append(Warning('Cache bawaan (LocMem) terpisah di tiap proses server, sedangkan cPanel/Passenger menjalankan beberapa proses: '
						   'pembatas permintaan dan cache laporan tidak dibagi.',
						   hint='Pakai django.core.cache.backends.filebased.FileBasedCache (lihat QR_INSTALL.md, bagian VPS cPanel).',
						   id='attendance.W004'))
	return out
