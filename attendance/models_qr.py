from django.db import models
from django.contrib.auth.models import User
from employee.models import Employee

__all__ = ['QRCard', 'ScanDevice', 'AttendanceTime', 'ScanLog', 'OfficeNetwork']


class QRCard(models.Model):
	"""Kartu QR absensi milik satu pegawai. QR-nya ditandatangani (HMAC-SHA256) dari id + version,
	jadi tidak ada token yang disimpan. Menaikkan `version` membuat QR lama tidak berlaku."""
	employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name='qrcard', verbose_name="Pessoal")
	version = models.PositiveIntegerField(default=1)
	is_active = models.BooleanField(default=True)
	printed_at = models.DateTimeField(null=True, blank=True)
	datetime = models.DateTimeField(null=True)
	user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

	def __str__(self):
		template = '{0.employee} v{0.version}'
		return template.format(self)


class ScanDevice(models.Model):
	"""PC kiosk yang boleh mengirim scan. Hanya SHA-256 dari token yang disimpan. Token di browser PC itu diputar
	otomatis; token lama yang muncul lagi setelah token baru dipakai = salinan, dan PC dicabut."""
	name = models.CharField(max_length=100, verbose_name="Naran")
	key_sha256 = models.CharField(max_length=64, unique=True)
	prev_sha256 = models.CharField(max_length=64, blank=True, db_index=True)     # token sebelum diputar (masa tenggang)
	rotated_at = models.DateTimeField(null=True, blank=True)                     # kapan token terakhir diputar
	current_used_at = models.DateTimeField(null=True, blank=True)                # token baru sudah dipakai PC sah
	is_active = models.BooleanField(default=True)
	last_seen = models.DateTimeField(null=True, blank=True)
	datetime = models.DateTimeField(null=True)
	user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

	def __str__(self):
		return self.name


class AttendanceTime(models.Model):
	"""Jam scan untuk satu baris Attendance. Baris Attendance tanpa AttendanceTime = diisi manual."""
	attendance = models.OneToOneField('attendance.Attendance', on_delete=models.CASCADE, related_name='scantime')
	check_in = models.DateTimeField()
	check_out = models.DateTimeField(null=True, blank=True)
	is_late = models.BooleanField(default=False)
	flagged = models.BooleanField(default=False)          # scan beruntun: perlu dicek admin (bisa dihapus setelah dicek)
	device = models.ForeignKey(ScanDevice, null=True, blank=True, on_delete=models.SET_NULL)

	def __str__(self):
		template = '{0.attendance_id} {0.check_in}'
		return template.format(self)


class ScanLog(models.Model):
	"""Catatan semua scan, termasuk yang ditolak, sebagai bukti audit."""
	employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name='scanlogs')
	device = models.ForeignKey(ScanDevice, null=True, blank=True, on_delete=models.SET_NULL)
	scanned_at = models.DateTimeField()
	code = models.CharField(max_length=20)
	message = models.CharField(max_length=200, blank=True)
	ip = models.GenericIPAddressField(null=True, blank=True)
	flag = models.CharField(max_length=20, blank=True)    # 'burst' bila scan ini bagian dari scan beruntun

	class Meta:
		ordering = ['-scanned_at']
		indexes = [models.Index(fields=['scanned_at'])]



class OfficeNetwork(models.Model):
	"""Alamat IP publik/rentang jaringan kantor yang boleh mengirim scan. Dikelola admin dari halaman Perangkat kiosk,
	jadi saat IP kantor berganti (IP dinamis) tidak perlu mengubah settings.py dan memulai ulang server."""
	cidr = models.CharField(max_length=64)
	note = models.CharField(max_length=100, blank=True)
	is_active = models.BooleanField(default=True)
	datetime = models.DateTimeField(null=True)
	user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

	def __str__(self):
		return self.cidr


from attendance import qr_checks  # noqa: E402,F401  (mendaftarkan pemeriksaan konfigurasi: manage.py check)
