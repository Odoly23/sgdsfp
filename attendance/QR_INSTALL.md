# Absensi QR — pemasangan di `mci_hrms`

File baru ini hanya **menambah** app `attendance`. View, URL, dan template lama tidak diubah.
Absensi manual per divisi tetap berjalan, dan baris yang dibuat scan muncul di daftar yang sama.

## 1. Salin berkas

Salin ke folder `attendance/` proyek Anda (struktur sama dengan repo ini):

```
attendance/models_qr.py        model: QRCard, ScanDevice, AttendanceTime, ScanLog
attendance/qr_service.py       aturan scan, tanda tangan QR, rekap
attendance/views_qr.py         halaman kiosk, hari ini, kartu, perangkat, rekap
attendance/urls_qr.py
attendance/tests_qr.py         45 tes otomatis
attendance/templates/attendance/qr/*
attendance/static/attendance/qr/*      (qr.css, kiosk.js, mdi-logo.jpg)
```

## 2. Tiga baris yang harus ditambah

**`attendance/models.py`** (paling bawah):
```python
from attendance.models_qr import QRCard, ScanDevice, AttendanceTime, ScanLog  # noqa
```

**`attendance/urls.py`** (pastikan `include` diimpor, lalu tambah satu baris di `urlpatterns`):
```python
from django.urls import path, include
...
	path('qr/', include('attendance.urls_qr')),
```

**Terminal:**
```bash
pip install -r requirements-qr.txt
python manage.py makemigrations attendance
python manage.py migrate
python manage.py collectstatic          # wajib: DEBUG=False memakai WhiteNoise
python manage.py test attendance.tests_qr
```
Migrasi hanya membuat 4 tabel baru. Kolom di tabel `Attendance` yang ada tidak diubah.

## 3. Pengaturan (`settings.py`) — semuanya opsional kecuali QR_SECRET

```python
ATTENDANCE_QR_SECRET = os.environ['ATTENDANCE_QR_SECRET']   # SANGAT disarankan, lihat catatan
ATTENDANCE_TIME_ZONE = 'Asia/Dili'      # zona waktu absensi saja; TIME_ZONE proyek tidak diubah
ATTENDANCE_LATE_AFTER = '08:15'         # masuk setelah jam ini = terlambat
ATTENDANCE_NOON = '12:00'               # sebelum = scan masuk (AM), sesudah = pulang (PM)
ATTENDANCE_WORK_END = '16:00'           # pulang sebelum jam ini ditandai "pulang awal"
ATTENDANCE_DUPLICATE_SECONDS = 60
ATTENDANCE_ACTIVE_STATUS_ID = 1         # Employee.status_id pegawai aktif
ATTENDANCE_PRESENT_CODE = 'P'           # AttendanceStatus.code untuk hadir
ATTENDANCE_MANAGER_GROUPS = ['admin', 'staff']              # kartu QR
ATTENDANCE_VIEWER_GROUPS = ['admin', 'staff', 'president']  # hari ini + rekap
ATTENDANCE_DEVICE_GROUPS = ['admin']                        # kunci perangkat kiosk
ATTENDANCE_SELF_GROUPS = ['admin', 'staff', 'president', 'funsionario']
ATTENDANCE_CARD_BACK_NOTE = '...'       # teks di sisi belakang kartu
ATTENDANCE_CARD_CONTACT = 'Telp ...'    # baris kontak di belakang kartu (opsional)
```

**Catatan `ATTENDANCE_QR_SECRET`:** kalau kosong, tanda tangan QR memakai `SECRET_KEY`. Lalu mengganti
`SECRET_KEY` (yang sebaiknya segera dilakukan karena sudah tampil di chat) membuat **semua kartu tidak berlaku**.
Dengan `ATTENDANCE_QR_SECRET` terpisah, kartu tetap aman walau `SECRET_KEY` diganti. Isi dengan string acak panjang
dan jangan diubah setelah kartu dicetak.

## 4. Menu

Tambahkan ke menu tiap grup (mis. di `adm.html`), contoh gaya yang sama dengan menu lama:
```html
<li class="nav-item dropdown active">
	<a class="nav-link dropdown-toggle" href="#" data-toggle="dropdown"><i class="fa fa-qrcode"></i> ABSENSI QR</a>
	<div class="dropdown-menu">
		<a class="dropdown-item" href="{% url 'qr-today' %}"><i class="fa fa-list"></i> Hari ini</a>
		<a class="dropdown-item" href="{% url 'qr-report' %}"><i class="fa fa-table"></i> Rekap bulanan</a>
		<a class="dropdown-item" href="{% url 'qr-cards' %}"><i class="fa fa-id-card"></i> Kartu QR</a>
		<a class="dropdown-item" href="{% url 'qr-devices' %}"><i class="fa fa-desktop"></i> Perangkat kiosk</a>
	</div>
</li>
```
Grup `funsionario` cukup tautan ke `{% url 'qr-me' %}`. Halaman sendiri sudah memeriksa grup, jadi
menu yang salah tidak membuka akses.

## 5. Pasang di kantor (sekali saja)

1. Login sebagai `admin` → **Perangkat kiosk** → beri nama "PC Pintu" → **Buat kunci** → salin kunci (tampil sekali).
2. Di PC pintu, buka `https://<server>/attendance/qr/kiosk/` dalam layar penuh (F11), tempel kunci. Kunci tersimpan di browser PC itu.
3. Colok scanner QR USB (mode "keyboard/HID", akhiran Enter). Tidak perlu driver.
4. **Kartu QR** → pilih pegawai → **Cetak kartu terpilih** → atur kertas 85,6 × 54 mm tanpa margin → cetak
   depan+belakang → **Tandai sudah dicetak**.

Kamera hanya cadangan (tombol "Pakai kamera", Chrome/Edge) dan butuh HTTPS atau `localhost`.

## Aturan yang berlaku

| Situasi | Hasil |
|---|---|
| Scan pertama sebelum 12:00 | Baris `Attendance` dibuat, `status_am` = P. Terlambat bila lewat 08:15 |
| Scan setelah 12:00 | `status_pm` = P, jam pulang = scan **terakhir**. Sebelum 16:00 ditandai pulang awal |
| Scan kedua sebelum 12:00 / ganda < 60 detik | Diabaikan |
| Sabtu, Minggu, `NacionalHoliday`, `Tolerance` | Ditolak, tidak ada data tercatat |
| `leave.Leaves` aktif + terkonfirmasi | QR terbaca, layar "Sedang cuti", absensi tidak diubah |
| Hari itu sudah diisi manual oleh admin | Ditolak |
| `AttendanceDiv.is_final` bulan itu | Ditolak |
| QR salah/dipalsukan, kartu diganti, kartu kedaluwarsa, pegawai non-aktif | Ditolak, tercatat di `ScanLog` |
| Tidak ada scan di hari kerja yang sudah lewat | Dihitung Alpha (F) di rekap, tanpa menulis baris |

Hari libur dicocokkan lewat bulan dan tanggal saja (seperti view lama): libur 20 Mei berlaku setiap tahun.

## Keamanan

- QR berisi `MDI.<id pegawai>.<versi>.<HMAC-SHA256>`. Tidak ada token yang disimpan. Kartu hilang → **Ganti QR**
  (versi naik, kartu lama langsung mati).
- Kunci perangkat kiosk hanya disimpan sebagai SHA-256. Scan tanpa kunci yang sah ditolak (401), jadi scan dari HP tidak bisa.
- Kolom `hashed` di `Attendance` tetap MD5 dari `getnewid()` karena kolomnya 32 karakter dan view lama mencari lewat itu.
  SHA-256 dipakai untuk bagian baru (QR dan kunci perangkat).
- Setiap scan, termasuk yang ditolak, tercatat di `ScanLog`.
