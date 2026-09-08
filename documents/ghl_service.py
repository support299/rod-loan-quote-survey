"""
GoHighLevel (GHL) Media API service.
Uploads files to GHL instead of storing on server; update/delete via GHL API.
"""
import requests
from django.conf import settings

GHL_UPLOAD_URL = "https://services.leadconnectorhq.com/medias/upload-file"
GHL_MEDIA_BASE = "https://services.leadconnectorhq.com/medias"
GHL_VERSION = "2021-07-28"


def _auth_headers(access_token=None):
    if access_token and str(access_token).strip():
        token = str(access_token).strip()
    else:
        token = getattr(settings, "GHL_ACCESS_TOKEN", None) or ""
    return {
        "Accept": "application/json",
        "Version": GHL_VERSION,
        "Authorization": f"Bearer {token}",
    }


def upload_file(file, name, parent_id=None, access_token=None):
    """
    Upload a file to GHL media.
    :param file: Django UploadedFile (request.FILES['file'])
    :param name: Display name for the file
    :param parent_id: GHL parentId (folder/location). Uses settings.GHL_PARENT_ID if None.
    :param access_token: Bearer token; if omitted, uses settings.GHL_ACCESS_TOKEN.
    :return: dict with fileId, url, traceId
    """
    parent_id = parent_id or getattr(settings, "GHL_PARENT_ID", "") or ""
    headers = _auth_headers(access_token)

    files = {
        "file": (file.name or "document", file, getattr(file, "content_type", "application/octet-stream")),
    }
    data = {
        "parentId": parent_id,
        "name": name,
    }

    resp = requests.post(GHL_UPLOAD_URL, headers=headers, data=data, files=files, timeout=60)
    resp.raise_for_status()
    result = resp.json()
    return {
        "fileId": result.get("fileId"),
        "url": result.get("url"),
        "traceId": result.get("traceId"),
    }


def update_media(document_id, name=None, alt_type=None, alt_id=None, access_token=None):
    """
    Update a media document in GHL.
    PATCH /medias/{document_id}
    """
    headers = _auth_headers(access_token)
    headers["Content-Type"] = "application/json"
    url = f"{GHL_MEDIA_BASE}/{document_id}"
    payload = {}
    if name is not None:
        payload["name"] = name
    if alt_type is not None:
        payload["altType"] = alt_type
    if alt_id is not None:
        payload["altId"] = alt_id
    if not payload:
        return
    resp = requests.patch(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()


def delete_media(document_id, alt_type=None, alt_id=None, access_token=None):
    """
    Delete a media document from GHL.
    DELETE /medias/{document_id}?altType=...&altId=...
    """
    headers = _auth_headers(access_token)
    url = f"{GHL_MEDIA_BASE}/{document_id}"
    params = {}
    if alt_type is not None:
        params["altType"] = alt_type
    if alt_id is not None:
        params["altId"] = alt_id
    resp = requests.delete(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()


GHL_OPPORTUNITIES_BASE = "https://services.leadconnectorhq.com/opportunities"
GHL_CONTACTS_BASE = "https://services.leadconnectorhq.com/contacts"
GHL_LOCATIONS_BASE = "https://services.leadconnectorhq.com/locations"


def get_opportunity(opportunity_id, access_token=None):
    """
    Fetch a single opportunity from GHL by ID.
    GET /opportunities/{opportunity_id}
    :param access_token: Bearer token; if omitted, uses settings.GHL_ACCESS_TOKEN.
    :return: dict with 'opportunity' (e.g. id, name, contactId, ...)
    """
    headers = _auth_headers(access_token)
    url = f"{GHL_OPPORTUNITIES_BASE}/{opportunity_id}"
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def update_opportunity_custom_fields(opportunity_id, custom_fields, access_token=None):
    """
    Update one or more custom fields on a GHL opportunity.

    PUT /opportunities/{opportunity_id}

    :param opportunity_id: GHL opportunity ID
    :param custom_fields: list of {"id": field_id, "field_value": value}
    :param access_token: Bearer token; if omitted, uses settings.GHL_ACCESS_TOKEN.
    :return: dict from API (opportunity payload)
    """
    if not custom_fields:
        return {}

    print("access_token", access_token)
    headers = _auth_headers(access_token)
    headers["Content-Type"] = "application/json"
    url = f"{GHL_OPPORTUNITIES_BASE}/{opportunity_id}"
    payload = {
        "customFields": custom_fields,
    }

    print("payload", payload)
    resp = requests.put(url, headers=headers, json=payload, timeout=30)
    print("resp", resp.json())
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def create_contact_note(contact_id, body, access_token=None):
    """
    Create a note on a GHL contact.
    POST /contacts/{contact_id}/notes
    :param contact_id: GHL contact ID
    :param body: note body text
    :param access_token: Bearer token; if omitted, uses settings.GHL_ACCESS_TOKEN.
    :return: dict from API (e.g. note id, etc.)
    """
    headers = _auth_headers(access_token)
    headers["Content-Type"] = "application/json"
    url = f"{GHL_CONTACTS_BASE}/{contact_id}/notes"
    payload = {"body": body}
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def update_contact_note(contact_id, note_id, body, access_token=None):
    """
    Update an existing note on a GHL contact.
    PUT /contacts/{contact_id}/notes/{note_id}

    :param contact_id: GHL contact ID
    :param note_id: GHL note ID
    :param body: new note body text
    :param access_token: Bearer token; if omitted, uses settings.GHL_ACCESS_TOKEN.
    :return: dict from API (e.g. note payload)
    """
    headers = _auth_headers(access_token)
    headers["Content-Type"] = "application/json"
    url = f"{GHL_CONTACTS_BASE}/{contact_id}/notes/{note_id}"
    payload = {"body": body}
    resp = requests.put(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def update_contact_custom_field(contact_id, field_id, value, access_token=None):
    """
    Update a single custom field on a GHL contact.
    PUT /contacts/{contact_id} with customFields [{id, field_value}, ...].

    :param contact_id: GHL contact ID
    :param field_id: GHL custom field ID (string)
    :param value: string value to set (sent as field_value)
    :param access_token: Bearer token; if omitted, uses settings.GHL_ACCESS_TOKEN.
    :return: dict from API (contact payload)
    """
    return update_contact_custom_fields(
        contact_id,
        [{"id": field_id, "field_value": value}],
        access_token=access_token,
    )


def update_contact_custom_fields(contact_id, custom_fields, access_token=None):
    """
    Update one or more custom fields on a GHL contact.
    PUT /contacts/{contact_id}

    :param contact_id: GHL contact ID
    :param custom_fields: list of {"id": field_id, "field_value": value}
    :param access_token: Bearer token; if omitted, uses settings.GHL_ACCESS_TOKEN.
    :return: dict from API (contact payload)
    """
    if not custom_fields:
        return {}
    headers = _auth_headers(access_token)
    headers["Content-Type"] = "application/json"
    url = f"{GHL_CONTACTS_BASE}/{contact_id}"
    payload = {"customFields": custom_fields}
    resp = requests.put(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def list_location_custom_fields(location_id, model="contact", access_token=None):
    """
    List custom fields for a location.
    GET /locations/{locationId}/customFields?model=contact|opportunity

    :return: list of custom field dicts from API
    """
    headers = _auth_headers(access_token)
    url = f"{GHL_LOCATIONS_BASE}/{location_id}/customFields"
    resp = requests.get(url, headers=headers, params={"model": model}, timeout=30)
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    return data.get("customFields") or []


def create_location_custom_field(
    location_id,
    name,
    data_type,
    model="contact",
    options=None,
    placeholder=None,
    position=None,
    access_token=None,
):
    """
    Create a custom field on a GHL location.
    POST /locations/{locationId}/customFields

    :param location_id: GHL location ID
    :param name: Field display name
    :param data_type: e.g. TEXT, LARGE_TEXT, DATE, SINGLE_OPTIONS, NUMERICAL
    :param model: "contact" or "opportunity"
    :param options: list of option strings for SINGLE_OPTIONS / RADIO / etc.
    :return: created custom field dict (from response.customField or response)
    """
    headers = _auth_headers(access_token)
    headers["Content-Type"] = "application/json"
    url = f"{GHL_LOCATIONS_BASE}/{location_id}/customFields"
    payload = {
        "name": name,
        "dataType": data_type,
        "model": model,
    }
    if placeholder is not None:
        payload["placeholder"] = placeholder
    if position is not None:
        payload["position"] = position
    if options:
        # Location customFields create expects `options` (not picklistOptions)
        payload["options"] = list(options)

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    return data.get("customField") or data


def _split_full_name(full_name):
    """Split 'Full Name' into firstName / lastName for GHL contact payloads."""
    parts = (full_name or "").strip().split(None, 1)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def lookup_duplicate_contact(location_id, email=None, phone=None, access_token=None):
    """
    Find an existing contact by email or phone.
    GET /contacts/search/duplicate?locationId=&email=&number=
    :return: contact dict or None
    """
    headers = _auth_headers(access_token)
    url = f"{GHL_CONTACTS_BASE}/search/duplicate"
    params = {"locationId": location_id}
    if email:
        params["email"] = email
    if phone:
        params["number"] = phone
    if "email" not in params and "number" not in params:
        return None
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    if resp.status_code in (400, 404, 422):
        return None
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    contact = data.get("contact") or data.get("contacts")
    if isinstance(contact, list):
        return contact[0] if contact else None
    return contact or None


def get_contact(contact_id, access_token=None):
    """
    Fetch a GHL contact by id.
    GET /contacts/{contactId}
    :return: contact dict
    """
    headers = _auth_headers(access_token)
    url = f"{GHL_CONTACTS_BASE}/{contact_id}"
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    return data.get("contact") or data


def create_contact(location_id, full_name=None, email=None, phone=None, access_token=None):
    """
    Create a GHL contact.
    POST /contacts/
    :return: contact dict (id, ...)
    """
    headers = _auth_headers(access_token)
    headers["Content-Type"] = "application/json"
    first_name, last_name = _split_full_name(full_name)
    payload = {"locationId": location_id}
    if first_name:
        payload["firstName"] = first_name
    if last_name:
        payload["lastName"] = last_name
    if full_name:
        payload["name"] = (full_name or "").strip()
    if email:
        payload["email"] = email
    if phone:
        payload["phone"] = phone
    resp = requests.post(GHL_CONTACTS_BASE + "/", headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    return data.get("contact") or data


def update_contact(contact_id, full_name=None, email=None, phone=None, access_token=None):
    """
    Update a GHL contact's standard fields.
    PUT /contacts/{contactId}
    :return: contact dict
    """
    headers = _auth_headers(access_token)
    headers["Content-Type"] = "application/json"
    payload = {}
    if full_name is not None:
        first_name, last_name = _split_full_name(full_name)
        payload["name"] = (full_name or "").strip()
        payload["firstName"] = first_name
        payload["lastName"] = last_name
    if email is not None:
        payload["email"] = email
    if phone is not None:
        payload["phone"] = phone
    if not payload:
        return {}
    url = f"{GHL_CONTACTS_BASE}/{contact_id}"
    resp = requests.put(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    return data.get("contact") or data


def upsert_contact_by_email_or_phone(
    location_id,
    full_name=None,
    email=None,
    phone=None,
    access_token=None,
):
    """
    Create or update a contact. Match by email first, then phone.
    :return: dict {contact, contact_id, created: bool}
    """
    email = (email or "").strip() or None
    phone = (phone or "").strip() or None
    full_name = (full_name or "").strip() or None

    existing = None
    if email:
        try:
            existing = lookup_duplicate_contact(
                location_id, email=email, access_token=access_token
            )
        except Exception:
            existing = None
    if not existing and phone:
        try:
            existing = lookup_duplicate_contact(
                location_id, phone=phone, access_token=access_token
            )
        except Exception:
            existing = None

    contact_id = (existing or {}).get("id") if existing else None
    if contact_id:
        contact = update_contact(
            contact_id,
            full_name=full_name,
            email=email,
            phone=phone,
            access_token=access_token,
        )
        if not contact.get("id"):
            contact = {**(existing or {}), **(contact or {}), "id": contact_id}
        return {"contact": contact, "contact_id": contact_id, "created": False}

    contact = create_contact(
        location_id,
        full_name=full_name,
        email=email,
        phone=phone,
        access_token=access_token,
    )
    contact_id = contact.get("id")
    if not contact_id:
        raise RuntimeError(f"GHL create contact returned no id: {contact}")
    return {"contact": contact, "contact_id": contact_id, "created": True}


def list_pipelines(location_id, access_token=None):
    """
    List opportunity pipelines for a location.
    GET /opportunities/pipelines?locationId=
    :return: list of pipeline dicts (id, name, stages, ...)
    """
    headers = _auth_headers(access_token)
    url = f"{GHL_OPPORTUNITIES_BASE}/pipelines"
    resp = requests.get(url, headers=headers, params={"locationId": location_id}, timeout=30)
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    return data.get("pipelines") or []


def create_opportunity(
    location_id,
    contact_id,
    pipeline_id,
    pipeline_stage_id,
    name,
    status="open",
    access_token=None,
):
    """
    Create a new GHL opportunity (always new — not upsert).
    POST /opportunities/
    :return: opportunity dict
    """
    headers = _auth_headers(access_token)
    headers["Content-Type"] = "application/json"
    payload = {
        "locationId": location_id,
        "contactId": contact_id,
        "pipelineId": pipeline_id,
        "pipelineStageId": pipeline_stage_id,
        "name": name,
        "status": status,
    }
    resp = requests.post(GHL_OPPORTUNITIES_BASE + "/", headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    return data.get("opportunity") or data


def update_opportunity(
    opportunity_id,
    name=None,
    pipeline_id=None,
    pipeline_stage_id=None,
    contact_id=None,
    status=None,
    access_token=None,
):
    """
    Update an existing GHL opportunity.
    PUT /opportunities/{opportunityId}
    :return: opportunity dict
    """
    headers = _auth_headers(access_token)
    headers["Content-Type"] = "application/json"
    payload = {}
    if name is not None:
        payload["name"] = name
    if pipeline_id is not None:
        payload["pipelineId"] = pipeline_id
    if pipeline_stage_id is not None:
        payload["pipelineStageId"] = pipeline_stage_id
    if contact_id is not None:
        payload["contactId"] = contact_id
    if status is not None:
        payload["status"] = status
    if not payload:
        return {}
    url = f"{GHL_OPPORTUNITIES_BASE}/{opportunity_id}"
    resp = requests.put(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    return data.get("opportunity") or data
