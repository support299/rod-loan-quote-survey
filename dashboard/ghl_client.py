"""
GHL API calls used only by the dashboard. Shared calls (pipelines, users, notes,
custom fields) come from documents.ghl_service.
"""
import requests

from documents.ghl_service import (
    GHL_CONTACTS_BASE,
    GHL_OPPORTUNITIES_BASE,
    _auth_headers,
    create_contact_note,
    get_opportunity,
    list_location_custom_fields,
    list_location_users,
    list_pipelines,
)

__all__ = [
    "create_contact_note",
    "create_contact_task",
    "get_opportunity",
    "iter_pipeline_opportunities",
    "list_location_custom_fields",
    "list_location_users",
    "list_pipelines",
    "update_opportunity_fields",
]


def iter_pipeline_opportunities(location_id, pipeline_id, access_token, page_size=100):
    """Yield every opportunity in a pipeline (all statuses), following cursor pagination."""
    params = {"location_id": location_id, "pipeline_id": pipeline_id, "limit": page_size}
    while True:
        resp = requests.get(
            f"{GHL_OPPORTUNITIES_BASE}/search",
            headers=_auth_headers(access_token),
            params=params,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        batch = data.get("opportunities") or []
        yield from batch
        meta = data.get("meta") or {}
        if not batch or not meta.get("nextPage") or not meta.get("startAfterId"):
            return
        params["startAfter"] = meta.get("startAfter")
        params["startAfterId"] = meta.get("startAfterId")


def create_contact_task(contact_id, title, due_at_iso, body="", assigned_to=None, access_token=None):
    """POST /contacts/{contactId}/tasks"""
    headers = _auth_headers(access_token)
    headers["Content-Type"] = "application/json"
    payload = {"title": title, "body": body or "", "dueDate": due_at_iso, "completed": False}
    if assigned_to:
        payload["assignedTo"] = assigned_to
    resp = requests.post(f"{GHL_CONTACTS_BASE}/{contact_id}/tasks", headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    return data.get("task") or data


def complete_contact_task(contact_id, task_id, completed=True, access_token=None):
    """PUT /contacts/{contactId}/tasks/{taskId}/completed"""
    headers = _auth_headers(access_token)
    headers["Content-Type"] = "application/json"
    resp = requests.put(
        f"{GHL_CONTACTS_BASE}/{contact_id}/tasks/{task_id}/completed",
        headers=headers,
        json={"completed": bool(completed)},
        timeout=30,
    )
    resp.raise_for_status()


def update_opportunity_fields(opportunity_id, custom_fields, access_token=None):
    """PUT /opportunities/{id} with customFields [{id, field_value}]."""
    if not custom_fields:
        return {}
    headers = _auth_headers(access_token)
    headers["Content-Type"] = "application/json"
    resp = requests.put(
        f"{GHL_OPPORTUNITIES_BASE}/{opportunity_id}",
        headers=headers,
        json={"customFields": custom_fields},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json() if resp.content else {}
