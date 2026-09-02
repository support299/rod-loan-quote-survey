"""
Loan Quote Survey → GHL contact upsert + opportunity create/update.
Pipeline/stage resolved by exact name and cached.
"""
import logging

from django.core.cache import cache

from accounts.models import GHLAuthCredentials
from documents.ghl_service import (
    create_opportunity,
    list_pipelines,
    update_opportunity,
    upsert_contact_by_email_or_phone,
)

logger = logging.getLogger(__name__)

GHL_LOAN_PIPELINE_NAME = "01 Loan Pipeline"
GHL_QUICK_APP_STAGE_NAME = "quick app submitted"

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


def resolve_loan_pipeline_stage(account, access_token=None):
    """
    Look up pipeline "01 Loan Pipeline" and stage "quick app submitted" by exact name.
    Cache ids on Django cache.

    :return: dict {pipeline_id, pipeline_stage_id, location_id}
    """
    token = access_token or account.access_token
    location_id = account.location_id
    cache_key = f"ghl_loan_pipeline_stage:{location_id}"
    cached = cache.get(cache_key)
    if cached and cached.get("pipeline_id") and cached.get("pipeline_stage_id"):
        return cached

    pipelines = list_pipelines(location_id, access_token=token)
    pipeline = None
    target_pipe = _normalize_name(GHL_LOAN_PIPELINE_NAME)
    for p in pipelines:
        if _normalize_name(p.get("name")) == target_pipe:
            pipeline = p
            break
    if not pipeline:
        raise ValueError(
            f'GHL pipeline "{GHL_LOAN_PIPELINE_NAME}" not found for location {location_id}'
        )

    target_stage = _normalize_name(GHL_QUICK_APP_STAGE_NAME)
    stage = None
    for s in pipeline.get("stages") or []:
        if _normalize_name(s.get("name")) == target_stage:
            stage = s
            break
    if not stage or not stage.get("id"):
        raise ValueError(
            f'GHL stage "{GHL_QUICK_APP_STAGE_NAME}" not found on pipeline '
            f'"{GHL_LOAN_PIPELINE_NAME}"'
        )

    result = {
        "pipeline_id": pipeline["id"],
        "pipeline_stage_id": stage["id"],
        "location_id": location_id,
        "pipeline_name": pipeline.get("name"),
        "stage_name": stage.get("name"),
    }
    cache.set(cache_key, result, _PIPELINE_CACHE_TTL)
    logger.info(
        "Resolved GHL pipeline/stage for %s: pipeline=%s stage=%s",
        location_id,
        result["pipeline_id"],
        result["pipeline_stage_id"],
    )
    return result


def build_opportunity_name(form_data):
    """'{Entity Name} - {Subject Property Address}'"""
    form_data = form_data or {}
    entity = (form_data.get("entity_name") or "").strip()
    address = (form_data.get("subject_property_address") or "").strip()
    if entity and address:
        return f"{entity} - {address}"
    return entity or address or "Loan Quote Survey"


def ensure_contact_and_opportunity(form_data, opportunity_id=None, account=None):
    """
    Upsert GHL contact (email then phone), then create or update opportunity.

    - opportunity_id is None  → always CREATE a new opportunity
    - opportunity_id set      → UPDATE that opportunity

    :return: dict with contact_id, opportunity_id, location_id, created_opportunity, ...
    """
    account = account or get_default_ghl_account()
    if not account:
        raise ValueError("No GHLAuthCredentials installed")

    token = account.access_token
    location_id = account.location_id
    form_data = form_data or {}

    full_name = (form_data.get("full_name") or "").strip()
    email = (form_data.get("email") or "").strip()
    phone = (form_data.get("phone") or "").strip()
    if not full_name or not email or not phone:
        raise ValueError("Full Name, Email, and Phone are required.")

    contact_result = upsert_contact_by_email_or_phone(
        location_id,
        full_name=full_name,
        email=email,
        phone=phone,
        access_token=token,
    )
    contact_id = contact_result["contact_id"]

    pipe = resolve_loan_pipeline_stage(account, access_token=token)
    opp_name = build_opportunity_name(form_data)

    created_opportunity = False
    if opportunity_id:
        opportunity = update_opportunity(
            opportunity_id,
            name=opp_name,
            pipeline_id=pipe["pipeline_id"],
            pipeline_stage_id=pipe["pipeline_stage_id"],
            contact_id=contact_id,
            access_token=token,
        )
        if not opportunity.get("id"):
            opportunity = {"id": opportunity_id, **(opportunity or {})}
    else:
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
            raise RuntimeError(f"GHL create opportunity returned no id: {opportunity}")
        created_opportunity = True

    summary = {
        "contact_id": contact_id,
        "contact_created": contact_result.get("created"),
        "opportunity_id": opportunity_id,
        "opportunity_created": created_opportunity,
        "opportunity_name": opp_name,
        "location_id": location_id,
        "pipeline_id": pipe["pipeline_id"],
        "pipeline_stage_id": pipe["pipeline_stage_id"],
        "account": account,
    }
    logger.info("Loan Quote Survey contact/opportunity ready: %s", {
        k: v for k, v in summary.items() if k != "account"
    })
    return summary
