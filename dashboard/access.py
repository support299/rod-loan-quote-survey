"""
Role access (Rod):
  CEO            – admin, every KPI and deal.
  AE             – only deals assigned to them, KPIs for their own production.
  Processor      – only deals where they are "Processor 1" or "Processor 2" in GHL.
                   The profile's analyst_name holds their name as used in those dropdowns.
  Analyst        – all loans + general company KPIs.
  Post Closing   – all loans + general company KPIs (draws / servicing are later phase).
"""
from django.db.models import Q
from rest_framework.permissions import BasePermission

from .constants import (
    ROLE_AE,
    ROLE_ANALYST,
    ROLE_CEO,
    ROLE_POST_CLOSING,
    ROLE_PROCESSOR,
)

MODULES_BY_ROLE = {
    ROLE_CEO: {"overview", "funnel", "loss", "scorecards", "leaderboards", "exceptions", "deals", "settings"},
    ROLE_AE: {"overview", "funnel", "loss", "scorecards", "exceptions", "deals"},
    ROLE_PROCESSOR: {"overview", "funnel", "loss", "exceptions", "deals"},
    ROLE_ANALYST: {"overview", "funnel", "loss", "exceptions", "deals"},
    ROLE_POST_CLOSING: {"overview", "funnel", "loss", "exceptions", "deals"},
}


def get_profile(user):
    if not user or not user.is_authenticated:
        return None
    return getattr(user, "dashboard_profile", None)


def get_role(user):
    profile = get_profile(user)
    if profile:
        return profile.role
    if user and user.is_authenticated and user.is_superuser:
        return ROLE_CEO
    return None


def can_access(user, module):
    return module in MODULES_BY_ROLE.get(get_role(user), set())


def is_ceo(user):
    return get_role(user) == ROLE_CEO


def deal_scope_q(user):
    """Q filter limiting deals to what the user may see; None = everything."""
    role = get_role(user)
    profile = get_profile(user)
    if role == ROLE_AE:
        q = Q(pk__in=[])
        if profile.ae_name:
            q |= Q(ae_name__iexact=profile.ae_name)
        if profile.ghl_user_id:
            q |= Q(assigned_to=profile.ghl_user_id)
        return q
    if role == ROLE_PROCESSOR:
        name = profile.analyst_name
        if not name:
            return Q(pk__in=[])
        return Q(processor_name__iexact=name) | Q(processor2_name__iexact=name)
    if role in (ROLE_CEO, ROLE_ANALYST, ROLE_POST_CLOSING):
        return None
    return Q(pk__in=[])


def scope_deals(qs, user):
    q = deal_scope_q(user)
    return qs if q is None else qs.filter(q)


def is_team_member_on_deal(profile, deal):
    if not profile:
        return False
    if profile.role in (ROLE_CEO, ROLE_ANALYST, ROLE_POST_CLOSING):
        return True
    names = {n.lower() for n in (deal.ae_name, deal.analyst_name, deal.analyst2_name, deal.processor_name, deal.processor2_name) if n}
    own = {n.lower() for n in (profile.ae_name, profile.analyst_name) if n}
    return bool(names & own) or (profile.ghl_user_id and profile.ghl_user_id == deal.assigned_to)


class ModulePermission(BasePermission):
    """Use as `permission_classes = [module_permission("overview")]`."""

    module = None

    def has_permission(self, request, view):
        return can_access(request.user, self.module)


def module_permission(module):
    return type(f"{module.title()}Permission", (ModulePermission,), {"module": module})
