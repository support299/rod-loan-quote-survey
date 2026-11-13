"""
Operational exception engine (module 6) + notifications.

refresh_issues() re-evaluates every in-scope deal: new triggers open an issue and
notify the owner, cleared triggers auto-resolve (history is kept), expired snoozes
re-open. Thresholds come from CompanySettings and are configurable, not SLAs.
"""
import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .constants import (
    ISSUE_CLOSING_MISSED,
    ISSUE_INCOMPLETE_APP,
    ISSUE_MISSING_FINANCIALS,
    ISSUE_MISSING_LOSS,
    ISSUE_NO_PROGRESS,
    ISSUE_PAUSE_OVERDUE,
    ISSUE_STALLED,
    ISSUE_TYPE_CHOICES,
    ROLE_CEO,
    STAGE_INDEX,
)
from .metrics import (
    APP_COMPLETE_INDEX,
    APPROVED_INDEX,
    _meta,
    days_in_stage,
    is_dormant,
    is_open,
    is_paused,
    load_dataset,
    local_today,
    loss_reason,
)
from .models import DashboardProfile, DealEvent, DealIssue, DealNote, DealTask, Notification

logger = logging.getLogger(__name__)
ISSUE_LABELS = dict(ISSUE_TYPE_CHOICES)
ACCEPTED_INDEX = STAGE_INDEX["term_sheet_accepted"]


def notify(users, kind, title, body="", deal=None, issue=None, task=None):
    seen = set()
    for user in users:
        if not user or user.pk in seen:
            continue
        seen.add(user.pk)
        Notification.objects.create(recipient=user, kind=kind, title=title[:255], body=body, deal=deal, issue=issue, task=task)


def ceo_users():
    User = get_user_model()
    return list(User.objects.filter(dashboard_profile__role=ROLE_CEO, is_active=True))


class Owners:
    def __init__(self):
        self.by_ae, self.by_analyst = {}, {}
        for p in DashboardProfile.objects.select_related("user").filter(user__is_active=True):
            if p.ae_name:
                self.by_ae[p.ae_name.lower()] = p
            if p.analyst_name:
                self.by_analyst[p.analyst_name.lower()] = p

    def ae(self, deal):
        return self.by_ae.get((deal.ae_name or "").lower()), deal.ae_name

    def processor(self, deal):
        """Processor 1/2 first (Rod: separate from analysts), then the loan analysts, then the AE."""
        for name in (deal.processor_name, deal.processor2_name, deal.analyst_name, deal.analyst2_name):
            if name and name.lower() in self.by_analyst:
                return self.by_analyst[name.lower()], name
        for name in (deal.processor_name, deal.processor2_name, deal.analyst_name, deal.analyst2_name):
            if name:
                return None, name
        return self.ae(deal)

    def stage_owner(self, deal):
        return self.processor(deal) if deal.pipeline_key == "processing" else self.ae(deal)


def _last_human_activity(deal_ids):
    latest = {}
    for model, field in ((DealNote, "created_at"), (DealTask, "created_at")):
        for deal_id, at in model.objects.filter(deal_id__in=deal_ids).values_list("deal_id", field):
            if at and (deal_id not in latest or at > latest[deal_id]):
                latest[deal_id] = at
    return latest


def evaluate(ds, owners, activity):
    """Yield (deal, issue_type, severity, days_overdue, details, (owner_profile, owner_name))."""
    s = ds.settings
    today = local_today(ds.tz)
    coverage = s.coverage_start
    recent_moves = {}
    for deal_id, old, new in DealEvent.objects.filter(
        kind="close_date_change", occurred_at__gte=ds.now - timedelta(days=7)
    ).values_list("deal_id", "from_value", "to_value"):
        if old and new and new > old:
            recent_moves[deal_id] = (old, new)

    for deal in ds.deals:
        fin = ds.fin[deal.ghl_id]
        stalled = False
        if is_open(deal) and deal.stage_key:
            days = days_in_stage(ds, deal)
            limit = s.stall_days_for(deal.stage_key)
            if days is not None and days > limit:
                stalled = True
                dormant = is_dormant(ds, deal)
                if dormant:
                    severity = "low"
                elif deal.stage_key in ("clear_to_close", "underwriting"):
                    severity = "critical"
                else:
                    severity = "high" if deal.pipeline_key == "processing" else "medium"
                yield deal, ISSUE_STALLED, severity, days - limit, {
                    "days_in_stage": days, "threshold": limit, "stage": deal.stage_name, "dormant": dormant,
                }, owners.stage_owner(deal)

            entered = ds.ms(deal, deal.stage_key) or deal.last_stage_change_at
            last = max(filter(None, [entered, activity.get(deal.ghl_id)]), default=None)
            if not stalled and last and (ds.now - last).days > s.no_progress_days:
                idle = (ds.now - last).days
                yield deal, ISSUE_NO_PROGRESS, "medium", idle - s.no_progress_days, {"days_without_progress": idle, "threshold": s.no_progress_days}, owners.stage_owner(deal)

            if deal.expected_close_date and deal.expected_close_date < today:
                late = (today - deal.expected_close_date).days
                yield deal, ISSUE_CLOSING_MISSED, "critical" if late > 7 else "high", late, {"expected_close_date": deal.expected_close_date.isoformat()}, owners.ae(deal)
            elif deal.ghl_id in recent_moves:
                old, new = recent_moves[deal.ghl_id]
                yield deal, ISSUE_CLOSING_MISSED, "medium", 0, {"moved_from": old, "moved_to": new}, owners.ae(deal)

            if ACCEPTED_INDEX <= deal.furthest_stage_index < APP_COMPLETE_INDEX:
                started = ds.ms(deal, "term_sheet_accepted") or ds.ms(deal, "qc_review") or deal.last_stage_change_at
                if started:
                    age = (ds.now - started).days
                    if age > s.incomplete_application_days:
                        yield deal, ISSUE_INCOMPLETE_APP, "medium", age - s.incomplete_application_days, {"days_incomplete": age, "threshold": s.incomplete_application_days}, owners.ae(deal)

        if deal.is_lost and loss_reason(deal) == "Unknown" and coverage and deal.lost_at and deal.lost_at >= coverage:
            yield deal, ISSUE_MISSING_LOSS, "medium", (ds.now - deal.lost_at).days, {"lost_at": deal.lost_at.isoformat(), "status": deal.status}, owners.ae(deal)

        meta = _meta(deal)
        if is_paused(deal) and meta.pause_review_date and meta.pause_review_date < today:
            yield deal, ISSUE_PAUSE_OVERDUE, "medium", (today - meta.pause_review_date).days, {"review_date": meta.pause_review_date.isoformat()}, owners.ae(deal)

        if deal.stage_index >= APPROVED_INDEX and not deal.is_lost:
            missing = [m for m in fin["missing"] if m == "opportunity_value"]
            if fin["loan_amount"] is None:
                missing.append("total_loan_amount")
            if not deal.ae_name:
                missing.append("account_executive")
            if missing:
                yield deal, ISSUE_MISSING_FINANCIALS, "high", 0, {"missing": missing}, owners.processor(deal)


def refresh_issues():
    ds = load_dataset(None, system=True)
    owners = Owners()
    activity = _last_human_activity([d.ghl_id for d in ds.deals])
    now = timezone.now()
    current = {
        (i.deal_id, i.issue_type): i
        for i in DealIssue.objects.exclude(status=DealIssue.STATUS_RESOLVED).select_related("deal")
    }
    # Manually resolved issues stay resolved until their condition clears once.
    held = {
        (i.deal_id, i.issue_type): i
        for i in DealIssue.objects.filter(status=DealIssue.STATUS_RESOLVED, auto_resolved=False, details__hold=True)
    }
    triggered = set()
    stats = {"opened": 0, "updated": 0, "resolved": 0, "reopened": 0}
    ceos = ceo_users()
    # First run builds the baseline backlog silently instead of flooding everyone.
    baseline = not DealIssue.objects.exists()

    with transaction.atomic():
        for deal, itype, severity, overdue, details, (owner, owner_name) in evaluate(ds, owners, activity):
            key = (deal.ghl_id, itype)
            triggered.add(key)
            if key in held and key not in current:
                continue
            issue = current.get(key)
            if issue:
                issue.severity = severity
                issue.days_overdue = max(0, overdue)
                issue.details = {**issue.details, **details}
                if not issue.details.get("owner_manual"):
                    issue.owner, issue.owner_name = owner, owner_name or ""
                if issue.status == DealIssue.STATUS_SNOOZED and issue.snoozed_until and issue.snoozed_until <= now:
                    issue.status = DealIssue.STATUS_OPEN
                    stats["reopened"] += 1
                issue.save()
                stats["updated"] += 1
                continue
            issue = DealIssue.objects.create(
                deal=deal, issue_type=itype, severity=severity, days_overdue=max(0, overdue),
                details=details, owner=owner, owner_name=owner_name or "",
            )
            stats["opened"] += 1
            if baseline or severity == "low":
                continue
            recipients = [owner.user] if owner else []
            if severity == "critical":
                recipients += ceos
            notify(recipients, "issue_opened", f"{ISSUE_LABELS[itype]}: {deal.name}", _issue_body(details), deal=deal, issue=issue)

        for key, issue in current.items():
            if key in triggered:
                continue
            issue.status = DealIssue.STATUS_RESOLVED
            issue.auto_resolved = True
            issue.resolved_at = now
            issue.resolution_note = issue.resolution_note or "Triggering condition cleared."
            issue.save()
            stats["resolved"] += 1

        for key, issue in held.items():
            if key not in triggered:
                issue.details = {k: v for k, v in issue.details.items() if k != "hold"}
                issue.save(update_fields=["details", "updated_at"])
    return stats


def _issue_body(details):
    return ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in details.items() if k != "owner_manual")


def send_reminders():
    """Due tasks and overdue issue deadlines → in-app notifications (once each)."""
    now = timezone.now()
    sent = 0
    for task in DealTask.objects.filter(completed_at__isnull=True, reminded_at__isnull=True, due_at__lte=now).select_related("assignee__user", "deal"):
        if task.assignee:
            notify([task.assignee.user], "task_due", f"Task due: {task.title}", task.deal.name, deal=task.deal, task=task)
            sent += 1
        task.reminded_at = now
        task.save(update_fields=["reminded_at"])

    today = timezone.localdate()
    for issue in DealIssue.objects.filter(status=DealIssue.STATUS_OPEN, deadline__lt=today).select_related("owner__user", "deal"):
        if issue.details.get("deadline_reminded_on") == today.isoformat() or not issue.owner:
            continue
        notify([issue.owner.user], "issue_deadline", f"Deadline passed: {ISSUE_LABELS.get(issue.issue_type)} — {issue.deal.name}",
               issue.next_action, deal=issue.deal, issue=issue)
        issue.details = {**issue.details, "deadline_reminded_on": today.isoformat()}
        issue.save(update_fields=["details", "updated_at"])
        sent += 1
    return sent
