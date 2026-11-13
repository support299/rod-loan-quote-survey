from getpass import getpass

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from dashboard.constants import ROLE_CHOICES
from dashboard.models import DashboardProfile


class Command(BaseCommand):
    help = "Create a dashboard login, e.g. the first CEO account: dashboard_create_user rod@flipfunding.com --role ceo --ae-name Rod"

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--role", required=True, choices=[r for r, _ in ROLE_CHOICES])
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")
        parser.add_argument("--ae-name", default="", help="Value used in the GHL Account Executive dropdown")
        parser.add_argument("--analyst-name", default="", help="Processors: their name in the GHL Processor 1/2 dropdowns, e.g. 'Shan Iqbal'")
        parser.add_argument("--ghl-user-id", default="")
        parser.add_argument("--password", default="", help="Omit to be prompted")

    def handle(self, *args, **opts):
        email = opts["email"].strip().lower()
        User = get_user_model()
        if User.objects.filter(username__iexact=email).exists():
            raise CommandError(f"User {email} already exists")
        password = opts["password"] or getpass("Password: ")
        if len(password) < 8:
            raise CommandError("Password must be at least 8 characters")
        with transaction.atomic():
            user = User.objects.create_user(
                username=email, email=email, password=password,
                first_name=opts["first_name"], last_name=opts["last_name"],
            )
            DashboardProfile.objects.create(
                user=user, role=opts["role"], ae_name=opts["ae_name"],
                analyst_name=opts["analyst_name"], ghl_user_id=opts["ghl_user_id"],
            )
        self.stdout.write(self.style.SUCCESS(f"Created {opts['role']} login for {email}"))
