from django.core.management.base import BaseCommand

from dashboard.constants import NEW_GHL_FIELDS
from dashboard.invites import GHL_CONTACT_FIELDS
from dashboard.sync import _norm, get_account
from documents.ghl_service import create_location_custom_field, list_location_custom_fields


class Command(BaseCommand):
    help = (
        "List (default) or create (--apply) the GHL custom fields the dashboard needs. "
        "Opportunity: Expected Closing Date, AE Commission, Loan Analyst 2, Loss Reason, Loss Notes, Referral Source, "
        "Processor 1, Processor 2, Deal Sourced By, Broker Paid By, Funded Date. "
        "Contact: Dashboard Invite Link, Dashboard Password Reset Link (used by the GHL email workflows)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Create the missing fields in GHL")

    def handle(self, *args, **options):
        account = get_account()
        for model, specs in (("opportunity", NEW_GHL_FIELDS), ("contact", GHL_CONTACT_FIELDS)):
            self.stdout.write(f"{model.title()} fields:")
            self._ensure(account, model, specs, options.get("apply"))
        if not options.get("apply"):
            self.stdout.write("Dry run. Re-run with --apply to create missing fields.")

    def _ensure(self, account, model, specs, apply):
        existing = list_location_custom_fields(account.location_id, model=model, access_token=account.access_token)
        by_name = {_norm(f.get("name")): f for f in existing}

        for spec in specs:
            found = by_name.get(_norm(spec["name"]))
            if found:
                key = f"  {{{{{found['fieldKey']}}}}}" if found.get("fieldKey") else ""
                self.stdout.write(f"  exists   {spec['name']}{key}")
                continue
            options_list = spec.get("options")
            if spec.get("options_from"):
                source = by_name.get(_norm(spec["options_from"])) or {}
                options_list = source.get("picklistOptions") or []
            if not apply:
                self.stdout.write(self.style.WARNING(f"  missing  {spec['name']} ({spec['data_type']})"))
                continue
            created = create_location_custom_field(
                account.location_id, spec["name"], spec["data_type"], model=model,
                options=options_list, access_token=account.access_token,
            )
            key = f"  {{{{{created['fieldKey']}}}}}" if created.get("fieldKey") else ""
            self.stdout.write(self.style.SUCCESS(f"  created  {spec['name']}{key}"))
