"""
Resolve GoHighLevel API credentials from GHLAuthCredentials using location_id.

Pass location_id on query string (?location_id=...) or in POST/JSON so embedded
pages and APIs use the correct sub-account token instead of a single .env token.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from accounts.models import GHLAuthCredentials


def extract_location_id(request, json_body: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """location_id from JSON body (if dict), then GET, then POST."""
    if json_body and isinstance(json_body, dict):
        raw = json_body.get("location_id")
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    q = request.GET.get("location_id")
    if q and str(q).strip():
        return str(q).strip()
    if request.method == "POST":
        p = request.POST.get("location_id")
        if p and str(p).strip():
            return str(p).strip()
    return None


def get_ghl_api_context(location_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Load stored OAuth credentials for a GHL location.

    Returns dict with access_token, parent_id, alt_type, alt_id, credentials
    or None if location_id missing / no row / empty token.
    """
    if not location_id or not str(location_id).strip():
        return None
    creds = GHLAuthCredentials.objects.filter(location_id=str(location_id).strip()).first()
    if not creds or not (creds.access_token or "").strip():
        return None
    token = creds.access_token.strip()
    # GHL media upload parentId: use stored parent_id; if unset, use location_id.
    parent = (creds.parent_id or "").strip() or (creds.location_id or "").strip() or None
    templates_parent = (creds.templates_parent_id or "").strip() or None
    alt_type = (creds.alt_type or "location").strip() or "location"
    alt_id = (creds.alt_id or "").strip() or (creds.location_id or "").strip() or None
    return {
        "access_token": token,
        "parent_id": parent,
        "templates_parent_id": templates_parent,
        "alt_type": alt_type,
        "alt_id": alt_id,
        "credentials": creds,
    }
