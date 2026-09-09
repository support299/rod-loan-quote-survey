"""
Loan Quote Survey → GHL contact upsert + opportunity create/update.
Pipeline/stage resolved by exact name and cached.
"""
import logging

from django.core.cache import cache

from accounts.models import GHLAuthCredentials
from documents.ghl_service import (
    create_opportunity,
    get_contact,
    get_opportunity,
    list_pipelines,
    update_opportunity,
    upsert_contact_by_email_or_phone,
)

logger = logging.getLogger(__name__)

# 01 Loan Pipeline stages
GHL_LOAN_PIPELINE_NAME = "01 Loan Pipeline"
GHL_QUICK_APP_STAGE_NAME = "quick app submitted"
GHL_UNDER_REVIEW_STAGE_NAME = "Under Review"
GHL_TERMSHEET_SENT_STAGE_NAME = "TERMSHEET SENT"
GHL_TERMSHEET_ACCEPTED_STAGE_NAME = "TERMSHEET ACCEPTED/SECURE LINK"

# 02 Processing Pipeline
GHL_PROCESSING_PIPELINE_NAME = "02 Processing Pipeline"
GHL_DOCUMENT_UPLOADED_STAGE_NAME = "DOCUMENT UPLOADED"

_PIPELINE_CACHE_TTL = 60 * 60 * 24  # 24 hours


def _normalize_name(name):
    return " ".join((name or "").replace("\xa0", " ").replace("\u200b", "").split()).lower()


def get_default_ghl_account():
    """Single-location install: first account with an access token."""
    return (
        GHLAuthCredentials.objects.exclude(access_token="")
        .exclude(access_token__isnull=True)
        .order_by("created_at")
        .first()
    )


def resolve_pipeline_stage(account, pipeline_name, stage_name, access_token=None):
    """
    Look up pipeline + stage by exact display name (case/spacing insensitive).
    Cached per location + pipeline + stage.

    :return: dict {pipeline_id, pipeline_stage_id, location_id, pipeline_name, stage_name}
    """
    token = access_token or account.access_token
    location_id = account.location_id
    cache_key = (
        f"ghl_pipe_stage:{location_id}:"
        f"{_normalize_name(pipeline_name)}:{_normalize_name(stage_name)}"
    )
    cached = cache.get(cache_key)
    if cached and cached.get("pipeline_id") and cached.get("pipeline_stage_id"):
        return cached

    pipelines = list_pipelines(location_id, access_token=token)
    pipeline = None
    target_pipe = _normalize_name(pipeline_name)
    for p in pipelines:
        if _normalize_name(p.get("name")) == target_pipe:
            pipeline = p
            break
    if not pipeline:
        raise ValueError(
            f'GHL pipeline "{pipeline_name}" not found for location {location_id}'
        )

    target_stage = _normalize_name(stage_name)
    stage = None
    for s in pipeline.get("stages") or []:
        if _normalize_name(s.get("name")) == target_stage:
            stage = s
            break
    if not stage or not stage.get("id"):
        raise ValueError(
            f'GHL stage "{stage_name}" not found on pipeline "{pipeline_name}"'
        )

    result = {
        "pipeline_id": pipeline["id"],
        "pipeline_stage_id": stage["id"],
        "location_id": location_id,
        "pipeline_name": pipeline.get("name") or pipeline_name,
        "stage_name": stage.get("name") or stage_name,
    }
    cache.set(cache_key, result, _PIPELINE_CACHE_TTL)
    logger.info(
        "Resolved GHL pipeline/stage for %s: %s / %s (%s / %s)",
        location_id,
        result["pipeline_name"],
        result["stage_name"],
        result["pipeline_id"],
        result["pipeline_stage_id"],
    )
    return result


def resolve_loan_pipeline_stage(account, access_token=None):
    """Backward-compatible: 01 Loan Pipeline → quick app submitted."""
    return resolve_pipeline_stage(
        account,
        GHL_LOAN_PIPELINE_NAME,
        GHL_QUICK_APP_STAGE_NAME,
        access_token=access_token,
    )


def get_opportunity_pipeline_info(opportunity_id, account=None, access_token=None):
    """
    Load opportunity and resolve current pipeline/stage names from location pipelines.
    """
    account = account or get_default_ghl_account()
    if not account or not opportunity_id:
        return None
    token = access_token or account.access_token
    opp_data = get_opportunity(opportunity_id, access_token=token)
    opportunity = opp_data.get("opportunity") or {}
    pipeline_id = opportunity.get("pipelineId") or opportunity.get("pipeline_id")
    stage_id = (
        opportunity.get("pipelineStageId")
        or opportunity.get("pipeline_stage_id")
        or opportunity.get("statusId")
    )
    pipeline_name = ""
    stage_name = ""
    try:
        pipelines = list_pipelines(account.location_id, access_token=token)
        for p in pipelines:
            if p.get("id") == pipeline_id:
                pipeline_name = p.get("name") or ""
                for s in p.get("stages") or []:
                    if s.get("id") == stage_id:
                        stage_name = s.get("name") or ""
                        break
                break
    except Exception as e:
        logger.warning(
            "Failed to resolve pipeline names for opportunity %s: %s",
            opportunity_id,
            e,
            exc_info=True,
        )

    return {
        "opportunity": opportunity,
        "opportunity_id": opportunity_id,
        "contact_id": opportunity.get("contactId"),
        "pipeline_id": pipeline_id,
        "pipeline_stage_id": stage_id,
        "pipeline_name": pipeline_name,
        "stage_name": stage_name,
        "stage_key": _normalize_name(stage_name),
        "pipeline_key": _normalize_name(pipeline_name),
        "account": account,
    }


def stage_matches(stage_name, expected):
    return _normalize_name(stage_name) == _normalize_name(expected)


def move_opportunity_to_stage(
    opportunity_id,
    pipeline_name,
    stage_name,
    account=None,
    access_token=None,
    only_from_stages=None,
):
    """
    Move opportunity to pipeline_name / stage_name.
    If only_from_stages is set, skip unless current stage matches one of them.
    """
    account = account or get_default_ghl_account()
    if not account:
        raise ValueError("No GHLAuthCredentials installed")
    token = access_token or account.access_token

    info = get_opportunity_pipeline_info(opportunity_id, account=account, access_token=token)
    if not info:
        raise ValueError(f"Could not load opportunity {opportunity_id}")

    current_stage = info.get("stage_name") or ""
    if only_from_stages:
        allowed = [_normalize_name(s) for s in only_from_stages]
        if _normalize_name(current_stage) not in allowed:
            logger.info(
                "Skip stage move for %s: current '%s' not in %s",
                opportunity_id,
                current_stage,
                only_from_stages,
            )
            return {
                "success": False,
                "skipped": True,
                "reason": "stage_mismatch",
                "current_stage": current_stage,
                "target_stage": stage_name,
                "target_pipeline": pipeline_name,
            }

    target = resolve_pipeline_stage(
        account, pipeline_name, stage_name, access_token=token
    )
    if (
        info.get("pipeline_id") == target["pipeline_id"]
        and info.get("pipeline_stage_id") == target["pipeline_stage_id"]
    ):
        return {
            "success": True,
            "skipped": True,
            "reason": "already_there",
            "current_stage": current_stage,
            "target_stage": target["stage_name"],
            "target_pipeline": target["pipeline_name"],
        }

    update_opportunity(
        opportunity_id,
        pipeline_id=target["pipeline_id"],
        pipeline_stage_id=target["pipeline_stage_id"],
        access_token=token,
    )
    logger.info(
        "Moved opportunity %s to %s / %s (from %s / %s)",
        opportunity_id,
        target["pipeline_name"],
        target["stage_name"],
        info.get("pipeline_name"),
        current_stage,
    )
    return {
        "success": True,
        "skipped": False,
        "current_stage": current_stage,
        "target_stage": target["stage_name"],
        "target_pipeline": target["pipeline_name"],
        "pipeline_id": target["pipeline_id"],
        "pipeline_stage_id": target["pipeline_stage_id"],
    }


def move_to_under_review(opportunity_id, account=None):
    return move_opportunity_to_stage(
        opportunity_id,
        GHL_LOAN_PIPELINE_NAME,
        GHL_UNDER_REVIEW_STAGE_NAME,
        account=account,
        only_from_stages=[GHL_QUICK_APP_STAGE_NAME],
    )


def move_to_termsheet_sent(opportunity_id, account=None):
    return move_opportunity_to_stage(
        opportunity_id,
        GHL_LOAN_PIPELINE_NAME,
        GHL_TERMSHEET_SENT_STAGE_NAME,
        account=account,
        only_from_stages=[GHL_UNDER_REVIEW_STAGE_NAME],
    )


def rollback_to_termsheet_sent(opportunity_id, account=None):
    """
    TERMSHEET ACCEPTED/SECURE LINK → TERMSHEET SENT when an accepted upload
    is later rejected (or otherwise no longer fully accepted).
    """
    return move_opportunity_to_stage(
        opportunity_id,
        GHL_LOAN_PIPELINE_NAME,
        GHL_TERMSHEET_SENT_STAGE_NAME,
        account=account,
        only_from_stages=[GHL_TERMSHEET_ACCEPTED_STAGE_NAME],
    )


def move_to_termsheet_accepted(opportunity_id, account=None):
    return move_opportunity_to_stage(
        opportunity_id,
        GHL_LOAN_PIPELINE_NAME,
        GHL_TERMSHEET_ACCEPTED_STAGE_NAME,
        account=account,
        only_from_stages=[GHL_TERMSHEET_SENT_STAGE_NAME],
    )


def move_to_processing_document_uploaded(opportunity_id, account=None):
    return move_opportunity_to_stage(
        opportunity_id,
        GHL_PROCESSING_PIPELINE_NAME,
        GHL_DOCUMENT_UPLOADED_STAGE_NAME,
        account=account,
        only_from_stages=[GHL_TERMSHEET_ACCEPTED_STAGE_NAME],
    )


def build_opportunity_name(form_data):
    """'{Entity Name} - {Subject Property Address}'"""
    form_data = form_data or {}
    entity = (form_data.get("entity_name") or "").strip()
    address = (form_data.get("subject_property_address") or "").strip()
    if entity and address:
        return f"{entity} - {address}"
    return entity or address or "Quick App Submission Form"


def contact_form_fields_from_ghl(contact):
    """Map a GHL contact dict to survey full_name / email / phone."""
    contact = contact or {}
    name = (contact.get("name") or "").strip()
    if not name:
        first = (contact.get("firstName") or "").strip()
        last = (contact.get("lastName") or "").strip()
        name = f"{first} {last}".strip()
    return {
        "full_name": name,
        "email": (contact.get("email") or "").strip(),
        "phone": (contact.get("phone") or "").strip(),
        "contact_id": contact.get("id") or "",
    }


def fetch_opportunity_linked_contact(opportunity_id, account=None):
    """
    Load the contact already tied to a GHL opportunity.
    :return: dict {full_name, email, phone, contact_id} or None
    """
    account = account or get_default_ghl_account()
    if not account or not opportunity_id:
        return None
    token = account.access_token
    try:
        opp_data = get_opportunity(opportunity_id, access_token=token)
        opportunity = opp_data.get("opportunity") or {}
        contact_id = opportunity.get("contactId")
        if not contact_id:
            return None
        contact = get_contact(contact_id, access_token=token)
        if isinstance(contact, dict) and contact.get("contact"):
            contact = contact["contact"]
        fields = contact_form_fields_from_ghl(contact)
        fields["contact_id"] = contact_id
        return fields
    except Exception as e:
        logger.warning(
            "Failed to load linked contact for opportunity %s: %s",
            opportunity_id,
            e,
            exc_info=True,
        )
        return None


def ensure_contact_and_opportunity(
    form_data,
    opportunity_id=None,
    account=None,
    lock_existing_contact=False,
):
    """
    Upsert GHL contact (email then phone), then create or update opportunity.

    - opportunity_id is None  → CREATE opportunity at quick app submitted
    - opportunity_id set      → UPDATE name (do not reset stage)
    - lock_existing_contact   → keep opportunity's current contact
    """
    account = account or get_default_ghl_account()
    if not account:
        raise ValueError("No GHLAuthCredentials installed")

    token = account.access_token
    location_id = account.location_id
    form_data = form_data or {}

    opp_name = build_opportunity_name(form_data)
    created_opportunity = False
    contact_created = False
    pipe = None

    if opportunity_id and lock_existing_contact:
        linked = fetch_opportunity_linked_contact(opportunity_id, account=account)
        if not linked or not linked.get("contact_id"):
            raise ValueError(
                "This opportunity has no associated contact in GHL. "
                "Link a contact on the opportunity, then try again."
            )
        contact_id = linked["contact_id"]
        if linked.get("full_name"):
            form_data["full_name"] = linked["full_name"]
        if linked.get("email"):
            form_data["email"] = linked["email"]
        if linked.get("phone"):
            form_data["phone"] = linked["phone"]

        opportunity = update_opportunity(
            opportunity_id,
            name=opp_name,
            access_token=token,
        )
        if not opportunity.get("id"):
            opportunity = {"id": opportunity_id, **(opportunity or {})}
    else:
        full_name = (form_data.get("full_name") or "").strip()
        email = (form_data.get("email") or "").strip()
        phone = (form_data.get("phone") or "").strip()
        if not full_name or not email or not phone:
            raise ValueError("Full Name, Email, and Phone are required.")

        from documents.contact_validation import (
            is_valid_email,
            is_valid_full_name,
            normalize_phone,
        )

        if not is_valid_full_name(full_name):
            raise ValueError(
                "Enter a valid name using letters "
                "(spaces and - ' . allowed between name parts)."
            )
        if not is_valid_email(email):
            raise ValueError("Enter a valid email address (e.g. name@example.com).")
        phone = normalize_phone(phone)
        if not phone:
            raise ValueError(
                "Enter a valid phone number (e.g. +1 555 123 4567 or (415) 555-1234)."
            )
        form_data["phone"] = phone

        contact_result = upsert_contact_by_email_or_phone(
            location_id,
            full_name=full_name,
            email=email,
            phone=phone,
            access_token=token,
        )
        contact_id = contact_result["contact_id"]
        contact_created = bool(contact_result.get("created"))

        if opportunity_id:
            opportunity = update_opportunity(
                opportunity_id,
                name=opp_name,
                contact_id=contact_id,
                access_token=token,
            )
            if not opportunity.get("id"):
                opportunity = {"id": opportunity_id, **(opportunity or {})}
        else:
            pipe = resolve_loan_pipeline_stage(account, access_token=token)
            opportunity = create_opportunity(
                location_id,
                contact_id=contact_id,
                pipeline_id=pipe["pipeline_id"],
                pipeline_stage_id=pipe["pipeline_stage_id"],
                name=opp_name,
                status="open",
                access_token=token,
            )
            opportunity_id = opportunity.get("id")
            if not opportunity_id:
                raise RuntimeError(
                    f"GHL create opportunity returned no id: {opportunity}"
                )
            created_opportunity = True

    summary = {
        "contact_id": contact_id,
        "contact_created": contact_created,
        "opportunity_id": opportunity_id,
        "opportunity_created": created_opportunity,
        "opportunity_name": opp_name,
        "location_id": location_id,
        "pipeline_id": (pipe or {}).get("pipeline_id"),
        "pipeline_stage_id": (pipe or {}).get("pipeline_stage_id"),
        "contact_locked": bool(opportunity_id and lock_existing_contact),
        "account": account,
    }
    logger.info("Loan Quote Survey contact/opportunity ready: %s", {
        k: v for k, v in summary.items() if k != "account"
    })
    return summary
