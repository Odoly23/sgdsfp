(function () {
	'use strict';
	/* Grafik absensi (Highcharts). Satu permintaan JSON per halaman; tiap .qr-chart mengambil bagiannya (data-part). */
	var root = document.querySelector('.qr-charts');
	if (!root) return;
	var blocks = root.querySelectorAll('.qr-chart');
	var COLORS = { present: '#1f9d55', late: '#d98e04', leave: '#2563eb', absent: '#8b1a1a' };

	function message(el, text) { el.style.height = 'auto'; el.innerHTML = '<p class="text-muted mb-0">' + text + '</p>'; }
	if (!window.Highcharts) { blocks.forEach(function (b) { message(b, root.dataset.na); }); return; }

	var base = {
		chart: { backgroundColor: 'transparent', style: { fontFamily: 'inherit' }, spacing: [10, 6, 10, 6] },
		title: { text: null }, credits: { enabled: false }, exporting: { enabled: false },
		legend: { itemStyle: { fontWeight: '600' } }, tooltip: { shared: true },
		responsive: { rules: [{ condition: { maxWidth: 520 }, chartOptions: {
			legend: { layout: 'horizontal', align: 'center', verticalAlign: 'bottom', itemDistance: 8, itemStyle: { fontSize: '11px' } },
			yAxis: { title: { text: null } }
		} }] }
	};
	function colored(series) {
		return series.map(function (s) { return { name: s.name, data: s.data, color: COLORS[s.key] }; });
	}
	var yAxis = { min: 0, allowDecimals: false, title: { text: null } };
	var builders = {
		stack: function (d) {
			return { chart: { type: 'column' }, xAxis: { categories: d.label }, yAxis: yAxis,
				plotOptions: { column: { stacking: 'normal', borderRadius: 3 } }, series: colored(d.series) };
		},
		line: function (d) {
			return { chart: { type: 'line' }, xAxis: { categories: d.label, tickInterval: window.innerWidth < 520 ? 5 : 1 },
				yAxis: yAxis, plotOptions: { line: { marker: { radius: 3 } } }, series: colored(d.series) };
		},
		bar: function (d) {
			return { chart: { type: 'bar', height: Math.max(220, 46 * d.label.length + 80) }, xAxis: { categories: d.label },
				yAxis: yAxis, legend: { enabled: false },
				plotOptions: { bar: { borderRadius: 3, dataLabels: { enabled: true } } }, series: colored(d.series) };
		},
		pie: function (d) {
			return { chart: { type: 'pie' }, tooltip: { pointFormat: '<b>{point.y}</b> ({point.percentage:.0f}%)' },
				plotOptions: { pie: { showInLegend: true, size: '78%', dataLabels: { enabled: true, format: '{point.y}', distance: 8, filter: { property: 'y', operator: '>', value: 0 } } } },
				series: [{ name: '', data: d.label.map(function (n, i) { return { name: n, y: d.obj[i], color: COLORS[d.keys[i]] }; }) }] };
		}
	};
	builders.column = builders.stack;

	function isEmpty(kind, d) {
		if (kind === 'pie') return !d.obj.some(function (v) { return v > 0; });
		if (kind === 'bar') return d.label.length === 0;
		return !d.series.some(function (s) { return s.data.some(function (v) { return v; }); });
	}
	function draw(el, data) {
		var kind = el.dataset.kind;
		if (!data) { message(el, root.dataset.empty); return; }
		if (isEmpty(kind, data)) { message(el, kind === 'bar' ? root.dataset.emptyLate : root.dataset.empty); return; }
		Highcharts.chart(el, Highcharts.merge(base, builders[kind](data)));
	}

	fetch(root.dataset.url, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
		.then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
		.then(function (data) { blocks.forEach(function (b) { draw(b, data[b.dataset.part]); }); })
		.catch(function () { blocks.forEach(function (b) { message(b, root.dataset.na); }); });
})();
