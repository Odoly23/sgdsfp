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
attendance/templatetags/       __init__.py + qr_i18n.py ({% t %}, {% t4 %}, {% flags %}, {% qr_tab_url %}, {% qr_chart_url %})
attendance/qr_checks.py        pemeriksaan konfigurasi (python manage.py check)
attendance/management/commands/prune_scanlogs.py   pembersih log scan
attendance/tests_qr.py         162 tes otomatis
attendance/templates/attendance/qr/*
attendance/static/attendance/qr/*      (qr.css, kiosk.js, charts.js, mdi-logo.jpg)
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
ATTENDANCE_TIME_ZONE = 'Asia/Dili'      # zona waktu absensi saja; TIME_ZONE proyek tidak diubah
ATTENDANCE_DEFAULT_LANG = 'tet'         # tet | en | id | pt
ATTENDANCE_LANG_IN_NAVBAR = False       # True setelah bendera ditempel di navbar (bagian 4)
# --- keamanan (bawaan sudah ketat; ubah hanya bila perlu)
# ATTENDANCE_KIOSK_ALLOWED_IPS = ['192.168.1.0/24']   # bawaan: semua jaringan privat. [] = TERBUKA (tidak aman)
# ATTENDANCE_REQUIRE_DEVICE_KEY = True    # scan hanya dari PC yang dipasangkan admin (bawaan True)
# ATTENDANCE_ALLOW_CAMERA = False         # kamera cadangan di kiosk (bawaan mati: hanya alat pembaca QR)
# ATTENDANCE_TRUST_PROXY_HEADER = True    # True bila server di belakang Nginx/proxy lokal tepercaya
# ATTENDANCE_CLIENT_IP_HEADER = 'HTTP_CF_CONNECTING_IP'   # alamat klien dari header front-end tepercaya (Cloudflare)
# ATTENDANCE_THROTTLE_PER_MINUTE = 60     # batas scan per alamat IP per menit
# --- kecepatan
# ATTENDANCE_REPORT_CACHE_SECONDS = 60    # cache dashboard/rekap seluruh pegawai (0 = mati)
# ATTENDANCE_HOLIDAY_CACHE_SECONDS = 60   # cache daftar libur pada jalur scan (0 = mati)
ATTENDANCE_LATE_AFTER = '08:15'         # masuk setelah jam ini = terlambat
ATTENDANCE_NOON = '12:00'               # sebelum = scan masuk (AM), sesudah = pulang (PM)
ATTENDANCE_WORK_END = '16:00'           # pulang sebelum jam ini ditandai "pulang awal"
ATTENDANCE_DUPLICATE_SECONDS = 60
ATTENDANCE_ACTIVE_STATUS_ID = 1         # Employee.status_id pegawai aktif
ATTENDANCE_PRESENT_CODE = 'P'           # AttendanceStatus.code untuk hadir
ATTENDANCE_MANAGER_GROUPS = ['admin', 'staff']              # kartu QR
ATTENDANCE_VIEWER_GROUPS = ['admin', 'staff', 'president']  # hari ini + rekap
ATTENDANCE_DEVICE_GROUPS = ['admin']                        # memasangkan PC kiosk
ATTENDANCE_SELF_GROUPS = ['admin', 'staff', 'president', 'funsionario', 'employee']
ATTENDANCE_CARD_BACK_NOTE = {'tet': '...', 'en': '...', 'id': '...', 'pt': '...'}  # atau satu string
ATTENDANCE_CARD_CONTACT = 'Telp ...'    # baris kontak di belakang kartu (string atau dict per bahasa)
```

**`ATTENDANCE_QR_SECRET`:** kalau kosong, tanda tangan QR memakai `SECRET_KEY`. Lalu mengganti
`SECRET_KEY` (yang sebaiknya segera dilakukan karena sudah tampil di chat) membuat **semua kartu tidak berlaku**.
Dengan `ATTENDANCE_QR_SECRET` terpisah, kartu tetap aman walau `SECRET_KEY` diganti. Isi dengan string acak panjang
dan jangan diubah setelah kartu dicetak.

**Jaringan kantor (`ATTENDANCE_KIOSK_ALLOWED_IPS`):** bawaannya hanya jaringan privat (192.168.x.x, 10.x.x.x, 172.16-31.x.x,
localhost). Pegawai di rumah memakai alamat publik, jadi scan mereka ditolak walau memegang kartu dan punya fotonya.
Persempit ke jaringan kantor Anda saja bila perlu, misalnya `['192.168.1.0/24']`. Mengisi `[]` membuka scan dari seluruh
internet dan memunculkan peringatan merah.

**Di belakang Nginx/proxy:** server hanya melihat alamat proxy, jadi alamat asli pegawai tidak diketahui. Karena itu,
**bila ada header `X-Forwarded-For` tetapi `ATTENDANCE_TRUST_PROXY_HEADER` belum True, scan ditolak** (gagal-aman) dan
tercatat di log `attendance.qr`. Setelah proxy Anda tepercaya, set `ATTENDANCE_TRUST_PROXY_HEADER = True` dan pastikan
Nginx menimpa header itu, bukan menambahkan ke milik klien:
```nginx
proxy_set_header X-Forwarded-For $remote_addr;
```
Alamat klien diambil dari entri paling kanan header itu.

**Cache:** `ATTENDANCE_REPORT_CACHE_SECONDS` menyimpan hasil dashboard dan rekap seluruh pegawai sebentar (60 detik bawaan)
dan dipakai bersama semua pimpinan; data di dashboard bisa tertinggal sampai selama itu. Halaman Hari ini, laporan pribadi,
dan scan selalu langsung. Dengan beberapa proses gunicorn, tiap proses punya cache sendiri; pakai cache bersama
(Redis/Memcached) bila mau satu cache untuk semua.

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

**Menu RELATORIU di navbar** (sama seperti menu RELATORIU Tabular / Grafiku yang Anda punya): tempel satu baris di
`adm.html`, `staff.html`, `prezident.html`, dan `funsionario.html`, sebelum menu profil:
```html
{% include 'attendance/qr/_navbar_report.html' %}
```
Hasilnya dropdown **RELATORIU → Tabular / Grafiku** (4 bahasa). Tujuannya otomatis mengikuti grup:
admin, staff, president ke dashboard seluruh pegawai; funsionario dan employee ke laporan absensinya sendiri.

**Tombol di Varanda (home)**, besar dan mudah diketuk di HP:
```html
{% include 'attendance/qr/_home_button.html' %}
```

**Menu ABSENSI QR untuk admin (opsional)**, mis. di `adm.html`:
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
Halaman sendiri sudah memeriksa grup, jadi menu yang salah tidak membuka akses.

**Bendera di topbar kanan:** tempel satu baris ini di setiap menu navbar (`adm.html`, `staff.html`, ...),
tepat sebelum menu profil (`<li class="nav-item dropdown active">` yang berisi `request.user.username`):
```html
{% include 'attendance/qr/_navbar_lang.html' %}
```
lalu set `ATTENDANCE_LANG_IN_NAVBAR = True`. Selama belum ditempel, bendera tampil di baris tombol di dalam
halaman absensi sehingga tetap bisa dipakai. Layar kiosk selalu punya bendera di header kanan.

## 5. Pasang di kantor (sekali saja)

1. Colok alat pembaca QR USB ke PC pintu (mode "keyboard/HID", akhiran Enter, bawaan hampir semua alat). Tanpa driver.
2. Di **PC pintu itu**, login sebagai `admin` → **Perangkat kiosk** → **Jadikan PC ini kiosk**. Satu klik, tanpa mengetik
   kunci. Di VPS/internet, biarkan kotak "Izinkan juga IP kantor ini" tercentang (lihat bagian 7). Server membuat kunci acak, hanya sidik SHA-256-nya yang disimpan, dan kunci aslinya dititipkan ke browser PC itu
   sebagai cookie `HttpOnly` (tidak bisa dibaca JavaScript, hanya terkirim ke `/attendance/qr/`).
3. Buka `/attendance/qr/kiosk/` di browser yang sama, layar penuh (F11), lalu logout dari akun admin. Selesai: tidak
   perlu klik atau fokus. Di kiri bawah ada "Server terhubung" dan "Scanner terdeteksi" (hijau setelah scan pertama).
   PC yang belum dipasangkan menampilkan petunjuk dan tidak menerima scan.
4. **Kartu QR** → pilih pegawai → **Cetak kartu terpilih** → pilih bahasa kartu (bendera) → atur kertas 85,6 × 54 mm
   tanpa margin → cetak depan+belakang → **Tandai sudah dicetak**.

Mencabut PC kiosk (hilang/diganti): **Perangkat kiosk** → **Nonaktifkan**; PC itu langsung tidak bisa scan.
Memasangkan ulang di browser yang sama otomatis mematikan pemasangan lamanya. Kamera sengaja mati (hanya alat pembaca QR
yang dipakai); nyalakan dengan `ATTENDANCE_ALLOW_CAMERA = True` bila perlu, dan butuh HTTPS.

## 6. Topbar: ganti MCI-HRMS menjadi MDI-HRMS dan warna merah MDI

Ini ada di berkas proyek Anda, bukan di berkas absensi QR. Cari semua tulisan lama di template:
```bash
grep -rn "MCI-HRMS" --include="*.html" .
```
Ganti semuanya (topbar, judul tab, footer, halaman login) dengan satu perintah, lalu periksa hasilnya dengan `git diff`:
```bash
grep -rl "MCI-HRMS" --include="*.html" . | xargs sed -i 's/MCI-HRMS/MDI-HRMS/g'
```
Warna topbar ada di `main/templates/main/navbar.html`, pada tag `<nav class="navbar ... fixed-top" style="background-color: ...">`.
Ganti nilainya dengan merah MDI:
```html
<nav class="navbar navbar-expand-lg navbar-dark fixed-top" style="background-color: #e3171e;">
```
Warna yang dipakai fitur QR: merah `#e3171e`, merah gelap `#a80f14` (untuk hover), abu perak `#b8b4aa`.
Kalau mau topbar sedikit lebih gelap supaya teks putih lebih terbaca, pakai `#c8141a`.

## 7. Deploy di VPS dengan cPanel

**Hal terpenting:** di VPS, server tidak melihat alamat lokal kantor (192.168.x.x), melainkan **IP publik kantor**. Karena itu
bawaan "hanya jaringan privat" akan menolak semua scan sampai IP publik kantor didaftarkan. Cukup sekali, dari PC kantor:
login `admin` → **Perangkat kiosk**. Halaman itu menampilkan "IP browser ini (terlihat oleh server)" dan statusnya.
Centang **Izinkan juga IP kantor ini** lalu klik **Jadikan PC ini kiosk**: PC dipasangkan dan IP kantor terdaftar sekaligus.
Bila IP kantor berubah (IP dinamis), kiosk menampilkan "Di luar jaringan kantor"; ulangi dari PC kantor (30 detik) atau
tambahkan rentang tetap dari ISP. Daftar bisa dinonaktifkan seketika. Rentang yang terlalu luas (lebih besar dari /16)
ditolak agar tidak sengaja membuka scan ke seluruh internet.

### Menyiapkan aplikasi
1. cPanel → **Setup Python App** → Python 3.9 atau lebih baru (3.8 tetap didukung) → *Application root* folder proyek,
   *Startup file* `passenger_wsgi.py`, *Entry point* `application`:
   ```python
   import os, sys
   sys.path.insert(0, os.path.dirname(__file__))
   os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mci_hrms.settings')      # nama folder proyek Anda saat ini; sesuaikan bila diganti
   from mci_hrms.wsgi import application
   ```
2. Pasang paket (tombol *Run Pip Install* atau terminal virtualenv yang ditampilkan cPanel):
   `pip install -r requirements.txt -r requirements-qr.txt` (memuat `tzdata` dan `backports.zoneinfo` untuk Python 3.8).
3. Isi rahasia lewat **Environment variables** di halaman Python App, bukan di kode: `DJANGO_SECRET_KEY`,
   `ATTENDANCE_QR_SECRET`, dan kata sandi basis data.
4. Pengaturan produksi:
   ```python
   DEBUG = False
   ALLOWED_HOSTS = ['absensi.domainanda.org']
   CSRF_TRUSTED_ORIGINS = ['https://absensi.domainanda.org']
   SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True          # setelah AutoSSL aktif
   SECURE_HSTS_SECONDS = 31536000                              # setelah HTTPS terbukti jalan
   DATABASES['default']['CONN_MAX_AGE'] = 60                   # koneksi MySQL dipakai ulang: scan lebih cepat
   CACHES = {'default': {                                      # cache dibagi semua proses Passenger (LocMem tidak)
       'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
       'LOCATION': '/home/USERNAME/tmp/mdi_cache', 'TIMEOUT': 300, 'OPTIONS': {'MAX_ENTRIES': 2000}}}
   ATTENDANCE_QR_SECRET = os.environ['ATTENDANCE_QR_SECRET']
   ```
   Buat folder cache: `mkdir -p ~/tmp/mdi_cache && chmod 700 ~/tmp/mdi_cache`. Pembatas permintaan dan cache laporan
   bekerja di cache berbasis berkas ini (sudah diuji). `python manage.py check` memberi peringatan bila cache masih LocMem.
5. `python manage.py migrate && python manage.py collectstatic --noinput`, lalu klik **Restart** (atau
   `touch ~/APP/tmp/restart.txt`). Lakukan lagi setiap kali kode diperbarui.

### Berkas statis dan foto
- Statis (CSS/JS, termasuk Highcharts) dilayani WhiteNoise dari `STATIC_ROOT`. Agar lebih ringan, biarkan Apache yang
  melayani langsung: `ln -s ~/APP/staticfiles ~/public_html/static`.
- Foto pegawai (`/media/`) **tidak** dilayani Django saat `DEBUG=False`: `ln -s ~/APP/media ~/public_html/media`
  (tanpa ini foto di kiosk dan kartu kosong).

### Alamat klien di belakang proxy
| Susunan di server | Yang perlu diatur |
|---|---|
| Passenger/LiteSpeed langsung (paling umum di cPanel) | Tidak ada. Alamat klien = `REMOTE_ADDR`; header dari klien diabaikan |
| Nginx reverse proxy di server yang sama (EA-Nginx) | `ATTENDANCE_TRUST_PROXY_HEADER = True`; Nginx harus menimpa header: `proxy_set_header X-Forwarded-For $remote_addr;` |
| Cloudflare di depan | `ATTENDANCE_CLIENT_IP_HEADER = 'HTTP_CF_CONNECTING_IP'`, dan batasi akses langsung ke server hanya dari alamat Cloudflare |

Cek hasilnya di halaman **Perangkat kiosk** dari PC kantor: alamat yang tampil harus IP publik kantor. Bila tampil
`127.0.0.1` atau alamat server, proxy belum dipercaya; scan ditolak (gagal-aman) dan log `attendance.qr` memberi tahu.

### Pemeliharaan
- cPanel → **Cron Jobs**, mingguan:
  `0 3 * * 0  /home/USERNAME/virtualenv/APP/3.9/bin/python /home/USERNAME/APP/manage.py prune_scanlogs --days 180 >> /home/USERNAME/logs/prune.log 2>&1`
- Cadangan: pakai cadangan basis data cPanel. Tabel baru: `attendance_qrcard`, `attendance_scandevice`,
  `attendance_attendancetime`, `attendance_scanlog`, `attendance_officenetwork`.
- Batas sumber daya (CloudLinux LVE): kode sengaja ringan (19 query per scan, laporan di-cache, tanpa proses latar).
  Cukup 2 sampai 4 proses aplikasi.

### Bila ada masalah
| Gejala | Penyebab biasa | Perbaikan |
|---|---|---|
| Kiosk: "Di luar jaringan kantor" padahal di kantor | IP publik kantor belum terdaftar atau berganti; atau proxy belum dipercaya | Perangkat kiosk → lihat IP yang tampil → Izinkan IP ini |
| Kiosk: "PC ini belum dipasangkan" | Belum dipasangkan, cookie terhapus, atau situs dibuka lewat `http` setelah dipasangkan lewat `https` | Pasangkan lagi; selalu pakai `https` (aktifkan HSTS) |
| Formulir admin: 403 CSRF | `CSRF_TRUSTED_ORIGINS` belum memuat `https://domain` | Tambahkan, restart |
| 500 setelah deploy | `migrate` belum dijalankan (tabel baru) | `python manage.py migrate` |
| `ZoneInfoNotFoundError` | Server tanpa basis data zona waktu | `pip install tzdata` |
| Grafik kosong, tulisan "Grafik tidak tersedia" | `collectstatic` belum dijalankan atau Highcharts tidak ditemukan | `collectstatic`; periksa `main/js_custom/highcharts.js` |
| Foto kosong di kiosk/kartu | `/media/` tidak dilayani | symlink `public_html/media` (lihat di atas) |

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

## Laporan: Tabular dan Grafiku

| Siapa | Tabular | Grafiku |
|---|---|---|
| admin, staff, president | `/attendance/qr/dash/`: angka total, ringkasan per diresaun (klik ke rekap), 10 paling sering terlambat | `/attendance/qr/dash/chart/`: kehadiran per diresaun, tren harian, paling sering terlambat |
| funsionario, employee | `/attendance/qr/me/`: angka ringkas yang bisa diklik (menyaring daftar), kalender mini, daftar harian | `/attendance/qr/me/chart/`: grafik setahun per bulan dan diagram status bulan terpilih |

- Pegawai hanya melihat datanya sendiri. Akun dihubungkan lewat `EmployeeUser`; server tidak menerima id pegawai dari
  browser, jadi tidak bisa mengintip data orang lain. Akun yang belum tertaut melihat pesan "hubungi admin".
- Grafik memakai **Highcharts** dari `main/js_custom/highcharts.js` (berkas yang sama dengan grafik Anda yang sudah ada).
  Datanya dari endpoint JSON `api/dash/` dan `api/me/` (login + grup; 401/403 berupa JSON; tidak di-cache). Kalau
  Highcharts belum termuat, tiap kotak grafik menampilkan pesan dan halaman tetap berfungsi.
- Kolom "Hadir" pada grafik adalah hadir tepat waktu; "Hadir, terlambat" dipisah agar tidak terhitung ganda.

## Tampilan responsif (mobile-first)

CSS ditulis dari ukuran HP lalu diperluas pada 576 / 768 / 900 / 992 px. Diuji di 8 ukuran layar: HP kecil 320 px, HP
360 dan 412 px, HP mendatar 740×360, tablet tegak 768 dan mendatar 1024, PC 1280, dan layar lebar 1920 px, pada 11
halaman: tanpa geser horizontal, grafik tampil, tanpa galat JavaScript, dan tombol minimal 44 px pada layar sentuh.
- **HP:** tabel menjadi kartu berlabel; rekap bulanan menjadi satu kartu per pegawai dengan kalender mini;
  kotak angka dua kolom; filter bertumpuk dengan kolom besar; tombol menu dua kolom.
- **Tablet dan PC:** tabel biasa, rekap berbentuk tabel 31 kolom, kotak angka tiga sampai enam kolom.
- **Layar sentuh (`pointer: coarse`):** sasaran ketuk 44 px berlaku juga pada tablet lebar; ukuran huruf kolom isian
  minimal 16 px sehingga iPhone tidak memperbesar halaman saat kolom diketuk.
- **Kiosk:** satu kolom di HP dan tablet tegak, dua kolom di layar lebar dan HP mendatar; mengikuti area aman layar
  berponi (`viewport-fit=cover`); menghormati pengaturan "kurangi gerakan".
- Syarat: `main/layout.html` harus punya `<meta name="viewport" content="width=device-width, initial-scale=1">`
  (layout Anda sudah punya). Tanpa itu HP menampilkan versi desktop yang diperkecil.

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
| Scan dari alamat di luar jaringan kantor (mis. dari rumah) | Ditolak (403), tidak tercatat |
| Scan dari PC/browser yang belum dipasangkan admin, atau sudah dicabut | Ditolak (401); kiosk menampilkan petunjuk pemasangan |
| Lebih dari 60 permintaan/menit dari satu alamat IP | Ditolak (429) |
| Tidak ada scan di hari kerja yang sudah lewat | Dihitung Alpha (F) di rekap, tanpa menulis baris |

Hari libur dicocokkan lewat bulan dan tanggal saja (seperti view lama): libur 20 Mei berlaku setiap tahun.

## Keamanan

Scan hanya sah bila **dari jaringan kantor, dari PC yang dipasangkan admin, dengan kartu yang tanda tangannya benar**.
Dari rumah scan gagal walau memegang kartu atau fotonya.

| Lapis | Perlindungan |
|---|---|
| Kartu | QR berisi `MDI.<id>.<versi>.<HMAC-SHA256>`; tidak ada token yang disimpan. Dipalsukan = ditolak. Hilang → **Ganti QR** (versi naik, kartu lama langsung mati). Kartu lama tidak menampilkan nama pemiliknya di kiosk |
| Jaringan | Hanya jaringan privat bawaan dan IP kantor yang didaftarkan admin (VPS); rentang terlalu luas ditolak; header proxy dari klien tidak dipercaya, dan koneksi dari proxy lokal yang tidak dipercaya = ditolak (gagal-aman) |
| PC kiosk | Cookie `HttpOnly`, `SameSite=Strict`, `Secure` di HTTPS, dibatasi ke `/attendance/qr/`; hanya SHA-256 kunci yang disimpan; bisa dicabut seketika |
| Permintaan scan | Wajib header `X-Kiosk: 1` dan JSON (formulir lintas situs tidak bisa), ukuran dibatasi 1 KB, 60 permintaan/menit per alamat IP, masukan dibatasi panjang dan karakternya sebelum diperiksa |
| Hak akses | Login + keanggotaan salah satu grup (bukan hanya grup pertama); tidak berhak = HTTP 403; semua tombol yang mengubah data wajib POST dan token CSRF |
| Data pegawai | Pegawai hanya melihat datanya sendiri (id tidak pernah diterima dari browser); endpoint JSON mengembalikan 401/403 berupa JSON dan tidak di-cache |
| Keluaran | Tidak ada `innerHTML` untuk teks dari server (DOM + `textContent`), semua template di-escape, ekspor CSV/Excel menetralkan injeksi rumus (`=`, `+`, `-`, `@`) |
| Audit | Setiap scan yang sampai ke server, termasuk yang ditolak, tercatat di `ScanLog` beserta IP; bersihkan berkala dengan `prune_scanlogs` |
| Konfigurasi | `python manage.py check` memberi peringatan: `QR_SECRET` kosong, jaringan dibuka, pemasangan PC dimatikan, zona waktu/jam/IP tidak valid |

Batas yang perlu diketahui: pembaca QR USB bekerja seperti keyboard, jadi server tidak bisa membedakannya dari ketikan
yang sangat cepat. Pencegahnya berlapis (jaringan kantor + PC terpasang + kamera mati + halaman kiosk hanya dibuka
di PC pintu). Pegawai yang berada di kantor tetap bisa memfoto QR rekannya; cegah dengan prosedur kantor atau kamera
pengawas di pintu. Kolom `hashed` di `Attendance` tetap MD5 dari `getnewid()` karena kolomnya 32 karakter dan view lama
mencari lewat itu; SHA-256/HMAC dipakai untuk bagian baru.

## Server ringan

- **Scan:** 19 query per scan pertama dan 14 per scan pulang; scan ditolak 2 sampai 8 query; masukan sampah hanya 2.
  Hal-hal yang berat dihindari: foto dan unit tidak diambil per pegawai, daftar libur di-cache, kartu QR dibuat sekaligus.
- **Laporan:** jumlah query tetap walau pegawainya ratusan (diuji 6 vs 66 pegawai); dashboard dan grafik berbagi satu
  hasil hitung per menit; CSV dialirkan baris demi baris dan Excel memakai mode tulis-saja agar memori kecil.
- **Halaman:** Highcharts (280 KB) hanya dimuat di halaman grafik dan memakai `defer`; CSS/JS memakai `?v=<waktu ubah>`
  sehingga boleh disimpan lama di cache peramban; halaman Hari ini menyegarkan diri tiap menit dan berhenti saat tab
  tidak terlihat; jam kiosk berhenti saat layar tidak terlihat.
- **Pemeliharaan:** jadwalkan pembersihan log tiap minggu: `0 3 * * 0  python manage.py prune_scanlogs --days 180`.
- **Indeks basis data** (opsional, mempercepat pencarian absensi per pegawai dan tanggal pada tabel besar):
  `CREATE INDEX att_emp_date ON attendance_attendance (employee_id, date);`
- Produksi: jalankan `collectstatic` dan aktifkan kompresi WhiteNoise (`whitenoise.storage.CompressedManifestStaticFilesStorage`).
