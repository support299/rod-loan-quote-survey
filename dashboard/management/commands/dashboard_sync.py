from django.core.management.base import BaseCommand

from dashboard.issues import refresh_issues
from dashboard.sync import sync_all


class Command(BaseCommand):
    help = "Pull every 01/02 pipeline opportunity from GHL into the dashboard, then refresh exceptions."

    def handle(self, *args, **options):
        run = sync_all()
        self.stdout.write(self.style.SUCCESS(f"Sync {run.status}: {run.stats}"))
        self.stdout.write(f"Exceptions: {refresh_issues()}")
