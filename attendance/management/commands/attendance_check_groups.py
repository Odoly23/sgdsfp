from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from attendance import qr_service as svc

ROLES = [
	('MANAGER_GROUPS', 'Kartu QR, cuti-libur, tinjau scan beruntun'),
	('VIEWER_GROUPS', 'Hari ini, rekap, dashboard, grafik pimpinan'),
	('DEVICE_GROUPS', 'Memasangkan PC kiosk dan IP kantor'),
	('SELF_GROUPS', 'Laporan absensi pribadi'),
]


class Command(BaseCommand):
	help = "Periksa apakah nama grup di ATTENDANCE_*_GROUPS benar-benar ada di basis data dan berapa anggotanya."

	def handle(self, *args, **opts):
		existing = {g.name: g.user_set.count() for g in Group.objects.all()}
		used, missing = set(), 0
		for setting, role in ROLES:
			self.stdout.write('\nATTENDANCE_%s  (%s)' % (setting, role))
			for name in svc.conf(setting):
				used.add(name)
				if name in existing:
					self.stdout.write('  OK       %-18s %d anggota' % (name, existing[name]))
				else:
					missing += 1
					self.stdout.write(self.style.WARNING('  TIDAK ADA %-17s grup ini belum ada di basis data' % name))
		others = sorted(set(existing) - used)
		if others:
			self.stdout.write('\nGrup lain di basis data yang BELUM diberi hak absensi QR: ' + ', '.join(
				'%s (%d)' % (n, existing[n]) for n in others))
			self.stdout.write('Bila ada yang seharusnya, tambahkan ke ATTENDANCE_*_GROUPS di settings.py.')
		self.stdout.write(self.style.SUCCESS('\nSemua grup ditemukan.') if not missing
						  else self.style.WARNING('\n%d nama grup tidak ditemukan.' % missing))
