(function () {
	'use strict';
	var KEY_STORE = 'mdiKioskKey';
	var body = document.body;
	var scanUrl = body.dataset.scanUrl, checkUrl = body.dataset.checkUrl, tz = body.dataset.tz;
	var langBase = body.dataset.langBase;
	var bundle = JSON.parse(document.getElementById('i18n').textContent);
	var LANGS = Object.keys(bundle);
	var lang = LANGS.indexOf(document.documentElement.lang) >= 0 ? document.documentElement.lang : LANGS[0];
	var setup = document.getElementById('setup'), setupErr = document.getElementById('setup-err');
	var result = document.getElementById('result');
	var busy = false, hideTimer = null, audio = null, idleShown = true, setupNeeded = false;

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
		document.querySelectorAll('[data-i18n-ph]').forEach(function (el) { el.placeholder = t(el.dataset.i18nPh); });
		document.querySelectorAll('.qr-flag').forEach(function (b) { b.classList.toggle('on', b.dataset.lang === lang); });
		if (idleShown) idle();
		renderChips();
		tick();
	}
	function setLang(l) {
		lang = l;
		document.cookie = 'mdi_lang=' + l + '; path=/; max-age=31536000; SameSite=Lax';   // dibaca server untuk halaman lain
		applyLang();
	}
	document.querySelectorAll('.qr-flag').forEach(function (b) {
		b.addEventListener('click', function (ev) { ev.stopPropagation(); setLang(b.dataset.lang); });
	});

	function getKey() { try { return localStorage.getItem(KEY_STORE) || ''; } catch (e) { return ''; } }
	function setKey(k) { try { localStorage.setItem(KEY_STORE, k); } catch (e) {} }
	function clearKey() { try { localStorage.removeItem(KEY_STORE); } catch (e) {} }

	/* ---------- jam (zona waktu kantor) ---------- */
	var parts = new Intl.DateTimeFormat('en-GB', { timeZone: tz, hour12: false, weekday: 'short', year: 'numeric',
		month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
	var DOW = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };
	function tick() {
		var o = {};
		parts.formatToParts(new Date()).forEach(function (p) { o[p.type] = p.value; });
		var hh = o.hour === '24' ? '00' : o.hour;
		document.getElementById('clock').textContent = hh + ':' + o.minute + ':' + o.second;
		var b = bundle[lang];
		document.getElementById('today').textContent =
			b._weekdays[DOW[o.weekday]] + ', ' + parseInt(o.day, 10) + ' ' + b._months[parseInt(o.month, 10) - 1] + ' ' + o.year;
	}
	tick(); setInterval(tick, 1000);

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

	/* ---------- hasil scan ---------- */
	function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }
	function show(res) {
		idleShown = false;
		var e = res.employee, html = '<div class="k-card">';
		if (e && e.photo) html += '<img class="k-photo" src="' + esc(e.photo) + '" alt="">';
		html += '<div class="k-title">' + esc(res.title) + '</div>';
		if (e) {
			html += '<div class="k-name">' + esc(e.name) + '</div>';
			html += '<div class="k-meta">' + esc([e.position, e.division].filter(Boolean).join(' · ')) + '</div>';
		}
		html += '<div class="k-msg">' + esc(res.message) + '</div></div>';
		result.className = 'k-result ' + (res.level || 'info');
		result.innerHTML = html;
		beep(SOUND[res.level] || SOUND.info);
		clearTimeout(hideTimer);
		hideTimer = setTimeout(idle, (res.level === 'error' || res.level === 'leave') ? 7000 : 4500);
	}
	function idle() {
		idleShown = true;
		result.className = 'k-result';
		result.innerHTML = '<div class="k-idle"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4">' +
			'<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/>' +
			'<rect x="7" y="7" width="4" height="4"/><rect x="13" y="7" width="4" height="4"/><rect x="7" y="13" width="4" height="4"/>' +
			'<path d="M13 13h4v4"/></svg>' + esc(t('k.idle')) + '</div>';
	}

	function send(code) {
		code = (code || '').trim();
		if (!code || busy) return;
		busy = true;
		var headers = { 'Content-Type': 'application/json' };
		if (getKey()) headers['X-Kiosk-Key'] = getKey();
		fetch(scanUrl, { method: 'POST', headers: headers, body: JSON.stringify({ code: code, lang: lang }) })
			.then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); })
			.then(function (r) {
				if (r.status === 401) { clearKey(); needKey(true); }
				if (r.status === 403) state.srv = 'net';
				else if (r.status < 500) state.srv = 'ok';
				renderChips();
				show(r.body);
			})
			.catch(function () {
				state.srv = 'down'; renderChips();
				show({ level: 'error', title: t('k.offline_title'), message: t('k.offline_msg') });
			})
			.then(function () { busy = false; });
	}

	/* ---------- scanner USB: langsung jalan tanpa fokus ----------
	   Scanner bekerja seperti keyboard: mengetik kode sangat cepat lalu Enter. Semua tombol ditangkap di sini,
	   jadi tidak perlu mengklik apa pun. Ketikan manusia (lambat) dibuang. Tanpa Enter pun, kode berbentuk
	   MDI.x.y.<32 hex> dikirim otomatis setelah jeda singkat. */
	var buf = '', lastKey = 0, idleTimer = null, scannerSeen = false;
	var CODE_RE = /^MDI\.\d+\.\d+\.[0-9a-f]{32}$/;
	function flush() {
		var v = buf; buf = ''; clearTimeout(idleTimer);
		if (!v) return;
		scannerSeen = true; renderChips();
		send(v);
	}
	document.addEventListener('keydown', function (ev) {
		if (setup.classList.contains('on')) return;
		if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
		if (ev.key === 'Enter') { if (buf) { ev.preventDefault(); flush(); } return; }
		if (ev.key.length !== 1) return;
		var now = Date.now();
		if (now - lastKey > 200) buf = '';
		lastKey = now; buf += ev.key; ev.preventDefault();
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
		else if (state.srv === 'down') chip('st-srv', 'err', 'k.srv_down');
		else chip('st-srv', '', 'k.srv_down');
		if (scannerSeen) chip('st-scn', 'ok', 'k.scanner_ok'); else chip('st-scn', '', 'k.scanner_wait');
	}
	function check() {
		var headers = {};
		if (getKey()) headers['X-Kiosk-Key'] = getKey();
		fetch(checkUrl, { headers: headers, cache: 'no-store' }).then(function (r) {
			if (r.status === 200) { state.srv = 'ok'; needKey(false); }
			else if (r.status === 403) state.srv = 'net';
			else if (r.status === 401) { state.srv = 'ok'; needKey(true); }
			renderChips();
		}).catch(function () { state.srv = 'down'; renderChips(); });
	}
	setInterval(check, 15000);

	/* ---------- kunci perangkat: hanya muncul bila server mewajibkannya ---------- */
	var keyBtn = document.getElementById('btn-key');
	function needKey(on) {
		setupNeeded = on;
		keyBtn.style.display = on ? '' : 'none';
		if (on && !setup.classList.contains('on')) openSetup('');
		if (!on) setup.classList.remove('on');
	}
	function openSetup(msg) {
		setupErr.textContent = msg || '';
		setup.classList.add('on');
		document.getElementById('setup-key').focus();
	}
	document.getElementById('setup-form').addEventListener('submit', function (ev) {
		ev.preventDefault();
		var k = document.getElementById('setup-key').value.trim();
		fetch(checkUrl, { headers: { 'X-Kiosk-Key': k } }).then(function (r) {
			if (r.ok) { setKey(k); setup.classList.remove('on'); setupNeeded = false; check(); }
			else setupErr.textContent = t('k.setup_bad');
		}).catch(function () { setupErr.textContent = t('k.setup_unreach'); });
	});
	keyBtn.addEventListener('click', function (ev) { ev.stopPropagation(); openSetup(''); });

	/* ---------- kamera (cadangan): hanya jika browser mendukung BarcodeDetector ---------- */
	var camBtn = document.getElementById('btn-cam'), video = document.getElementById('cam');
	if (window.BarcodeDetector && navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
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
