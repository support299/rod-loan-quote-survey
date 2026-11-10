from django.core.management.base import BaseCommand

from dashboard.constants import NEW_GHL_FIELDS
from dashboard.sync import _norm, get_account
from documents.ghl_service import create_location_custom_field, list_location_custom_fields


class Command(BaseCommand):
    help = (
        "List (default) or create (--apply) the opportunity custom fields the dashboard needs: "
        "Expected Closing Date, AE Commission, Loan Analyst 2, Loss Reason, Loss Notes, Referral Source."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Create the missing fields in GHL")

    def handle(self, *args, **options):
        account = get_account()
        existing = list_location_custom_fields(account.location_id, model="opportunity", access_token=account.access_token)
        by_name = {_norm(f.get("name")): f for f in existing}

        for spec in NEW_GHL_FIELDS:
            if _norm(spec["name"]) in by_name:
                self.stdout.write(f"  exists   {spec['name']}")
                continue
            options_list = spec.get("options")
            if spec.get("options_from"):
                source = by_name.get(_norm(spec["options_from"])) or {}
                options_list = source.get("picklistOptions") or []
            if not options.get("apply"):
                self.stdout.write(self.style.WARNING(f"  missing  {spec['name']} ({spec['data_type']})"))
                continue
            create_location_custom_field(
                account.location_id, spec["name"], spec["data_type"], model="opportunity",
                options=options_list, access_token=account.access_token,
            )
            self.stdout.write(self.style.SUCCESS(f"  created  {spec['name']}"))
        if not options.get("apply"):
            self.stdout.write("Dry run. Re-run with --apply to create missing fields.")
