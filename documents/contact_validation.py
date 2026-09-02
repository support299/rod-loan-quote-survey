"""
Contact field validation for Quick App Submission Form.
Unicode-aware names; phones via Google libphonenumber (E.164).
"""
import re
import unicodedata

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat

# Default region when user omits country code (US-centric product).
DEFAULT_PHONE_REGION = "US"


def _is_letter_or_mark(ch: str) -> bool:
    """True for Unicode letters (L*) and combining marks (M*)."""
    cat = unicodedata.category(ch)
    return cat.startswith("L") or cat.startswith("M")


def is_valid_full_name(value) -> bool:
    """
    Permissive international name check.

    Intent (Unicode property escapes):
      ^[\\p{L}\\p{M}]+(?:[ '\\-.]+[\\p{L}\\p{M}]+)*$

    Allows letters + combining marks, with space / ' / - / . between parts
    (including forms like "St. John"). Rejects digits and other junk without
    trying to decide if a name is "real."
    """
    name = " ".join(str(value or "").strip().split())
    if not name or len(name) > 200:
        return False

    i = 0
    n = len(name)
    saw_letter_group = False

    while i < n:
        if not _is_letter_or_mark(name[i]):
            return False
        while i < n and _is_letter_or_mark(name[i]):
            i += 1
        saw_letter_group = True

        if i >= n:
            break

        # One or more allowed separators between letter groups
        if name[i] not in (" ", "'", "-", "."):
            return False
        while i < n and name[i] in (" ", "'", "-", "."):
            i += 1
        if i >= n or not _is_letter_or_mark(name[i]):
            return False

    return saw_letter_group


def is_valid_email(value) -> bool:
    email = str(value or "").strip()
    return bool(re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$", email))


def parse_phone(value, default_region=DEFAULT_PHONE_REGION):
    """
    Parse a natural-format phone number.
    :return: phonenumbers.PhoneNumber or None
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        number = phonenumbers.parse(raw, default_region)
    except NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    return number


def normalize_phone(value, default_region=DEFAULT_PHONE_REGION) -> str:
    """
    Normalize to E.164 (e.g. +14155551234), or '' if invalid.
    Extensions are not included in E.164 (validated if present, then dropped).
    """
    number = parse_phone(value, default_region=default_region)
    if not number:
        return ""
    return phonenumbers.format_number(number, PhoneNumberFormat.E164)


def is_valid_phone(value, default_region=DEFAULT_PHONE_REGION) -> bool:
    return parse_phone(value, default_region=default_region) is not None


def validate_contact_fields(form_data) -> str:
    """
    Validate contact full name, email, phone.
    When phone is valid, writes E.164 back onto form_data['phone'].
    :return: error message or ''
    """
    form_data = form_data or {}
    full_name = (form_data.get("full_name") or "").strip()
    email = (form_data.get("email") or "").strip()
    phone_raw = form_data.get("phone") or ""

    if not full_name:
        return "Full Name is required."
    if not is_valid_full_name(full_name):
        return (
            "Enter a valid name using letters "
            "(spaces and - ' . allowed between name parts)."
        )
    if not is_valid_email(email):
        return "Enter a valid email address (e.g. name@example.com)."

    e164 = normalize_phone(phone_raw)
    if not e164:
        return "Enter a valid phone number (e.g. +1 555 123 4567 or (415) 555-1234)."

    form_data["phone"] = e164
    return ""
