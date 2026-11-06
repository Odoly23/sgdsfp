(function () {
	'use strict';
	/* Kiosk absensi. Tanpa kunci yang diketik: PC ini dikenali dari cookie HttpOnly yang dipasang admin.
	   Semua teks dari server dimasukkan lewat textContent / elemen DOM (bukan innerHTML) agar tidak ada celah XSS. */
	var body = document.body;
	var scanUrl = body.dataset.scanUrl, checkUrl = body.dataset.checkUrl, tz = body.dataset.tz;
	var bundle = JSON.parse(document.getElementById('i18n').textContent);
	var LANGS = Object.keys(bundle);
	var lang = LANGS.indexOf(document.documentElement.lang) >= 0 ? document.documentElement.lang : LANGS[0];
	var setup = document.getElementById('setup');
	var result = document.getElementById('result');
	var busy = false, hideTimer = null, audio = null, idleShown = true;

	/* ---------- bahasa: ganti langsung tanpa memuat ulang ---------- */
	function t(key, params) {
		var s = (bundle[lang] && bundle[lang][key]) || (bundle.en && bundle.en[key]) || key;
		if (params) Object.keys(params).forEach(function (k) { s = s.split('{' + k + '}').join(params[k]); });
		return s;
	}
	function applyLang() {
		document.documentElement.lang = lang;
		var p = { late: body.dataset.late, end: body.dataset.end };
		document.querySelectorAll('[data-i18n]').forEach(function (el) { el.textContent = t(el.dataset.i18n, p); });
		document.querySelectorAll('.qr-flag').forEach(function (b) { b.classList.toggle('on', b.dataset.lang === lang); });
		if (idleShown) idle();
		renderChips();
		tick();
	}
	function setLang(l) {
		lang = l;
		document.cookie = 'mdi_lang=' + l + '; path=/; max-age=31536000; SameSite=Lax' + (location.protocol === 'https:' ? '; Secure' : '');
		applyLang();
	}
	document.querySelectorAll('.qr-flag').forEach(function (b) {
		b.addEventListener('click', function (ev) { ev.stopPropagation(); setLang(b.dataset.lang); });
	});

	/* ---------- jam (zona waktu kantor) ---------- */
	var parts = new Intl.DateTimeFormat('en-GB', { timeZone: tz, hour12: false, weekday: 'short', year: 'numeric',
		month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
	var DOW = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };
	var clockEl = document.getElementById('clock'), dateEl = document.getElementById('today');
	function tick() {
		if (document.hidden) return;
		var o = {};
		parts.formatToParts(new Date()).forEach(function (p) { o[p.type] = p.value; });
		clockEl.textContent = (o.hour === '24' ? '00' : o.hour) + ':' + o.minute + ':' + o.second;
		var b = bundle[lang];
		dateEl.textContent = b._weekdays[DOW[o.weekday]] + ', ' + parseInt(o.day, 10) + ' ' + b._months[parseInt(o.month, 10) - 1] + ' ' + o.year;
	}
	setInterval(tick, 1000);

	/* ---------- suara ---------- */
	function beep(pattern) {
		try {
			audio = audio || new (window.AudioContext || window.webkitAudioContext)();
			var now = audio.currentTime;
			pattern.forEach(function (p) {
				var o = audio.createOscillator(), g = audio.createGain();
				o.frequency.value = p[0]; o.connect(g); g.connect(audio.destination);
				g.gain.setValueAtTime(0.2, now + p[1]); g.gain.setValueAtTime(0, now + p[1] + p[2]);
				o.start(now + p[1]); o.stop(now + p[1] + p[2] + 0.02);
			});
		} catch (e) {}
	}
	var SOUND = { success: [[880, 0, .15]], warn: [[660, 0, .15], [660, .22, .15]], leave: [[740, 0, .2]],
				  info: [[520, 0, .12]], error: [[220, 0, .5]] };

	/* ---------- hasil scan (dibangun dengan DOM, bukan innerHTML) ---------- */
	function node(tag, cls, text) {
		var e = document.createElement(tag);
		if (cls) e.className = cls;
		if (text != null) e.textContent = text;
		return e;
	}
	function show(res) {
		idleShown = false;
		var e = res.employee, card = node('div', 'k-card');
		if (e && e.photo && /^(\/|https?:)/.test(e.photo)) {
			var img = node('img', 'k-photo'); img.alt = ''; img.src = e.photo; card.appendChild(img);
		}
		card.appendChild(node('div', 'k-title', res.title));
		if (e) {
			card.appendChild(node('div', 'k-name', e.name));
			card.appendChild(node('div', 'k-meta', [e.position, e.division].filter(Boolean).join(' · ')));
		}
		card.appendChild(node('div', 'k-msg', res.message));
		if (res.flag === 'burst') card.appendChild(node('div', 'k-flag', '\u2691 ' + t('k.flag_burst')));   // scan beruntun: ditandai
		pushRecent(res);
		result.className = 'k-result ' + (['success', 'warn', 'leave', 'info', 'error'].indexOf(res.level) >= 0 ? res.level : 'info');
		result.replaceChildren(card);
		beep(SOUND[res.level] || SOUND.info);
		clearTimeout(hideTimer);
		hideTimer = setTimeout(idle, (res.level === 'error' || res.level === 'leave') ? 7000 : 4500);
	}
	/* daftar scan terakhir (hanya di layar ini, tidak disimpan): orang di dekat pintu mudah melihat siapa saja yang baru scan */
	var recent = [], recentBox = document.getElementById('recent-box'), recentList = document.getElementById('recent');
	var EVENTS = ['in', 'in_late', 'out', 'out_early'];
	function pushRecent(res) {
		if (!res.ok || !res.employee || EVENTS.indexOf(res.code) < 0) return;
		recent.unshift({ time: clockEl.textContent.slice(0, 5), name: res.employee.name, flag: res.flag === 'burst' });
		recent = recent.slice(0, 5);
		var items = recent.map(function (r) {
			var li = node('li', r.flag ? 'flag' : '');
			li.appendChild(node('time', '', r.time));
			li.appendChild(node('span', '', (r.flag ? '\u2691 ' : '\u2713 ') + r.name));
			return li;
		});
		recentList.replaceChildren.apply(recentList, items);
		recentBox.hidden = false;
	}

	function idle() {
		idleShown = true;
		result.className = 'k-result';
		var box = node('div', 'k-idle');
		box.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">' +
			'<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/>' +
			'<rect x="7" y="7" width="4" height="4"/><rect x="13" y="7" width="4" height="4"/><rect x="7" y="13" width="4" height="4"/>' +
			'<path d="M13 13h4v4"/></svg>';        // konstanta, bukan data dari server
		box.appendChild(document.createTextNode(t('k.idle')));
		result.replaceChildren(box);
	}

	/* ---------- kirim scan ---------- */
	function send(code) {
		code = (code || '').trim();
		if (!code || busy) return;
		busy = true;
		fetch(scanUrl, { method: 'POST', credentials: 'same-origin',
						 headers: { 'Content-Type': 'application/json', 'X-Kiosk': '1' },
						 body: JSON.stringify({ code: code, lang: lang }) })
			.then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); })
			.then(function (r) {
				if (r.status === 401) { state.srv = 'unpaired'; needPair(true); }
				else if (r.status === 403) state.srv = 'net';
				else if (r.status < 500) state.srv = 'ok';
				renderChips();
				if (r.body && r.body.title) show(r.body);
			})
			.catch(function () {
				state.srv = 'down'; renderChips();
				show({ level: 'error', title: t('k.offline_title'), message: t('k.offline_msg') });
			})
			.then(function () { busy = false; });
	}

	/* ---------- alat pembaca QR (USB): langsung jalan tanpa fokus ----------
	   Pembaca bekerja seperti keyboard: mengetik kode sangat cepat lalu Enter. Semua tombol ditangkap di sini, jadi
	   tidak perlu mengklik apa pun. Ketikan manusia (lambat) dibuang. Alat berakhiran Tab atau tanpa akhiran pun terbaca:
	   kode berbentuk MDI.x.y.<32 hex> dikirim otomatis setelah jeda singkat. */
	var buf = '', lastKey = 0, idleTimer = null, scannerSeen = false;
	var CODE_RE = /MDI\.\d+\.\d+\.[0-9a-f]{32}(?![0-9a-z])/i;      // tanpa membedakan huruf: Caps Lock; awalan/akhiran alat diabaikan
	function flush() {
		var v = buf; buf = ''; clearTimeout(idleTimer);
		if (!v) return;
		scannerSeen = true; renderChips();
		var m = v.match(CODE_RE);
		send(m ? m[0] : v);
	}
	document.addEventListener('keydown', function (ev) {
		if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
		if (ev.key === 'Enter') { if (buf) { ev.preventDefault(); flush(); } return; }
		if (ev.key.length !== 1) return;
		var now = Date.now();
		if (now - lastKey > 200) buf = '';
		lastKey = now; buf += ev.key; ev.preventDefault();
		if (buf.length > 200) { buf = ''; return; }
		clearTimeout(idleTimer);
		idleTimer = setTimeout(function () { if (CODE_RE.test(buf)) flush(); else buf = ''; }, 250);
	});

	/* ---------- status koneksi ---------- */
	var state = { srv: 'wait' };
	function chip(id, cls, key) {
		var el = document.getElementById(id);
		el.className = 'k-chip ' + cls; el.textContent = t(key);
	}
	function renderChips() {
		if (state.srv === 'ok') chip('st-srv', 'ok', 'k.srv_ok');
		else if (state.srv === 'net') chip('st-srv', 'err', 'k.srv_net');
		else if (state.srv === 'unpaired') chip('st-srv', 'err', 'k.srv_unpaired');
		else chip('st-srv', '', 'k.srv_down');
		if (scannerSeen) chip('st-scn', 'ok', 'k.scanner_ok'); else chip('st-scn', '', 'k.scanner_wait');
	}
	function needPair(on) { setup.classList.toggle('on', on); }
	function check() {
		if (document.hidden) return;
		fetch(checkUrl, { credentials: 'same-origin', cache: 'no-store' }).then(function (r) {
			if (r.status === 200) { state.srv = 'ok'; needPair(false); }
			else if (r.status === 401) { state.srv = 'unpaired'; needPair(true); }
			else if (r.status === 403) state.srv = 'net';
			renderChips();
		}).catch(function () { state.srv = 'down'; renderChips(); });
	}
	setInterval(check, 30000);
	document.addEventListener('visibilitychange', function () { if (!document.hidden) { tick(); check(); } });

	/* ---------- kamera: hanya jika server mengizinkan (ATTENDANCE_ALLOW_CAMERA) dan browser mendukung ---------- */
	var camBtn = document.getElementById('btn-cam'), video = document.getElementById('cam');
	if (body.dataset.camera === '1' && window.BarcodeDetector && navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
		camBtn.style.display = '';
		var stream = null, timer = null, last = '', lastAt = 0, detector = new BarcodeDetector({ formats: ['qr_code'] });
		camBtn.addEventListener('click', function (ev) {
			ev.stopPropagation();
			if (stream) {
				clearInterval(timer); stream.getTracks().forEach(function (tr) { tr.stop(); });
				stream = null; video.style.display = 'none'; camBtn.dataset.i18n = 'k.cam_on'; camBtn.textContent = t('k.cam_on'); return;
			}
			navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } }).then(function (s) {
				stream = s; video.srcObject = s; video.play(); video.style.display = 'block';
				camBtn.dataset.i18n = 'k.cam_off'; camBtn.textContent = t('k.cam_off');
				timer = setInterval(function () {
					if (video.readyState < 2) return;
					detector.detect(video).then(function (codes) {
						if (!codes.length) return;
						var v = codes[0].rawValue, now = Date.now();
						if (v === last && now - lastAt < 4000) return;
						last = v; lastAt = now; send(v);
					}).catch(function () {});
				}, 350);
			}).catch(function () {
				show({ level: 'error', title: t('k.cam_err_title'), message: t('k.cam_err_msg') });
			});
		});
	}

	applyLang();
	check();
})();
