from django.urls import path
from . import views_qr as v

urlpatterns = [
	# halaman kiosk (tanpa login, dilindungi kunci perangkat)
	path('kiosk/', v.kiosk, name="qr-kiosk"),
	path('kiosk/scan/', v.kiosk_scan, name="qr-kiosk-scan"),
	path('kiosk/check/', v.kiosk_check, name="qr-kiosk-check"),

	path('today/', v.today, name="qr-today"),

	path('devices/', v.devices, name="qr-devices"),
	path('devices/add/', v.device_add, name="qr-device-add"),
	path('devices/<int:pk>/toggle/', v.device_toggle, name="qr-device-toggle"),

	path('cards/', v.cards, name="qr-cards"),
	path('cards/print/', v.card_print, name="qr-card-print"),
	path('cards/action/', v.card_action, name="qr-card-action"),

	path('report/', v.report, name="qr-report"),
	path('report/export/', v.report_export, name="qr-report-export"),
	path('me/', v.my_attendance, name="qr-me"),
]
