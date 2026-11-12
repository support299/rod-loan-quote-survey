"""
KPI calculations for the six dashboard modules.

Everything works from the locally synced Deal / DealMilestone tables. Data volume
is a few thousand deals, so calculations run in Python over one scoped dataset.
Every headline number carries the deal ids behind it for drill-through.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from .access import deal_scope_q, get_profile, get_role
from .constants import (
    FUNDED_INDEX,
    MILESTONE_APP_COMPLETE,
    MILESTONE_APPROVED,
    MILESTONE_FUNDED,
    MILESTONE_SUBMITTED_UW,
    MILESTONE_TERM_SHEET,
    ROLE_AE,
    ROLE_CEO,
    ROLE_PROCESSOR,
    STAGE_FLOW,
    STAGE_INDEX,
    STAGE_KEYS,
    STAGE_LABELS,
    STATUS_OPEN,
)
from .finance import FinanceContext, credited_ae, deal_financials
from .models import CompanySettings, DashboardProfile, Deal, DealIssue, DealMilestone, MonthlyTarget, SyncRun
from .periods import business_days, local_today, month_bounds, months_overlapping, prior_period

ZERO = Decimal("0")
BACKFILL_SOURCE = "ghl_last_stage_change"
APP_COMPLETE_INDEX = STAGE_INDEX[MILESTONE_APP_COMPLETE]
APPROVED_INDEX = STAGE_INDEX[MILESTONE_APPROVED]

FILTER_FIELDS = {
    "ae": "ae_name__iexact",
    "program": "program__iexact",
    "source": "source__iexact",
    "broker": "broker_name__iexact",
    "referrer": "referral_source__iexact",
    "stage": "stage_key",
    "status": "status",
    "borrower": "contact_id",
}


# ---------------------------------------------------------------- dataset

@dataclass
class Dataset:
    deals: list
    milestones: dict  # deal id -> {key: datetime}
    fin: dict  # deal id -> financials
    settings: CompanySettings
    fctx: FinanceContext
    now: object
    tz: object
    role: str = ""
    profile: object = None
    open_issue_counts: dict = field(default_factory=dict)
    backfilled: set = field(default_factory=set)  # (deal id, key) dated from GHL's lastStageChangeAt

    def ms(self, deal, key):
        return self.milestones.get(deal.ghl_id, {}).get(key)

    def observed_ms(self, deal, key):
        """Milestone only if it was observed live (not backfilled at the first sync)."""
        if (deal.ghl_id, key) in self.backfilled:
            return None
        return self.ms(deal, key)


def base_queryset(user, params=None, system=False):
    """In-scope, non-excluded deals the user may see. system=True: no user scope (background jobs)."""
    params = params or {}
    qs = Deal.objects.filter(in_scope=True, deleted=False).exclude(meta__excluded=True)
    q = None if system else deal_scope_q(user)
    if q is not None:
        qs = qs.filter(q)
    for param, lookup in FILTER_FIELDS.items():
        value = (params.get(param) or "").strip()
        if value:
            qs = qs.filter(**{lookup: value})
    processor = (params.get("processor") or "").strip()
    if processor:
        qs = qs.filter(Q(analyst_name__iexact=processor) | Q(analyst2_name__iexact=processor))
    return qs.select_related("meta")


def load_dataset(user, params=None, system=False):
    settings = CompanySettings.load()
    fctx = FinanceContext.build(settings)
    deals = list(base_queryset(user, params, system=system))
    ids = [d.ghl_id for d in deals]
    milestones = defaultdict(dict)
    backfilled = set()
    for deal_id, key, at, source in DealMilestone.objects.filter(deal_id__in=ids).values_list("deal_id", "key", "reached_at", "source"):
        milestones[deal_id][key] = at
        if source == BACKFILL_SOURCE:
            backfilled.add((deal_id, key))
    issue_counts = Counter(
        DealIssue.objects.filter(deal_id__in=ids).exclude(status=DealIssue.STATUS_RESOLVED).values_list("deal_id", flat=True)
    )
    from zoneinfo import ZoneInfo

    return Dataset(
        deals=deals,
        milestones=milestones,
        fin={d.ghl_id: deal_financials(d, fctx) for d in deals},
        settings=settings,
        fctx=fctx,
        now=timezone.now(),
        tz=ZoneInfo(settings.timezone),
        role=ROLE_CEO if system else get_role(user),
        profile=None if system else get_profile(user),
        open_issue_counts=dict(issue_counts),
        backfilled=backfilled,
    )


# ---------------------------------------------------------------- small helpers

def _num(value):
    if value is None:
        return None
    return float(value)


def _sum(values):
    total = ZERO
    for v in values:
        if v is not None:
            total += v
    return total


def _rate(numerator, denominator):
    return round(numerator / denominator * 100, 1) if denominator else None


def _ids(deals):
    return [d.ghl_id for d in deals]


def is_paused(deal):
    meta = _meta(deal)
    return bool(meta and meta.paused)


def _meta(deal):
    try:
        return deal.meta
    except Deal.meta.RelatedObjectDoesNotExist:
        return None


def is_open(deal):
    return deal.status == STATUS_OPEN and not deal.is_funded


def outcome(deal):
    if deal.is_funded:
        return "funded"
    if deal.is_lost:
        return "lost"
    return "unresolved"


def days_in_stage(ds, deal):
    entered = ds.ms(deal, deal.stage_key) or deal.last_stage_change_at or deal.ghl_created_at
    if not entered:
        return None
    return max(0, (ds.now - entered).days)


def loss_reason(deal):
    meta = _meta(deal)
    # GHL is the source of truth; dashboard-only values fill in when GHL is empty.
    return deal.ghl_loss_reason or (meta and meta.loss_primary_reason) or "Unknown"


def stage_probability(ds, deal):
    value = (ds.settings.stage_probabilities or {}).get(deal.stage_key)
    try:
        return Decimal(str(value)) if value is not None else None
    except (TypeError, ValueError, ArithmeticError):
        return None


def is_stalled(ds, deal):
    """Over the stage threshold but not yet dormant — the deals that need intervention."""
    if not is_open(deal) or not deal.stage_key:
        return False
    days = days_in_stage(ds, deal)
    return days is not None and ds.settings.stall_days_for(deal.stage_key) < days <= ds.settings.dormant_days


def is_dormant(ds, deal):
    if not is_open(deal) or not deal.stage_key:
        return False
    days = days_in_stage(ds, deal)
    return days is not None and days > max(ds.settings.dormant_days, ds.settings.stall_days_for(deal.stage_key))


def cohort_summary(deals, ds=None, cohort_key=None):
    """Funded + lost + unresolved always add to 100% of the cohort."""
    counts = Counter(outcome(d) for d in deals)
    total = len(deals)
    ages = []
    if ds and cohort_key:
        for d in deals:
            at = ds.ms(d, cohort_key)
            if at:
                ages.append((ds.now - at).days)
    return {
        "cohort": total,
        "funded": counts["funded"],
        "lost": counts["lost"],
        "unresolved": counts["unresolved"],
        "funded_share": _rate(counts["funded"], total),
        "lost_share": _rate(counts["lost"], total),
        "unresolved_share": _rate(counts["unresolved"], total),
        "avg_cohort_age_days": round(sum(ages) / len(ages), 1) if ages else None,
        "deal_ids": _ids(deals),
        "funded_ids": [d.ghl_id for d in deals if outcome(d) == "funded"],
        "lost_ids": [d.ghl_id for d in deals if outcome(d) == "lost"],
        "unresolved_ids": [d.ghl_id for d in deals if outcome(d) == "unresolved"],
    }


def conversion(cohort, converted_test, label, note=""):
    converted = [d for d in cohort if converted_test(d)]
    lost = [d for d in cohort if d not in converted and d.is_lost]
    return {
        "label": label,
        "note": note,
        "cohort": len(cohort),
        "converted": len(converted),
        "rate": _rate(len(converted), len(cohort)),
        "lost": len(lost),
        "unresolved": len(cohort) - len(converted) - len(lost),
        "deal_ids": _ids(cohort),
        "converted_ids": _ids(converted),
    }


def funded_in(ds, period):
    return [d for d in ds.deals if d.is_funded and period.contains(d.funded_at)]


def milestone_in(ds, key, period):
    """
    Deals that first reached `key` in the period. Backfilled milestones are left out:
    at the first sync only each deal's *current* stage gets a date, so counting them
    would make every pre-coverage cohort look like 0% conversion.
    """
    return [d for d in ds.deals if period.contains(ds.observed_ms(d, key))]


def created_in(ds, period):
    return [d for d in ds.deals if period.contains(d.ghl_created_at)]


def lost_in(ds, period):
    return [d for d in ds.deals if d.is_lost and period.contains(d.lost_at)]


def financial_totals(ds, deals):
    fins = [ds.fin[d.ghl_id] for d in deals]
    return {
        "count": len(deals),
        "volume": _num(_sum(f["loan_amount"] for f in fins)),
        "volume_missing": sum(1 for f in fins if f["loan_amount"] is None),
        "revenue": _num(_sum(f["revenue"] for f in fins)),
        "revenue_missing": sum(1 for f in fins if f["revenue"] is None),
        "revenue_estimated": sum(1 for f in fins if f["revenue_estimated"]),
        "net_revenue": _num(_sum(f["net_revenue"] for f in fins)),
        "profit": _num(_sum(f["profit"] for f in fins)),
        "cost_complete": sum(1 for f in fins if f["complete"]),
        "provisional": any(not f["complete"] for f in fins),
        "deal_ids": _ids(deals),
    }


# ---------------------------------------------------------------- targets

def _monthly_target(ds, month, attr, profile=None, ae_default=False):
    """attr: 'volume_target' | 'revenue_target'. ae_default: an AE without a login/profile."""
    if ae_default:
        return ds.settings.default_ae_volume_target if attr == "volume_target" else None
    row = MonthlyTarget.objects.filter(month=month, profile=profile).first()
    if row and getattr(row, attr) is not None:
        return getattr(row, attr)
    if profile is not None:
        value = getattr(profile, f"monthly_{attr}")
        if value is None and attr == "volume_target":
            value = ds.settings.default_ae_volume_target
        return value
    return getattr(ds.settings, f"monthly_{attr}")


def _target_window(period):
    """MTD/QTD/YTD compare against the whole month/quarter/year target (pacing shown separately)."""
    start, end = period.start, period.end
    if period.key == "mtd":
        end = month_bounds(start)[1]
    elif period.key == "qtd":
        end = month_bounds(date(start.year, start.month + 2, 1))[1]
    elif period.key == "ytd":
        end = date(start.year, 12, 31)
    return start, end


def period_target(ds, period, attr, profile=None, ae_default=False):
    """Monthly targets; partial months (rolling/custom/today/wtd) are prorated by business days."""
    start, end = _target_window(period)
    total, any_target = ZERO, False
    for month in months_overlapping(start, end):
        first, last = month_bounds(month)
        value = _monthly_target(ds, month, attr, profile, ae_default)
        if value is None:
            continue
        any_target = True
        overlap = business_days(max(first, start), min(last, end))
        month_days = business_days(first, last)
        if start <= first and end >= last:
            total += value
        elif month_days:
            total += value * Decimal(overlap) / Decimal(month_days)
    return total if any_target else None


def scope_target_profile(ds):
    """AEs see their own target; company scope otherwise; processors have none."""
    if ds.role == ROLE_AE:
        return ds.profile, True
    if ds.role == ROLE_PROCESSOR:
        return None, False
    return None, True


def month_pacing(ds):
    today = local_today(ds.tz)
    first, last = month_bounds(today)
    elapsed = business_days(first, today)
    total = business_days(first, last)
    return {"business_days_elapsed": elapsed, "business_days_total": total, "pace_pct": _rate(elapsed, total)}


def _vs_target(actual, target, pace_pct=None):
    if target is None:
        return {"target": None, "variance": None, "attainment_pct": None, "pace_target": None}
    target_f = float(target)
    return {
        "target": round(target_f, 2),
        "variance": round((actual or 0) - target_f, 2),
        "attainment_pct": _rate(actual or 0, target_f),
        "pace_target": round(target_f * pace_pct / 100, 2) if pace_pct is not None else None,
    }


# ---------------------------------------------------------------- weekly trends (charts)

def weekly_trends(ds, weeks=12):
    """Last `weeks` Mon–Sun buckets (company timezone) for the trend charts."""
    from .periods import Period

    today = local_today(ds.tz)
    first_monday = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks - 1)
    rows = []
    for i in range(weeks):
        start = first_monday + timedelta(weeks=i)
        p = Period("custom", start, start + timedelta(days=6), ds.tz)
        funded = funded_in(ds, p)
        rows.append({
            "week": start.isoformat(),
            "new_opportunities": len(created_in(ds, p)),
            "funded": len(funded),
            "funded_volume": _num(_sum(d.total_loan_amount for d in funded)),
            "lost": len(lost_in(ds, p)),
        })
    return rows


# ---------------------------------------------------------------- module 1: CEO overview

def month_end_forecast(ds):
    today = local_today(ds.tz)
    first, last = month_bounds(today)
    from .periods import Period

    month = Period("mtd", first, today, ds.tz)
    actual_deals = funded_in(ds, month)
    actual = _sum(ds.fin[d.ghl_id]["revenue"] for d in actual_deals)
    open_value = ZERO
    missing_revenue, missing_probability, missing_close = [], [], []
    contributing = []
    for d in ds.deals:
        if not is_open(d) or is_paused(d):
            continue
        if d.expected_close_date and d.expected_close_date > last:
            continue  # expected after this month
        revenue = ds.fin[d.ghl_id]["revenue"]
        prob = stage_probability(ds, d)
        if revenue is None:
            missing_revenue.append(d.ghl_id)
            continue
        if prob is None:
            missing_probability.append(d.ghl_id)
            continue
        if not d.expected_close_date:
            missing_close.append(d.ghl_id)
        open_value += revenue * prob / 100
        contributing.append(d.ghl_id)
    return {
        "month": first.isoformat(),
        "actual_mtd_revenue": _num(actual),
        "open_forecast_revenue": round(float(open_value), 2),
        "expected_month_end_revenue": round(float(actual + open_value), 2),
        "probabilities_are_assumptions": True,
        "stage_probabilities": ds.settings.stage_probabilities,
        "completeness": {
            "contributing_deals": len(contributing),
            "missing_revenue": len(missing_revenue),
            "missing_probability": len(missing_probability),
            "missing_expected_close_date": len(missing_close),
        },
        "deal_ids": contributing,
        "missing_revenue_ids": missing_revenue,
    }


def overview(ds, period):
    prior = prior_period(period)
    pacing = month_pacing(ds) if period.key == "mtd" else None
    pace_pct = pacing["pace_pct"] if pacing else None
    target_profile, has_target = scope_target_profile(ds)

    funded = financial_totals(ds, funded_in(ds, period))
    funded_prior = financial_totals(ds, funded_in(ds, prior))
    volume_target = period_target(ds, period, "volume_target", target_profile) if has_target else None
    revenue_target = period_target(ds, period, "revenue_target", target_profile) if has_target else None

    apps = created_in(ds, period)
    complete = milestone_in(ds, MILESTONE_APP_COMPLETE, period)
    uw = milestone_in(ds, MILESTONE_SUBMITTED_UW, period)

    today = local_today(ds.tz)
    horizon = today + timedelta(days=30)
    upcoming = [
        d for d in ds.deals
        if is_open(d) and (
            (d.expected_close_date and today <= d.expected_close_date <= horizon)
            or (not d.expected_close_date and d.stage_key == MILESTONE_APPROVED)
        )
    ]
    readiness = Counter(
        "ready" if d.stage_key == MILESTONE_APPROVED else "underwriting" if d.stage_key == MILESTONE_SUBMITTED_UW else "earlier"
        for d in upcoming
    )
    stalled = [d for d in ds.deals if is_stalled(ds, d)]
    dormant = [d for d in ds.deals if is_dormant(ds, d)]

    return {
        "period": period.as_dict(),
        "prior_period": prior.as_dict(),
        "pacing": pacing,
        "funded_revenue": {
            "actual": funded["revenue"],
            "prior": funded_prior["revenue"],
            "missing_revenue": funded["revenue_missing"],
            "estimated": funded["revenue_estimated"],
            **_vs_target(funded["revenue"], revenue_target, pace_pct),
            "deal_ids": funded["deal_ids"],
        },
        "contribution_profit": {
            "actual": funded["profit"],
            "net_revenue": funded["net_revenue"],
            "prior": funded_prior["profit"],
            "provisional": funded["provisional"],
            "cost_complete": funded["cost_complete"],
            "deals": funded["count"],
            "deal_ids": funded["deal_ids"],
        },
        "funded_loans": {
            "count": funded["count"],
            "volume": funded["volume"],
            "volume_missing": funded["volume_missing"],
            "prior_count": funded_prior["count"],
            "prior_volume": funded_prior["volume"],
            **_vs_target(funded["volume"], volume_target, pace_pct),
            "deal_ids": funded["deal_ids"],
        },
        "applications": {
            "received": len(apps),
            "complete": len(complete),
            "incomplete": len([d for d in apps if d.furthest_stage_index < APP_COMPLETE_INDEX]),
            "prior_received": len(created_in(ds, prior)),
            "prior_complete": len(milestone_in(ds, MILESTONE_APP_COMPLETE, prior)),
            "received_ids": _ids(apps),
            "complete_ids": _ids(complete),
        },
        "submitted_to_underwriting": {
            "count": len(uw),
            "prior": len(milestone_in(ds, MILESTONE_SUBMITTED_UW, prior)),
            "deal_ids": _ids(uw),
        },
        "forecast": month_end_forecast(ds),
        "upcoming_closings": {
            "count": len(upcoming),
            "loan_amount": _num(_sum(d.total_loan_amount for d in upcoming)),
            "expected_revenue": _num(_sum(ds.fin[d.ghl_id]["revenue"] for d in upcoming)),
            "missing_expected_close_date": sum(1 for d in upcoming if not d.expected_close_date),
            "readiness": dict(readiness),
            "deal_ids": _ids(upcoming),
        },
        "fallout": cohort_summary(milestone_in(ds, MILESTONE_APP_COMPLETE, period), ds, MILESTONE_APP_COMPLETE),
        "intervention": {
            "count": len(stalled),
            "revenue_at_risk": _num(_sum(ds.fin[d.ghl_id]["revenue"] for d in stalled)),
            "revenue_missing": sum(1 for d in stalled if ds.fin[d.ghl_id]["revenue"] is None),
            "threshold_days": ds.settings.stall_days_default,
            "deal_ids": _ids(stalled),
        },
        "dormant": {
            "count": len(dormant),
            "threshold_days": ds.settings.dormant_days,
            "deal_ids": _ids(dormant),
        },
        "coverage_start": ds.settings.coverage_start,
        "trends": weekly_trends(ds),
    }


# ---------------------------------------------------------------- module 2: funnel

def funnel(ds, period):
    inventory = []
    for key in STAGE_KEYS:
        deals = [d for d in ds.deals if d.stage_key == key and d.status == STATUS_OPEN]
        inventory.append({
            "stage_key": key,
            "label": STAGE_LABELS[key],
            "pipeline": next(p for k, p, _ in STAGE_FLOW if k == key),
            "count": len(deals),
            "paused": sum(1 for d in deals if is_paused(d)),
            "loan_amount": _num(_sum(d.total_loan_amount for d in deals)),
            "stalled": sum(1 for d in deals if is_stalled(ds, d)),
            "dormant": sum(1 for d in deals if is_dormant(ds, d)),
            "deal_ids": _ids(deals),
        })
    status_counts = Counter(d.status or "unknown" for d in ds.deals)

    coverage = ds.settings.coverage_start
    skipped = {}
    for key in STAGE_KEYS[1:]:
        idx = STAGE_INDEX[key]
        passed = [
            d for d in ds.deals
            if d.furthest_stage_index > idx and key not in ds.milestones.get(d.ghl_id, {})
            and coverage and d.ghl_created_at and d.ghl_created_at >= coverage
        ]
        skipped[key] = len(passed)

    activity = {
        "new_opportunities": created_in(ds, period),
        "term_sheets_issued": milestone_in(ds, MILESTONE_TERM_SHEET, period),
        "applications_complete": milestone_in(ds, MILESTONE_APP_COMPLETE, period),
        "submitted_to_underwriting": milestone_in(ds, MILESTONE_SUBMITTED_UW, period),
        "approved": milestone_in(ds, MILESTONE_APPROVED, period),
        "funded": funded_in(ds, period),
        "lost": lost_in(ds, period),
    }

    uw_cohort = milestone_in(ds, MILESTONE_SUBMITTED_UW, period)
    approved = [d for d in uw_cohort if d.furthest_stage_index >= APPROVED_INDEX]
    withdrawn = [d for d in uw_cohort if d not in approved and d.is_lost and (_meta(d) and _meta(d).loss_outcome == "withdrawn")]
    declined = [d for d in uw_cohort if d not in approved and d.is_lost and d not in withdrawn]
    pending = [d for d in uw_cohort if d not in approved and not d.is_lost]

    reached_app = lambda d: d.furthest_stage_index >= APP_COMPLETE_INDEX  # noqa: E731
    return {
        "period": period.as_dict(),
        "coverage_start": coverage,
        "trends": weekly_trends(ds),
        "inventory": inventory,
        "status_counts": dict(status_counts),
        "skipped_stages": skipped,
        "activity": {k: {"count": len(v), "deal_ids": _ids(v)} for k, v in activity.items()},
        "conversions": [
            conversion(created_in(ds, period), reached_app, "New opportunity → application complete",
                       "Opportunities created in the period that have reached Onboarded as of now."),
            conversion(milestone_in(ds, MILESTONE_TERM_SHEET, period), reached_app, "Term sheet → application complete"),
            {**conversion(milestone_in(ds, MILESTONE_APP_COMPLETE, period), lambda d: d.is_funded, "Application complete → funded"),
             "avg_cohort_age_days": cohort_summary(milestone_in(ds, MILESTONE_APP_COMPLETE, period), ds, MILESTONE_APP_COMPLETE)["avg_cohort_age_days"]},
            conversion(milestone_in(ds, MILESTONE_APPROVED, period), lambda d: d.is_funded, "Approval (clear to close) → funded"),
        ],
        "underwriting_approval": {
            "cohort": len(uw_cohort),
            "approved": len(approved),
            "declined": len(declined),
            "withdrawn": len(withdrawn),
            "pending": len(pending),
            "rate": _rate(len(approved), len(approved) + len(declined)),
            "note": "Approval = reached Clear to Close. Latest status as of now; first-submission cohort.",
            "deal_ids": _ids(uw_cohort),
        },
    }


# ---------------------------------------------------------------- module 3: loss analysis

def loss(ds, period):
    lost = lost_in(ds, period)
    by_reason, by_stage, by_ae = defaultdict(list), defaultdict(list), defaultdict(list)
    for d in lost:
        by_reason[loss_reason(d)].append(d)
        by_stage[d.stage_name or "Unknown"].append(d)
        by_ae[d.ae_name or "Unknown"].append(d)

    def table(groups):
        rows = []
        for name, deals in groups.items():
            rows.append({
                "name": name,
                "count": len(deals),
                "share": _rate(len(deals), len(lost)),
                "potential_revenue": _num(_sum(ds.fin[d.ghl_id]["revenue"] for d in deals)),
                "deal_ids": _ids(deals),
            })
        return sorted(rows, key=lambda r: -r["count"])

    recoverable = [d for d in ds.deals if d.is_lost and _meta(d) and _meta(d).recoverable == "yes"]
    missing_info = [d for d in ds.deals if d.is_lost and loss_reason(d) == "Unknown"]

    today = local_today(ds.tz)
    cohorts = []
    month = today.replace(day=1)
    from .periods import Period

    for _ in range(6):
        first, last = month_bounds(month)
        cohort = milestone_in(ds, MILESTONE_APP_COMPLETE, Period("custom", first, last, ds.tz))
        cohorts.append({"month": first.isoformat(), **cohort_summary(cohort, ds, MILESTONE_APP_COMPLETE)})
        month = (first - timedelta(days=1)).replace(day=1)

    return {
        "period": period.as_dict(),
        "coverage_start": ds.settings.coverage_start,
        "lost": {
            "count": len(lost),
            "potential_revenue_lost": _num(_sum(ds.fin[d.ghl_id]["revenue"] for d in lost)),
            "revenue_missing": sum(1 for d in lost if ds.fin[d.ghl_id]["revenue"] is None),
            "pre_application": sum(1 for d in lost if d.furthest_stage_index < APP_COMPLETE_INDEX),
            "post_application": sum(1 for d in lost if d.furthest_stage_index >= APP_COMPLETE_INDEX),
            "deal_ids": _ids(lost),
        },
        "by_reason": table(by_reason),
        "by_stage": table(by_stage),
        "by_ae": table(by_ae),
        "recoverable": {"count": len(recoverable), "deal_ids": _ids(recoverable)},
        "missing_loss_info": {"count": len(missing_info), "deal_ids": _ids(missing_info)},
        "application_cohorts": cohorts,
        "notes": [
            "A deal counts as lost when its GHL status is Lost or Abandoned.",
            "Application cohorts only include deals seen reaching Onboarded after stage tracking started.",
            "Pre-application losses appear here but not in application fallout.",
            "Cohort shares (funded + lost + unresolved) always total 100%.",
        ],
    }


# ---------------------------------------------------------------- module 4: AE scorecards

def _prior_funded_contacts(ds, before):
    return {d.contact_id for d in ds.deals if d.is_funded and d.funded_at and d.funded_at < before and d.contact_id}


def scorecards(ds, period):
    names = set()
    for d in ds.deals:
        for n in (credited_ae(d), d.ae_name, d.ae_at_application):
            if n:
                names.add(n)
    for p in DashboardProfile.objects.filter(role=ROLE_AE).exclude(ae_name=""):
        names.add(p.ae_name)
    if ds.role == ROLE_AE and ds.profile:
        names = {n for n in names if n.lower() == (ds.profile.ae_name or "").lower()}
    profiles = {p.ae_name.lower(): p for p in DashboardProfile.objects.exclude(ae_name="")}
    returning = _prior_funded_contacts(ds, period.start_dt)

    rows = []
    for name in sorted(names, key=str.lower):
        low = name.lower()
        funded = [d for d in funded_in(ds, period) if credited_ae(d).lower() == low]
        totals = financial_totals(ds, funded)
        complete = [ds.fin[d.ghl_id] for d in funded if ds.fin[d.ghl_id]["profit"] is not None]
        pipeline = [d for d in ds.deals if d.ae_name.lower() == low and is_open(d) and d.furthest_stage_index >= APP_COMPLETE_INDEX]
        cohort = [d for d in milestone_in(ds, MILESTONE_APP_COMPLETE, period) if (d.ae_at_application or d.ae_name).lower() == low]
        submissions = [d for d in milestone_in(ds, MILESTONE_SUBMITTED_UW, period) if d.ae_name.lower() == low]
        repeat = [d for d in funded if d.contact_id in returning]
        profile = profiles.get(low)
        volume_target = period_target(ds, period, "volume_target", profile, ae_default=profile is None)
        revenue_target = period_target(ds, period, "revenue_target", profile) if profile else None
        rows.append({
            "ae": name,
            "has_login": profile is not None,
            "funded_count": totals["count"],
            "funded_volume": totals["volume"],
            "revenue": totals["revenue"],
            "net_revenue": totals["net_revenue"],
            "profit": totals["profit"],
            "cost_complete": totals["cost_complete"],
            "avg_profit_per_loan": round(sum(float(f["profit"]) for f in complete) / len(complete), 2) if complete else None,
            "volume": _vs_target(totals["volume"], volume_target),
            "revenue_vs_target": _vs_target(totals["revenue"], revenue_target),
            "pipeline": {
                "count": len(pipeline),
                "potential_revenue": _num(_sum(ds.fin[d.ghl_id]["revenue"] for d in pipeline)),
                "loan_amount": _num(_sum(d.total_loan_amount for d in pipeline)),
                "next_expected_close": min((d.expected_close_date for d in pipeline if d.expected_close_date), default=None),
                "deal_ids": _ids(pipeline),
            },
            "submissions": {"count": len(submissions), "deal_ids": _ids(submissions)},
            "conversion": cohort_summary(cohort, ds, MILESTONE_APP_COMPLETE),
            "repeat_borrower": {"count": len(repeat), "volume": _num(_sum(d.total_loan_amount for d in repeat)), "deal_ids": _ids(repeat)},
            "self_generated": None,
            "forecast_reliability": None,
            "open_issues": sum(ds.open_issue_counts.get(d.ghl_id, 0) for d in ds.deals if d.ae_name.lower() == low),
            "funded_ids": totals["deal_ids"],
        })
    rows.sort(key=lambda r: (r["profit"] is None, -(r["profit"] or 0), -(r["funded_volume"] or 0)))
    return {
        "period": period.as_dict(),
        "rows": rows,
        "notes": [
            "Funded production is credited to the AE on the loan at closing.",
            "Conversion follows the AE at application (Onboarded).",
            "Self-generated production and forecast reliability need inputs that do not exist yet.",
        ],
    }


# ---------------------------------------------------------------- module 5: relationship leaderboards

def _dimension_key(dimension, deal):
    if dimension == "borrower":
        if not deal.contact_id:
            return "unknown", "Unknown"
        return deal.contact_id, deal.contact_name or deal.contact_company or deal.entity_name or deal.contact_id
    if dimension == "broker":
        key = (deal.broker_email or deal.broker_name).strip().lower()
        if not key:
            return "direct", "Direct / no broker"
        return key, deal.broker_name or deal.broker_email
    key = (deal.referral_source or deal.source).strip()
    return (key.lower(), key) if key else ("unknown", "Unknown")


def leaderboard(ds, period, dimension="borrower", sort="profit"):
    groups = defaultdict(list)
    labels = {}
    for d in ds.deals:
        key, label = _dimension_key(dimension, d)
        groups[key].append(d)
        labels.setdefault(key, label)

    inactivity = ds.settings.relationship_inactivity_days
    rows = []
    for key, deals in groups.items():
        funded_period = [d for d in deals if d.is_funded and period.contains(d.funded_at)]
        funded_life = [d for d in deals if d.is_funded]
        open_deals = [d for d in deals if is_open(d)]
        if not (funded_life or open_deals or any(period.contains(d.ghl_created_at) for d in deals)):
            continue
        totals = financial_totals(ds, funded_period)
        complete = [ds.fin[d.ghl_id]["profit"] for d in funded_period if ds.fin[d.ghl_id]["profit"] is not None]
        last_funded = max((d.funded_at for d in funded_life if d.funded_at), default=None)
        days_since = (ds.now - last_funded).days if last_funded else None
        forecast = _sum(
            ds.fin[d.ghl_id]["revenue"] * stage_probability(ds, d) / 100
            for d in open_deals
            if ds.fin[d.ghl_id]["revenue"] is not None and stage_probability(ds, d) is not None
        )
        reasons = Counter(loss_reason(d) for d in deals if d.is_lost)
        rows.append({
            "key": key,
            "name": labels[key],
            "funded_count": totals["count"],
            "funded_volume": totals["volume"],
            "revenue": totals["revenue"],
            "profit": totals["profit"],
            "cost_complete": totals["cost_complete"],
            "avg_profit_per_loan": round(float(sum(complete)) / len(complete), 2) if complete else None,
            "lifetime": {"funded_count": len(funded_life), "funded_volume": _num(_sum(d.total_loan_amount for d in funded_life))},
            "conversion": {k: v for k, v in cohort_summary([d for d in deals if d.furthest_stage_index >= APP_COMPLETE_INDEX]).items() if not k.endswith("_ids")},
            "pipeline": {
                "count": len(open_deals),
                "potential_revenue": _num(_sum(ds.fin[d.ghl_id]["revenue"] for d in open_deals)),
                "forecast_revenue": _num(forecast),
                "next_expected_close": min((d.expected_close_date for d in open_deals if d.expected_close_date), default=None),
            },
            "last_funded_at": last_funded,
            "days_since_funded": days_since,
            "never_funded": not funded_life,
            "inactive": bool(funded_life) and days_since is not None and days_since > inactivity,
            "loss_reasons": [{"reason": r, "count": c} for r, c in reasons.most_common(3)],
            "deal_ids": _ids(deals),
            "funded_ids": totals["deal_ids"],
        })

    sort_keys = {"profit": "profit", "revenue": "revenue", "funded_count": "funded_count", "volume": "funded_volume"}
    sk = sort_keys.get(sort, "profit")
    rows.sort(key=lambda r: (-(r[sk] or 0), -(r["lifetime"]["funded_count"]), r["name"].lower()))

    period_revenue = sum(r["revenue"] or 0 for r in rows)
    by_revenue = sorted((r["revenue"] or 0 for r in rows), reverse=True)
    concentration = {
        f"top_{n}": _rate(sum(by_revenue[:n]), period_revenue) for n in (1, 5, 10)
    }
    extra = {}
    if dimension == "borrower":
        funded_contacts = {d.contact_id for d in funded_in(ds, period) if d.contact_id}
        returning = funded_contacts & _prior_funded_contacts(ds, period.start_dt)
        extra["repeat_borrower_rate"] = {
            "returning": len(returning),
            "distinct_funded": len(funded_contacts),
            "rate": _rate(len(returning), len(funded_contacts)),
        }
    else:
        first_ts, first_funded = 0, 0
        for key, deals in groups.items():
            ts_dates = [ds.ms(d, MILESTONE_TERM_SHEET) for d in deals if ds.ms(d, MILESTONE_TERM_SHEET)]
            fund_dates = [d.funded_at for d in deals if d.funded_at]
            if ts_dates and period.contains(min(ts_dates)):
                first_ts += 1
            if fund_dates and period.contains(min(fund_dates)):
                first_funded += 1
        extra["activation"] = {"first_qualified_in_period": first_ts, "first_funded_in_period": first_funded}

    return {
        "period": period.as_dict(),
        "dimension": dimension,
        "sort": sk,
        "rows": rows[:200],
        "total_rows": len(rows),
        "revenue_concentration": concentration,
        **extra,
        "notes": ["Totals across different leaderboards must not be added together as company revenue."],
    }


# ---------------------------------------------------------------- data status

def data_status(settings=None):
    settings = settings or CompanySettings.load()
    last_ok = SyncRun.objects.filter(kind="full", status="success").order_by("-finished_at").first()
    last_any = SyncRun.objects.filter(kind="full").order_by("-started_at").first()
    last_hook = SyncRun.objects.filter(kind="webhook").order_by("-started_at").first()
    stale = True
    if last_ok and last_ok.finished_at:
        stale = (timezone.now() - last_ok.finished_at) > timedelta(minutes=settings.stale_sync_minutes)
    return {
        "last_successful_sync": last_ok.finished_at if last_ok else None,
        "last_sync_status": last_any.status if last_any else None,
        "last_sync_error": (last_any.error if last_any and last_any.status == "failed" else ""),
        "last_webhook_at": last_hook.started_at if last_hook else None,
        "stale": stale,
        "coverage_start": settings.coverage_start,
        "timezone": settings.timezone,
    }
