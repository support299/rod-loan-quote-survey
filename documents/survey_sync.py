"""
Ensure Loan Quote Survey contact custom fields exist, then write values to GHL.
Also assign a permanent opportunity Loan ID on first survey submit only.
"""
import logging
import uuid

import requests

from accounts.models import GHLAuthCredentials, GHLCustomField
from documents.ghl_service import (
    create_location_custom_field,
    get_opportunity,
    list_location_custom_fields,
    update_contact_custom_fields,
    update_opportunity_custom_fields,
)
from documents.models import OpportunityCardSubmission
from documents.survey_ghl_fields import (
    GHL_OPPORTUNITY_LOAN_ID_FIELD_KEY,
    GHL_OPPORTUNITY_LOAN_ID_FIELD_NAME,
    GHL_TEXT,
    LOAN_QUOTE_SURVEY_GHL_FIELDS,
    field_spec,
    normalize_survey_value,
)

logger = logging.getLogger(__name__)


def _normalize_field_name(name):
    """Normalize for matching (NBSP, extra spaces, case)."""
    return " ".join((name or "").replace("\xa0", " ").replace("\u200b", "").split()).lower()


def _resolve_account(location_id=None, opportunity=None):
    loc = (location_id or "").strip() or None
    if not loc and opportunity:
        loc = (opportunity.get("locationId") or "").strip() or None
    if not loc:
        return None
    return GHLAuthCredentials.objects.filter(location_id=loc).first()


def _name_index(fields):
    """Map normalized field name -> field dict."""
    index = {}
    for field in fields or []:
        name = (field.get("name") or "").strip()
        if name and field.get("id"):
            index[_normalize_field_name(name)] = field
    return index


def _custom_field_raw_value(custom_fields, field_id):
    """Read value for a field id from a GHL customFields list."""
    if not field_id:
        return None
    for cf in custom_fields or []:
        if cf.get("id") != field_id:
            continue
        for key in ("fieldValue", "value", "field_value"):
            if key in cf and cf.get(key) is not None:
                return cf.get(key)
        return None
    return None


def generate_loan_id():
    """Permanent unique Loan ID string (stable format, not derived from opportunity id)."""
    return f"LQ-{uuid.uuid4().hex[:12].upper()}"


def resolve_opportunity_loan_id_field(account, access_token=None):
    """
    Resolve opportunity custom field id for Loan ID (fieldKey opportunity.loan_id).
    Creates a TEXT opportunity field if missing. Caches on GHLCustomField.
    """
    token = access_token or account.access_token
    location_id = account.location_id

    cached = GHLCustomField.objects.filter(
        account=account,
        field_name=GHL_OPPORTUNITY_LOAN_ID_FIELD_NAME,
        is_active=True,
        description__icontains="opportunity",
    ).first()
    if not cached:
        cached = GHLCustomField.objects.filter(
            account=account,
            field_name=GHL_OPPORTUNITY_LOAN_ID_FIELD_NAME,
            is_active=True,
        ).first()

    fields = list_location_custom_fields(location_id, model="opportunity", access_token=token)
    found = None
    for field in fields:
        key = (field.get("fieldKey") or "").strip().lower()
        name = (field.get("name") or "").strip()
        if key == GHL_OPPORTUNITY_LOAN_ID_FIELD_KEY.lower() or _normalize_field_name(
            name
        ) == _normalize_field_name(GHL_OPPORTUNITY_LOAN_ID_FIELD_NAME):
            found = field
            break

    if found:
        field_id = found["id"]
    elif cached:
        field_id = cached.ghl_field_id
    else:
        created = create_location_custom_field(
            location_id,
            name=GHL_OPPORTUNITY_LOAN_ID_FIELD_NAME,
            data_type=GHL_TEXT,
            model="opportunity",
            access_token=token,
        )
        field_id = created.get("id")
        if not field_id:
            raise RuntimeError(f"Failed to create opportunity Loan ID field: {created}")
        logger.info(
            "Created opportunity Loan ID field %s for location %s",
            field_id,
            location_id,
        )

    GHLCustomField.objects.update_or_create(
        account=account,
        ghl_field_id=field_id,
        defaults={
            "field_name": GHL_OPPORTUNITY_LOAN_ID_FIELD_NAME,
            "field_type": "text",
            "description": "Opportunity:Loan ID (set once on first Loan Quote Survey submit)",
            "is_active": True,
        },
    )
    return field_id


def ensure_opportunity_loan_id(opportunity_id, opportunity, account, access_token=None):
    """
    If opportunity Loan ID is empty, generate a unique id and write it once.
    If already set, leave it unchanged.

    :return: dict {loan_id, field_id, created: bool, skipped: bool}
    """
    token = access_token or account.access_token
    field_id = resolve_opportunity_loan_id_field(account, access_token=token)
    existing = _custom_field_raw_value(opportunity.get("customFields"), field_id)
    if isinstance(existing, list):
        existing = existing[0] if existing else None
    existing_str = str(existing).strip() if existing is not None else ""

    if existing_str:
        logger.info(
            "Opportunity %s already has Loan ID %s — not overwriting",
            opportunity_id,
            existing_str,
        )
        return {
            "loan_id": existing_str,
            "field_id": field_id,
            "created": False,
            "skipped": True,
        }

    loan_id = generate_loan_id()
    update_opportunity_custom_fields(
        opportunity_id,
        [{"id": field_id, "field_value": loan_id}],
        access_token=token,
    )
    logger.info("Set permanent Loan ID %s on opportunity %s", loan_id, opportunity_id)
    return {
        "loan_id": loan_id,
        "field_id": field_id,
        "created": True,
        "skipped": False,
    }


def ensure_loan_quote_survey_contact_fields(account, access_token=None):
    """
    For each catalog field: reuse existing contact custom field by name, else create.
    Persist ids on GHLCustomField for this account.

    :return: dict form_key -> ghl_field_id
    """
    token = access_token or account.access_token
    location_id = account.location_id
    existing = list_location_custom_fields(location_id, model="contact", access_token=token)
    by_name = _name_index(existing)
    mapping = {}

    for form_key, name in LOAN_QUOTE_SURVEY_GHL_FIELDS.items():
        spec = field_spec(form_key)
        found = by_name.get(_normalize_field_name(name))
        if found:
            field_id = found["id"]
        else:
            try:
                created = create_location_custom_field(
                    location_id,
                    name=name,
                    data_type=spec["data_type"],
                    model="contact",
                    options=None,
                    access_token=token,
                )
                field_id = created.get("id")
            except requests.exceptions.HTTPError as e:
                # Race / fieldKey collision: GHL may return existingId in meta
                field_id = None
                resp = getattr(e, "response", None)
                if resp is not None:
                    try:
                        body = resp.json()
                    except Exception:
                        body = {}
                    field_id = (body.get("meta") or {}).get("existingId")
                    if not field_id:
                        # Refresh list and retry name match (NBSP / slight rename)
                        existing = list_location_custom_fields(
                            location_id, model="contact", access_token=token
                        )
                        by_name = _name_index(existing)
                        found = by_name.get(_normalize_field_name(name))
                        if found:
                            field_id = found["id"]
                if not field_id:
                    raise
                logger.info(
                    "Reused existing GHL field for '%s' via conflict (%s)",
                    name,
                    field_id,
                )

            if not field_id:
                raise RuntimeError(f"GHL create custom field returned no id for '{name}': {created}")
            by_name[_normalize_field_name(name)] = {"id": field_id, "name": name}
            logger.info(
                "Created GHL contact %s custom field '%s' (%s) for location %s",
                spec["data_type"],
                name,
                field_id,
                location_id,
            )

        mapping[form_key] = field_id
        GHLCustomField.objects.update_or_create(
            account=account,
            ghl_field_id=field_id,
            defaults={
                "field_name": name,
                "field_type": spec["db_type"],
                "description": f"Loan Quote Survey:{form_key}",
                "is_active": True,
            },
        )

    return mapping


def build_contact_custom_field_payload(form_data, key_to_field_id):
    """Build [{id, field_value}, ...] from survey form_data."""
    payload = []
    form_data = form_data or {}
    for form_key, field_id in key_to_field_id.items():
        value = normalize_survey_value(form_key, form_data.get(form_key))
        if value is None:
            continue
        payload.append({"id": field_id, "field_value": value})
    return payload


def sync_loan_quote_survey_submission(request_id, location_id=None):
    """
    Load OpportunityCardSubmission for request_id (opportunity id), ensure contact
    custom fields exist on the location, write submitted values to the opportunity's contact.
    Also set opportunity Loan ID once if empty.

    :return: dict summary {contact_id, location_id, fields_written, loan_id, ...}
    """
    try:
        submission = OpportunityCardSubmission.objects.get(request_id=request_id)
    except OpportunityCardSubmission.DoesNotExist:
        raise ValueError(f"No OpportunityCardSubmission for request_id={request_id}")

    account = _resolve_account(location_id)
    opportunity = {}
    if account:
        opp_data = get_opportunity(request_id, access_token=account.access_token)
        opportunity = opp_data.get("opportunity") or {}
    else:
        last_err = None
        for cred in GHLAuthCredentials.objects.exclude(access_token="").iterator():
            try:
                opp_data = get_opportunity(request_id, access_token=cred.access_token)
                opportunity = opp_data.get("opportunity") or {}
                account = _resolve_account(opportunity.get("locationId")) or cred
                break
            except Exception as e:
                last_err = e
                continue
        if not account:
            raise ValueError(
                f"No GHLAuthCredentials able to load opportunity {request_id}: {last_err}"
            )

    contact_id = opportunity.get("contactId")
    if not contact_id:
        raise ValueError(f"Opportunity {request_id} has no contactId")

    token = account.access_token

    loan_id_result = ensure_opportunity_loan_id(
        request_id, opportunity, account, access_token=token
    )

    key_to_field_id = ensure_loan_quote_survey_contact_fields(account, access_token=token)
    custom_fields = build_contact_custom_field_payload(submission.form_data, key_to_field_id)

    if custom_fields:
        # GHL may reject very large single payloads; chunk if needed
        chunk_size = 40
        for i in range(0, len(custom_fields), chunk_size):
            update_contact_custom_fields(
                contact_id,
                custom_fields[i : i + chunk_size],
                access_token=token,
            )

    summary = {
        "request_id": request_id,
        "contact_id": contact_id,
        "location_id": account.location_id,
        "fields_written": len(custom_fields),
        "fields_ensured": len(key_to_field_id),
        "loan_id": loan_id_result.get("loan_id"),
        "loan_id_created": loan_id_result.get("created"),
        "loan_id_skipped": loan_id_result.get("skipped"),
    }
    logger.info("Loan Quote Survey GHL sync complete: %s", summary)
    return summary
