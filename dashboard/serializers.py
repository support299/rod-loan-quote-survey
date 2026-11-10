"""Plain dict serializers for dashboard API responses."""
from .constants import ISSUE_TYPE_CHOICES, STAGE_LABELS
from .metrics import _meta, _num, days_in_stage, is_paused, is_stalled, loss_reason

ISSUE_LABELS = dict(ISSUE_TYPE_CHOICES)


def deal_row(ds, deal):
    fin = ds.fin.get(deal.ghl_id) or {}
    return {
        "id": deal.ghl_id,
        "name": deal.name,
        "borrower": deal.contact_name or deal.contact_company,
        "contact_id": deal.contact_id,
        "entity_name": deal.entity_name,
        "loan_id": deal.loan_id,
        "program": deal.program,
        "pipeline": deal.pipeline_key,
        "stage_key": deal.stage_key,
        "stage": deal.stage_name or STAGE_LABELS.get(deal.stage_key, ""),
        "status": deal.status,
        "outcome": "funded" if deal.is_funded else "lost" if deal.is_lost else "open",
        "paused": is_paused(deal),
        "ae": deal.ae_name,
        "processors": [n for n in (deal.analyst_name, deal.analyst2_name) if n],
        "broker": deal.broker_name or deal.broker_email,
        "source": deal.source,
        "loan_amount": _num(fin.get("loan_amount")),
        "revenue": _num(fin.get("revenue")),
        "revenue_estimated": fin.get("revenue_estimated", False),
        "profit": _num(fin.get("profit")),
        "cost_complete": fin.get("complete", False),
        "expected_close_date": deal.expected_close_date,
        "days_in_stage": days_in_stage(ds, deal),
        "stalled": is_stalled(ds, deal),
        "created_at": deal.ghl_created_at,
        "funded_at": deal.funded_at,
        "lost_at": deal.lost_at,
        "loss_reason": loss_reason(deal) if deal.is_lost else None,
        "open_issues": ds.open_issue_counts.get(deal.ghl_id, 0),
        "ghl_url": deal.ghl_url,
    }


def deal_detail(ds, deal):
    fin = ds.fin.get(deal.ghl_id) or {}
    meta = _meta(deal)
    return {
        **deal_row(ds, deal),
        "property_address": deal.property_address,
        "broker_email": deal.broker_email,
        "referral_source": deal.referral_source,
        "termsheet_status": deal.termsheet_status,
        "ae_at_application": deal.ae_at_application,
        "ae_at_funding": deal.ae_at_funding,
        "financials": {k: (_num(v) if not isinstance(v, (bool, list, str)) else v) for k, v in fin.items()},
        "fees": {
            "origination_fee": _num(deal.origination_fee),
            "origination_points": _num(deal.origination_points),
            "processing_fee": _num(deal.processing_fee),
            "underwriting_fee": _num(deal.underwriting_fee),
            "broker_fee": _num(deal.broker_fee),
            "ae_commission": _num(deal.ae_commission),
        },
        "meta": {
            "loss_primary_reason": meta.loss_primary_reason if meta else "",
            "loss_secondary_reason": meta.loss_secondary_reason if meta else "",
            "loss_notes": meta.loss_notes if meta else "",
            "loss_outcome": meta.loss_outcome if meta else "",
            "competitor": meta.competitor if meta else "",
            "recoverable": meta.recoverable if meta else "",
            "paused": meta.paused if meta else False,
            "pause_reason": meta.pause_reason if meta else "",
            "pause_review_date": meta.pause_review_date if meta else None,
            "excluded": meta.excluded if meta else False,
            "exclude_reason": meta.exclude_reason if meta else "",
        },
        "milestones": [
            {"key": m.key, "label": STAGE_LABELS.get(m.key, m.key), "reached_at": m.reached_at, "source": m.source}
            for m in deal.milestones.order_by("reached_at")
        ],
        "events": [
            {"kind": e.kind, "from": e.from_value, "to": e.to_value, "at": e.occurred_at, "source": e.source}
            for e in deal.events.order_by("-occurred_at")[:100]
        ],
        "issues": [issue_row(ds, i, include_deal=False) for i in deal.issues.select_related("owner__user").all()[:50]],
        "notes": [
            {"id": n.id, "body": n.body, "author": _user_name(n.author), "created_at": n.created_at, "synced_to_ghl": bool(n.ghl_note_id)}
            for n in deal.notes.select_related("author").all()[:50]
        ],
        "tasks": [task_row(t) for t in deal.tasks.select_related("assignee__user").all()[:50]],
    }


def _user_name(user):
    if not user:
        return ""
    return user.get_full_name() or user.get_username()


def profile_ref(profile):
    if not profile:
        return None
    return {"id": profile.id, "name": profile.display_name, "role": profile.role}


def issue_row(ds, issue, include_deal=True):
    row = {
        "id": issue.id,
        "type": issue.issue_type,
        "type_label": ISSUE_LABELS.get(issue.issue_type, issue.issue_type),
        "severity": issue.severity,
        "status": issue.status,
        "owner": profile_ref(issue.owner),
        "owner_name": issue.owner.display_name if issue.owner else issue.owner_name,
        "days_overdue": issue.days_overdue,
        "details": {k: v for k, v in issue.details.items() if k not in ("owner_manual", "hold", "deadline_reminded_on")},
        "next_action": issue.next_action,
        "deadline": issue.deadline,
        "snoozed_until": issue.snoozed_until,
        "snooze_reason": issue.snooze_reason,
        "resolution_note": issue.resolution_note,
        "auto_resolved": issue.auto_resolved,
        "detected_at": issue.detected_at,
        "resolved_at": issue.resolved_at,
        "updated_at": issue.updated_at,
    }
    if include_deal and ds is not None:
        deal = issue.deal
        fin = ds.fin.get(deal.ghl_id) or {}
        row["deal"] = {
            "id": deal.ghl_id,
            "name": deal.name,
            "borrower": deal.contact_name or deal.contact_company,
            "program": deal.program,
            "stage": deal.stage_name,
            "ae": deal.ae_name,
            "processors": [n for n in (deal.analyst_name, deal.analyst2_name) if n],
            "expected_close_date": deal.expected_close_date,
            "revenue_at_risk": _num(fin.get("revenue")),
            "ghl_url": deal.ghl_url,
        }
    return row


def task_row(task):
    return {
        "id": task.id,
        "deal_id": task.deal_id,
        "deal_name": task.deal.name if task.deal_id else "",
        "title": task.title,
        "body": task.body,
        "assignee": profile_ref(task.assignee),
        "created_by": _user_name(task.created_by),
        "issue_id": task.issue_id,
        "due_at": task.due_at,
        "completed_at": task.completed_at,
        "synced_to_ghl": bool(task.ghl_task_id),
        "created_at": task.created_at,
    }


def team_member(profile):
    user = profile.user
    return {
        "id": profile.id,
        "user_id": user.id,
        "email": user.email or user.get_username(),
        "first_name": user.first_name,
        "last_name": user.last_name,
        "name": profile.display_name,
        "role": profile.role,
        "is_active": user.is_active,
        "ghl_user_id": profile.ghl_user_id,
        "ae_name": profile.ae_name,
        "analyst_name": profile.analyst_name,
        "commission_pct": _num(profile.commission_pct),
        "monthly_volume_target": _num(profile.monthly_volume_target),
        "monthly_revenue_target": _num(profile.monthly_revenue_target),
    }


def settings_dict(s):
    return {
        "timezone": s.timezone,
        "monthly_volume_target": _num(s.monthly_volume_target),
        "monthly_revenue_target": _num(s.monthly_revenue_target),
        "default_ae_volume_target": _num(s.default_ae_volume_target),
        "default_ae_commission_pct": _num(s.default_ae_commission_pct),
        "stall_days_default": s.stall_days_default,
        "stall_days_by_stage": s.stall_days_by_stage,
        "dormant_days": s.dormant_days,
        "no_progress_days": s.no_progress_days,
        "incomplete_application_days": s.incomplete_application_days,
        "relationship_inactivity_days": s.relationship_inactivity_days,
        "stale_sync_minutes": s.stale_sync_minutes,
        "stage_probabilities": s.stage_probabilities,
        "coverage_start": s.coverage_start,
    }
