# Absensi QR — pemasangan di `mdihrms`

File baru ini hanya **menambah** app `attendance`. View, URL, dan template lama tidak diubah.
Absensi manual per divisi tetap berjalan, dan baris yang dibuat scan muncul di daftar yang sama.

## 1. Salin berkas

Salin ke folder `attendance/` proyek Anda (struktur sama dengan repo ini):

```
attendance/models_qr.py        model: QRCard, ScanDevice, AttendanceTime, ScanLog
attendance/qr_service.py       aturan scan, tanda tangan QR, jaringan, rekap
attendance/qr_i18n.py          semua teks 4 bahasa (Tetun, English, Indonesia, Português)
attendance/views_qr.py         halaman kiosk, hari ini, kartu, perangkat, rekap, ganti bahasa
attendance/urls_qr.py
attendance/templatetags/       __init__.py + qr_i18n.py ({% t %}, {% t4 %}, {% flags %})
attendance/tests_qr.py         78 tes otomatis
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

## 3. Pengaturan (`settings.py`) — semuanya opsional, tapi dua yang pertama sangat disarankan

```python
ATTENDANCE_QR_SECRET = os.environ['ATTENDANCE_QR_SECRET']   # kunci tanda tangan QR, lihat catatan
ATTENDANCE_KIOSK_ALLOWED_IPS = ['192.168.1.0/24']           # jaringan kantor; kosong = dari mana saja
ATTENDANCE_TIME_ZONE = 'Asia/Dili'      # zona waktu absensi saja; TIME_ZONE proyek tidak diubah
ATTENDANCE_DEFAULT_LANG = 'tet'         # tet | en | id | pt
ATTENDANCE_LANG_IN_NAVBAR = False       # True setelah bendera ditempel di navbar (bagian 4)
ATTENDANCE_LATE_AFTER = '08:15'         # masuk setelah jam ini = terlambat
ATTENDANCE_NOON = '12:00'               # sebelum = scan masuk (AM), sesudah = pulang (PM)
ATTENDANCE_WORK_END = '16:00'           # pulang sebelum jam ini ditandai "pulang awal"
ATTENDANCE_DUPLICATE_SECONDS = 60
ATTENDANCE_ACTIVE_STATUS_ID = 1         # Employee.status_id pegawai aktif
ATTENDANCE_PRESENT_CODE = 'P'           # AttendanceStatus.code untuk hadir
ATTENDANCE_REQUIRE_DEVICE_KEY = False   # True = kiosk wajib memakai kunci perangkat (opsional)
ATTENDANCE_TRUST_PROXY_HEADER = False   # True hanya bila server di belakang proxy tepercaya (Nginx)
ATTENDANCE_MANAGER_GROUPS = ['admin', 'staff']              # kartu QR
ATTENDANCE_VIEWER_GROUPS = ['admin', 'staff', 'president']  # hari ini + rekap
ATTENDANCE_DEVICE_GROUPS = ['admin']                        # kunci perangkat kiosk
ATTENDANCE_SELF_GROUPS = ['admin', 'staff', 'president', 'funsionario']
ATTENDANCE_CARD_BACK_NOTE = {'tet': '...', 'en': '...', 'id': '...', 'pt': '...'}  # atau satu string
ATTENDANCE_CARD_CONTACT = 'Telp ...'    # baris kontak di belakang kartu (string atau dict per bahasa)
```

**`ATTENDANCE_QR_SECRET`:** kalau kosong, tanda tangan QR memakai `SECRET_KEY`. Lalu mengganti
`SECRET_KEY` (yang sebaiknya segera dilakukan karena sudah tampil di chat) membuat **semua kartu tidak berlaku**.
Dengan `ATTENDANCE_QR_SECRET` terpisah, kartu tetap aman walau `SECRET_KEY` diganti. Isi dengan string acak panjang
dan jangan diubah setelah kartu dicetak.

**`ATTENDANCE_KIOSK_ALLOWED_IPS`:** kiosk tidak memakai kunci lagi, jadi pembatas utamanya adalah jaringan.
Tanpa ini, orang yang bisa membuka alamat server dan punya foto QR kartunya bisa absen dari rumah. Selama kosong,
halaman admin menampilkan peringatan merah. Isi dengan alamat jaringan kantor (CIDR atau satu alamat).
Jika server di belakang Nginx/proxy, set `ATTENDANCE_TRUST_PROXY_HEADER = True`; alamat klien diambil dari entri
**paling kanan** `X-Forwarded-For` (yang ditambahkan proxy Anda, tidak bisa dipalsukan klien).

## 4. Pintu masuk, menu, dan bendera bahasa

Halaman QR memakai kerangka yang sama dengan halaman absensia lama (breadcrumb "PAINEL ABSENSIA / ...", kartu
berbayang, tabel `table-striped`), tetapi tombol dan garis kartunya **merah MDI** (`btn-mdi`, `#e3171e`), bukan teal.
Breadcrumb menaut ke `attendance-dash`. Ganti warna di `:root` pada `qr.css` (`--mdi-red`, `--mdi-red-dk`).

**Tombol di Painel Absensia** (`attendance/dash.html`): tempel satu baris di samping tombol Relatoriu Absensia
dan Konfigurasaun:
```html
<a class="btn btn-sm btn-info" href="{% url 'attendance-custom-dash' %}">Konfigurasaun</a>   <!-- yang sudah ada -->
{% include 'attendance/qr/_dash_button.html' %}                                      <!-- baru: tombol "Absensia QR" -->
```
Tombol itu membuka **Hari ini**; dari sana ada tombol ke Rekap, Kartu QR, dan Perangkat kiosk. Tombolnya ikut 4 bahasa.

**Menu navbar (opsional)**, mis. di `adm.html`:
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

**Bendera di topbar kanan:** tempel satu baris ini di setiap menu navbar (`adm.html`, `staff.html`, ...),
tepat sebelum menu profil (`<li class="nav-item dropdown active">` yang berisi `request.user.username`):
```html
{% include 'attendance/qr/_navbar_lang.html' %}
```
lalu set `ATTENDANCE_LANG_IN_NAVBAR = True`. Selama belum ditempel, bendera tampil di baris tombol di dalam
halaman absensi sehingga tetap bisa dipakai. Layar kiosk selalu punya bendera di header kanan.

## 5. Pasang di kantor (sekali saja)

1. Colok scanner QR USB ke PC pintu (mode "keyboard/HID", akhiran Enter, default hampir semua scanner). Tanpa driver.
2. Buka `http://<server>/attendance/qr/kiosk/` di browser PC itu, layar penuh (F11). **Selesai**: tidak ada kunci,
   tidak perlu klik atau fokus. Dua penanda di kiri bawah: "Server terhubung" dan "Scanner terdeteksi"
   (hijau setelah scan pertama). Scanner yang tidak mengirim Enter tetap terbaca otomatis.
3. **Kartu QR** → pilih pegawai → **Cetak kartu terpilih** → pilih bahasa kartu (bendera) → atur kertas 85,6 × 54 mm
   tanpa margin → cetak depan+belakang → **Tandai sudah dicetak**.

Kunci perangkat masih ada sebagai pilihan (menu **Perangkat kiosk** + `ATTENDANCE_REQUIRE_DEVICE_KEY = True`).
Kamera hanya cadangan (tombol "Pakai kamera", Chrome/Edge) dan butuh HTTPS atau `localhost`.

## Bahasa

Empat bahasa: **Tetun (TET), English (EN), Indonesia (ID), Português (PT)**. Bawaan Tetun (`ATTENDANCE_DEFAULT_LANG`).
- **Kiosk:** klik bendera di header kanan, teks berganti seketika tanpa memuat ulang. Hasil scan berikutnya
  juga memakai bahasa itu.
- **Halaman admin:** klik bendera, halaman dimuat ulang dalam bahasa baru. Pilihan tersimpan di cookie `mdi_lang`
  (satu tahun), jadi sama untuk kiosk dan admin di browser yang sama.
- **Cetak kartu:** bendera di toolbar mengganti teks kartu seketika (judul, "No.", masa berlaku, pesan belakang,
  "Scan saat masuk & pulang"). Yang tercetak adalah bahasa yang sedang tampil. Nama pegawai, jabatan, dan nama
  libur/cuti dari database tidak diterjemahkan.
- Semua teks ada di `qr_i18n.py` (`MESSAGES`). Tes memastikan setiap kunci punya keempat bahasa dan placeholder yang sama.
  **Teks Tetun sebaiknya dibaca ulang oleh penutur asli** sebelum dipakai resmi.

## Hubungan dengan absensi manual dan Ativa Tinan / Ativa Fulan

- Scan **memakai tanggal sebenarnya** dan tidak membaca Ativa Tinan / Ativa Fulan. Daftar absensia lama menampilkan
  bulan yang diaktifkan di Konfigurasaun, jadi admin tetap perlu mengaktifkan bulan baru agar data scan bulan itu
  terlihat di daftar lama. Rekap bulanan QR tidak bergantung pada pengaktifan itu.
- Scan pertama pada satu bulan membuat baris `AttendanceDiv` (divisi, tahun, bulan) bila belum ada, sama seperti
  alur manual, sehingga unggah PDF, Konfirmasi, dan Konfirmasi final bulan itu tetap bisa dipakai.
- Baris `Attendance` dari scan memakai status P (hadir). Daftar lama menampilkan status itu seperti biasa; jam masuk
  dan pulang hanya terlihat di halaman QR (Hari ini, Rekap).

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
| Scan dari alamat di luar `ATTENDANCE_KIOSK_ALLOWED_IPS` | Ditolak (403), tidak tercatat |
| Tidak ada scan di hari kerja yang sudah lewat | Dihitung Alpha (F) di rekap, tanpa menulis baris |

Hari libur dicocokkan lewat bulan dan tanggal saja (seperti view lama): libur 20 Mei berlaku setiap tahun.

## Keamanan

- QR berisi `MDI.<id pegawai>.<versi>.<HMAC-SHA256>`. Tidak ada token yang disimpan. Kartu hilang → **Ganti QR**
  (versi naik, kartu lama langsung mati).
- Tanpa kunci perangkat, pembatas absen-dari-rumah adalah `ATTENDANCE_KIOSK_ALLOWED_IPS`. **Isi ini** setelah kiosk
  berjalan. Kalau memakai kunci perangkat, hanya sidik SHA-256-nya yang disimpan.
- Kolom `hashed` di `Attendance` tetap MD5 dari `getnewid()` karena kolomnya 32 karakter dan view lama mencari lewat itu.
  SHA-256 dipakai untuk bagian baru (QR dan kunci perangkat).
- Setiap scan yang sampai ke server, termasuk yang ditolak, tercatat di `ScanLog` beserta alamat IP-nya.
