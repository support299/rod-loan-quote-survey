"""
Server-to-server API for a trusted partner server.

Auth: the partner sends the shared secret (EXTERNAL_API_SECRET_KEY in both
servers' .env) in the `X-API-Key` header, or as `Authorization: Bearer <key>`.
"""
import hmac
import logging
from functools import wraps

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import (
    AdminDocumentSelection,
    DocumentRequest,
    OpportunityCardSubmission,
    UserDocumentUpload,
)

logger = logging.getLogger(__name__)


def _provided_key(request):
    key = request.headers.get("X-API-Key", "")
    if key:
        return key.strip()
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def require_external_api_key(view):
    """Reject the request unless it carries the shared EXTERNAL_API_SECRET_KEY."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        expected = getattr(settings, "EXTERNAL_API_SECRET_KEY", "")
        if not expected:
            # Fail closed: never serve data when the secret is not configured.
            logger.error("EXTERNAL_API_SECRET_KEY is not configured; rejecting external API call")
            return JsonResponse({"error": "External API is not configured"}, status=503)

        provided = _provided_key(request)
        if not provided or not hmac.compare_digest(provided.encode(), expected.encode()):
            logger.warning(
                "External API auth failed for %s from %s",
                request.path,
                request.META.get("REMOTE_ADDR"),
            )
            return JsonResponse({"error": "Unauthorized"}, status=401)

        return view(request, *args, **kwargs)

    return wrapper


def _iso(dt):
    return dt.isoformat() if dt else None


def _upload_status(upload):
    if upload.accepted:
        return "accepted"
    if upload.rejected_at:
        return "rejected"
    return "pending"


def _serialize_upload(upload):
    return {
        "id": upload.id,
        "file_name": upload.get_file_name(),
        "file_url": upload.get_file_url(),
        "ghl_file_id": upload.ghl_file_id,
        "status": _upload_status(upload),
        "accepted": upload.accepted,
        "accepted_at": _iso(upload.accepted_at),
        "rejection_reason": upload.rejection_reason,
        "rejected_at": _iso(upload.rejected_at),
        "uploaded_at": _iso(upload.uploaded_at),
        "updated_at": _iso(upload.updated_at),
    }


def _serialize_selection(selection, uploads):
    doc = selection.document
    template = selection.template or doc.blank_template
    return {
        "id": selection.id,
        "section_type": selection.section_type,
        "section_label": selection.get_section_type_display(),
        "document": {
            "id": doc.id,
            "name": doc.name,
            "description": doc.description,
            "category": doc.category.name if doc.category_id else None,
            "attachment_mode": doc.attachment_mode,
            "is_custom_for_request": doc.request_id is not None,
        },
        "print_group": (
            {"id": selection.print_group.id, "name": selection.print_group.name}
            if selection.print_group_id
            else None
        ),
        "template": (
            {
                "id": template.id,
                "name": template.name,
                "file_name": template.file_name,
                "file_url": template.ghl_file_url,
            }
            if template
            else None
        ),
        "uploads": [_serialize_upload(u) for u in uploads],
        "created_at": _iso(selection.created_at),
    }


def _serialize_document_request(doc_request):
    selections = (
        AdminDocumentSelection.objects.filter(request=doc_request)
        .select_related("document__category", "document__blank_template", "print_group", "template")
        .order_by("section_type", "created_at")
    )
    uploads_by_selection = {}
    for upload in UserDocumentUpload.objects.filter(admin_selection__request=doc_request).order_by("-uploaded_at"):
        uploads_by_selection.setdefault(upload.admin_selection_id, []).append(upload)

    serialized = [_serialize_selection(s, uploads_by_selection.get(s.id, [])) for s in selections]
    all_uploads = [u for s in serialized for u in s["uploads"]]

    account = doc_request.ghl_account
    return {
        "created_at": _iso(doc_request.created_at),
        "updated_at": _iso(doc_request.updated_at),
        "ghl_account": (
            {"location_id": account.location_id, "location_name": account.location_name}
            if account
            else None
        ),
        "title_company": {
            "name": doc_request.title_company_name,
            "email": doc_request.title_company_email,
            "phone": doc_request.title_company_phone,
            "by_scope": doc_request.title_company_by_scope or {},
        },
        "insurance_agent": {
            "name": doc_request.insurance_agent_name,
            "email": doc_request.insurance_agent_email,
            "phone": doc_request.insurance_agent_phone,
        },
        "ghl_needs_list_note_id": doc_request.ghl_needs_list_note_id,
        "summary": {
            "requested_documents": len(serialized),
            "uploads_total": len(all_uploads),
            "uploads_accepted": sum(1 for u in all_uploads if u["status"] == "accepted"),
            "uploads_rejected": sum(1 for u in all_uploads if u["status"] == "rejected"),
            "uploads_pending": sum(1 for u in all_uploads if u["status"] == "pending"),
        },
        "requested_documents": serialized,
    }


@csrf_exempt
@require_http_methods(["GET"])
@require_external_api_key
def opportunity_details(request, opportunity_id):
    """
    GET /api/external/opportunities/<opportunity_id>/

    Everything stored for a GHL opportunity: the opportunity card / loan quote
    survey submission and the document request (requested docs + uploads).
    """
    submission = OpportunityCardSubmission.objects.filter(request_id=opportunity_id).first()
    doc_request = (
        DocumentRequest.objects.select_related("ghl_account").filter(request_id=opportunity_id).first()
    )
    if not submission and not doc_request:
        return JsonResponse({"error": "Opportunity not found", "opportunity_id": opportunity_id}, status=404)

    return JsonResponse({
        "opportunity_id": opportunity_id,
        "opportunity_card_submission": (
            {
                "form_data": submission.form_data,
                "submitted_at": _iso(submission.submitted_at),
                "ghl_note_id": submission.ghl_note_id,
            }
            if submission
            else None
        ),
        "document_request": _serialize_document_request(doc_request) if doc_request else None,
    })
