"""
REST API for the standalone CEO dashboard frontend (JWT auth).
All routes live under /api/dashboard/.
"""
import logging
from datetime import datetime, time, timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import ghl_client, metrics
from .access import (
    MODULES_BY_ROLE,
    get_profile,
    get_role,
    is_ceo,
    is_team_member_on_deal,
    module_permission,
)
from .constants import (
    FIELD_LOSS_NOTES,
    FIELD_LOSS_REASON,
    ISSUE_TYPE_CHOICES,
    LOSS_OUTCOMES,
    LOSS_REASONS,
    ROLE_CEO,
    ROLE_CHOICES,
    STAGE_FLOW,
    STAGE_LABELS,
)
from .issues import notify
from .models import (
    ActivityLog,
    CompanySettings,
    DashboardProfile,
    DealIssue,
    DealMeta,
    DealNote,
    DealTask,
    GHLUser,
    MonthlyTarget,
    Notification,
)
from .periods import PRESETS, resolve_period
from .serializers import deal_detail, deal_row, issue_row, settings_dict, task_row, team_member

logger = logging.getLogger(__name__)
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _period(request, settings=None):
    settings = settings or CompanySettings.load()
    p = request.query_params
    return resolve_period(p.get("period", "mtd"), p.get("start"), p.get("end"), settings.timezone)


def _decimal(value, field):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} must be a number")


def _log(request, action, deal=None, issue=None, **detail):
    ActivityLog.objects.create(user=request.user, deal=deal, issue=issue, action=action, detail=detail)


def _scoped_deal(request, deal_id):
    """404 unless the deal exists and the user's role may see it."""
    qs = metrics.base_queryset(request.user)
    return get_object_or_404(qs, pk=deal_id)


def _ghl_token():
    from documents.survey_opportunity import get_default_ghl_account

    account = get_default_ghl_account()
    return account.access_token if account else None


# ---------------------------------------------------------------- session

class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        profile = get_profile(user)
        role = get_role(user)
        return Response({
            "id": user.id,
            "email": user.email or user.get_username(),
            "name": user.get_full_name() or user.get_username(),
            "role": role,
            "role_label": dict(ROLE_CHOICES).get(role, ""),
            "profile_id": profile.id if profile else None,
            "ae_name": profile.ae_name if profile else "",
            "analyst_name": profile.analyst_name if profile else "",
            "modules": sorted(MODULES_BY_ROLE.get(role, set())),
            "unread_notifications": Notification.objects.filter(recipient=user, read_at__isnull=True).count(),
        })


class StatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(metrics.data_status())


class OptionsView(APIView):
    """Filter dropdown values, scoped to deals the user can see."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = metrics.base_queryset(request.user)

        def distinct(field):
            return sorted({v for v in qs.values_list(field, flat=True) if v}, key=str.lower)

        processors = sorted(set(distinct("analyst_name")) | set(distinct("analyst2_name")), key=str.lower)
        return Response({
            "periods": [{"key": k, "label": v} for k, v in PRESETS.items()],
            "stages": [{"key": k, "label": STAGE_LABELS[k], "pipeline": p} for k, p, _ in STAGE_FLOW],
            "statuses": ["open", "won", "lost", "abandoned"],
            "aes": distinct("ae_name"),
            "processors": processors,
            "programs": distinct("program"),
            "sources": distinct("source"),
            "brokers": distinct("broker_name"),
            "referrers": distinct("referral_source"),
            "loss_reasons": LOSS_REASONS,
            "loss_outcomes": LOSS_OUTCOMES,
            "issue_types": [{"key": k, "label": v} for k, v in ISSUE_TYPE_CHOICES],
            "roles": [{"key": k, "label": v} for k, v in ROLE_CHOICES],
        })


# ---------------------------------------------------------------- modules 1-5

class OverviewView(APIView):
    permission_classes = [IsAuthenticated, module_permission("overview")]

    def get(self, request):
        ds = metrics.load_dataset(request.user, request.query_params)
        return Response({**metrics.overview(ds, _period(request, ds.settings)), "data_status": metrics.data_status(ds.settings)})


class FunnelView(APIView):
    permission_classes = [IsAuthenticated, module_permission("funnel")]

    def get(self, request):
        ds = metrics.load_dataset(request.user, request.query_params)
        return Response(metrics.funnel(ds, _period(request, ds.settings)))


class LossView(APIView):
    permission_classes = [IsAuthenticated, module_permission("loss")]

    def get(self, request):
        ds = metrics.load_dataset(request.user, request.query_params)
        return Response(metrics.loss(ds, _period(request, ds.settings)))


class ScorecardsView(APIView):
    permission_classes = [IsAuthenticated, module_permission("scorecards")]

    def get(self, request):
        ds = metrics.load_dataset(request.user, request.query_params)
        return Response(metrics.scorecards(ds, _period(request, ds.settings)))


class LeaderboardView(APIView):
    permission_classes = [IsAuthenticated, module_permission("leaderboards")]

    def get(self, request):
        dimension = request.query_params.get("dimension", "borrower")
        if dimension not in ("borrower", "broker", "referral"):
            return Response({"error": "dimension must be borrower, broker or referral"}, status=400)
        ds = metrics.load_dataset(request.user, request.query_params)
        return Response(metrics.leaderboard(ds, _period(request, ds.settings), dimension, request.query_params.get("sort", "profit")))


# ---------------------------------------------------------------- deals

DEAL_ORDERING = {
    "created": lambda r: r["created_at"] or datetime.min.replace(tzinfo=dt_timezone.utc),
    "amount": lambda r: r["loan_amount"] or 0,
    "revenue": lambda r: r["revenue"] or 0,
    "days_in_stage": lambda r: r["days_in_stage"] or 0,
    "funded": lambda r: r["funded_at"] or datetime.min.replace(tzinfo=dt_timezone.utc),
    "name": lambda r: (r["name"] or "").lower(),
}


def _deal_list_response(request, ds, deals):
    rows = [deal_row(ds, d) for d in deals]
    order = request.query_params.get("ordering", "-created")
    key = order.lstrip("-")
    if key in DEAL_ORDERING:
        rows.sort(key=DEAL_ORDERING[key], reverse=order.startswith("-"))
    try:
        limit = min(int(request.query_params.get("limit", 50)), 500)
        offset = max(int(request.query_params.get("offset", 0)), 0)
    except ValueError:
        limit, offset = 50, 0
    return Response({"count": len(rows), "results": rows[offset:offset + limit]})


class DealListView(APIView):
    permission_classes = [IsAuthenticated, module_permission("deals")]

    def get(self, request):
        p = request.query_params
        ds = metrics.load_dataset(request.user, p)
        deals = ds.deals
        search = (p.get("search") or "").strip().lower()
        if search:
            deals = [
                d for d in deals
                if search in " ".join([d.name, d.contact_name, d.contact_company, d.entity_name, d.loan_id, d.property_address]).lower()
            ]
        bucket = p.get("bucket")
        if bucket == "open":
            deals = [d for d in deals if metrics.is_open(d)]
        elif bucket == "funded":
            deals = [d for d in deals if d.is_funded]
        elif bucket == "lost":
            deals = [d for d in deals if d.is_lost]
        elif bucket == "stalled":
            deals = [d for d in deals if metrics.is_stalled(ds, d)]
        elif bucket == "dormant":
            deals = [d for d in deals if metrics.is_dormant(ds, d)]
        return _deal_list_response(request, ds, deals)


class DealLookupView(APIView):
    """POST {ids: [...]} → rows for a KPI drill-through (ids come from KPI payloads)."""

    permission_classes = [IsAuthenticated, module_permission("deals")]

    def post(self, request):
        ids = request.data.get("ids") or []
        if not isinstance(ids, list):
            return Response({"error": "ids must be a list"}, status=400)
        ds = metrics.load_dataset(request.user)
        wanted = set(map(str, ids[:5000]))
        return _deal_list_response(request, ds, [d for d in ds.deals if d.ghl_id in wanted])


class DealDetailView(APIView):
    permission_classes = [IsAuthenticated, module_permission("deals")]

    def get(self, request, deal_id):
        deal = _scoped_deal(request, deal_id)
        ds = metrics.load_dataset(request.user)
        return Response(deal_detail(ds, deal))


class DealMetaView(APIView):
    """Loss info, pause with review date, test/duplicate exclusion (CEO)."""

    permission_classes = [IsAuthenticated, module_permission("deals")]
    TEXT_FIELDS = ("loss_primary_reason", "loss_secondary_reason", "loss_notes", "loss_outcome", "competitor", "recoverable", "pause_reason")

    def patch(self, request, deal_id):
        deal = _scoped_deal(request, deal_id)
        meta, _ = DealMeta.objects.get_or_create(deal=deal)
        data = request.data
        before = {f: getattr(meta, f) for f in self.TEXT_FIELDS + ("paused", "pause_review_date", "excluded")}

        for f in self.TEXT_FIELDS:
            if f in data:
                setattr(meta, f, str(data.get(f) or "").strip())
        for f in ("loss_primary_reason", "loss_secondary_reason"):
            if getattr(meta, f) and getattr(meta, f) not in LOSS_REASONS:
                return Response({"error": f"{f} must be one of the loss reasons"}, status=400)
        if meta.loss_primary_reason == "Other" and not meta.loss_notes:
            return Response({"error": "Loss notes are required when the reason is Other"}, status=400)
        if meta.loss_outcome and meta.loss_outcome not in LOSS_OUTCOMES:
            return Response({"error": "invalid loss_outcome"}, status=400)
        if meta.recoverable and meta.recoverable not in ("yes", "no", "maybe"):
            return Response({"error": "recoverable must be yes, no or maybe"}, status=400)

        if "paused" in data:
            meta.paused = bool(data.get("paused"))
            if meta.paused:
                review = parse_date(str(data.get("pause_review_date") or ""))
                if not review:
                    return Response({"error": "A paused deal needs a review date"}, status=400)
                meta.pause_review_date = review
            else:
                meta.pause_review_date = None
        elif "pause_review_date" in data and meta.paused:
            review = parse_date(str(data.get("pause_review_date") or ""))
            if not review:
                return Response({"error": "A paused deal needs a review date"}, status=400)
            meta.pause_review_date = review

        if "excluded" in data:
            if not is_ceo(request.user):
                return Response({"error": "Only the CEO can exclude records"}, status=403)
            meta.excluded = bool(data.get("excluded"))
            meta.exclude_reason = str(data.get("exclude_reason") or "").strip()

        meta.updated_by = request.user
        meta.save()
        after = {f: getattr(meta, f) for f in before}
        changes = {k: [str(before[k]), str(after[k])] for k in before if before[k] != after[k]}
        _log(request, "deal_meta_updated", deal=deal, changes=changes)

        ghl_synced = None
        if {"loss_primary_reason", "loss_notes"} & set(data):
            ghl_synced = _push_loss_to_ghl(deal, meta)
        ds = metrics.load_dataset(request.user)
        return Response({**deal_detail(ds, deal), "ghl_synced": ghl_synced})


def _push_loss_to_ghl(deal, meta):
    """Best effort: write loss reason/notes to GHL fields when they exist."""
    try:
        from .sync import build_context, field_id_for

        ctx = build_context()
        fields = []
        reason_id, notes_id = field_id_for(ctx, FIELD_LOSS_REASON), field_id_for(ctx, FIELD_LOSS_NOTES)
        if reason_id and meta.loss_primary_reason:
            fields.append({"id": reason_id, "field_value": meta.loss_primary_reason})
        if notes_id and meta.loss_notes:
            fields.append({"id": notes_id, "field_value": meta.loss_notes})
        if not fields:
            return False
        ghl_client.update_opportunity_fields(deal.ghl_id, fields, access_token=ctx.token)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("Could not push loss info to GHL for %s", deal.ghl_id)
        return False


class DealNoteView(APIView):
    permission_classes = [IsAuthenticated, module_permission("deals")]

    def post(self, request, deal_id):
        deal = _scoped_deal(request, deal_id)
        body = str(request.data.get("body") or "").strip()
        if not body:
            return Response({"error": "Note body is required"}, status=400)
        note = DealNote.objects.create(deal=deal, author=request.user, body=body)
        if deal.contact_id:
            try:
                author = request.user.get_full_name() or request.user.get_username()
                prefix = f"[{deal.name}] " if deal.name else ""
                result = ghl_client.create_contact_note(deal.contact_id, f"{prefix}{body}\n— {author} (CEO Dashboard)", access_token=_ghl_token())
                note.ghl_note_id = str((result.get("note") or result).get("id") or "")
                note.save(update_fields=["ghl_note_id"])
            except Exception:  # noqa: BLE001
                logger.exception("Could not create GHL note for %s", deal.ghl_id)
        _log(request, "note_added", deal=deal, note_id=note.id)
        return Response({"id": note.id, "body": note.body, "created_at": note.created_at, "synced_to_ghl": bool(note.ghl_note_id)}, status=201)


# ---------------------------------------------------------------- tasks

def _parse_due(value):
    if not value:
        return None
    dt = parse_datetime(str(value))
    if dt is None:
        day = parse_date(str(value))
        if not day:
            return None
        dt = datetime.combine(day, time(17, 0))
    if timezone.is_naive(dt):
        from zoneinfo import ZoneInfo

        dt = dt.replace(tzinfo=ZoneInfo(CompanySettings.load().timezone))
    return dt


class DealTaskView(APIView):
    """Assign a task on a loan to yourself or another team member on that loan."""

    permission_classes = [IsAuthenticated, module_permission("deals")]

    def post(self, request, deal_id):
        deal = _scoped_deal(request, deal_id)
        title = str(request.data.get("title") or "").strip()
        due_at = _parse_due(request.data.get("due_at"))
        if not title or not due_at:
            return Response({"error": "title and due_at are required"}, status=400)
        me = get_profile(request.user)
        assignee = me
        if request.data.get("assignee_id"):
            assignee = get_object_or_404(DashboardProfile.objects.select_related("user"), pk=request.data["assignee_id"], user__is_active=True)
        if assignee is None:
            return Response({"error": "assignee_id is required"}, status=400)
        if assignee != me and not is_ceo(request.user) and not is_team_member_on_deal(assignee, deal):
            return Response({"error": "Tasks can only be assigned to yourself or a team member on this loan"}, status=400)
        issue = None
        if request.data.get("issue_id"):
            issue = get_object_or_404(DealIssue, pk=request.data["issue_id"], deal=deal)

        task = DealTask.objects.create(
            deal=deal, title=title[:255], body=str(request.data.get("body") or ""),
            assignee=assignee, created_by=request.user, due_at=due_at, issue=issue,
        )
        if deal.contact_id:
            try:
                result = ghl_client.create_contact_task(
                    deal.contact_id, f"[{deal.name}] {title}" if deal.name else title,
                    due_at.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    body=task.body, assigned_to=assignee.ghl_user_id or None, access_token=_ghl_token(),
                )
                task.ghl_task_id = str(result.get("id") or "")
                task.save(update_fields=["ghl_task_id"])
            except Exception:  # noqa: BLE001
                logger.exception("Could not create GHL task for %s", deal.ghl_id)
        if assignee.user_id != request.user.id:
            notify([assignee.user], "task_assigned", f"New task: {title}", f"{deal.name} — due {due_at:%b %d}", deal=deal, task=task)
        _log(request, "task_created", deal=deal, task_id=task.id, assignee=assignee.id)
        return Response(task_row(task), status=201)


class TaskListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        profile = get_profile(request.user)
        qs = DealTask.objects.select_related("deal", "assignee__user", "created_by")
        if request.query_params.get("scope") == "all" and is_ceo(request.user):
            pass
        else:
            qs = qs.filter(assignee=profile) if profile else qs.none()
        if request.query_params.get("open", "1") == "1":
            qs = qs.filter(completed_at__isnull=True)
        return Response({"results": [task_row(t) for t in qs[:200]]})


class TaskDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, task_id):
        task = get_object_or_404(DealTask.objects.select_related("deal", "assignee"), pk=task_id)
        profile = get_profile(request.user)
        if not (is_ceo(request.user) or task.assignee == profile or task.created_by_id == request.user.id):
            return Response({"error": "Not allowed"}, status=403)
        if "completed" in request.data:
            task.completed_at = timezone.now() if request.data.get("completed") else None
            if task.ghl_task_id and task.deal.contact_id:
                try:
                    ghl_client.complete_contact_task(task.deal.contact_id, task.ghl_task_id, bool(task.completed_at), access_token=_ghl_token())
                except Exception:  # noqa: BLE001
                    logger.exception("Could not update GHL task %s", task.ghl_task_id)
        if "due_at" in request.data:
            due = _parse_due(request.data.get("due_at"))
            if not due:
                return Response({"error": "invalid due_at"}, status=400)
            task.due_at, task.reminded_at = due, None
        task.save()
        _log(request, "task_updated", deal=task.deal, task_id=task.id, completed=bool(task.completed_at))
        return Response(task_row(task))


# ---------------------------------------------------------------- exceptions (module 6)

class IssueListView(APIView):
    permission_classes = [IsAuthenticated, module_permission("exceptions")]

    def get(self, request):
        p = request.query_params
        # `status` here is the issue status, not the deal-status filter.
        deal_filters = {k: v for k, v in p.items() if k != "status"}
        ds = metrics.load_dataset(request.user, deal_filters)
        visible = {d.ghl_id for d in ds.deals}
        qs = DealIssue.objects.filter(deal_id__in=visible).select_related("deal", "owner__user")
        state = p.get("status", "active")
        if state == "active":
            qs = qs.exclude(status=DealIssue.STATUS_RESOLVED)
        elif state in ("open", "snoozed", "resolved"):
            qs = qs.filter(status=state)
        if p.get("type"):
            qs = qs.filter(issue_type=p["type"])
        dormant_hidden = 0
        if p.get("severity"):
            qs = qs.filter(severity=p["severity"])
        elif p.get("include_dormant") != "1":
            # Long-idle leads (low severity) are counted but not listed by default.
            dormant_hidden = qs.filter(severity="low").count()
            qs = qs.exclude(severity="low")
        if p.get("owner") == "me":
            qs = qs.filter(owner=get_profile(request.user))
        elif p.get("owner"):
            qs = qs.filter(owner_id=p["owner"])

        issues = sorted(qs[:2000], key=lambda i: (SEVERITY_RANK.get(i.severity, 9), -i.days_overdue))
        deal_ids = {i.deal_id for i in issues}
        at_risk = sum(float(ds.fin[d]["revenue"] or 0) for d in deal_ids if d in ds.fin)
        return Response({
            "headline": {
                "issues": len(issues),
                "deals": len(deal_ids),
                "revenue_at_risk": round(at_risk, 2),
                "revenue_missing": sum(1 for d in deal_ids if d in ds.fin and ds.fin[d]["revenue"] is None),
                "by_type": {k: sum(1 for i in issues if i.issue_type == k) for k, _ in ISSUE_TYPE_CHOICES},
                "dormant_hidden": dormant_hidden,
            },
            "results": [issue_row(ds, i) for i in issues],
        })


class IssueDetailView(APIView):
    permission_classes = [IsAuthenticated, module_permission("exceptions")]

    def patch(self, request, issue_id):
        issue = get_object_or_404(DealIssue.objects.select_related("deal", "owner__user"), pk=issue_id)
        _scoped_deal(request, issue.deal_id)
        data = request.data
        changes = {}
        if "next_action" in data:
            issue.next_action = str(data.get("next_action") or "").strip()
            changes["next_action"] = issue.next_action
        if "deadline" in data:
            issue.deadline = parse_date(str(data.get("deadline") or "")) if data.get("deadline") else None
            changes["deadline"] = str(issue.deadline or "")
        if "owner_id" in data:
            owner = get_object_or_404(DashboardProfile, pk=data["owner_id"]) if data.get("owner_id") else None
            issue.owner = owner
            issue.owner_name = owner.display_name if owner else ""
            issue.details = {**issue.details, "owner_manual": True}
            changes["owner"] = owner.id if owner else None
            if owner and owner.user_id != request.user.id:
                notify([owner.user], "issue_assigned", f"Assigned to you: {issue.get_issue_type_display()} — {issue.deal.name}",
                       issue.next_action, deal=issue.deal, issue=issue)

        action = data.get("action")
        if action == "resolve":
            note = str(data.get("resolution_note") or "").strip()
            if not note:
                return Response({"error": "A resolution note is required"}, status=400)
            issue.status = DealIssue.STATUS_RESOLVED
            issue.resolution_note = note
            issue.resolved_at = timezone.now()
            issue.auto_resolved = False
            issue.details = {**issue.details, "hold": True}
        elif action == "snooze":
            reason = str(data.get("snooze_reason") or "").strip()
            until = _parse_due(data.get("snoozed_until"))
            if not reason or not until or until <= timezone.now():
                return Response({"error": "Snooze needs a reason and a future expiry"}, status=400)
            issue.status, issue.snooze_reason, issue.snoozed_until = DealIssue.STATUS_SNOOZED, reason, until
        elif action == "reopen":
            issue.status, issue.snoozed_until, issue.resolved_at = DealIssue.STATUS_OPEN, None, None
            issue.details = {k: v for k, v in issue.details.items() if k != "hold"}
        elif action:
            return Response({"error": "action must be resolve, snooze or reopen"}, status=400)
        if action:
            changes["issue_action"] = action
        issue.save()
        _log(request, "issue_updated", deal=issue.deal, issue=issue, **changes)
        ds = metrics.load_dataset(request.user)
        return Response(issue_row(ds, issue))


# ---------------------------------------------------------------- notifications

class NotificationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = Notification.objects.filter(recipient=request.user).select_related("deal")
        if request.query_params.get("unread") == "1":
            qs = qs.filter(read_at__isnull=True)
        return Response({
            "unread": Notification.objects.filter(recipient=request.user, read_at__isnull=True).count(),
            "results": [
                {"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "deal_id": n.deal_id,
                 "issue_id": n.issue_id, "task_id": n.task_id, "created_at": n.created_at, "read_at": n.read_at}
                for n in qs[:100]
            ],
        })

    def post(self, request):
        """Mark read: {ids: [...]} or {all: true}."""
        qs = Notification.objects.filter(recipient=request.user, read_at__isnull=True)
        if not request.data.get("all"):
            qs = qs.filter(pk__in=request.data.get("ids") or [])
        updated = qs.update(read_at=timezone.now())
        return Response({"updated": updated})


# ---------------------------------------------------------------- team, settings, targets (CEO)

class TeamView(APIView):
    """GET: active team (everyone, for task assignment). POST: create a login (CEO)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = DashboardProfile.objects.select_related("user").order_by("role", "user__first_name")
        if not is_ceo(request.user):
            qs = qs.filter(user__is_active=True)
            return Response({"results": [{"id": p.id, "name": p.display_name, "role": p.role, "ae_name": p.ae_name, "analyst_name": p.analyst_name} for p in qs]})
        return Response({
            "results": [team_member(p) for p in qs],
            "ghl_users": [{"id": u.ghl_id, "name": u.name, "email": u.email} for u in GHLUser.objects.order_by("name")],
        })

    def post(self, request):
        if not is_ceo(request.user):
            return Response({"error": "Only the CEO can manage the team"}, status=403)
        data = request.data
        email = str(data.get("email") or "").strip().lower()
        password = str(data.get("password") or "")
        role = data.get("role")
        if not email or len(password) < 8 or role not in dict(ROLE_CHOICES):
            return Response({"error": "email, password (8+ chars) and a valid role are required"}, status=400)
        User = get_user_model()
        if User.objects.filter(Q(username__iexact=email) | Q(email__iexact=email)).exists():
            return Response({"error": "A user with this email already exists"}, status=400)
        try:
            with transaction.atomic():
                user = User.objects.create_user(username=email, email=email, password=password,
                                                first_name=str(data.get("first_name") or ""), last_name=str(data.get("last_name") or ""))
                profile = DashboardProfile(user=user, role=role)
                _apply_profile(profile, data)
                profile.save()
        except ValueError as exc:
            return Response({"error": str(exc)}, status=400)
        _log(request, "team_member_created", profile_id=profile.id, role=role)
        return Response(team_member(profile), status=201)


def _apply_profile(profile, data):
    for f in ("ghl_user_id", "ae_name", "analyst_name"):
        if f in data:
            setattr(profile, f, str(data.get(f) or "").strip())
    for f in ("commission_pct", "monthly_volume_target", "monthly_revenue_target"):
        if f in data:
            setattr(profile, f, _decimal(data.get(f), f))
    if "role" in data:
        if data["role"] not in dict(ROLE_CHOICES):
            raise ValueError("invalid role")
        profile.role = data["role"]


class TeamMemberView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, profile_id):
        if not is_ceo(request.user):
            return Response({"error": "Only the CEO can manage the team"}, status=403)
        profile = get_object_or_404(DashboardProfile.objects.select_related("user"), pk=profile_id)
        user = profile.user
        try:
            _apply_profile(profile, request.data)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=400)
        if profile.user_id == request.user.id and profile.role != ROLE_CEO:
            return Response({"error": "You cannot remove your own CEO role"}, status=400)
        for f in ("first_name", "last_name"):
            if f in request.data:
                setattr(user, f, str(request.data.get(f) or ""))
        if "is_active" in request.data and user.id != request.user.id:
            user.is_active = bool(request.data.get("is_active"))
        if request.data.get("password"):
            if len(str(request.data["password"])) < 8:
                return Response({"error": "Password must be at least 8 characters"}, status=400)
            user.set_password(str(request.data["password"]))
        user.save()
        profile.save()
        _log(request, "team_member_updated", profile_id=profile.id, fields=sorted(k for k in request.data if k != "password"))
        return Response(team_member(profile))


class SettingsView(APIView):
    permission_classes = [IsAuthenticated, module_permission("settings")]
    DECIMALS = ("monthly_volume_target", "monthly_revenue_target", "default_ae_volume_target", "default_ae_commission_pct")
    INTS = ("stall_days_default", "dormant_days", "no_progress_days", "incomplete_application_days", "relationship_inactivity_days", "stale_sync_minutes")

    def get(self, request):
        return Response(settings_dict(CompanySettings.load()))

    def patch(self, request):
        s = CompanySettings.load()
        data = request.data
        try:
            for f in self.DECIMALS:
                if f in data:
                    setattr(s, f, _decimal(data.get(f), f))
            for f in self.INTS:
                if f in data:
                    value = int(data.get(f))
                    if value < 0:
                        raise ValueError(f"{f} must be positive")
                    setattr(s, f, value)
        except (TypeError, ValueError) as exc:
            return Response({"error": str(exc)}, status=400)
        if s.monthly_volume_target is None:
            return Response({"error": "monthly_volume_target is required"}, status=400)
        for f in ("stall_days_by_stage", "stage_probabilities"):
            if f in data:
                value = data.get(f) or {}
                if not isinstance(value, dict) or any(not isinstance(v, (int, float)) or v < 0 for v in value.values()):
                    return Response({"error": f"{f} must map stage keys to non-negative numbers"}, status=400)
                if f == "stage_probabilities" and any(v > 100 for v in value.values()):
                    return Response({"error": "probabilities must be between 0 and 100"}, status=400)
                setattr(s, f, value)
        if "timezone" in data:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                ZoneInfo(str(data["timezone"]))
            except (ZoneInfoNotFoundError, ValueError):
                return Response({"error": "unknown timezone"}, status=400)
            s.timezone = str(data["timezone"])
        s.save()
        _log(request, "settings_updated", fields=sorted(data.keys()))
        return Response(settings_dict(s))


class TargetsView(APIView):
    """Per-month overrides. GET ?month=YYYY-MM-01 ; PUT {month, profile_id|null, volume_target, revenue_target}."""

    permission_classes = [IsAuthenticated, module_permission("settings")]

    def get(self, request):
        qs = MonthlyTarget.objects.select_related("profile__user").order_by("-month")
        month = parse_date(request.query_params.get("month") or "")
        if month:
            qs = qs.filter(month=month.replace(day=1))
        return Response({"results": [
            {"id": t.id, "month": t.month, "profile_id": t.profile_id,
             "profile_name": t.profile.display_name if t.profile else "Company",
             "volume_target": metrics._num(t.volume_target), "revenue_target": metrics._num(t.revenue_target)}
            for t in qs[:200]
        ]})

    def put(self, request):
        month = parse_date(str(request.data.get("month") or ""))
        if not month:
            return Response({"error": "month (YYYY-MM-DD) is required"}, status=400)
        profile = None
        if request.data.get("profile_id"):
            profile = get_object_or_404(DashboardProfile, pk=request.data["profile_id"])
        try:
            volume = _decimal(request.data.get("volume_target"), "volume_target")
            revenue = _decimal(request.data.get("revenue_target"), "revenue_target")
        except ValueError as exc:
            return Response({"error": str(exc)}, status=400)
        target, _ = MonthlyTarget.objects.update_or_create(
            month=month.replace(day=1), profile=profile,
            defaults={"volume_target": volume, "revenue_target": revenue},
        )
        _log(request, "target_set", month=str(target.month), profile_id=profile.id if profile else None)
        return Response({"id": target.id, "month": target.month}, status=200)


class SyncTriggerView(APIView):
    permission_classes = [IsAuthenticated, module_permission("settings")]

    def post(self, request):
        from .tasks import sync_dashboard

        try:
            sync_dashboard.delay()
        except Exception:  # noqa: BLE001 — broker unavailable
            logger.exception("Could not queue dashboard sync")
            return Response({"error": "Background worker unavailable; try again shortly."}, status=503)
        _log(request, "sync_requested")
        return Response({"queued": True}, status=202)
