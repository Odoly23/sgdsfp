import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from attendance.models_qr import ScanLog


class Command(BaseCommand):
	help = "Hapus catatan scan (ScanLog) yang lebih lama dari N hari agar tabel tidak membengkak. Jadwalkan lewat cron."

	def add_arguments(self, parser):
		parser.add_argument('--days', type=int, default=180, help='umur catatan yang disimpan (bawaan 180 hari)')
		parser.add_argument('--batch', type=int, default=5000, help='jumlah baris per hapusan (agar tidak mengunci tabel)')

	def handle(self, *args, **opts):
		cutoff = timezone.now() - datetime.timedelta(days=max(opts['days'], 1))
		total = 0
		while True:
			ids = list(ScanLog.objects.filter(scanned_at__lt=cutoff).values_list('pk', flat=True)[:max(opts['batch'], 100)])
			if not ids:
				break
			total += ScanLog.objects.filter(pk__in=ids).delete()[0]
		self.stdout.write('ScanLog: %d baris lebih lama dari %d hari dihapus.' % (total, opts['days']))
