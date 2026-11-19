"""
Invite and password-reset emails for dashboard users, sent by GHL.

The backend never sends email itself (same as the quick app emails). Instead it:
  1. makes a one-time "set your password" link,
  2. finds or creates the person as a GHL contact (tagged "dashboard user"),
  3. writes the link into a contact field ("Dashboard Invite Link" / "Dashboard Password Reset Link"),
  4. removes and re-adds a trigger tag ("dashboard-invite" / "dashboard-password-reset"),
and a GHL workflow listening for that tag sends the email with the link.

Links are signed with Django's password-reset tokens, so a link stops working once the
password is set (or the user signs in), and it expires: invites after 7 days, resets after 1 day.
"""
import logging
from dataclasses import dataclass

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import base36_to_int, urlsafe_base64_decode, urlsafe_base64_encode

from documents.ghl_service import (
    GHL_CONTACTS_BASE,
    _auth_headers,
    _split_full_name,
    list_location_custom_fields,
    lookup_duplicate_contact,
    update_contact_custom_fields,
)

logger = logging.getLogger(__name__)

KIND_INVITE = "invite"
KIND_RESET = "reset"

DASHBOARD_USER_TAG = "dashboard user"


@dataclass(frozen=True)
class LinkKind:
    field_name: str  # GHL contact custom field that holds the link
    trigger_tag: str  # adding this tag starts the GHL workflow that sends the email
    max_age: int  # seconds the link stays valid


KINDS = {
    KIND_INVITE: LinkKind("Dashboard Invite Link", "dashboard-invite", 7 * 24 * 3600),
    KIND_RESET: LinkKind("Dashboard Password Reset Link", "dashboard-password-reset", 24 * 3600),
}

# Contact fields `dashboard_setup_ghl_fields --apply` creates in GHL.
GHL_CONTACT_FIELDS = [{"name": k.field_name, "data_type": "TEXT"} for k in KINDS.values()]


class InviteError(Exception):
    """Something the admin should see (e.g. the GHL field is missing)."""


class _KindTokenGenerator(PasswordResetTokenGenerator):
    """Django's reset token, with its own salt per kind and its own expiry."""

    def __init__(self, kind):
        super().__init__()
        self.key_salt = f"dashboard.invites.{kind}"
        self.max_age = KINDS[kind].max_age

    def check_token(self, user, token):
        if not super().check_token(user, token):  # signature, used/changed, PASSWORD_RESET_TIMEOUT
            return False
        try:
            issued = base36_to_int(token.split("-")[0])
        except ValueError:
            return False
        return self._num_seconds(self._now()) - issued <= self.max_age


TOKENS = {kind: _KindTokenGenerator(kind) for kind in KINDS}


def user_email(user):
    """The address to send to: the email field, else the login if it is an email."""
    email = (user.email or "").strip()
    if not email and "@" in (user.get_username() or ""):
        email = user.get_username().strip()
    return email.lower()


def frontend_url():
    return getattr(settings, "DASHBOARD_FRONTEND_URL", "").rstrip("/")


def make_link(user, kind):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = TOKENS[kind].make_token(user)
    return f"{frontend_url()}/set-password?kind={kind}&uid={uid}&token={token}"


def user_from_link(kind, uid, token):
    """The active user the link belongs to, or None if the link is invalid, used or expired."""
    if kind not in KINDS or not uid or not token:
        return None
    try:
        pk = force_str(urlsafe_base64_decode(uid))
        user = get_user_model().objects.get(pk=pk)
    except (ValueError, TypeError, OverflowError, get_user_model().DoesNotExist):
        return None
    if not user.is_active or not TOKENS[kind].check_token(user, token):
        return None
    return user


# ---------------------------------------------------------------- GHL

def _account():
    from documents.survey_opportunity import get_default_ghl_account

    account = get_default_ghl_account()
    if not account:
        raise InviteError("GHL is not connected.")
    return account


def _contact_field_id(account, field_name):
    fields = list_location_custom_fields(account.location_id, model="contact", access_token=account.access_token)
    for f in fields:
        if (f.get("name") or "").strip().lower() == field_name.lower():
            return f.get("id")
    raise InviteError(f'The GHL contact field "{field_name}" is missing. Run dashboard_setup_ghl_fields --apply.')


def _contact_for(account, user, email):
    contact = lookup_duplicate_contact(account.location_id, email=email, access_token=account.access_token)
    if contact and contact.get("id"):
        return contact["id"]
    return create_contact(account, user, email)


def create_contact(account, user, email):
    """
    New GHL contact for a team member, tagged "dashboard user" from the start so GHL
    workflows that fire on new contacts (lead follow-ups etc.) can skip staff.
    """
    first, last = _split_full_name(user.get_full_name())
    payload = {"locationId": account.location_id, "email": email, "tags": [DASHBOARD_USER_TAG], "source": "CEO Dashboard"}
    if first:
        payload["firstName"] = first
    if last:
        payload["lastName"] = last
    headers = _auth_headers(account.access_token)
    headers["Content-Type"] = "application/json"
    resp = requests.post(GHL_CONTACTS_BASE + "/", headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    contact_id = (data.get("contact") or data).get("id")
    if not contact_id:
        raise InviteError("GHL did not return a contact id.")
    return contact_id


def _tags(method, contact_id, tags, token):
    headers = _auth_headers(token)
    headers["Content-Type"] = "application/json"
    resp = requests.request(method, f"{GHL_CONTACTS_BASE}/{contact_id}/tags", headers=headers, json={"tags": tags}, timeout=30)
    resp.raise_for_status()


def send_link(user, kind):
    """
    Put a fresh link on the user's GHL contact and fire the workflow tag.
    Returns the GHL contact id. Raises InviteError with a message for the admin.
    """
    email = user_email(user)
    if not email:
        raise InviteError("This user has no email address.")
    spec = KINDS[kind]
    account = _account()
    try:
        field_id = _contact_field_id(account, spec.field_name)
        contact_id = _contact_for(account, user, email)
        # Link first, then the tag, so the workflow reads the new link.
        update_contact_custom_fields(
            contact_id, [{"id": field_id, "field_value": make_link(user, kind)}], access_token=account.access_token
        )
        # GHL only triggers on a tag being added, so drop it first in case it is still there.
        _tags("DELETE", contact_id, [spec.trigger_tag], account.access_token)
        _tags("POST", contact_id, [DASHBOARD_USER_TAG, spec.trigger_tag], account.access_token)
    except requests.RequestException as exc:
        logger.exception("Dashboard %s email for user %s failed in GHL", kind, user.pk)
        raise InviteError("Could not reach GHL to send the email. Please try again.") from exc
    logger.info("Dashboard %s email queued in GHL for user %s (contact %s)", kind, user.pk, contact_id)
    return contact_id
