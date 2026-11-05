from django import template
from django.utils.html import format_html_join, escape
from django.utils.safestring import mark_safe

from django.urls import reverse

from attendance import qr_i18n as i18n
from attendance import qr_service as svc

register = template.Library()

_STAR = '1.10,10.00 2.85,9.38 2.90,7.53 4.02,9.00 5.80,8.47 4.75,10.00 5.80,11.53 4.02,11.00 2.90,12.47 2.85,10.62'

_SVG = '<svg viewBox="0 0 {w} 20" width="30" height="20" preserveAspectRatio="xMidYMid slice" aria-hidden="true">{body}</svg>'
FLAG_SVG = {
	'tet': _SVG.format(w=30, body=(
		'<rect width="30" height="20" fill="#DC241F"/><polygon points="0,0 15,10 0,20" fill="#FFC726"/>'
		'<polygon points="0,0 10,10 0,20" fill="#000"/><polygon points="%s" fill="#fff"/>' % _STAR)),
	'en': _SVG.format(w=40, body=(
		'<rect width="40" height="20" fill="#012169"/>'
		'<path d="M0,0 L40,20 M40,0 L0,20" stroke="#fff" stroke-width="4"/>'
		'<path d="M0,0 L40,20 M40,0 L0,20" stroke="#C8102E" stroke-width="1.5"/>'
		'<path d="M20,0 V20 M0,10 H40" stroke="#fff" stroke-width="6.5"/>'
		'<path d="M20,0 V20 M0,10 H40" stroke="#C8102E" stroke-width="4"/>')),
	'id': _SVG.format(w=30, body=(
		'<rect width="30" height="10" fill="#E70011"/><rect y="10" width="30" height="10" fill="#fff"/>')),
	'pt': _SVG.format(w=30, body=(
		'<rect width="30" height="20" fill="#FF0000"/><rect width="12" height="20" fill="#006600"/>'
		'<circle cx="12" cy="10" r="3.8" fill="#FFE000"/><rect x="10.2" y="8.2" width="3.6" height="4" rx="1" fill="#fff" stroke="#FF0000" stroke-width=".7"/>')),
}


def _lang(context):
	return i18n.get_lang(context.get('request'))


@register.simple_tag(takes_context=True)
def t(context, key, **params):
	"""Teks sesuai bahasa pilihan: {% t "nav.today" %} atau {% t "msg.printed" n=3 %}."""
	return i18n.tr(_lang(context), key, **params)


@register.simple_tag(takes_context=True)
def t4(context, key, **params):
	"""Keempat bahasa sekaligus (span data-l), untuk teks yang bahasanya diganti langsung di browser."""
	cur = _lang(context)
	rows = [(code, '' if code == cur else ' hidden', i18n.tr(code, key, **params)) for code in i18n.LANGS]
	return format_html_join('', '<span data-l="{}"{}>{}</span>', rows)


@register.simple_tag(takes_context=True)
def lang_code(context):
	return _lang(context)


@register.inclusion_tag('attendance/qr/_flags.html', takes_context=True)
def flags(context, mode='link'):
	"""Tombol bendera. mode='link' muat ulang halaman (halaman server), mode='js' ganti langsung di browser."""
	request = context.get('request')
	cur = _lang(context)
	return {
		'items': [{'code': c, 'name': i18n.LANG_NAMES[c], 'svg': mark_safe(FLAG_SVG[c]), 'on': c == cur} for c in i18n.LANGS],
		'mode': mode, 'next': request.get_full_path() if request is not None else '/',
	}


def _is_viewer(context):
	request = context.get('request')
	return request is not None and svc.group_of(request.user) in svc.conf('VIEWER_GROUPS')


@register.simple_tag(takes_context=True)
def qr_tab_url(context):
	"""Tujuan menu "Tabular": dashboard untuk pimpinan, laporan pribadi untuk pegawai."""
	return reverse('qr-dash' if _is_viewer(context) else 'qr-me')


@register.simple_tag(takes_context=True)
def qr_chart_url(context):
	"""Tujuan menu "Grafiku"."""
	return reverse('qr-dash-chart' if _is_viewer(context) else 'qr-me-chart')
