(function () {
	'use strict';
	var KEY_STORE = 'mdiKioskKey';
	var body = document.body;
	var scanUrl = body.dataset.scanUrl, checkUrl = body.dataset.checkUrl, tz = body.dataset.tz;
	var input = document.getElementById('scan-input');
	var setup = document.getElementById('setup'), setupErr = document.getElementById('setup-err');
	var result = document.getElementById('result');
	var busy = false, hideTimer = null, audio = null;

	function getKey() { try { return localStorage.getItem(KEY_STORE) || ''; } catch (e) { return ''; } }
	function setKey(k) { try { localStorage.setItem(KEY_STORE, k); } catch (e) {} }
	function clearKey() { try { localStorage.removeItem(KEY_STORE); } catch (e) {} }

	/* ---- jam ---- */
	var fTime = new Intl.DateTimeFormat('id-ID', { timeZone: tz, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
	var fDate = new Intl.DateTimeFormat('id-ID', { timeZone: tz, weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
	function tick() {
		var n = new Date();
		document.getElementById('clock').textContent = fTime.format(n).replace(/\./g, ':');
		document.getElementById('today').textContent = fDate.format(n);
	}
	tick(); setInterval(tick, 1000);

	/* ---- suara ---- */
	function beep(pattern) {
		try {
			audio = audio || new (window.AudioContext || window.webkitAudioContext)();
			var t = audio.currentTime;
			pattern.forEach(function (p) {
				var o = audio.createOscillator(), g = audio.createGain();
				o.frequency.value = p[0]; o.connect(g); g.connect(audio.destination);
				g.gain.setValueAtTime(0.2, t + p[1]); g.gain.setValueAtTime(0, t + p[1] + p[2]);
				o.start(t + p[1]); o.stop(t + p[1] + p[2] + 0.02);
			});
		} catch (e) {}
	}
	var SOUND = { success: [[880, 0, .15]], warn: [[660, 0, .15], [660, .22, .15]], leave: [[740, 0, .2]],
				  info: [[520, 0, .12]], error: [[220, 0, .5]] };

	/* ---- tampilan hasil ---- */
	function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }
	function show(res) {
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
		result.className = 'k-result';
		result.innerHTML = '<div class="k-idle"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4">' +
			'<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/>' +
			'<rect x="7" y="7" width="4" height="4"/><rect x="13" y="7" width="4" height="4"/><rect x="7" y="13" width="4" height="4"/>' +
			'<path d="M13 13h4v4"/></svg>Siap memindai kartu</div>';
	}
	idle();

	/* ---- kirim scan ---- */
	function send(code) {
		code = (code || '').trim();
		if (!code || busy) return;
		busy = true;
		fetch(scanUrl, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Kiosk-Key': getKey() },
						 body: JSON.stringify({ code: code }) })
			.then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); })
			.then(function (r) {
				if (r.status === 401) { clearKey(); openSetup(r.body.message); return; }
				show(r.body);
			})
			.catch(function () {
				show({ level: 'error', title: 'Tidak ada koneksi', message: 'Server absensi tidak bisa dihubungi. Coba lagi.' });
			})
			.then(function () { busy = false; });
	}

	/* ---- scanner USB: mengetik kode lalu Enter ---- */
	input.addEventListener('keydown', function (ev) {
		if (ev.key === 'Enter') { ev.preventDefault(); var v = input.value; input.value = ''; send(v); }
	});
	function focusInput() { if (!setup.classList.contains('on')) input.focus(); }
	document.addEventListener('click', focusInput);
	setInterval(focusInput, 1000);

	/* ---- kunci perangkat ---- */
	function openSetup(msg) {
		setupErr.textContent = msg || '';
		setup.classList.add('on');
		document.getElementById('setup-key').focus();
	}
	document.getElementById('setup-form').addEventListener('submit', function (ev) {
		ev.preventDefault();
		var k = document.getElementById('setup-key').value.trim();
		fetch(checkUrl, { headers: { 'X-Kiosk-Key': k } }).then(function (r) {
			if (r.ok) { setKey(k); setup.classList.remove('on'); focusInput(); }
			else setupErr.textContent = 'Kunci salah atau perangkat dinonaktifkan.';
		}).catch(function () { setupErr.textContent = 'Server tidak bisa dihubungi.'; });
	});
	document.getElementById('btn-key').addEventListener('click', function (ev) { ev.stopPropagation(); openSetup(''); });
	if (!getKey()) openSetup('');

	/* ---- kamera (cadangan): hanya jika browser mendukung BarcodeDetector ---- */
	var camBtn = document.getElementById('btn-cam'), video = document.getElementById('cam');
	if (window.BarcodeDetector && navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
		camBtn.style.display = '';
		var stream = null, timer = null, last = '', lastAt = 0, detector = new BarcodeDetector({ formats: ['qr_code'] });
		camBtn.addEventListener('click', function (ev) {
			ev.stopPropagation();
			if (stream) {
				clearInterval(timer); stream.getTracks().forEach(function (t) { t.stop(); });
				stream = null; video.style.display = 'none'; camBtn.textContent = 'Pakai kamera'; return;
			}
			navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } }).then(function (s) {
				stream = s; video.srcObject = s; video.play(); video.style.display = 'block'; camBtn.textContent = 'Matikan kamera';
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
				show({ level: 'error', title: 'Kamera tidak bisa dibuka', message: 'Izinkan akses kamera, dan buka halaman lewat HTTPS.' });
			});
		});
	}
})();
