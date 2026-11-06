"""Terjemahan absensi QR: Tetun (tet), Inggris (en), Indonesia (id), Portugis (pt).

Sengaja tidak memakai gettext/LocaleMiddleware supaya proyek lain tidak terpengaruh.
Bahasa dipilih lewat cookie `mdi_lang` (bendera di setiap halaman). Tambah kunci baru di MESSAGES.
Teks Tetun sebaiknya dibaca ulang oleh penutur asli sebelum dipakai resmi.
"""
import datetime

from django.conf import settings

LANGS = ('tet', 'en', 'id', 'pt')
LANG_NAMES = {'tet': 'Tetun', 'en': 'English', 'id': 'Indonesia', 'pt': 'Português'}
COOKIE = 'mdi_lang'
COOKIE_AGE = 60 * 60 * 24 * 365


def default_lang():
	lang = getattr(settings, 'ATTENDANCE_DEFAULT_LANG', 'tet')
	return lang if lang in LANGS else 'tet'


def norm(lang):
	return lang if lang in LANGS else default_lang()


def get_lang(request):
	return norm(request.COOKIES.get(COOKIE) if request is not None else None)


MONTHS = {
	'tet': ['Janeiru', 'Fevereiru', 'Marsu', 'Abril', 'Maiu', 'Junhu', 'Julhu', 'Agostu', 'Setembru', 'Outubru', 'Novembru', 'Dezembru'],
	'en': ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'],
	'id': ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'],
	'pt': ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'],
}
# indeks 0 = Senin
WEEKDAYS = {
	'tet': ['Segunda', 'Tersa', 'Kuarta', 'Kinta', 'Sexta', 'Sábadu', 'Domingu'],
	'en': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'],
	'id': ['Senin', 'Selasa', 'Rabu', 'Kamis', 'Jumat', 'Sabtu', 'Minggu'],
	'pt': ['Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira', 'Sábado', 'Domingo'],
}


def weekday_short(lang):
	return [w[:3] for w in WEEKDAYS[norm(lang)]]


def month_short(lang):
	return [m[:3] for m in MONTHS[norm(lang)]]


def month_name(month, lang):
	return MONTHS[norm(lang)][month - 1]


def fmt_date(d, lang):
	lang = norm(lang)
	return '%s, %d %s %d' % (WEEKDAYS[lang][d.weekday()], d.day, MONTHS[lang][d.month - 1], d.year)


# urutan nilai: (tet, en, id, pt). Placeholder {nama} harus sama di keempat bahasa (dicek oleh tes).
MESSAGES = {
	# ---------------------------------------------------------------- navigasi
	'nav.today': ('Ohin', 'Today', 'Hari ini', 'Hoje'),
	'nav.report': ('Rezumu fulan', 'Monthly report', 'Rekap bulanan', 'Relatório mensal'),
	'nav.cards': ('Kartaun QR', 'QR cards', 'Kartu QR', 'Cartões QR'),
	'nav.devices': ('Aparelu kiosk', 'Kiosk devices', 'Perangkat kiosk', 'Dispositivos do quiosque'),
	'nav.me': ('Ha\'u nia absensia', 'My attendance', 'Absensi saya', 'A minha assiduidade'),
	'nav.kiosk': ('Loke ekran kiosk', 'Open kiosk screen', 'Buka layar kiosk', 'Abrir ecrã do quiosque'),
	'nav.qr': ('Absensia QR', 'QR attendance', 'Absensi QR', 'Assiduidade QR'),
	'crumb.panel': ('Painel absensia', 'Attendance panel', 'Panel absensi', 'Painel de assiduidade'),
	'lang.label': ('Lian', 'Language', 'Bahasa', 'Idioma'),

	# ---------------------------------------------------------------- hasil scan (server)
	'res.invalid.title': ('QR la rekoñese', 'Unknown QR code', 'QR tidak dikenal', 'QR desconhecido'),
	'res.invalid.msg': ('Kartaun ida-ne\'e laos kartaun absensia ne\'ebé válidu.',
		'This is not a valid attendance card.', 'Kartu ini bukan kartu absensi yang sah.', 'Este não é um cartão de assiduidade válido.'),
	'res.revoked.title': ('Kartaun la válidu', 'Card not valid', 'Kartu tidak berlaku', 'Cartão inválido'),
	'res.revoked.msg': ('Kartaun ne\'e troka ona ka la ativu. Kontaktu administradór.',
		'This card has been replaced or deactivated. Contact the admin.',
		'Kartu ini sudah diganti atau dinonaktifkan. Hubungi admin.',
		'Este cartão foi substituído ou desativado. Contacte o administrador.'),
	'res.inactive.title': ('Funsionáriu la ativu', 'Employee not active', 'Pegawai tidak aktif', 'Funcionário inativo'),
	'res.inactive.msg': ('Kontaktu administradór.', 'Contact the admin.', 'Hubungi admin.', 'Contacte o administrador.'),
	'res.card_expired.title': ('Kartaun validade remata ona', 'Card expired', 'Kartu kedaluwarsa', 'Cartão expirado'),
	'res.card_expired.msg': ('Validade kartaun funsionáriu remata ona. Kontaktu administradór.',
		'The employee card has expired. Contact the admin.',
		'Masa berlaku kartu pegawai sudah habis. Hubungi admin.',
		'O cartão de funcionário expirou. Contacte o administrador.'),
	'res.holiday.title': ('Loron feriadu', 'Day off', 'Hari libur', 'Dia de folga'),
	'res.holiday.msg': ('{reason}. Absensia la rejistu ohin.', '{reason}. Attendance is not recorded today.',
		'{reason}. Absensi tidak dicatat hari ini.', '{reason}. A assiduidade não é registada hoje.'),
	'res.leave.title': ('Iha lisensa', 'On leave', 'Sedang cuti', 'Em licença'),
	'res.leave.msg': ('Ita-nia status ohin: {kind} (to\'o {end}). Absensia la muda.',
		'Your status today: {kind} (until {end}). Attendance is not changed.',
		'Status Anda hari ini: {kind} (sampai {end}). Absensi tidak diubah.',
		'O seu estado hoje: {kind} (até {end}). A assiduidade não é alterada.'),
	'res.month_final.title': ('Fulan final ona', 'Month is final', 'Bulan sudah final', 'Mês finalizado'),
	'res.month_final.msg': ('Absensia fulan ne\'e finaliza ona. Kontaktu administradór.',
		'This month\'s attendance has been finalized. Contact the admin.',
		'Absensi bulan ini sudah difinalkan. Hubungi admin.',
		'A assiduidade deste mês foi finalizada. Contacte o administrador.'),
	'res.config.title': ('Konfigurasaun la kompletu', 'Setup incomplete', 'Pengaturan belum lengkap', 'Configuração incompleta'),
	'res.config.msg': ('Status presensa (kódigu {code}) seidauk iha iha AttendanceStatus.',
		'The "present" status (code {code}) does not exist in AttendanceStatus.',
		'Status hadir (kode {code}) belum ada di AttendanceStatus.',
		'O estado "presente" (código {code}) não existe em AttendanceStatus.'),
	'res.manual.title': ('Admin hatama ona', 'Already filled by admin', 'Sudah diisi admin', 'Já preenchido pelo administrador'),
	'res.manual.msg': ('Absensia ohin admin hatama ona manuál, scan la simu.',
		'Today\'s attendance was filled in manually by the admin; the scan is not accepted.',
		'Absensi hari ini sudah diisi manual oleh admin, scan tidak diterima.',
		'A assiduidade de hoje foi preenchida manualmente pelo administrador; a leitura não é aceite.'),
	'res.busy.title': ('Koko fali', 'Try again', 'Coba lagi', 'Tente novamente'),
	'res.busy.msg': ('Sistema okupadu, scan fali ita-nia kartaun.', 'The system is busy, scan your card again.',
		'Sistem sibuk, scan ulang kartu Anda.', 'O sistema está ocupado, leia o cartão novamente.'),
	'res.in_late.title': ('Tama rejistu (tardi)', 'Check-in recorded (late)', 'Masuk tercatat (terlambat)', 'Entrada registada (atraso)'),
	'res.in_late.msg': ('Tama iha oras {time}. Limite tama {limit}.', 'Checked in at {time}. Check-in limit is {limit}.',
		'Masuk pukul {time}. Batas masuk {limit}.', 'Entrada às {time}. Limite de entrada {limit}.'),
	'res.in.title': ('Bem-vindu', 'Welcome', 'Selamat datang', 'Bem-vindo'),
	'res.in.msg': ('Tama iha oras {time}.', 'Checked in at {time}.', 'Masuk pukul {time}.', 'Entrada às {time}.'),
	'res.duplicate.title': ('Scan ona', 'Already scanned', 'Sudah discan', 'Já lido'),
	'res.duplicate.msg': ('Ita-nia kartaun foin scan ona.', 'Your card was just scanned.',
		'Kartu Anda baru saja discan.', 'O seu cartão acabou de ser lido.'),
	'res.already_in.title': ('Rejistu tama ona', 'Already checked in', 'Sudah tercatat masuk', 'Entrada já registada'),
	'res.already_in.msg': ('Tama iha oras {time}. Scan fali bainhira sai.', 'Checked in at {time}. Scan again when you leave.',
		'Masuk pukul {time}. Scan lagi saat pulang.', 'Entrada às {time}. Leia novamente ao sair.'),
	'res.out_early.title': ('Sai sedu rejistu', 'Early check-out recorded', 'Pulang awal tercatat', 'Saída antecipada registada'),
	'res.out_early.msg': ('Sai iha oras {time}. Oras sai eskritóriu {limit}.', 'Checked out at {time}. Office closing time is {limit}.',
		'Pulang pukul {time}. Jam pulang kantor {limit}.', 'Saída às {time}. Hora de saída do escritório {limit}.'),
	'res.out.title': ('To\'o fali tan', 'See you', 'Sampai jumpa', 'Até breve'),
	'res.out.msg': ('Sai iha oras {time}.', 'Checked out at {time}.', 'Pulang pukul {time}.', 'Saída às {time}.'),
	'res.net.title': ('Sai husi rede eskritóriu', 'Outside the office network', 'Di luar jaringan kantor', 'Fora da rede do escritório'),
	'reason.weekend': ('Final semana', 'Weekend', 'Akhir pekan', 'Fim de semana'),
	'reason.holiday': ('Feriadu nasionál', 'National holiday', 'Libur nasional', 'Feriado nacional'),
	'reason.tolerance': ('Loron toleránsia', 'Tolerance day', 'Hari toleransi', 'Dia de tolerância'),
	'leave.default': ('Lisensa', 'Leave', 'Cuti', 'Licença'),

	# ---------------------------------------------------------------- kiosk
	'k.title': ('ABSENSIA FUNSIONÁRIU', 'EMPLOYEE ATTENDANCE', 'ABSENSI PEGAWAI', 'ASSIDUIDADE DOS FUNCIONÁRIOS'),
	'k.hours': ('Tama to\'o {late} · Sai hahú {end}', 'In until {late} · Out from {end}',
		'Masuk sampai {late} · Pulang mulai {end}', 'Entrada até {late} · Saída a partir das {end}'),
	'k.hint': ('Hatudu QR kartaun membru ba scanner', 'Show your member card QR to the scanner',
		'Tempelkan QR kartu member ke scanner', 'Aproxime o QR do cartão de membro do leitor'),
	'k.rule': ('Scan dahuluk = tama · Scan ikus = sai', 'First scan = in · Last scan = out',
		'Scan pertama = masuk · Scan terakhir = pulang', 'Primeira leitura = entrada · Última leitura = saída'),
	'k.foot': ('Se ekran la responde, klik iha fatin ruma hodi scan fali.', 'If the screen does not respond, click anywhere and scan again.',
		'Jika layar tidak merespons, klik di mana saja lalu scan ulang.', 'Se o ecrã não responder, clique em qualquer lado e leia novamente.'),
	'k.cam_on': ('Uza kámera', 'Use camera', 'Pakai kamera', 'Usar câmara'),
	'k.cam_off': ('Taka kámera', 'Turn camera off', 'Matikan kamera', 'Desligar câmara'),
	'k.idle': ('Prontu atu scan kartaun', 'Ready to scan a card', 'Siap memindai kartu', 'Pronto para ler o cartão'),
	'k.srv_ok': ('Servidór konekta', 'Server connected', 'Server terhubung', 'Servidor ligado'),
	'k.srv_down': ('Servidór la konekta', 'Server disconnected', 'Server terputus', 'Servidor desligado'),
	'k.srv_net': ('Sai husi rede eskritóriu', 'Outside the office network', 'Di luar jaringan kantor', 'Fora da rede do escritório'),
	'k.scanner_wait': ('Scanner: scan kartaun ida atu teste', 'Scanner: scan a card to test', 'Scanner: scan kartu untuk tes', 'Leitor: leia um cartão para testar'),
	'k.scanner_ok': ('Scanner detekta', 'Scanner detected', 'Scanner terdeteksi', 'Leitor detetado'),
	'k.offline_title': ('La iha konexaun', 'No connection', 'Tidak ada koneksi', 'Sem ligação'),
	'k.offline_msg': ('Servidór absensia la konsege konekta. Koko fali.', 'The attendance server cannot be reached. Try again.',
		'Server absensi tidak bisa dihubungi. Coba lagi.', 'Não foi possível contactar o servidor de assiduidade. Tente novamente.'),
	'k.cam_err_title': ('Kámera la loke', 'Camera cannot be opened', 'Kamera tidak bisa dibuka', 'Não foi possível abrir a câmara'),
	'k.cam_err_msg': ('Fó lisensa ba kámera, no loke pájina liu husi HTTPS.', 'Allow camera access and open the page over HTTPS.',
		'Izinkan akses kamera, dan buka halaman lewat HTTPS.', 'Permita o acesso à câmara e abra a página por HTTPS.'),

	# ---------------------------------------------------------------- umum
	'th.name': ('Naran', 'Name', 'Nama', 'Nome'),
	'th.division': ('Diresaun', 'Division', 'Divisi', 'Direção'),
	'th.position': ('Pojisaun', 'Position', 'Jabatan', 'Cargo'),
	'th.in': ('Tama', 'In', 'Masuk', 'Entrada'),
	'th.out': ('Sai', 'Out', 'Pulang', 'Saída'),
	'th.note': ('Observasaun', 'Note', 'Keterangan', 'Observação'),
	'th.time': ('Oras', 'Time', 'Jam', 'Hora'),
	'th.person': ('Funsionáriu', 'Employee', 'Pegawai', 'Funcionário'),
	'th.result': ('Rezultadu', 'Result', 'Hasil', 'Resultado'),
	'th.message': ('Mensajen', 'Message', 'Pesan', 'Mensagem'),
	'th.device': ('Aparelu', 'Device', 'Perangkat', 'Dispositivo'),
	'th.status': ('Status', 'Status', 'Status', 'Estado'),
	'th.last_seen': ('Ativu ikus', 'Last active', 'Terakhir aktif', 'Última atividade'),
	'th.nrf': ('NRF', 'NRF', 'NRF', 'NRF'),
	'th.qr': ('Kartaun QR', 'QR card', 'Kartu QR', 'Cartão QR'),
	'th.emp': ('Funsionáriu', 'Employee', 'Pegawai', 'Funcionário'),
	'th.workdays': ('Loron servisu', 'Work days', 'Hari kerja', 'Dias úteis'),
	'th.present': ('Presensa', 'Present', 'Hadir', 'Presente'),
	'th.late': ('Tardi', 'Late', 'Terlambat', 'Atrasos'),
	'th.leave': ('Lisensa', 'Leave', 'Cuti', 'Licença'),
	'th.absent': ('Falta', 'Absent', 'Alpha', 'Faltas'),
	'st.active': ('Ativu', 'Active', 'Aktif', 'Ativo'),
	'st.inactive': ('La ativu', 'Inactive', 'Nonaktif', 'Inativo'),
	'none': ('La iha.', 'None.', 'Tidak ada.', 'Nenhum.'),
	'f.division': ('Diresaun', 'Division', 'Divisi', 'Direção'),
	'f.all_div': ('Diresaun hotu', 'All divisions', 'Semua divisi', 'Todas as direções'),
	'f.month': ('Fulan', 'Month', 'Bulan', 'Mês'),
	'f.year': ('Tinan', 'Year', 'Tahun', 'Ano'),
	'f.search': ('Buka naran ka NRF', 'Search name or NRF', 'Cari nama atau NRF', 'Pesquisar nome ou NRF'),
	'f.show': ('Hatudu', 'Show', 'Tampilkan', 'Mostrar'),
	# ---------------------------------------------------------------- hari ini
	'today.title': ('Absensia ohin', 'Attendance today', 'Absensi hari ini', 'Assiduidade de hoje'),
	'today.refresh': ('Pájina ne\'e atualiza an kada minutu 1', 'This page refreshes every minute',
		'Halaman ini menyegarkan diri tiap menit', 'Esta página atualiza-se a cada minuto'),
	'today.off': ('Ohin laos loron servisu: {reason}. Scan sei rejeita.', 'Today is not a workday: {reason}. Scans will be rejected.',
		'Hari ini bukan hari kerja: {reason}. Scan akan ditolak.', 'Hoje não é dia útil: {reason}. As leituras serão rejeitadas.'),
	'tile.scanned': ('Scan ona', 'Scanned', 'Sudah scan', 'Já leram'),
	'tile.late': ('Tardi', 'Late', 'Terlambat', 'Atrasos'),
	'tile.leave': ('Lisensa', 'On leave', 'Cuti', 'Em licença'),
	'tile.waiting': ('Seidauk scan', 'Not scanned yet', 'Belum scan', 'Ainda sem leitura'),
	'tile.total': ('Funsionáriu ativu', 'Active employees', 'Pegawai aktif', 'Funcionários ativos'),
	'tile.present': ('Presensa', 'Present', 'Hadir', 'Presente'),
	'tile.absent': ('Falta', 'Absent', 'Alpha', 'Faltas'),
	'today.t1': ('Scan ona ohin', 'Scanned today', 'Sudah scan hari ini', 'Leituras de hoje'),
	'today.t2': ('Scan ne\'ebé rejeita ka la tau-matan (foin dadauk)', 'Rejected or ignored scans (latest)',
		'Scan yang ditolak atau diabaikan (terbaru)', 'Leituras rejeitadas ou ignoradas (mais recentes)'),
	'today.empty': ('Seidauk iha scan.', 'No scans yet.', 'Belum ada scan.', 'Ainda sem leituras.'),
	'today.unknown': ('(QR la rekoñese)', '(unknown QR)', '(QR tidak dikenal)', '(QR desconhecido)'),
	'chip.ontime': ('Tuir oras', 'On time', 'Tepat waktu', 'Pontual'),
	'chip.late': ('Tardi', 'Late', 'Terlambat', 'Atrasado'),
	'chip.early': ('Sai sedu', 'Left early', 'Pulang awal', 'Saída antecipada'),

	# ---------------------------------------------------------------- perangkat
	'dev.title': ('Aparelu kiosk', 'Kiosk devices', 'Perangkat kiosk', 'Dispositivos do quiosque'),
	'dev.list_title': ('Lista aparelu', 'Device list', 'Daftar perangkat', 'Lista de dispositivos'),
	'dev.never': ('seidauk ativu', 'never', 'belum pernah', 'nunca'),
	'dev.disable': ('Lakon ativu', 'Disable', 'Nonaktifkan', 'Desativar'),
	'dev.enable': ('Ativa', 'Enable', 'Aktifkan', 'Ativar'),
	'dev.empty': ('Seidauk iha aparelu.', 'No devices yet.', 'Belum ada perangkat.', 'Ainda sem dispositivos.'),
	'dev.msg_on': ('Aparelu "{name}" ativa ona.', 'Device "{name}" enabled.', 'Perangkat "{name}" diaktifkan.', 'Dispositivo "{name}" ativado.'),
	'dev.msg_off': ('Aparelu "{name}" la ativu ona.', 'Device "{name}" disabled.', 'Perangkat "{name}" dinonaktifkan.', 'Dispositivo "{name}" desativado.'),

	# ---------------------------------------------------------------- kartu (admin)
	'cards.title': ('Kartaun QR funsionáriu', 'Employee QR cards', 'Kartu QR pegawai', 'Cartões QR dos funcionários'),
	'cards.sub': ('Hili funsionáriu, klik "Imprime kartaun hili", imprime oin no kotuk, depois marka katak imprime ona.',
		'Select employees, click "Print selected cards", print front and back, then mark them as printed.',
		'Pilih pegawai, klik "Cetak kartu terpilih", cetak depan dan belakang, lalu tandai sudah dicetak.',
		'Selecione os funcionários, clique em "Imprimir cartões selecionados", imprima frente e verso e marque como impressos.'),
	'cards.limit': ('Funsionáriu 500 primeiru de\'it mak hatudu. Hamenus ho filtru diresaun.', 'Only the first 500 employees are shown. Narrow down with the division filter.',
		'Hanya 500 pegawai pertama yang ditampilkan. Persempit dengan filter divisi.', 'Apenas os primeiros 500 funcionários são mostrados. Restrinja com o filtro de direção.'),
	'btn.print': ('Imprime kartaun hili', 'Print selected cards', 'Cetak kartu terpilih', 'Imprimir cartões selecionados'),
	'btn.printed': ('Marka imprime ona', 'Mark as printed', 'Tandai sudah dicetak', 'Marcar como impresso'),
	'btn.regen': ('Troka QR (kartaun lakon)', 'Replace QR (lost card)', 'Ganti QR (kartu hilang)', 'Substituir QR (cartão perdido)'),
	'btn.toggle': ('Ativu/la ativu', 'Enable/disable', 'Aktif/nonaktifkan', 'Ativar/desativar'),
	'btn.csv': ('Download CSV', 'Download CSV', 'Unduh CSV', 'Transferir CSV'),
	'btn.xlsx': ('Download Excel', 'Download Excel', 'Unduh Excel', 'Transferir Excel'),
	'regen.confirm': ('Kria QR foun ba funsionáriu hili? Kartaun tuan la válidu hanesan.', 'Create a new QR for the selected employees? Old cards stop working immediately.',
		'Buat QR baru untuk pegawai terpilih? Kartu lama langsung tidak berlaku.', 'Criar um novo QR para os funcionários selecionados? Os cartões antigos deixam de funcionar de imediato.'),
	'qr.none': ('Seidauk kria', 'Not created', 'Belum dibuat', 'Não criado'),
	'qr.off': ('La ativu', 'Inactive', 'Nonaktif', 'Inativo'),
	'qr.printed': ('Imprime ona', 'Printed', 'Sudah dicetak', 'Impresso'),
	'qr.notprinted': ('Seidauk imprime', 'Not printed', 'Belum dicetak', 'Por imprimir'),
	'cards.empty': ('La iha funsionáriu.', 'No employees.', 'Tidak ada pegawai.', 'Sem funcionários.'),
	'msg.select': ('Hili funsionáriu ida at leu.', 'Select at least one employee.', 'Pilih minimal satu pegawai.', 'Selecione pelo menos um funcionário.'),
	'msg.printed': ('Kartaun {n} marka imprime ona.', '{n} cards marked as printed.', '{n} kartu ditandai sudah dicetak.', '{n} cartões marcados como impressos.'),
	'msg.regen': ('QR foun kria ona. Kartaun tuan la válidu tan, favór imprime fali.', 'New QR created. The old card is no longer valid; please print again.',
		'QR baru dibuat. Kartu lama tidak berlaku lagi, silakan cetak ulang.', 'Novo QR criado. O cartão antigo deixou de ser válido; imprima novamente.'),
	'msg.toggle': ('Status kartaun troka ona.', 'Card status changed.', 'Status kartu diubah.', 'Estado do cartão alterado.'),
	'msg.noxlsx': ('Excel (.xlsx) seidauk disponivel. Instala openpyxl ka uza CSV.', 'Excel (.xlsx) is not available. Install openpyxl or use CSV.',
		'Excel (.xlsx) belum tersedia. Pasang openpyxl atau pakai CSV.', 'Excel (.xlsx) indisponível. Instale o openpyxl ou use CSV.'),

	# ---------------------------------------------------------------- halaman cetak kartu
	'cp.title': ('Imprime kartaun MDI', 'Print MDI cards', 'Cetak kartu MDI', 'Imprimir cartões MDI'),
	'cp.count': ('Kartaun {n}', '{n} cards', '{n} kartu', '{n} cartões'),
	'cp.both': ('Oin + kotuk', 'Front + back', 'Depan + belakang', 'Frente + verso'),
	'cp.front': ('Oin de\'it', 'Front only', 'Depan saja', 'Só frente'),
	'cp.back': ('Kotuk de\'it', 'Back only', 'Belakang saja', 'Só verso'),
	'cp.print': ('Imprime', 'Print', 'Cetak', 'Imprimir'),
	'cp.help': ('Hili folha 85,6 × 54 mm, la iha margem, eskala 100%. Ba printer sorin rua, hili "Oin + kotuk".',
		'Set paper to 85.6 × 54 mm, no margins, 100% scale. For duplex printers choose "Front + back".',
		'Atur kertas: ukuran 85,6 × 54 mm, tanpa margin, skala 100%. Untuk printer dua sisi, pilih "Depan + belakang".',
		'Defina o papel 85,6 × 54 mm, sem margens, escala 100%. Para impressoras frente e verso, escolha "Frente + verso".'),
	'cp.empty': ('La iha kartaun ativu atu imprime.', 'No active cards to print.', 'Tidak ada kartu aktif untuk dicetak.', 'Não há cartões ativos para imprimir.'),
	'cp.done_q': ('Imprime remata ona?', 'Finished printing?', 'Sudah selesai mencetak?', 'Terminou a impressão?'),
	'cp.mark': ('Marka imprime ona', 'Mark as printed', 'Tandai sudah dicetak', 'Marcar como impresso'),
	'cp.card_lang': ('Lian kartaun', 'Card language', 'Bahasa kartu', 'Idioma do cartão'),
	'card.kind': ('KARTAUN FUNSIONÁRIU', 'EMPLOYEE CARD', 'KARTU PEGAWAI', 'CARTÃO DE FUNCIONÁRIO'),
	'card.no': ('Nú.', 'No.', 'No.', 'N.º'),
	'card.valid': ('Válidu to\'o', 'Valid until', 'Berlaku s/d', 'Válido até'),
	'card.att': ('Kartaun absensia', 'Attendance card', 'Kartu absensi', 'Cartão de assiduidade'),
	'card.note': (
		'Kartaun ida-ne\'e ba funsionáriu Mata Dalan Institute. Scan QR iha mákina absensia bainhira tama no sai. '
		'Kartaun ne\'e pesoál, la bele empresta. Se hetan kartaun ida-ne\'e, favór fó fila ba eskritóriu Mata Dalan Institute.',
		'This card belongs to an employee of Mata Dalan Institute. Scan the QR at the attendance reader when you arrive and leave. '
		'The card is personal and must not be lent. If you find this card, please return it to the Mata Dalan Institute office.',
		'Kartu ini milik pegawai Mata Dalan Institute. Scan QR pada alat absensi saat masuk dan pulang. '
		'Kartu bersifat pribadi dan tidak boleh dipinjamkan. Jika menemukan kartu ini, harap kembalikan ke kantor Mata Dalan Institute.',
		'Este cartão pertence a um funcionário do Mata Dalan Institute. Leia o QR no leitor de assiduidade ao entrar e ao sair. '
		'O cartão é pessoal e não deve ser emprestado. Se encontrar este cartão, devolva-o ao escritório do Mata Dalan Institute.'),
	'card.scan': ('Scan bainhira tama no sai', 'Scan when entering & leaving', 'Scan saat masuk & pulang', 'Leia ao entrar e ao sair'),

	# ---------------------------------------------------------------- rekap
	'rep.title': ('Rezumu absensia', 'Attendance report', 'Rekap absensi', 'Relatório de assiduidade'),
	'rep.heading': ('ABSENSIA FULAN {month} TINAN {year}', 'ATTENDANCE FOR {month} {year}',
		'ABSENSI BULAN {month} TAHUN {year}', 'ASSIDUIDADE DE {month} DE {year}'),
	'rep.all': ('diresaun hotu', 'all divisions', 'semua divisi', 'todas as direções'),
	'legend.present': ('Presensa', 'Present', 'Hadir', 'Presente'),
	'legend.late': ('Presensa, tardi', 'Present, late', 'Hadir, terlambat', 'Presente, com atraso'),
	'legend.leave': ('Lisensa', 'Leave', 'Cuti', 'Licença'),
	'legend.absent': ('Falta', 'Absent', 'Alpha', 'Falta'),
	'legend.off': ('Feriadu / final semana', 'Holiday / weekend', 'Libur / akhir pekan', 'Feriado / fim de semana'),
	'legend.flags': ('↓ seidauk scan sai · ↑ sai sedu', '↓ no check-out yet · ↑ left early',
		'↓ belum scan pulang · ↑ pulang awal', '↓ sem saída registada · ↑ saída antecipada'),
	'grid.empty': ('La iha dadus.', 'No data.', 'Tidak ada data.', 'Sem dados.'),
	'grid.in': ('Tama', 'In', 'Masuk', 'Entrada'),
	'grid.out': ('Sai', 'Out', 'Pulang', 'Saída'),
	'csv.noout': ('Seidauk scan sai', 'No check-out', 'Belum scan pulang', 'Sem saída'),
	'csv.early': ('Sai sedu', 'Left early', 'Pulang awal', 'Saída antecipada'),
	# ---------------------------------------------------------------- laporan / dashboard
	'nav.dash': ('Dashboard absensia', 'Attendance dashboard', 'Dasbor absensi', 'Painel de assiduidade'),
	'nav.myreport': ('Relatoriu absensia', 'Attendance report', 'Laporan absensi', 'Relatório de assiduidade'),
	'menu.report': ('RELATORIU', 'REPORTS', 'LAPORAN', 'RELATÓRIOS'),
	'nav.tab': ('Tabular', 'Tabular', 'Tabular', 'Tabular'),
	'nav.chart': ('Grafiku', 'Charts', 'Grafik', 'Gráficos'),
	'dash.title': ('Dashboard relatoriu absensia', 'Attendance report dashboard', 'Dasbor laporan absensi', 'Painel de relatórios de assiduidade'),
	'dash.t_present': ('Loron presensa', 'Days present', 'Hari hadir', 'Dias de presença'),
	'dash.t_late': ('Loron tardi', 'Late days', 'Hari terlambat', 'Dias com atraso'),
	'dash.t_leave': ('Loron lisensa', 'Leave days', 'Hari cuti', 'Dias de licença'),
	'dash.t_absent': ('Loron falta', 'Absent days', 'Hari alpha', 'Dias de falta'),
	'dash.t_rate': ('Taxa presensa', 'Attendance rate', 'Tingkat kehadiran', 'Taxa de assiduidade'),
	'dash.by_div': ('Absensia tuir diresaun', 'Attendance by division', 'Absensi per divisi', 'Assiduidade por direção'),
	'dash.daily': ('Tendénsia loron-loron', 'Daily trend', 'Tren harian', 'Tendência diária'),
	'dash.late_top': ('Funsionáriu tardi barak liu', 'Most frequently late', 'Paling sering terlambat', 'Mais atrasos'),
	'dash.div_table': ('Rezumu tuir diresaun', 'Summary by division', 'Ringkasan per divisi', 'Resumo por direção'),
	'dash.chart_na': ('Gráfiku la disponivel (Highcharts seidauk karga).', 'Charts unavailable (Highcharts is not loaded).',
		'Grafik tidak tersedia (Highcharts belum dimuat).', 'Gráficos indisponíveis (Highcharts não carregado).'),
	'dash.no_late': ('La iha funsionáriu tardi.', 'Nobody was late.', 'Tidak ada yang terlambat.', 'Ninguém se atrasou.'),
	'th.rate': ('Taxa', 'Rate', 'Tingkat', 'Taxa'),
	'th.date': ('Data', 'Date', 'Tanggal', 'Data'),
	'tile.early': ('Sai sedu', 'Left early', 'Pulang awal', 'Saída antecipada'),
	'tile.noout': ('Seidauk scan sai', 'No check-out', 'Belum scan pulang', 'Sem saída'),
	'me.days': ('Lista loron-loron', 'Daily list', 'Daftar harian', 'Lista diária'),
	'me.year_chart': ('Absensia tinan {year}', 'Attendance in {year}', 'Absensi tahun {year}', 'Assiduidade em {year}'),
	'me.status_chart': ('Status fulan ne\'e', 'Status this month', 'Status bulan ini', 'Estado deste mês'),
	'me.filter': ('Filtru: {status}', 'Filter: {status}', 'Filter: {status}', 'Filtro: {status}'),
	'me.clear': ('Hamoos filtru', 'Clear filter', 'Hapus filter', 'Limpar filtro'),
	'me.empty': ('La iha loron ne\'ebé loos ho filtru ne\'e.', 'No days match this filter.', 'Tidak ada hari yang cocok dengan filter ini.', 'Nenhum dia corresponde a este filtro.'),
	'day.holiday': ('Feriadu', 'Holiday', 'Libur', 'Feriado'),
	'day.pending': ('Seidauk', 'Pending', 'Belum', 'Pendente'),
	'day.none': ('La iha dadus', 'No record', 'Tidak ada data', 'Sem registo'),
	'me.title': ('Ha\'u nia absensia', 'My attendance', 'Absensi saya', 'A minha assiduidade'),
	'me.unlinked': ('Ita-nia konta seidauk liga ba dadus funsionáriu. Kontaktu administradór.', 'Your account is not linked to an employee record. Contact the admin.',
		'Akun Anda belum dihubungkan ke data pegawai. Hubungi admin.', 'A sua conta ainda não está associada a um funcionário. Contacte o administrador.'),
	# ---------------------------------------------------------------- keamanan: pemasangan PC kiosk
	'res.device.title': ('PC ne\'e seidauk hanesan kiosk', 'This PC is not set up as a kiosk', 'PC ini belum dipasang sebagai kiosk', 'Este PC não está configurado como quiosque'),
	'res.device.msg': ('Husu administradór atu loke menu Aparelu kiosk iha PC ne\'e no klik "Halo PC ne\'e kiosk".',
		'Ask the admin to open the Kiosk devices menu on this PC and press "Make this PC the kiosk".',
		'Minta admin membuka menu Perangkat kiosk di PC ini dan menekan "Jadikan PC ini kiosk".',
		'Peça ao administrador para abrir o menu Dispositivos do quiosque neste PC e premir "Tornar este PC o quiosque".'),
	'res.throttle.title': ('Barak liu', 'Too many scans', 'Terlalu banyak scan', 'Demasiadas leituras'),
	'res.throttle.msg': ('Hein segundu balun no koko fali.', 'Wait a few seconds and try again.',
		'Tunggu beberapa detik lalu coba lagi.', 'Aguarde alguns segundos e tente novamente.'),
	'k.pair_title': ('PC ne\'e seidauk hanesan kiosk', 'This PC is not a kiosk yet', 'PC ini belum dipasang sebagai kiosk', 'Este PC ainda não é um quiosque'),
	'k.pair_desc': ('Administradór tenke login iha PC ne\'e, loke menu Aparelu kiosk no klik "Halo PC ne\'e kiosk". Dala ida de\'it.',
		'An admin must log in on this PC, open the Kiosk devices menu and press "Make this PC the kiosk". Only once.',
		'Admin perlu login di PC ini, membuka menu Perangkat kiosk, lalu menekan "Jadikan PC ini kiosk". Cukup sekali.',
		'Um administrador tem de iniciar sessão neste PC, abrir o menu Dispositivos do quiosque e premir "Tornar este PC o quiosque". Apenas uma vez.'),
	'k.srv_unpaired': ('PC seidauk hanesan kiosk', 'PC not paired', 'PC belum dipasangkan', 'PC não emparelhado'),
	'dev.sub': ('Scan simu de\'it husi PC kiosk ne\'ebé admin hanesan ona no husi rede eskritóriu. Husi uma la bele.',
		'Scans are accepted only from a kiosk PC the admin has paired, on the office network. From home it is not possible.',
		'Scan hanya diterima dari PC kiosk yang sudah dipasangkan admin dan dari jaringan kantor. Dari rumah tidak bisa.',
		'As leituras só são aceites de um PC de quiosque emparelhado pelo administrador e a partir da rede do escritório. De casa não é possível.'),
	'dev.pair_title': ('Halo PC ne\'e kiosk', 'Make this PC the kiosk', 'Jadikan PC ini kiosk', 'Tornar este PC o quiosque'),
	'dev.pair_desc': ('Halo ida-ne\'e iha PC odamatan, ho login admin. Browser ne\'e sei hetan sinál seguru (la bele lee husi JavaScript).',
		'Do this on the door PC while logged in as admin. This browser receives a secure marker (not readable by JavaScript).',
		'Lakukan di PC pintu sambil login sebagai admin. Browser ini menerima penanda aman (tidak bisa dibaca JavaScript).',
		'Faça isto no PC da entrada com sessão de administrador. Este navegador recebe um marcador seguro (não legível por JavaScript).'),
	'dev.pair_btn': ('Halo PC ne\'e kiosk', 'Make this PC the kiosk', 'Jadikan PC ini kiosk', 'Tornar este PC o quiosque'),
	'dev.pair_name': ('Naran PC (opsionál)', 'PC name (optional)', 'Nama PC (opsional)', 'Nome do PC (opcional)'),
	'dev.pair_default': ('PC kiosk', 'Kiosk PC', 'PC kiosk', 'PC do quiosque'),
	'dev.paired': ('PC ne\'e agora kiosk: {name}.', 'This PC is now a kiosk: {name}.', 'PC ini sekarang menjadi kiosk: {name}.', 'Este PC é agora um quiosque: {name}.'),
	'dev.this_yes': ('Browser ne\'e hanesan ona: {name}', 'This browser is paired as: {name}', 'Browser ini sudah dipasangkan sebagai: {name}', 'Este navegador está emparelhado como: {name}'),
	'dev.this_no': ('Browser ne\'e seidauk hanesan kiosk.', 'This browser is not paired as a kiosk.', 'Browser ini belum dipasangkan sebagai kiosk.', 'Este navegador não está emparelhado como quiosque.'),
	'sec.warn': ('Atensaun seguransa: ATTENDANCE_KIOSK_ALLOWED_IPS = [] loke scan husi internet tomak. Hasai setting ne\'e (rede privadu uza ho automátiku).',
		'Security notice: ATTENDANCE_KIOSK_ALLOWED_IPS = [] opens scanning to the whole internet. Remove that setting (private networks are used by default).',
		'Peringatan keamanan: ATTENDANCE_KIOSK_ALLOWED_IPS = [] membuka scan dari seluruh internet. Hapus setelan itu (jaringan privat dipakai secara bawaan).',
		'Aviso de segurança: ATTENDANCE_KIOSK_ALLOWED_IPS = [] abre a leitura a toda a internet. Remova essa definição (as redes privadas são usadas por omissão).'),
	# ---------------------------------------------------------------- jaringan kantor (VPS: IP publik)
	'res.net.msg': ('Scan simu de\'it husi rede eskritóriu. Husu administradór atu fó lisensa IP eskritóriu (menu Aparelu kiosk).',
		'Scans are only accepted from the office network. Ask the admin to allow the office IP (Kiosk devices menu).',
		'Scan hanya diterima dari jaringan kantor. Minta admin mengizinkan IP kantor (menu Perangkat kiosk).',
		'As leituras só são aceites a partir da rede do escritório. Peça ao administrador para permitir o IP do escritório (menu Dispositivos do quiosque).'),
	'net.title': ('Rede eskritóriu (IP públiku)', 'Office network (public IP)', 'Jaringan kantor (IP publik)', 'Rede do escritório (IP público)'),
	'net.sub': ('Iha VPS/internet, servidór haree IP públiku eskritóriu. Rejistu iha ne\'e atu scan bele halo husi eskritóriu de\'it.',
		'On a VPS/internet the server sees the office public IP. Register it here so scans work from the office only.',
		'Di VPS/internet, server melihat IP publik kantor. Daftarkan di sini agar scan hanya bisa dari kantor.',
		'Num VPS/internet, o servidor vê o IP público do escritório. Registe-o aqui para que as leituras só funcionem a partir do escritório.'),
	'net.mine': ('IP browser ne\'e (haree husi servidór): {ip}', 'This browser\'s IP as seen by the server: {ip}',
		'IP browser ini (terlihat oleh server): {ip}', 'IP deste navegador (visto pelo servidor): {ip}'),
	'net.ok': ('Simu ona', 'Accepted', 'Diterima', 'Aceite'),
	'net.no': ('Seidauk simu', 'Not accepted', 'Belum diterima', 'Não aceite'),
	'net.warn': ('Halo ida-ne\'e husi PC eskritóriu, la husi uma.', 'Do this from the office PC, not from home.',
		'Lakukan dari PC kantor, bukan dari rumah.', 'Faça isto a partir do PC do escritório, não de casa.'),
	'net.add_title': ('Aumenta IP/rede eskritóriu', 'Add office IP/network', 'Tambah IP/jaringan kantor', 'Adicionar IP/rede do escritório'),
	'net.cidr_ph': ('IP ka rede, ezemplu 114.4.20.10 ka 114.4.20.0/24', 'IP or range, e.g. 114.4.20.10 or 114.4.20.0/24',
		'IP atau rentang, mis. 114.4.20.10 atau 114.4.20.0/24', 'IP ou intervalo, ex.: 114.4.20.10 ou 114.4.20.0/24'),
	'net.note_ph': ('Nota (opsionál)', 'Note (optional)', 'Catatan (opsional)', 'Nota (opcional)'),
	'net.use_mine': ('Fó lisensa IP ne\'e', 'Allow this IP', 'Izinkan IP ini', 'Permitir este IP'),
	'net.add_btn': ('Aumenta', 'Add', 'Tambah', 'Adicionar'),
	'net.list_title': ('Rede ne\'ebé simu', 'Accepted networks', 'Jaringan yang diterima', 'Redes aceites'),
	'net.from_settings': ('Husi settings.py: {nets}', 'From settings.py: {nets}', 'Dari settings.py: {nets}', 'De settings.py: {nets}'),
	'net.empty': ('Seidauk iha IP eskritóriu rejistu.', 'No office IP registered yet.', 'Belum ada IP kantor terdaftar.', 'Ainda sem IP do escritório registado.'),
	'net.msg_added': ('IP/rede {cidr} simu ona.', '{cidr} is now accepted.', '{cidr} sekarang diterima.', '{cidr} é agora aceite.'),
	'net.msg_invalid': ('IP/rede la válidu.', 'Invalid IP/network.', 'IP/jaringan tidak valid.', 'IP/rede inválido.'),
	'net.msg_broad': ('Rede luan liu. Uza rede ki\'ik liu (minimu /16).', 'Network too broad. Use a smaller range (at least /16).',
		'Jaringan terlalu luas. Pakai rentang lebih kecil (minimal /16).', 'Rede demasiado ampla. Use um intervalo menor (pelo menos /16).'),
	'net.msg_on': ('{cidr} ativa ona.', '{cidr} enabled.', '{cidr} diaktifkan.', '{cidr} ativado.'),
	'net.msg_off': ('{cidr} la ativu ona.', '{cidr} disabled.', '{cidr} dinonaktifkan.', '{cidr} desativado.'),
	'dev.allow_ip': ('Fó lisensa mós IP eskritóriu ne\'e ({ip})', 'Also allow this office IP ({ip})',
		'Izinkan juga IP kantor ini ({ip})', 'Permitir também este IP do escritório ({ip})'),
	'th.cidr': ('IP / rede', 'IP / network', 'IP / jaringan', 'IP / rede'),
}

# daftar kunci yang dikirim ke JavaScript kiosk
KIOSK_PREFIXES = ('k.', 'res.net', 'res.device')


def tr(lang, key, **params):
	row = MESSAGES.get(key)
	if row is None:
		return key
	text = row[LANGS.index(norm(lang))] or row[1]
	if params:
		try:
			return text.format(**params)
		except (KeyError, IndexError, ValueError):
			return text
	return text


def reason_text(kind, name, lang):
	"""Alasan hari libur untuk ditampilkan. Nama libur dari database dipakai apa adanya."""
	if kind == 'weekend':
		return tr(lang, 'reason.weekend')
	return name or tr(lang, 'reason.' + kind)


def kiosk_bundle():
	"""Kamus untuk halaman kiosk: {lang: {key: teks}} + nama hari/bulan, agar ganti bahasa tanpa reload."""
	out = {}
	for i, lang in enumerate(LANGS):
		d = {k: v[i] for k, v in MESSAGES.items() if k.startswith(KIOSK_PREFIXES)}
		d['_weekdays'] = WEEKDAYS[lang]
		d['_months'] = MONTHS[lang]
		out[lang] = d
	return out


def note_for(lang):
	"""Teks belakang kartu: bisa diganti lewat ATTENDANCE_CARD_BACK_NOTE (string atau dict per bahasa)."""
	custom = getattr(settings, 'ATTENDANCE_CARD_BACK_NOTE', None)
	if isinstance(custom, dict):
		return custom.get(lang) or custom.get('en') or tr(lang, 'card.note')
	return custom or tr(lang, 'card.note')


def contact_for(lang):
	custom = getattr(settings, 'ATTENDANCE_CARD_CONTACT', '')
	if isinstance(custom, dict):
		return custom.get(lang) or custom.get('en') or ''
	return custom or ''
