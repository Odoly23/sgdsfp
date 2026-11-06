from django.urls import path
from . import views_qr as v

urlpatterns = [
	# halaman kiosk (tanpa login; scan hanya dari jaringan kantor dan PC yang dipasangkan admin)
	path('kiosk/', v.kiosk, name="qr-kiosk"),
	path('kiosk/scan/', v.kiosk_scan, name="qr-kiosk-scan"),
	path('kiosk/check/', v.kiosk_check, name="qr-kiosk-check"),

	path('lang/<str:code>/', v.set_lang, name="qr-lang"),

	path('today/', v.today, name="qr-today"),

	path('devices/', v.devices, name="qr-devices"),
	path('devices/pair/', v.device_pair, name="qr-device-pair"),
	path('devices/<int:pk>/toggle/', v.device_toggle, name="qr-device-toggle"),
	path('devices/net/add/', v.net_add, name="qr-net-add"),
	path('devices/net/<int:pk>/toggle/', v.net_toggle, name="qr-net-toggle"),

	path('cards/', v.cards, name="qr-cards"),
	path('cards/print/', v.card_print, name="qr-card-print"),
	path('cards/action/', v.card_action, name="qr-card-action"),

	path('dash/', v.dash, name="qr-dash"),
	path('dash/chart/', v.dash_chart, name="qr-dash-chart"),
	path('api/dash/', v.api_dash, name="qr-api-dash"),

	path('report/', v.report, name="qr-report"),
	path('report/export/', v.report_export, name="qr-report-export"),
	path('me/', v.my_attendance, name="qr-me"),
	path('me/chart/', v.my_chart, name="qr-me-chart"),
	path('api/me/', v.api_me, name="qr-api-me"),
]
