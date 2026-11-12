"""
GHL → dashboard sync.

GHL only keeps an opportunity's *current* stage, so stage history exists only from
the moment we observe it. Every change we observe (full sync or webhook) is written
as a DealEvent, and the first time a deal enters a stage becomes a DealMilestone.
Milestone dates are never guessed: the only backfilled date is GHL's own
lastStageChangeAt for the stage a deal is in when we first see it.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation

import requests
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from documents.models import OpportunityCardSubmission
from documents.survey_opportunity import get_default_ghl_account

from . import ghl_client
from .constants import (
    FIELD_AE,
    FIELD_AE_COMMISSION,
    FIELD_AE_NAME,
    FIELD_ANALYST,
    FIELD_ANALYST_2,
    FIELD_ANALYST_NAME,
    FIELD_BROKER_EMAIL,
    FIELD_BROKER_FEE,
    FIELD_BROKER_NAME,
    FIELD_ENTITY_NAME,
    FIELD_EXPECTED_CLOSE,
    FIELD_LOAN_ID,
    FIELD_LOSS_NOTES,
    FIELD_LOSS_REASON,
    FIELD_ORIGINATION_FEE,
    FIELD_ORIGINATION_POINTS,
    FIELD_PROCESSING_FEE,
    FIELD_PROPERTY_ADDRESS,
    FIELD_REFERRAL_SOURCE,
    FIELD_TERMSHEET_STATUS,
    FIELD_TOTAL_LOAN_AMOUNT,
    FIELD_UNDERWRITING_FEE,
    FUNDED_INDEX,
    LOST_STATUSES,
    MILESTONE_APP_COMPLETE,
    MILESTONE_FUNDED,
    PIPELINE_KEYS,
    STAGE_FLOW,
    STAGE_INDEX,
)
from .models import (
    CompanySettings,
    Deal,
    DealEvent,
    DealMilestone,
    GHLUser,
    PipelineStage,
    SyncRun,
)

logger = logging.getLogger(__name__)

FIELD_NAMES_CACHE_KEY = "dashboard:ghl_opportunity_field_names:{}"
WEBHOOK_EVENT_TYPES = {
    "OpportunityCreate",
    "OpportunityUpdate",
    "OpportunityStageUpdate",
    "OpportunityStatusUpdate",
    "OpportunityAssignedToUpdate",
    "OpportunityMonetaryValueUpdate",
    "OpportunityDelete",
}
# Fields a webhook payload may carry without the full opportunity.
PARTIAL_FIELDS = ("pipeline_id", "pipeline_key", "stage_id", "stage_key", "stage_name", "stage_index", "status", "assigned_to")


# ---------------------------------------------------------------- parsing helpers

def _norm(name):
    return re.sub(r"\s+", " ", (name or "").strip()).upper()


def parse_dt(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=dt_timezone.utc)
    dt = parse_datetime(str(value).replace("Z", "+00:00"))
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, dt_timezone.utc)
    return dt


def parse_money(value):
    """'$12,500.00' / 12500 / '1.5%' → Decimal, or None when blank/unparseable."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    if cleaned in ("", ".", "-"):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_day(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return parse_dt(value).date()
    text = str(value).strip()
    day = parse_date(text[:10])
    if day:
        return day
    dt = parse_dt(text)
    return dt.date() if dt else None


def field_value(entry):
    for key in ("fieldValueString", "fieldValueNumber", "fieldValueDate", "fieldValue", "field_value", "value"):
        val = entry.get(key)
        if val not in (None, "", []):
            if isinstance(val, list):
                return ", ".join(str(v) for v in val if v not in (None, ""))
            return val
    arr = entry.get("fieldValueArray")
    if arr:
        return ", ".join(str(v) for v in arr)
    return None


# ---------------------------------------------------------------- sync context

@dataclass
class SyncContext:
    account: object
    field_names: dict = field(default_factory=dict)  # field id -> display name
    stages: dict = field(default_factory=dict)  # stage id -> PipelineStage
    pipeline_names: dict = field(default_factory=dict)  # pipeline id -> name
    users: dict = field(default_factory=dict)  # ghl user id -> GHLUser
    programs: dict = field(default_factory=dict)  # opportunity id -> loan type
    known_aes: dict = field(default_factory=dict)  # lower name -> canonical AE name

    @property
    def token(self):
        return self.account.access_token


def get_account():
    account = get_default_ghl_account()
    if not account:
        raise RuntimeError("No GHL credentials stored; connect the Flip Funding sub-account first.")
    return account


def refresh_pipelines(ctx):
    flow = {(_norm(p), _norm(s)): key for key, p, s in STAGE_FLOW}
    for pipeline in ghl_client.list_pipelines(ctx.account.location_id, access_token=ctx.token):
        for stage in pipeline.get("stages") or []:
            obj, _ = PipelineStage.objects.update_or_create(
                ghl_id=stage["id"],
                defaults={
                    "pipeline_id": pipeline["id"],
                    "pipeline_name": pipeline.get("name") or "",
                    "name": stage.get("name") or "",
                    "position": stage.get("position") or 0,
                    "stage_key": flow.get((_norm(pipeline.get("name")), _norm(stage.get("name"))), ""),
                },
            )
    _load_stages(ctx)


def _load_stages(ctx):
    ctx.stages = {s.ghl_id: s for s in PipelineStage.objects.all()}
    ctx.pipeline_names = {s.pipeline_id: s.pipeline_name for s in ctx.stages.values()}


def refresh_users(ctx):
    for u in ghl_client.list_location_users(ctx.account.location_id, access_token=ctx.token):
        if not u.get("id"):
            continue
        name = u.get("name") or " ".join(filter(None, [u.get("firstName"), u.get("lastName")]))
        GHLUser.objects.update_or_create(
            ghl_id=u["id"],
            defaults={
                "name": name or "",
                "email": u.get("email") or "",
                "role": (u.get("roles") or {}).get("role") or "",
            },
        )
    ctx.users = {u.ghl_id: u for u in GHLUser.objects.all()}


def load_field_names(ctx, refresh=False):
    key = FIELD_NAMES_CACHE_KEY.format(ctx.account.location_id)
    cached = None if refresh else cache.get(key)
    if cached is None:
        fields = ghl_client.list_location_custom_fields(ctx.account.location_id, model="opportunity", access_token=ctx.token)
        names = {f["id"]: f.get("name") or "" for f in fields if f.get("id")}
        ae_options = next((f.get("picklistOptions") or [] for f in fields if _norm(f.get("name")) == _norm(FIELD_AE)), [])
        cached = {"names": names, "ae_options": list(ae_options)}
        cache.set(key, cached, 60 * 60)
    ctx.field_names = cached["names"]
    ctx.known_aes = _known_aes(cached["ae_options"])
    return ctx.field_names


def _known_aes(ae_options):
    """Lower-case name → canonical AE name: GHL dropdown, survey AE list, AE logins."""
    from documents.survey_ghl_fields import ACCOUNT_EXECUTIVE_EMAILS

    from .models import DashboardProfile

    known = {n.lower(): n.title() for n in ACCOUNT_EXECUTIVE_EMAILS}
    known.update({n.strip().lower(): n.strip() for n in ae_options if n and n.strip()})
    known.update({n.lower(): n for n in DashboardProfile.objects.exclude(ae_name="").values_list("ae_name", flat=True)})
    known.pop("n/a", None)
    return known


def field_id_for(ctx, name):
    for fid, fname in ctx.field_names.items():
        if _norm(fname) == _norm(name):
            return fid
    return None


def _programs_for(opportunity_ids):
    rows = OpportunityCardSubmission.objects.filter(request_id__in=list(opportunity_ids)).values_list("request_id", "form_data")
    return {rid: str((data or {}).get("loan_type") or "").strip() for rid, data in rows}


def build_context(account=None, refresh=False):
    ctx = SyncContext(account=account or get_account())
    if refresh or not PipelineStage.objects.exists():
        refresh_pipelines(ctx)
    else:
        _load_stages(ctx)
    if refresh or not GHLUser.objects.exists():
        refresh_users(ctx)
    else:
        ctx.users = {u.ghl_id: u for u in GHLUser.objects.all()}
    load_field_names(ctx, refresh=refresh)
    return ctx


# ---------------------------------------------------------------- normalize + upsert

def _first_name(name):
    return (name or "").strip().split(" ")[0] if name else ""


def normalize_opportunity(opp, ctx):
    named = {}
    for entry in opp.get("customFields") or []:
        name = ctx.field_names.get(entry.get("id"))
        if not name:
            continue
        val = field_value(entry)
        if val is not None:
            named[name] = val

    def text(name):
        return str(named.get(name) or "").strip()

    stage = ctx.stages.get(opp.get("pipelineStageId") or "")
    stage_key = stage.stage_key if stage else ""
    pipeline_name = ctx.pipeline_names.get(opp.get("pipelineId") or "", "")
    contact = opp.get("contact") or {}
    owner = ctx.users.get(opp.get("assignedTo") or "")

    ae_name, ae_source = text(FIELD_AE), "field"
    if not ae_name or ae_name.lower() == "n/a":
        ae_name = text(FIELD_AE_NAME)
    if ae_name.lower() == "n/a":
        ae_name = ""
    if not ae_name and owner:
        # GHL owner as fallback only when the owner is a known AE — owners are often
        # the loan analyst, who must not be credited as the AE.
        ae_name = ctx.known_aes.get(_first_name(owner.name).lower(), "")
        ae_source = "owner" if ae_name else ""
    if not ae_name:
        ae_source = ""
    else:
        ae_name = ctx.known_aes.get(ae_name.lower(), ae_name)
    named["_ae_source"] = ae_source

    return {
        "location_id": opp.get("locationId") or ctx.account.location_id or "",
        "name": (opp.get("name") or "")[:512],
        "contact_id": opp.get("contactId") or contact.get("id") or "",
        "contact_name": (contact.get("name") or "")[:255],
        "contact_email": (contact.get("email") or "")[:255],
        "contact_company": (contact.get("companyName") or "")[:255],
        "pipeline_id": opp.get("pipelineId") or "",
        "pipeline_key": PIPELINE_KEYS.get(pipeline_name, ""),
        "stage_id": opp.get("pipelineStageId") or "",
        "stage_key": stage_key,
        "stage_name": stage.name if stage else "",
        "stage_index": STAGE_INDEX.get(stage_key, -1),
        "status": (opp.get("status") or "").lower(),
        "assigned_to": opp.get("assignedTo") or "",
        "ae_name": ae_name[:128],
        "analyst_name": (text(FIELD_ANALYST) or text(FIELD_ANALYST_NAME))[:128],
        "analyst2_name": text(FIELD_ANALYST_2)[:128],
        "loan_id": text(FIELD_LOAN_ID)[:64],
        "program": ctx.programs.get(opp.get("id"), "")[:128],
        "source": (opp.get("source") or "")[:255],
        "referral_source": text(FIELD_REFERRAL_SOURCE)[:255],
        "broker_name": text(FIELD_BROKER_NAME)[:255],
        "broker_email": text(FIELD_BROKER_EMAIL)[:255],
        "entity_name": text(FIELD_ENTITY_NAME)[:255],
        "property_address": text(FIELD_PROPERTY_ADDRESS)[:512],
        "termsheet_status": text(FIELD_TERMSHEET_STATUS)[:64],
        "lost_reason_id": opp.get("lostReasonId") or "",
        "ghl_loss_reason": text(FIELD_LOSS_REASON)[:255],
        "ghl_loss_notes": text(FIELD_LOSS_NOTES),
        "total_loan_amount": parse_money(named.get(FIELD_TOTAL_LOAN_AMOUNT)),
        "origination_fee": parse_money(named.get(FIELD_ORIGINATION_FEE)),
        "origination_points": parse_money(named.get(FIELD_ORIGINATION_POINTS)),
        "processing_fee": parse_money(named.get(FIELD_PROCESSING_FEE)),
        "underwriting_fee": parse_money(named.get(FIELD_UNDERWRITING_FEE)),
        "broker_fee": parse_money(named.get(FIELD_BROKER_FEE)),
        "ae_commission": parse_money(named.get(FIELD_AE_COMMISSION)),
        "expected_close_date": parse_day(named.get(FIELD_EXPECTED_CLOSE)),
        "ghl_created_at": parse_dt(opp.get("createdAt")),
        "ghl_updated_at": parse_dt(opp.get("updatedAt")),
        "last_stage_change_at": parse_dt(opp.get("lastStageChangeAt")),
        "last_status_change_at": parse_dt(opp.get("lastStatusChangeAt")),
        "custom_fields": {k: (v if isinstance(v, (str, int, float)) else str(v)) for k, v in named.items()},
    }


def _event(deal, kind, old, new, at, source):
    DealEvent.objects.create(
        deal=deal,
        kind=kind,
        from_value=str(old or "")[:255],
        to_value=str(new or "")[:255],
        occurred_at=at,
        source=source,
    )


def _milestone(deal, key, at, source):
    if not key or not at:
        return None
    obj, created = DealMilestone.objects.get_or_create(deal=deal, key=key, defaults={"reached_at": at, "source": source})
    return obj if created else None


def upsert_opportunity(opp, ctx, source="sync", partial=False):
    """Create/update a Deal from a GHL opportunity dict; record observed changes."""
    opp_id = opp.get("id")
    if not opp_id:
        return None
    values = normalize_opportunity(opp, ctx)
    now = timezone.now()
    deal = Deal.objects.filter(pk=opp_id).first()

    if not values["pipeline_key"]:
        # Unknown stage id (pipeline edited since last refresh) or outside 01/02.
        if opp.get("pipelineStageId") and opp.get("pipelineStageId") not in ctx.stages:
            refresh_pipelines(ctx)
            values = normalize_opportunity(opp, ctx)
    if not values["pipeline_key"]:
        if deal and deal.in_scope:
            deal.in_scope = False
            deal.save(update_fields=["in_scope", "last_synced_at"])
            _event(deal, "left_scope", deal.pipeline_key, ctx.pipeline_names.get(values["pipeline_id"], ""), now, source)
        return deal

    if partial:
        if not deal:
            return None
        keys = [k for k in PARTIAL_FIELDS if k not in ("status", "assigned_to")]
        if "status" in opp:
            keys.append("status")
        if "assignedTo" in opp:
            keys.append("assigned_to")
        values = {k: values[k] for k in keys}

    created = deal is None
    with transaction.atomic():
        if created:
            deal = Deal(ghl_id=opp_id)
            old = {}
        else:
            old = {k: getattr(deal, k) for k in ("stage_key", "stage_name", "status", "assigned_to", "ae_name", "analyst_name", "analyst2_name", "expected_close_date")}
        for key, val in values.items():
            setattr(deal, key, val)
        deal.in_scope = True
        deal.deleted = False
        deal.furthest_stage_index = max(deal.furthest_stage_index, deal.stage_index)

        stage_at = (None if partial else values.get("last_stage_change_at")) or now
        status_at = (None if partial else values.get("last_status_change_at")) or now
        change_at = (None if partial else values.get("ghl_updated_at")) or now

        # Lost / reopened.
        if deal.status in LOST_STATUSES and (created or old.get("status") not in LOST_STATUSES):
            deal.lost_at = status_at
        elif deal.status not in LOST_STATUSES:
            deal.lost_at = None
        deal.save()

        if created:
            created_at = deal.ghl_created_at or now
            _event(deal, "created", "", deal.stage_name, created_at, source)
            _milestone(deal, "quick_app", created_at, "created")
            if deal.stage_key and deal.stage_key != "quick_app":
                # GHL's own timestamp for entering the current stage — observed, not inferred.
                _milestone(deal, deal.stage_key, stage_at, "ghl_last_stage_change" if source == "sync" else source)
        else:
            if deal.stage_key != old["stage_key"] or deal.stage_name != old["stage_name"]:
                _event(deal, "stage_change", old["stage_name"], deal.stage_name, stage_at, source)
                _milestone(deal, deal.stage_key, stage_at, source)
            if deal.status != old["status"]:
                _event(deal, "status_change", old["status"], deal.status, status_at, source)
            if deal.assigned_to != old["assigned_to"]:
                _event(deal, "owner_change", old["assigned_to"], deal.assigned_to, change_at, source)
            if not partial:
                if deal.ae_name != old["ae_name"]:
                    _event(deal, "ae_change", old["ae_name"], deal.ae_name, change_at, source)
                if (deal.analyst_name, deal.analyst2_name) != (old["analyst_name"], old["analyst2_name"]):
                    _event(deal, "analyst_change", f"{old['analyst_name']} {old['analyst2_name']}".strip(),
                           f"{deal.analyst_name} {deal.analyst2_name}".strip(), change_at, source)
                if deal.expected_close_date != old["expected_close_date"]:
                    _event(deal, "close_date_change", old["expected_close_date"], deal.expected_close_date, change_at, source)

        _apply_milestone_fields(deal)
    return deal


def _apply_milestone_fields(deal):
    """funded_at / AE-at-application / AE-at-funding follow observed milestones."""
    changed = []
    if deal.funded_at is None and deal.stage_index >= FUNDED_INDEX:
        ms = deal.milestones.filter(key=MILESTONE_FUNDED).first()
        if ms:
            deal.funded_at = ms.reached_at
            changed.append("funded_at")
        # In Post Closing when first seen: funding date unknown → left empty (coverage gap).
    if deal.stage_index >= FUNDED_INDEX and not deal.ae_at_funding and deal.ae_name:
        deal.ae_at_funding = deal.ae_name
        changed.append("ae_at_funding")
    if not deal.ae_at_application and deal.ae_name and deal.furthest_stage_index >= STAGE_INDEX[MILESTONE_APP_COMPLETE]:
        deal.ae_at_application = deal.ae_name
        changed.append("ae_at_application")
    if changed:
        deal.save(update_fields=changed + ["last_synced_at"])


def mark_deleted(deal, source):
    if deal.deleted:
        return
    deal.deleted = True
    deal.in_scope = False
    deal.save(update_fields=["deleted", "in_scope", "last_synced_at"])
    _event(deal, "deleted", deal.stage_name, "", timezone.now(), source)


# ---------------------------------------------------------------- entry points

def sync_all(account=None):
    """Full pull of 01 + 02 pipelines. Returns the SyncRun."""
    run = SyncRun.objects.create(kind="full")
    stats = {"seen": 0, "created": 0, "missing_checked": 0, "deleted": 0}
    try:
        ctx = build_context(account, refresh=True)
        pipeline_ids = [pid for pid, name in ctx.pipeline_names.items() if name in PIPELINE_KEYS]
        opps = []
        for pid in pipeline_ids:
            opps.extend(ghl_client.iter_pipeline_opportunities(ctx.account.location_id, pid, ctx.token))
        ctx.programs = _programs_for(o["id"] for o in opps if o.get("id"))
        existing = set(Deal.objects.values_list("ghl_id", flat=True))
        seen = set()
        for opp in opps:
            upsert_opportunity(opp, ctx, source="sync")
            seen.add(opp.get("id"))
        stats["seen"] = len(seen)
        stats["created"] = len(seen - existing)

        # Deals we track that no longer show in 01/02: moved out or deleted.
        for deal in Deal.objects.filter(in_scope=True).exclude(ghl_id__in=seen):
            stats["missing_checked"] += 1
            try:
                data = ghl_client.get_opportunity(deal.ghl_id, access_token=ctx.token)
                upsert_opportunity(data.get("opportunity") or data, ctx, source="sync")
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code in (400, 404):
                    mark_deleted(deal, "sync")
                    stats["deleted"] += 1
                else:
                    raise

        settings_obj = CompanySettings.load()
        if not settings_obj.coverage_start:
            settings_obj.coverage_start = run.started_at
            settings_obj.save(update_fields=["coverage_start", "updated_at"])
        run.status = "success"
    except Exception as exc:  # noqa: BLE001 — recorded on the run, re-raised for Celery
        run.status = "failed"
        run.error = str(exc)[:2000]
        logger.exception("Dashboard full sync failed")
        raise
    finally:
        run.stats = stats
        run.finished_at = timezone.now()
        run.save()
    return run


def sync_opportunity(opportunity_id, account=None, source="webhook"):
    ctx = build_context(account)
    ctx.programs = _programs_for([opportunity_id])
    try:
        data = ghl_client.get_opportunity(opportunity_id, access_token=ctx.token)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code in (400, 404):
            deal = Deal.objects.filter(pk=opportunity_id).first()
            if deal:
                mark_deleted(deal, source)
            return deal
        raise
    return upsert_opportunity(data.get("opportunity") or data, ctx, source=source)


def ingest_webhook(payload):
    """Record what the webhook says happened, then refresh the full opportunity."""
    payload = payload or {}
    event_type = (payload.get("type") or payload.get("event") or "").strip()
    if event_type not in WEBHOOK_EVENT_TYPES:
        return {"skipped": True, "type": event_type}
    opp = dict(payload.get("opportunity") or payload)
    opp_id = opp.get("id") or payload.get("opportunityId")
    if not opp_id:
        return {"skipped": True, "reason": "missing_opportunity_id"}
    opp["id"] = opp_id

    run = SyncRun.objects.create(kind="webhook", stats={"type": event_type, "opportunity_id": opp_id})
    try:
        if event_type == "OpportunityDelete":
            deal = Deal.objects.filter(pk=opp_id).first()
            if deal:
                mark_deleted(deal, "webhook")
        else:
            if opp.get("pipelineStageId"):
                # The payload's stage is what happened at this moment; a later refetch
                # may already show a further auto-move (e.g. accepted → QC review).
                upsert_opportunity(opp, build_context(), source="webhook", partial=True)
            sync_opportunity(opp_id, source="webhook")
        run.status = "success"
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = str(exc)[:2000]
        logger.exception("Dashboard webhook ingest failed for %s", opp_id)
    finally:
        run.finished_at = timezone.now()
        run.save()
    return {"ok": run.status == "success", "type": event_type, "opportunity_id": opp_id}
