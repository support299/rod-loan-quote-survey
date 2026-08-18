from django.http import JsonResponse, Http404, HttpResponse
from django.views.decorators.http import require_http_methods
from django.shortcuts import render, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone
import json
import logging
from io import BytesIO
import html
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib import colors
from .models import (
    AccountDocumentLibrary,
    AccountPrintGroupLibrary,
    Category,
    Document,
    PrintGroup,
    DocumentRequest,
    AdminDocumentSelection,
    UserDocumentUpload,
    OpportunityCardSubmission,
    NeedsListTemplate,
)
from .account_library import (
    document_visible_to_account,
    filter_documents_for_listing,
    filter_print_groups_for_listing,
    print_group_visible_to_account,
    resolve_account_for_request,
    sync_master_document_to_all_accounts,
)
from .ghl_credentials import extract_location_id, get_ghl_api_context


def _document_request_by_url_id(request_id):
    if not request_id:
        return None
    return DocumentRequest.objects.filter(request_id=request_id).first()


@require_http_methods(["GET"])
def get_categories(request):
    """
    API endpoint to get all categories
    GET /api/categories/
    Query parameters:
    - request_id: When provided, return only global categories + custom categories for this request (optional)
    - location_id: When provided with global categories, limit to categories that have at least one
      document enabled for that GHL subaccount.
    - include_empty: When 1/true, skip the "has visible docs" filter (needed for catalog admin).
    """
    categories = Category.objects.select_related('request').annotate(
        document_count=Count('documents')
    )
    request_id = request.GET.get('request_id')
    doc_request = _document_request_by_url_id(request_id) if request_id else None
    account = resolve_account_for_request(request, doc_request=doc_request)
    include_empty = str(request.GET.get('include_empty') or '').lower() in ('1', 'true', 'yes')

    if request_id:
        categories = categories.filter(
            Q(request__isnull=True) | Q(request__request_id=request_id)
        )
    else:
        categories = categories.all()

    if account and not include_empty:
        visible_docs = filter_documents_for_listing(
            Document.objects.all(), account=account, doc_request=doc_request
        )
        visible_cat_ids = visible_docs.values_list("category_id", flat=True).distinct()
        if doc_request:
            categories = categories.filter(
                Q(request=doc_request)
                | (Q(request__isnull=True) & Q(id__in=visible_cat_ids))
            )
        else:
            categories = categories.filter(
                Q(request__isnull=True) & Q(id__in=visible_cat_ids)
            )
    elif include_empty and not request_id:
        # Catalog admin: only shared (non request-scoped) categories
        categories = categories.filter(request__isnull=True)
    data = [
        {
            'id': category.id,
            'name': category.name,
            'description': category.description,
            'document_count': getattr(category, 'document_count', 0),
            'created_at': category.created_at.isoformat() if category.created_at else None,
            'updated_at': category.updated_at.isoformat() if category.updated_at else None,
        }
        for category in categories
    ]
    return JsonResponse({'categories': data}, safe=False)


@csrf_exempt
@require_http_methods(["POST"])
def create_category(request):
    """
    API endpoint to create a new category
    POST /api/categories/create/
    Body: JSON with name, description (optional), request_id (optional - when provided, category is scoped to that request)
    """
    try:
        data = json.loads(request.body)
        name = data.get('name')
        description = data.get('description', '')
        request_id = data.get('request_id')
        
        if not name:
            return JsonResponse({'error': 'Missing required field: name'}, status=400)
        
        doc_request = None
        if request_id:
            doc_request, _ = DocumentRequest.objects.get_or_create(request_id=request_id)
            _link_doc_request_to_location(request, doc_request, json_body=data)
            # For request-scoped categories, only check uniqueness within this request
            if Category.objects.filter(name=name, request=doc_request).exists():
                return JsonResponse({'error': 'A category with this name already exists for this request'}, status=400)
        else:
            # Global category: check uniqueness among global categories only
            if Category.objects.filter(name=name, request__isnull=True).exists():
                return JsonResponse({'error': 'Category with this name already exists'}, status=400)
        
        # Create the category (request-scoped if request_id provided)
        category = Category.objects.create(
            name=name,
            description=description or f'Category: {name}',
            request=doc_request
        )
        
        return JsonResponse({
            'success': True,
            'category': {
                'id': category.id,
                'name': category.name,
                'description': category.description,
            }
        }, status=201)
    
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["DELETE"])
def delete_category(request, category_id):
    """
    Delete a catalog (global) category.
    DELETE /api/categories/<category_id>/
    Documents in the category are deleted with it (CASCADE).
    """
    try:
        category = Category.objects.get(id=category_id)
    except Category.DoesNotExist:
        return JsonResponse({'error': 'Category not found'}, status=404)

    if category.request_id:
        return JsonResponse(
            {'error': 'Request-scoped categories cannot be deleted from Catalog Admin'},
            status=400,
        )

    doc_count = category.documents.count()
    category.delete()
    return JsonResponse({
        'success': True,
        'deleted': category_id,
        'documents_deleted': doc_count,
    })


@require_http_methods(["GET"])
def get_documents(request):
    """
    API endpoint to get all documents
    GET /api/documents/
    
    Query parameters:
    - request_id: When provided, return only global documents + custom documents for this request (optional)
    - location_id: When provided, limit catalog documents to those enabled for that GHL subaccount
      (and account-owned templates).
    - category_id: Filter by category ID (optional)
    - print_group_id: Filter by print group ID (optional)
    """
    documents = Document.objects.select_related('category', 'request').prefetch_related('print_groups')
    
    request_id = request.GET.get('request_id')
    doc_request = _document_request_by_url_id(request_id) if request_id else None
    account = resolve_account_for_request(request, doc_request=doc_request)
    documents = filter_documents_for_listing(
        documents, account=account, doc_request=doc_request
    )
    
    # Filter by category if provided
    category_id = request.GET.get('category_id')
    if category_id:
        documents = documents.filter(category_id=category_id)
    
    # Filter by print group if provided
    print_group_id = request.GET.get('print_group_id')
    if print_group_id:
        documents = documents.filter(print_groups__id=print_group_id).distinct()
    
    data = [
        {
            'id': document.id,
            'name': document.name,
            'description': document.description,
            'category': {
                'id': document.category.id,
                'name': document.category.name,
            },
            'print_groups': [
                {
                    'id': pg.id,
                    'name': pg.name,
                }
                for pg in document.print_groups.all()
            ],
            'file': document.file.url if document.file else None,
            'file_name': document.file.name.split('/')[-1] if document.file else None,
            'created_at': document.created_at.isoformat() if document.created_at else None,
            'updated_at': document.updated_at.isoformat() if document.updated_at else None,
        }
        for document in documents
    ]
    return JsonResponse({'documents': data}, safe=False)


@require_http_methods(["GET"])
def get_print_groups(request):
    """
    API endpoint to get all print groups
    GET /api/print-groups/
    
    Query parameters:
    - request_id: When provided, return only global print groups + custom print groups for this request (optional)
    - location_id: When provided, catalog print groups are scoped to those enabled for that subaccount
      (same pattern as documents). Account-owned print groups are always included for that location.
    - document_id: Filter by document ID to get print groups for a specific document (optional)
    """
    print_groups = PrintGroup.objects.select_related("request", "owner_account")

    request_id = request.GET.get("request_id")
    doc_request = _document_request_by_url_id(request_id) if request_id else None
    account = resolve_account_for_request(request, doc_request=doc_request)

    if request_id:
        print_groups = print_groups.filter(
            Q(request__isnull=True) | Q(request__request_id=request_id)
        )
    else:
        print_groups = print_groups.all()

    print_groups = filter_print_groups_for_listing(
        print_groups, account=account, doc_request=doc_request
    )

    # Filter by document if provided
    document_id = request.GET.get('document_id')
    if document_id:
        print_groups = print_groups.filter(documents__id=document_id).distinct()
    
    data = [
        {
            'id': pg.id,
            'name': pg.name,
            'description': pg.description,
            'created_at': pg.created_at.isoformat() if pg.created_at else None,
            'updated_at': pg.updated_at.isoformat() if pg.updated_at else None,
        }
        for pg in print_groups
    ]
    return JsonResponse({'print_groups': data}, safe=False)


# All form field names from opportunity card template (for saving)
# Steps: 1 = applicant info, 2 = borrower experience, 3 = loan info
OPPORTUNITY_CARD_FIELD_NAMES = [
    # Step 1
    'entity_name', 'broker_or_borrower', 'account_executive', 'fico_score',
    # Step 2
    'fix_and_hold_properties', 'fix_and_flip_properties', 'residential_ground_up_projects',
    # Step 3
    'subject_property_address', 'loan_type', 'dscr_loan_type', 'property_type',
    'units_residential', 'units_commercial', 'number_of_units', 'total_number_of_units',
    'residential_sqft_51_percent', 'commercial_property_type', 'please_specify', 'lot_owned',
    'payoff', 'bridge_loan_type', 'purchase_price_original_if_refi', 'original_purchase_price',
    'original_purchase_date', 'purchase_price', 'rehab_budget', 'original_acquisition_cost',
    'original_acquisition_date', 'arv', 'estimated_property_value', 'annual_insurance',
    'annual_tax', 'monthly_rent', 'existing_mortgage', 'exit_strategy', 'current_property_value',
    'as_is_value', 'construction_budget', 'final_value', 'occupancy', 'units_leased',
    'zoning_permit_status', 'lot_purchase_price', 'intended_exit', 'existing_loan_balance',
    'sms_consent',
]

# Sections and labels for read-only view and PDF (section title, list of field keys)
OPPORTUNITY_CARD_SECTIONS = [
    ("Applicant Info", [
        'entity_name', 'broker_or_borrower', 'account_executive', 'fico_score',
    ]),
    ("Borrower Experience", [
        'fix_and_hold_properties', 'fix_and_flip_properties', 'residential_ground_up_projects',
    ]),
    ("Loan Info", [
        'subject_property_address', 'loan_type', 'dscr_loan_type', 'property_type',
        'units_residential', 'units_commercial', 'number_of_units', 'total_number_of_units',
        'residential_sqft_51_percent', 'commercial_property_type', 'please_specify', 'lot_owned',
        'payoff', 'bridge_loan_type', 'purchase_price_original_if_refi', 'original_purchase_price',
        'original_purchase_date', 'purchase_price', 'rehab_budget', 'original_acquisition_cost',
        'original_acquisition_date', 'arv', 'estimated_property_value', 'annual_insurance',
        'annual_tax', 'monthly_rent', 'existing_mortgage', 'exit_strategy', 'current_property_value',
        'as_is_value', 'construction_budget', 'final_value', 'occupancy', 'units_leased',
        'zoning_permit_status', 'lot_purchase_price', 'intended_exit', 'existing_loan_balance',
        'sms_consent',
    ]),
]
OPPORTUNITY_CARD_FIELD_LABELS = {
    'entity_name': 'Entity Name',
    'broker_or_borrower': 'Are you a Broker or Direct Borrower?',
    'account_executive': 'Account Executive',
    'fico_score': 'FICO Score',
    'fix_and_hold_properties': 'Fix-and-Hold properties currently generating income (past 36 months)',
    'fix_and_flip_properties': 'Fix-and-Flip properties sold (past 36 months)',
    'residential_ground_up_projects': 'Residential Ground-up projects sold (past 36 months)',
    'subject_property_address': 'Subject Property Address',
    'loan_type': 'Loan Type',
    'dscr_loan_type': 'DSCR Loan Type',
    'property_type': 'Property Type',
    'units_residential': 'How many units are Residential?',
    'units_commercial': 'How many units are Commercial?',
    'number_of_units': 'Number of Units',
    'total_number_of_units': 'Total Number of Units',
    'residential_sqft_51_percent': 'Is Residential Square footage at least 51% of the property sq footage?',
    'commercial_property_type': 'Commercial Property Type',
    'please_specify': 'Please Specify',
    'lot_owned': 'Lot Owned?',
    'payoff': 'Payoff',
    'bridge_loan_type': 'Bridge Loan Type',
    'purchase_price_original_if_refi': 'Purchase Price (Original Purchase Price if Refi)',
    'original_purchase_price': 'Original Purchase Price',
    'original_purchase_date': 'Original Purchase Date',
    'purchase_price': 'Purchase Price',
    'rehab_budget': 'Rehab Budget',
    'original_acquisition_cost': 'Original Acquisition Cost',
    'original_acquisition_date': 'Original Acquisition Date',
    'arv': 'ARV (After Repair Value)',
    'estimated_property_value': 'Estimated Property Value',
    'annual_insurance': 'Annual Insurance',
    'annual_tax': 'Annual Tax',
    'monthly_rent': 'Monthly Rent',
    'existing_mortgage': 'Existing Mortgage (put N/A if Purchase)',
    'exit_strategy': 'Exit Strategy',
    'current_property_value': 'Current Property Value',
    'as_is_value': 'As-Is Value',
    'construction_budget': 'Construction Budget',
    'final_value': 'Final Value',
    'occupancy': 'Occupancy',
    'units_leased': 'How many units are leased?',
    'zoning_permit_status': 'Zoning & Permit Status',
    'lot_purchase_price': 'Lot Purchase Price',
    'intended_exit': 'Intended Exit (Sale, Refi)',
    'existing_loan_balance': 'Existing Loan Balance (N/A if Purchase Bridge)',
    'sms_consent': 'SMS Consent',
}

# Always kept (steps 1–2 + step 3 shell).
OPPORTUNITY_CARD_ALWAYS_FIELDS = frozenset({
    'entity_name', 'broker_or_borrower', 'account_executive', 'fico_score',
    'fix_and_hold_properties', 'fix_and_flip_properties', 'residential_ground_up_projects',
    'subject_property_address', 'loan_type', 'sms_consent',
})

# Step 3 fields allowed per Loan Type (nested rules applied separately).
OPPORTUNITY_CARD_LOAN_TYPE_FIELDS = {
    'FIX & FLIP': frozenset({
        'property_type', 'number_of_units', 'commercial_property_type', 'please_specify',
        'units_residential', 'units_commercial', 'total_number_of_units', 'residential_sqft_51_percent',
        'purchase_price', 'rehab_budget', 'arv', 'exit_strategy',
    }),
    'DSCR': frozenset({
        'dscr_loan_type', 'property_type', 'number_of_units', 'commercial_property_type', 'please_specify',
        'units_residential', 'units_commercial', 'total_number_of_units', 'residential_sqft_51_percent',
        'purchase_price_original_if_refi', 'original_purchase_date',
        'estimated_property_value', 'annual_insurance', 'annual_tax', 'monthly_rent',
        'existing_mortgage', 'occupancy', 'units_leased',
    }),
    'BRIDGE': frozenset({
        'property_type', 'number_of_units', 'commercial_property_type', 'please_specify',
        'units_residential', 'units_commercial', 'total_number_of_units', 'residential_sqft_51_percent',
        'bridge_loan_type', 'original_purchase_price', 'original_purchase_date',
        'original_acquisition_cost', 'original_acquisition_date', 'purchase_price',
        'rehab_budget', 'arv',
        'current_property_value', 'intended_exit', 'existing_loan_balance',
    }),
    'GROUND-UP CONSTRUCTION': frozenset({
        'property_type', 'number_of_units', 'commercial_property_type', 'please_specify',
        'units_residential', 'units_commercial', 'total_number_of_units', 'residential_sqft_51_percent',
        'lot_owned', 'purchase_price', 'as_is_value', 'construction_budget', 'final_value',
        'zoning_permit_status', 'lot_purchase_price',
    }),
}

# Original Purchase Date: DSCR Refinance / Bridge Refi Bridge / Refi-Rehab / Purchase Bridge
# Refi Bridge only: Original Acquisition Cost + Date
# Refi-Rehab only: Original Purchase Price + Rehab Budget + ARV
OPPORTUNITY_CARD_REFI_DATE_FIELDS = frozenset({
    'original_purchase_date',
})
OPPORTUNITY_CARD_BRIDGE_REFI_BRIDGE_FIELDS = frozenset({
    'original_acquisition_cost',
    'original_acquisition_date',
})
OPPORTUNITY_CARD_BRIDGE_REFI_REHAB_FIELDS = frozenset({
    'original_purchase_price',
    'rehab_budget',
    'arv',
})
OPPORTUNITY_CARD_REFI_TRIGGERS = {
    'dscr_loan_type': frozenset({'DSCR Refinance'}),
    'bridge_loan_type': frozenset({'Refi Bridge', 'Refi-Rehab'}),
}


def _form_value(form_data, key):
    parent = form_data.get(key) if form_data else None
    if isinstance(parent, str):
        return parent.strip()
    return str(parent or '').strip()


def _is_refinance_submission(form_data):
    """True when the active loan path is a refinance subtype."""
    loan_type = _form_value(form_data, 'loan_type')
    if loan_type == 'DSCR':
        return _form_value(form_data, 'dscr_loan_type') == 'DSCR Refinance'
    if loan_type == 'BRIDGE':
        return _form_value(form_data, 'bridge_loan_type') in {'Refi Bridge', 'Refi-Rehab'}
    return False


def _is_bridge_refi_bridge_submission(form_data):
    return (
        _form_value(form_data, 'loan_type') == 'BRIDGE'
        and _form_value(form_data, 'bridge_loan_type') == 'Refi Bridge'
    )


def _is_bridge_refi_rehab_submission(form_data):
    return (
        _form_value(form_data, 'loan_type') == 'BRIDGE'
        and _form_value(form_data, 'bridge_loan_type') == 'Refi-Rehab'
    )


def _opportunity_card_field_visible(key, form_data):
    """True if field should be kept for this submission (mirrors step-3 form rules)."""
    if key in OPPORTUNITY_CARD_ALWAYS_FIELDS:
        return True

    loan_type = _form_value(form_data, 'loan_type')
    allowed = OPPORTUNITY_CARD_LOAN_TYPE_FIELDS.get(loan_type, frozenset())
    if key not in allowed:
        return False

    property_type = _form_value(form_data, 'property_type')
    bridge_type = _form_value(form_data, 'bridge_loan_type')

    if key == 'number_of_units':
        return property_type in {'Multifamily', '2-4 units', '5+ Units'}
    if key in {
        'units_residential',
        'units_commercial',
        'total_number_of_units',
        'residential_sqft_51_percent',
    }:
        return property_type == 'Mixed Use'
    if key == 'commercial_property_type':
        return property_type == 'Commercial'
    if key == 'please_specify':
        return (
            property_type == 'Commercial'
            and _form_value(form_data, 'commercial_property_type') == 'Others'
        )
    if key == 'units_leased':
        return _form_value(form_data, 'occupancy') == 'Partially Leased'
    if key == 'purchase_price':
        return loan_type in {'FIX & FLIP', 'GROUND-UP CONSTRUCTION'} or (
            loan_type == 'BRIDGE' and bridge_type == 'Purchase Bridge'
        )
    if key == 'rehab_budget' or key == 'arv':
        return loan_type == 'FIX & FLIP' or _is_bridge_refi_rehab_submission(form_data)
    if key == 'original_purchase_price':
        return _is_bridge_refi_rehab_submission(form_data)
    if key in OPPORTUNITY_CARD_BRIDGE_REFI_BRIDGE_FIELDS:
        return _is_bridge_refi_bridge_submission(form_data)
    if key in OPPORTUNITY_CARD_REFI_DATE_FIELDS:
        if loan_type == 'DSCR':
            return _form_value(form_data, 'dscr_loan_type') == 'DSCR Refinance'
        if loan_type == 'BRIDGE':
            return bridge_type == 'Refi-Rehab'
        return False
    return True


def _strip_hidden_opportunity_card_fields(form_data):
    """Drop values for fields that are hidden for the submitted answers (API-level skip)."""
    if not form_data:
        return form_data
    cleaned = dict(form_data)
    for key in list(cleaned.keys()):
        if not _opportunity_card_field_visible(key, cleaned):
            cleaned.pop(key, None)
    return cleaned


logger = logging.getLogger(__name__)

# Fallback GHL opportunity custom field IDs when no GHLCustomField row exists for the account
# (Prefer rows on GHLCustomField with field_name "Needs List Items" / "Needs List Url".)
GHL_OPPORTUNITY_NEEDS_LIST_ITEMS_FIELD_ID = "ncvtisK7OT5CJMxipP2N"
GHL_OPPORTUNITY_NEEDS_LIST_URL_FIELD_ID = "0FDWnJuZHsaSw8j0qxBv"


def _ghl_token_kwargs(request, json_body=None, doc_request=None):
    ctx = _resolve_ghl_context(request, json_body=json_body, doc_request=doc_request)
    if not ctx:
        return {}
    return {"access_token": ctx["access_token"]}


def _ghl_upload_kwargs(request, json_body=None, doc_request=None):
    ctx = _resolve_ghl_context(request, json_body=json_body, doc_request=doc_request)
    if not ctx:
        return {}
    # parent_id in context is GHLAuthCredentials.parent_id, else location_id (see ghl_credentials).
    kw = {"access_token": ctx["access_token"]}
    if ctx.get("parent_id"):
        kw["parent_id"] = ctx["parent_id"]
    return kw


def _ghl_media_delete_kwargs(request, json_body=None, doc_request=None):
    ctx = _resolve_ghl_context(request, json_body=json_body, doc_request=doc_request)
    if not ctx:
        return {}
    return {
        "access_token": ctx["access_token"],
        "alt_type": ctx["alt_type"],
        "alt_id": ctx["alt_id"],
    }


def _context_from_credentials(creds):
    if not creds or not (creds.access_token or "").strip():
        return None
    return {
        "access_token": creds.access_token.strip(),
        "parent_id": (creds.parent_id or "").strip() or (creds.location_id or "").strip() or None,
        "templates_parent_id": (creds.templates_parent_id or "").strip() or None,
        "alt_type": (creds.alt_type or "location").strip() or "location",
        "alt_id": (creds.alt_id or "").strip() or (creds.location_id or "").strip() or None,
        "credentials": creds,
    }


def _resolve_ghl_context(request, json_body=None, doc_request=None):
    ctx = get_ghl_api_context(extract_location_id(request, json_body))
    if ctx:
        return ctx
    if doc_request and getattr(doc_request, "ghl_account", None):
        return _context_from_credentials(doc_request.ghl_account)
    return None


def _link_doc_request_to_location(request, doc_request, json_body=None):
    location_id = extract_location_id(request, json_body)
    if not location_id or not doc_request:
        return
    from accounts.models import GHLAuthCredentials

    creds = GHLAuthCredentials.objects.filter(location_id=location_id).first()
    if creds and doc_request.ghl_account_id != creds.id:
        doc_request.ghl_account = creds
        doc_request.save(update_fields=["ghl_account"])


def _resolve_needs_list_opportunity_field_ids(ctx):
    """
    Resolve opportunity custom field IDs from GHLCustomField for this GHLAuthCredentials
    (matched by field_name). Falls back to module constants if rows are missing.
    """
    if not ctx or not ctx.get("credentials"):
        return GHL_OPPORTUNITY_NEEDS_LIST_ITEMS_FIELD_ID, GHL_OPPORTUNITY_NEEDS_LIST_URL_FIELD_ID

    from accounts.models import GHLCustomField

    creds = ctx["credentials"]
    items_cf = GHLCustomField.objects.filter(
        account=creds,
        field_name="Needs List Items",
        is_active=True,
    ).first()
    url_cf = GHLCustomField.objects.filter(
        account=creds,
        field_name="Needs List Url",
        is_active=True,
    ).first()
    return (
        (items_cf.ghl_field_id if items_cf else None) or GHL_OPPORTUNITY_NEEDS_LIST_ITEMS_FIELD_ID,
        (url_cf.ghl_field_id if url_cf else None) or GHL_OPPORTUNITY_NEEDS_LIST_URL_FIELD_ID,
    )


def _resolve_contact_custom_field_id(ctx, field_name):
    """
    Resolve a contact custom field GHL id from GHLCustomField for this account (by field_name).
    """
    if not ctx or not ctx.get("credentials") or not field_name:
        return None
    from accounts.models import GHLCustomField

    creds = ctx["credentials"]
    cf = GHLCustomField.objects.filter(
        account=creds,
        field_name=field_name,
        is_active=True,
    ).first()
    return cf.ghl_field_id if cf else None


def _resolve_opportunity_custom_field_id(ctx, field_name):
    """
    Resolve an opportunity custom field GHL id from GHLCustomField for this account (by field_name).
    """
    if not ctx or not ctx.get("credentials") or not field_name:
        return None
    from accounts.models import GHLCustomField

    creds = ctx["credentials"]
    cf = GHLCustomField.objects.filter(
        account=creds,
        field_name=field_name,
        is_active=True,
    ).first()
    return cf.ghl_field_id if cf else None


def _numbered_document_list(names):
    """Format document names as a numbered multiline list for GHL text fields."""
    return "\n".join(f"{i}. {name}" for i, name in enumerate(names, start=1))


def _build_uploaded_pending_document_names(doc_request):
    """
    Split admin-requested documents into uploaded vs still-pending for a request.
    A document name is uploaded if any selection with that name has at least one upload.
    """
    selections = AdminDocumentSelection.objects.filter(
        request=doc_request,
    ).select_related("document").prefetch_related("user_uploads")

    names_with_upload = set()
    for sel in selections:
        name = (sel.document.name or "").strip()
        if name and sel.user_uploads.exists():
            names_with_upload.add(name)

    uploaded_names = []
    pending_names = []
    uploaded_seen = set()
    pending_seen = set()

    for sel in selections:
        name = (sel.document.name or "").strip()
        if not name:
            continue
        if name in names_with_upload:
            if name not in uploaded_seen:
                uploaded_seen.add(name)
                uploaded_names.append(name)
        elif name not in pending_seen:
            pending_seen.add(name)
            pending_names.append(name)

    return uploaded_names, pending_names


def _sync_needs_list_upload_status_to_ghl(request, doc_request, json_body=None):
    """
    Update GHL opportunity fields "Needs List - Uploaded" and "Needs List - Pending"
    from current admin selections and user uploads. Failures are logged only.
    """
    try:
        from .ghl_service import update_opportunity_custom_fields

        ghl_ctx = _resolve_ghl_context(request, json_body=json_body, doc_request=doc_request)
        if not ghl_ctx:
            return

        uploaded_field_id = _resolve_opportunity_custom_field_id(
            ghl_ctx, "Needs List - Uploaded"
        )
        pending_field_id = _resolve_opportunity_custom_field_id(
            ghl_ctx, "Needs List - Pending"
        )
        if not uploaded_field_id and not pending_field_id:
            logger.warning(
                "Needs List Uploaded/Pending field IDs not configured for request %s",
                doc_request.request_id,
            )
            return

        uploaded_names, pending_names = _build_uploaded_pending_document_names(doc_request)

        custom_fields = []
        if uploaded_field_id:
            custom_fields.append(
                {
                    "id": uploaded_field_id,
                    "field_value": _numbered_document_list(uploaded_names)
                    if uploaded_names
                    else "",
                }
            )
        if pending_field_id:
            custom_fields.append(
                {
                    "id": pending_field_id,
                    "field_value": _numbered_document_list(pending_names)
                    if pending_names
                    else "",
                }
            )

        ghl_kw = {}
        if ghl_ctx.get("access_token"):
            ghl_kw["access_token"] = ghl_ctx["access_token"]
        update_opportunity_custom_fields(doc_request.request_id, custom_fields, **ghl_kw)
    except Exception as e:
        logger.warning(
            "Failed to sync Needs List Uploaded/Pending for request %s: %s",
            doc_request.request_id,
            e,
            exc_info=True,
        )


@csrf_exempt
def opportunity_card_form(request, request_id):
    """
    Opportunity Card – Loan Quote Survey Form with conditional fields.
    URL: {request_id}/opportunity-card/
    GET: show form (optionally pre-filled from existing submission).
    POST: save form data to OpportunityCardSubmission and show success.
    CSRF-exempt so the form works when embedded in an iframe on other origins (e.g. GoHighLevel).
    Submissions are still scoped by unique request_id.
    """
    if request.method == 'POST':
        form_data = {}
        for key in OPPORTUNITY_CARD_FIELD_NAMES:
            if key == 'sms_consent':
                continue
            value = request.POST.get(key)
            if value is not None:
                form_data[key] = value.strip() if isinstance(value, str) else value
        form_data['sms_consent'] = 'Yes' if request.POST.get('sms_consent') else 'No'
        # Skip values for fields hidden by loan/property conditionals (mirror template rules)
        form_data = _strip_hidden_opportunity_card_fields(form_data)
        submission, created = OpportunityCardSubmission.objects.update_or_create(
            request_id=request_id,
            defaults={'form_data': form_data}
        )
        location_id = extract_location_id(request) or ''
        # Create note on GHL contact only if we don't already have one (avoid duplicate notes on resubmit)
        if not submission.ghl_note_id:
            try:
                from .ghl_service import get_opportunity, create_contact_note
                ghl_kw = _ghl_token_kwargs(request)
                opp_data = get_opportunity(request_id, **ghl_kw)
                opportunity = opp_data.get("opportunity") or {}
                contact_id = opportunity.get("contactId")
                if not location_id:
                    location_id = opportunity.get("locationId") or ''
                if contact_id:
                    submitted_date = (submission.submitted_at or timezone.now()).strftime("%Y-%m-%d")
                    view_url = request.build_absolute_uri(
                        reverse("opportunity-submission-view", kwargs={"request_id": request_id})
                    )
                    note_body = f"Loan Quote Survey Form - {submitted_date} - {view_url}"
                    result = create_contact_note(contact_id, note_body, **ghl_kw)
                    note_id = (result.get("note") or {}).get("id") or result.get("id")
                    if note_id:
                        submission.ghl_note_id = note_id
                        submission.save(update_fields=["ghl_note_id"])
            except Exception as e:
                logger.warning("GHL note creation failed for opportunity %s: %s", request_id, e, exc_info=True)
        # Ensure contact custom fields exist + write survey values (Celery, inline fallback)
        try:
            from .tasks import enqueue_loan_quote_survey_ghl_sync
            enqueue_loan_quote_survey_ghl_sync(request_id, location_id or None)
        except Exception as e:
            logger.warning(
                "Loan Quote Survey GHL field sync failed for opportunity %s: %s",
                request_id,
                e,
                exc_info=True,
            )
        context = {
            'request_id': request_id,
            'success': True,
            'message': 'Loan Quote Survey submitted successfully.' if created else 'Loan Quote Survey updated successfully.',
            'initial': submission.form_data or {},
            'location_id': location_id or extract_location_id(request) or '',
        }
        return render(request, 'documents/opportunity_card_form.html', context)

    # GET: show form
    initial = {}
    try:
        existing = OpportunityCardSubmission.objects.get(request_id=request_id)
        initial = existing.form_data or {}
    except OpportunityCardSubmission.DoesNotExist:
        pass

    context = {
        'request_id': request_id,
        'initial': initial,
        'success': False,
        'location_id': extract_location_id(request) or '',
    }
    return render(request, 'documents/opportunity_card_form.html', context)


def _opportunity_submission_sections(form_data):
    """Build list of (section_title, [(label, value), ...]) for display/PDF. Skips empty values."""
    labels = OPPORTUNITY_CARD_FIELD_LABELS
    result = []
    for section_title, keys in OPPORTUNITY_CARD_SECTIONS:
        rows = []
        for key in keys:
            value = form_data.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            if isinstance(value, bool):
                value = "Yes" if value else "No"
            elif key == "interest_only" and value in ("Yes", "true", True):
                value = "Yes"
            label = labels.get(key, key.replace("_", " ").title())
            rows.append((label, str(value).strip()))
        if rows:
            result.append((section_title, rows))
    return result


@require_http_methods(["GET"])
def opportunity_submission_view(request, request_id):
    """
    Read-only view of an opportunity card submission.
    URL: {request_id}/opportunity-submission/
    Shows submitted form data (not editable) and a Download PDF button.
    """
    submission = get_object_or_404(OpportunityCardSubmission, request_id=request_id)
    form_data = submission.form_data or {}
    sections = _opportunity_submission_sections(form_data)
    context = {
        "request_id": request_id,
        "submission": submission,
        "form_data": form_data,
        "sections": sections,
    }
    return render(request, "documents/opportunity_submission_view.html", context)


@require_http_methods(["GET"])
def download_opportunity_submission_pdf(request, request_id):
    """
    Download a PDF of the opportunity card submission.
    URL: {request_id}/opportunity-submission/pdf/
    """
    submission = get_object_or_404(OpportunityCardSubmission, request_id=request_id)
    form_data = submission.form_data or {}
    sections = _opportunity_submission_sections(form_data)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        rightMargin=inch, leftMargin=inch, topMargin=inch, bottomMargin=inch
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "OpportunityTitle", parent=styles["Heading1"], fontSize=16, spaceAfter=12
    )
    heading_style = ParagraphStyle(
        "SectionHeading", parent=styles["Heading2"], fontSize=12, spaceAfter=8, spaceBefore=12
    )
    body_style = styles["Normal"]
    small_style = ParagraphStyle(
        "Small", parent=styles["Normal"], fontSize=9, spaceAfter=4, leftIndent=12
    )

    story = []
    story.append(Paragraph(f"Loan Quote Survey Form – {html.escape(request_id)}", title_style))
    story.append(Paragraph(f"Submitted: {submission.submitted_at.strftime('%Y-%m-%d %H:%M') if submission.submitted_at else '—'}", body_style))
    story.append(Spacer(1, 0.2 * inch))

    for section_title, rows in sections:
        story.append(Paragraph(html.escape(section_title), heading_style))
        for label, value in rows:
            story.append(Paragraph(f"<b>{html.escape(label)}:</b> {html.escape(value)}", small_style))
        story.append(Spacer(1, 0.1 * inch))
    doc.build(story)
    buffer.seek(0)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="opportunity-submission-{request_id}.pdf"'
    return response


def homepage(request, request_id):
    """
    Homepage view with 3 card options for admin
    """
    # Get or create the document request
    doc_request, created = DocumentRequest.objects.get_or_create(request_id=request_id)
    _link_doc_request_to_location(request, doc_request)
    
    context = {
        'request_id': request_id,
        'location_id': extract_location_id(request) or '',
    }
    return render(request, 'documents/homepage.html', context)


def library_admin_page(request, request_id):
    """
    Catalog admin UI: manage categories, documents, and needs-list print groups
    for the current GHL location so they appear on Individual Documents / Needs List.
    URL: {request_id}/request/admin/library/
    """
    doc_request, _ = DocumentRequest.objects.get_or_create(request_id=request_id)
    _link_doc_request_to_location(request, doc_request)
    location_id = extract_location_id(request) or ''
    if not location_id and doc_request.ghl_account_id:
        location_id = doc_request.ghl_account.location_id or ''
    context = {
        'request_id': request_id,
        'location_id': location_id,
    }
    return render(request, 'documents/library_admin.html', context)


def admin_user_uploads_view(request, request_id):
    """
    Admin view page to see all user uploads and accept/reject them.
    URL: {request_id}/request/admin/uploads/
    If no document list exists yet, shows a friendly "create the list first" page instead of 404.
    """
    try:
        doc_request = DocumentRequest.objects.get(request_id=request_id)
        _link_doc_request_to_location(request, doc_request)
    except DocumentRequest.DoesNotExist:
        return render(request, 'documents/request_not_found.html', {'request_id': request_id})
    adhoc_docs, individual_docs, needs_list_docs = _build_request_document_data(doc_request)
    context = {
        'request_id': request_id,
        'adhoc_documents': adhoc_docs,
        'individual_documents': individual_docs,
        'needs_list_documents': needs_list_docs,
    }
    return render(request, 'documents/admin_user_uploads.html', context)


def _serialize_user_upload(upload):
    """Shared upload dict for admin/user pages and APIs."""
    return {
        "id": upload.id,
        "file_url": upload.get_file_url(),
        "file_name": upload.get_file_name(),
        "uploaded_at": upload.uploaded_at,
        "accepted": upload.accepted,
        "accepted_at": upload.accepted_at,
        "rejection_reason": upload.rejection_reason or "",
        "rejected_at": upload.rejected_at,
        "is_rejected": bool(upload.rejected_at) and not upload.accepted,
    }


def _build_request_document_data(doc_request):
    """Build adhoc_docs, individual_docs, needs_list_docs for a document request (shared for PDF and pages)."""
    selections = AdminDocumentSelection.objects.filter(
        request=doc_request
    ).select_related('document', 'print_group', 'template').prefetch_related('user_uploads')

    adhoc_docs = []
    individual_docs = []
    needs_list_docs = {}

    for selection in selections:
        template = selection.template
        doc_data = {
            'selection_id': selection.id,
            'document_id': selection.document.id,
            'document_name': selection.document.name,
            'document_description': selection.document.description,
            'template_id': template.id if template else None,
            'template_name': template.name if template else None,
            'template_url': template.ghl_file_url if template else None,
            'template_file_name': (template.file_name or template.name) if template else None,
            'uploads': [
                _serialize_user_upload(upload)
                for upload in selection.user_uploads.all()
            ]
        }
        if selection.section_type == 'adhoc':
            adhoc_docs.append(doc_data)
        elif selection.section_type == 'individual':
            individual_docs.append(doc_data)
        elif selection.section_type == 'needs_list':
            print_group_name = selection.print_group.name if selection.print_group else 'Unknown'
            if print_group_name not in needs_list_docs:
                needs_list_docs[print_group_name] = []
            needs_list_docs[print_group_name].append(doc_data)

    return adhoc_docs, individual_docs, needs_list_docs


@require_http_methods(["GET"])
def download_request_pdf(request, request_id):
    """
    Download a PDF of the document request list: admin-requested documents with descriptions,
    and for each document any user uploads (file name + View link).
    URL: {request_id}/download-pdf/
    """
    try:
        doc_request = DocumentRequest.objects.get(request_id=request_id)
        _link_doc_request_to_location(request, doc_request)
    except DocumentRequest.DoesNotExist:
        raise Http404("Request not found")

    adhoc_docs, individual_docs, needs_list_docs = _build_request_document_data(doc_request)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        rightMargin=inch, leftMargin=inch, topMargin=inch, bottomMargin=inch
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle', parent=styles['Heading1'], fontSize=16, spaceAfter=12
    )
    heading_style = ParagraphStyle(
        'SectionHeading', parent=styles['Heading2'], fontSize=12, spaceAfter=8, spaceBefore=12
    )
    body_style = styles['Normal']
    small_style = ParagraphStyle(
        'Small', parent=styles['Normal'], fontSize=9, spaceAfter=4, leftIndent=12
    )
    link_style = ParagraphStyle(
        'Link', parent=styles['Normal'], fontSize=9, spaceAfter=4, leftIndent=24, textColor=colors.HexColor('#1565c0')
    )

    story = []
    story.append(Paragraph(f"Document Request – {html.escape(request_id)}", title_style))
    story.append(Spacer(1, 0.2 * inch))

    def add_doc_list(docs, section_title):
        if not docs:
            return
        story.append(Paragraph(html.escape(section_title), heading_style))
        for d in docs:
            doc_name = html.escape(d['document_name'] or 'Document')
            story.append(Paragraph(f"<b>{doc_name}</b>", body_style))
            desc = d.get('document_description') or ''
            desc_short = desc[:500] + ("..." if len(desc) > 500 else "")
            story.append(Paragraph(html.escape(desc_short), small_style))
            if d['uploads']:
                for u in d['uploads']:
                    name = html.escape(u.get('file_name') or 'Uploaded file')
                    story.append(Paragraph(f"• {name}", small_style))
                    url = u.get('file_url')
                    if url:
                        story.append(Paragraph(f'View: <a href="{html.escape(url)}">{html.escape(url)}</a>', link_style))
            else:
                story.append(Paragraph("No uploads yet", small_style))
            story.append(Spacer(1, 0.1 * inch))
        story.append(Spacer(1, 0.15 * inch))

    add_doc_list(adhoc_docs, "AD HOC Documents")
    add_doc_list(individual_docs, "Individual Documents")
    for pg_name, docs in sorted(needs_list_docs.items()):
        add_doc_list(docs, f"Needs List – {pg_name}")

    doc.build(story)
    buffer.seek(0)
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="document-request-{request_id}.pdf"'
    return response


def adhoc_page(request, request_id):
    """
    AD HOC - Request a custom document page
    """
    # Get or create the document request
    doc_request, created = DocumentRequest.objects.get_or_create(request_id=request_id)
    _link_doc_request_to_location(request, doc_request)
    account = resolve_account_for_request(request, doc_request=doc_request)
    
    # Load existing custom documents for adhoc section
    existing_selections = AdminDocumentSelection.objects.filter(
        request=doc_request,
        section_type='adhoc'
    ).select_related('document', 'document__category')
    
    # Get categories: global + custom for this request only
    # Do not hide empty categories — needed for "Create Custom Document" forms.
    categories = Category.objects.filter(
        Q(request__isnull=True) | Q(request=doc_request)
    ).order_by('name')
    
    import json as json_module
    context = {
        'request_id': request_id,
        'categories': categories,
        'existing_custom_documents': [
            {
                'id': sel.id,
                'document_id': sel.document.id,
                'name': sel.document.name,
                'description': sel.document.description,
                'category_id': sel.document.category.id,
                'category_name': sel.document.category.name,
            }
            for sel in existing_selections
        ],
    }
    return render(request, 'documents/adhoc.html', context)


def individual_documents_page(request, request_id):
    """
    Individual Documents - Request individual document(s) page
    """
    # Get or create the document request
    doc_request, created = DocumentRequest.objects.get_or_create(request_id=request_id)
    _link_doc_request_to_location(request, doc_request)
    account = resolve_account_for_request(request, doc_request=doc_request)
    
    # Load existing selections for individual section
    existing_selections = AdminDocumentSelection.objects.filter(
        request=doc_request,
        section_type='individual'
    ).select_related('document', 'document__category', 'template')
    
    selected_document_ids = [sel.document.id for sel in existing_selections]
    selected_templates = {
        str(sel.document.id): sel.template_id
        for sel in existing_selections
        if sel.template_id
    }
    
    # Get categories: global + custom for this request only
    # Do not hide empty categories — needed for "Create Custom Document" forms.
    categories = Category.objects.filter(
        Q(request__isnull=True) | Q(request=doc_request)
    ).order_by('name')
    
    import json as json_module
    context = {
        'request_id': request_id,
        'selected_document_ids': json_module.dumps(selected_document_ids),
        'selected_templates': json_module.dumps(selected_templates),
        'categories': categories,
        'existing_custom_documents': [
            {
                'id': sel.id,
                'document_id': sel.document.id,
                'name': sel.document.name,
                'description': sel.document.description,
                'category_id': sel.document.category.id,
                'category_name': sel.document.category.name,
                'template_id': sel.template_id,
                'template_name': sel.template.name if sel.template_id else None,
            }
            for sel in existing_selections
        ],
    }
    return render(request, 'documents/individual_documents.html', context)


def needs_list_page(request, request_id):
    """
    Needs List - Request needs list page
    """
    # Get or create the document request
    doc_request, created = DocumentRequest.objects.get_or_create(request_id=request_id)
    _link_doc_request_to_location(request, doc_request)
    account = resolve_account_for_request(request, doc_request=doc_request)
    
    # Load existing selections for needs_list section
    existing_selections = AdminDocumentSelection.objects.filter(
        request=doc_request,
        section_type='needs_list'
    ).select_related('document', 'print_group', 'document__category', 'template')
    
    # Group by print group (ids for checkbox restore) + template map
    selected_by_print_group = {}
    selected_templates_by_print_group = {}
    current_request_docs = []
    custom_print_groups = []
    for sel in existing_selections:
        pg_id = sel.print_group.id if sel.print_group else None
        pg_key = str(pg_id) if pg_id else 'null'
        if pg_key not in selected_by_print_group:
            selected_by_print_group[pg_key] = []
            selected_templates_by_print_group[pg_key] = {}
        selected_by_print_group[pg_key].append(sel.document.id)
        if sel.template_id:
            selected_templates_by_print_group[pg_key][str(sel.document.id)] = sel.template_id

        current_request_docs.append({
            'selection_id': sel.id,
            'document_id': sel.document.id,
            'name': sel.document.name,
            'description': sel.document.description or '',
            'category_name': sel.document.category.name if sel.document.category_id else '',
            'print_group_id': pg_id,
            'print_group_name': sel.print_group.name if sel.print_group else 'Needs List',
            'template_id': sel.template_id,
            'template_name': sel.template.name if sel.template_id else None,
        })
        
        # Collect custom print groups and their documents (only global or belonging to this request)
        if sel.print_group and (sel.print_group.request_id is None or sel.print_group.request_id == doc_request.id):
            pg_exists = any(pg['id'] == sel.print_group.id for pg in custom_print_groups)
            if not pg_exists:
                custom_print_groups.append({
                    'id': sel.print_group.id,
                    'name': sel.print_group.name,
                    'documents': []
                })
            # Add document to the print group
            for pg in custom_print_groups:
                if pg['id'] == sel.print_group.id:
                    pg['documents'].append({
                        'selection_id': sel.id,
                        'document_id': sel.document.id,
                        'name': sel.document.name,
                        'description': sel.document.description,
                        'category_id': sel.document.category.id,
                        'category_name': sel.document.category.name,
                        'template_name': sel.template.name if sel.template_id else None,
                    })
                    break
    
    # Get categories: global + custom for this request only
    # Do not hide empty categories — needed when adding custom docs to a needs list.
    categories = Category.objects.filter(
        Q(request__isnull=True) | Q(request=doc_request)
    ).order_by('name')
    all_print_groups = filter_print_groups_for_listing(
        PrintGroup.objects.select_related("request", "owner_account"),
        account=account,
        doc_request=doc_request,
    )
    
    import json as json_module
    context = {
        'request_id': request_id,
        'selected_by_print_group': json_module.dumps(selected_by_print_group),
        'selected_templates_by_print_group': json_module.dumps(selected_templates_by_print_group),
        'current_request_docs': current_request_docs,
        'categories': categories,
        'print_groups': all_print_groups,
        'custom_print_groups': custom_print_groups,
    }
    return render(request, 'documents/needs_list.html', context)


@csrf_exempt
@require_http_methods(["POST"])
def create_document(request):
    """
    API endpoint to create a new document
    POST /api/documents/create/
    Body: JSON with name, description, category_id, print_group_ids (optional)
    """
    try:
        data = json.loads(request.body)
        name = data.get('name')
        description = data.get('description')
        category_id = data.get('category_id')
        print_group_ids = data.get('print_group_ids', [])
        
        if not name or not description or not category_id:
            return JsonResponse({'error': 'Missing required fields: name, description, category_id'}, status=400)

        from accounts.models import GHLAuthCredentials

        location_id = data.get('location_id') or extract_location_id(request, data)
        owner_account = None
        if location_id:
            owner_account = GHLAuthCredentials.objects.filter(
                location_id=str(location_id).strip()
            ).first()
        
        try:
            category = Category.objects.get(id=category_id)
        except Category.DoesNotExist:
            return JsonResponse({'error': 'Category not found'}, status=404)
        
        document = Document.objects.create(
            name=name,
            description=description,
            category=category,
            owner_account=owner_account,
        )
        if owner_account is None:
            sync_master_document_to_all_accounts(document)

        acc_for_pg = owner_account or resolve_account_for_request(request, json_body=data)
        
        # Add print groups if provided
        if print_group_ids:
            print_groups = list(PrintGroup.objects.filter(id__in=print_group_ids))
            if len(print_groups) != len(print_group_ids):
                return JsonResponse({'error': 'One or more print groups not found'}, status=404)
            if acc_for_pg:
                for pg in print_groups:
                    if not print_group_visible_to_account(pg, acc_for_pg, None):
                        return JsonResponse(
                            {
                                'error': (
                                    f'Print group "{pg.name}" is not enabled for this subaccount '
                                    '(adjust the account print group library).'
                                )
                            },
                            status=403,
                        )
            document.print_groups.set(print_groups)
        
        return JsonResponse({
            'success': True,
            'document': {
                'id': document.id,
                'name': document.name,
                'description': document.description,
                'category': {
                    'id': document.category.id,
                    'name': document.category.name,
                }
            }
        }, status=201)
    
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def upload_document_file(request, document_id):
    """
    API endpoint to upload a file for a document
    POST /api/documents/<document_id>/upload/
    Body: multipart/form-data with 'file' field
    """
    try:
        document = Document.objects.get(id=document_id)
    except Document.DoesNotExist:
        return JsonResponse({'error': 'Document not found'}, status=404)
    
    if 'file' not in request.FILES:
        return JsonResponse({'error': 'No file provided'}, status=400)
    
    file = request.FILES['file']
    document.file = file
    document.save()
    
    return JsonResponse({
        'success': True,
        'file_url': document.file.url,
        'file_name': document.file.name.split('/')[-1]
    })


def admin_request_page(request, request_id):
    """
    Admin page for selecting documents for a specific request ID.
    URL: {request_id}/request/admin/
    """
    # Get or create the document request
    doc_request, created = DocumentRequest.objects.get_or_create(request_id=request_id)
    _link_doc_request_to_location(request, doc_request)
    
    context = {
        'request_id': request_id,
    }
    return render(request, 'documents/admin_request.html', context)


def user_upload_page(request, request_id):
    """
    User upload page for a specific request ID.
    URL: {request_id}/upload/
    If no document list exists yet, shows a friendly "create the list first" page instead of 404.
    """
    try:
        doc_request = DocumentRequest.objects.get(request_id=request_id)
        _link_doc_request_to_location(request, doc_request)
    except DocumentRequest.DoesNotExist:
        return render(request, 'documents/request_not_found.html', {'request_id': request_id, 'is_user_facing': True})
    adhoc_docs, individual_docs, needs_list_docs = _build_request_document_data(doc_request)
    context = {
        'request_id': request_id,
        'adhoc_documents': adhoc_docs,
        'individual_documents': individual_docs,
        'needs_list_documents': needs_list_docs,
    }
    return render(request, 'documents/user_upload.html', context)


@csrf_exempt
@require_http_methods(["POST"])
def create_adhoc_document(request, request_id):
    """
    API endpoint to create a custom AD HOC document.
    POST /api/{request_id}/admin/adhoc/create/
    Body: JSON with name, description, category_id
    """
    try:
        # Get or create the document request
        doc_request, created = DocumentRequest.objects.get_or_create(request_id=request_id)
        
        data = json.loads(request.body)
        _link_doc_request_to_location(request, doc_request, json_body=data)
        name = data.get('name')
        description = data.get('description')
        category_id = data.get('category_id')
        
        if not name or not description or not category_id:
            return JsonResponse({'error': 'Missing required fields: name, description, category_id'}, status=400)
        
        try:
            category = Category.objects.get(id=category_id)
        except Category.DoesNotExist:
            return JsonResponse({'error': 'Category not found'}, status=404)
        
        # Create the document (request-scoped so it appears only for this request)
        document = Document.objects.create(
            name=name,
            description=description,
            category=category,
            request=doc_request
        )
        
        # Create admin selection for this adhoc document
        selection = AdminDocumentSelection.objects.create(
            request=doc_request,
            section_type='adhoc',
            document=document
        )
        
        return JsonResponse({
            'success': True,
            'selection_id': selection.id,
            'document': {
                'id': document.id,
                'name': document.name,
                'description': document.description,
                'category': {
                    'id': document.category.id,
                    'name': document.category.name,
                }
            }
        }, status=201)
    
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def create_individual_document(request, request_id):
    """
    API endpoint to create a custom Individual document.
    POST /api/{request_id}/admin/individual/create/
    Body: JSON with name, description, category_id
    """
    try:
        # Get or create the document request
        doc_request, created = DocumentRequest.objects.get_or_create(request_id=request_id)
        
        data = json.loads(request.body)
        _link_doc_request_to_location(request, doc_request, json_body=data)
        name = data.get('name')
        description = data.get('description')
        category_id = data.get('category_id')
        
        if not name or not description or not category_id:
            return JsonResponse({'error': 'Missing required fields: name, description, category_id'}, status=400)
        
        try:
            category = Category.objects.get(id=category_id)
        except Category.DoesNotExist:
            return JsonResponse({'error': 'Category not found'}, status=404)
        
        # Create the document (request-scoped so it appears only for this request)
        document = Document.objects.create(
            name=name,
            description=description,
            category=category,
            request=doc_request
        )
        
        # Create admin selection for this individual document
        selection = AdminDocumentSelection.objects.create(
            request=doc_request,
            section_type='individual',
            document=document
        )
        
        return JsonResponse({
            'success': True,
            'selection_id': selection.id,
            'document': {
                'id': document.id,
                'name': document.name,
                'description': document.description,
                'category': {
                    'id': document.category.id,
                    'name': document.category.name,
                }
            }
        }, status=201)
    
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def create_needs_list_print_group(request, request_id):
    """
    API endpoint to create a custom print group (parent) for needs list.
    POST /api/{request_id}/admin/needs-list/print-group/create/
    Body: JSON with name, description (optional)
    """
    try:
        # Get or create the document request
        doc_request, created = DocumentRequest.objects.get_or_create(request_id=request_id)
        
        data = json.loads(request.body)
        _link_doc_request_to_location(request, doc_request, json_body=data)
        name = data.get('name')
        description = data.get('description', '')
        
        if not name:
            return JsonResponse({'error': 'Missing required field: name'}, status=400)
        
        # Create the print group (request-scoped so it appears only for this request)
        print_group = PrintGroup.objects.create(
            name=name,
            description=description or f'Print group: {name}',
            request=doc_request
        )
        
        return JsonResponse({
            'success': True,
            'print_group': {
                'id': print_group.id,
                'name': print_group.name,
                'description': print_group.description,
            }
        }, status=201)
    
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def create_needs_list_document(request, request_id):
    """
    API endpoint to create a custom document (child) for a print group in needs list.
    POST /api/{request_id}/admin/needs-list/document/create/
    Body: JSON with name, description, category_id, print_group_id
    """
    try:
        # Get or create the document request
        doc_request, created = DocumentRequest.objects.get_or_create(request_id=request_id)
        
        data = json.loads(request.body)
        _link_doc_request_to_location(request, doc_request, json_body=data)
        name = data.get('name')
        description = data.get('description')
        category_id = data.get('category_id')
        print_group_id = data.get('print_group_id')
        
        if not name or not description or not category_id or not print_group_id:
            return JsonResponse({'error': 'Missing required fields: name, description, category_id, print_group_id'}, status=400)
        
        try:
            category = Category.objects.get(id=category_id)
        except Category.DoesNotExist:
            return JsonResponse({'error': 'Category not found'}, status=404)
        
        try:
            print_group = PrintGroup.objects.get(id=print_group_id)
        except PrintGroup.DoesNotExist:
            return JsonResponse({'error': 'Print group not found'}, status=404)

        account = resolve_account_for_request(request, json_body=data, doc_request=doc_request)
        if account and not print_group_visible_to_account(print_group, account, doc_request):
            return JsonResponse(
                {'error': 'Print group is not enabled for this subaccount.'},
                status=403,
            )
        
        # Create the document (request-scoped so it appears only for this request)
        document = Document.objects.create(
            name=name,
            description=description,
            category=category,
            request=doc_request
        )
        
        # Add document to print group
        document.print_groups.add(print_group)

        # Do not create AdminDocumentSelection here — Send Request must attach
        # a template from the existing catalog before the document is saved.
        
        return JsonResponse({
            'success': True,
            'document': {
                'id': document.id,
                'name': document.name,
                'description': document.description,
                'category': {
                    'id': document.category.id,
                    'name': document.category.name,
                },
                'print_group': {
                    'id': print_group.id,
                    'name': print_group.name,
                }
            }
        }, status=201)
    
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["DELETE"])
def delete_adhoc_document(request, request_id, selection_id):
    """
    API endpoint to delete an AD HOC custom document.
    DELETE /api/{request_id}/admin/adhoc/{selection_id}/delete/
    """
    try:
        # Verify request exists
        doc_request = DocumentRequest.objects.get(request_id=request_id)
        _link_doc_request_to_location(request, doc_request)
        
        # Get the admin selection
        selection = get_object_or_404(
            AdminDocumentSelection,
            id=selection_id,
            request=doc_request,
            section_type='adhoc'
        )
        
        document = selection.document
        selection_id_val = selection.id
        
        # Delete the selection
        selection.delete()
        
        # If this was a request-scoped custom document, delete the document so it no longer appears for this request
        if document.request_id:
            document.delete()
        
        return JsonResponse({
            'success': True,
            'message': 'Custom document deleted successfully',
            'selection_id': selection_id_val
        })
    
    except DocumentRequest.DoesNotExist:
        return JsonResponse({'error': 'Request not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def save_admin_selections(request, request_id):
    """
    API endpoint to save admin document selections.
    POST /api/{request_id}/admin/selections/
    Body: JSON with section_type and document_ids (and print_group_id for needs_list)
    """
    try:
        # Get or create the document request
        doc_request, created = DocumentRequest.objects.get_or_create(request_id=request_id)
        
        data = json.loads(request.body)
        _link_doc_request_to_location(request, doc_request, json_body=data)
        ghl_ctx = _resolve_ghl_context(request, json_body=data, doc_request=doc_request)
        ghl_kw = {"access_token": ghl_ctx["access_token"]} if ghl_ctx else {}
        items_field_id, url_field_id = _resolve_needs_list_opportunity_field_ids(ghl_ctx)
        section_type = data.get('section_type')
        raw_document_ids = data.get('document_ids', [])
        print_group_id = data.get('print_group_id', None)
        # Map of document_id (str or int) -> template_id (needs_list + individual)
        templates_map = data.get('templates') or data.get('document_templates') or {}

        document_ids = []
        for raw_doc_id in raw_document_ids:
            try:
                document_ids.append(int(raw_doc_id))
            except (TypeError, ValueError):
                return JsonResponse({'error': 'Invalid document_ids'}, status=400)
        
        if not section_type or section_type not in ['adhoc', 'individual', 'needs_list']:
            return JsonResponse({'error': 'Invalid section_type. Must be: adhoc, individual, or needs_list'}, status=400)
        
        if not document_ids:
            return JsonResponse({'error': 'document_ids is required'}, status=400)
        
        if section_type == 'needs_list' and not print_group_id:
            return JsonResponse({'error': 'print_group_id is required for needs_list section'}, status=400)

        account = resolve_account_for_request(request, json_body=data, doc_request=doc_request)

        # Needs list + individual: every selected document must have a template
        template_by_doc_id = {}
        requires_templates = section_type in ('needs_list', 'individual')
        if requires_templates:
            missing_templates = []
            for raw_doc_id in document_ids:
                key = str(raw_doc_id)
                tid = templates_map.get(key)
                if tid is None:
                    tid = templates_map.get(raw_doc_id)
                if tid is None or tid == "" or tid == 0:
                    missing_templates.append(raw_doc_id)
                    continue
                try:
                    tid_int = int(tid)
                except (TypeError, ValueError):
                    missing_templates.append(raw_doc_id)
                    continue
                template_by_doc_id[int(raw_doc_id)] = tid_int

            if missing_templates:
                section_label = 'Needs List' if section_type == 'needs_list' else 'Individual Documents'
                return JsonResponse(
                    {
                        'error': (
                            f'Please choose a template file for every selected document '
                            f'before sending the {section_label} request.'
                        ),
                        'missing_template_document_ids': missing_templates,
                    },
                    status=400,
                )

            if account:
                template_ids = set(template_by_doc_id.values())
                found = {
                    t.id: t
                    for t in NeedsListTemplate.objects.filter(
                        account=account, id__in=template_ids
                    )
                }
                if len(found) != len(template_ids):
                    return JsonResponse(
                        {'error': 'One or more selected templates were not found for this location.'},
                        status=400,
                    )
                template_by_doc_id = {
                    doc_id: found[tid] for doc_id, tid in template_by_doc_id.items()
                }
            else:
                # No account resolved — still load templates by id
                template_ids = set(template_by_doc_id.values())
                found = {
                    t.id: t
                    for t in NeedsListTemplate.objects.filter(id__in=template_ids)
                }
                if len(found) != len(template_ids):
                    return JsonResponse(
                        {'error': 'One or more selected templates were not found.'},
                        status=400,
                    )
                template_by_doc_id = {
                    doc_id: found[tid] for doc_id, tid in template_by_doc_id.items()
                }

        print_group = None
        if print_group_id:
            try:
                print_group = PrintGroup.objects.get(id=print_group_id)
            except PrintGroup.DoesNotExist:
                return JsonResponse({'error': 'Print group not found'}, status=404)
            if section_type == 'needs_list' and account:
                if not print_group_visible_to_account(print_group, account, doc_request):
                    return JsonResponse(
                        {'error': 'Print group is not enabled for this subaccount.'},
                        status=403,
                    )
        
        # Validate documents exist
        documents = Document.objects.filter(id__in=document_ids)
        if documents.count() != len(document_ids):
            return JsonResponse({'error': 'One or more documents not found'}, status=404)

        if account:
            for doc in documents:
                if not document_visible_to_account(doc, account, doc_request):
                    return JsonResponse(
                        {
                            'error': (
                                f'Document "{doc.name}" is not enabled for this '
                                'subaccount (add it in the account document library).'
                            )
                        },
                        status=403,
                    )
        
        # Preserve existing selection rows when possible so previously uploaded files
        # stay linked. Only delete rows that are no longer selected.
        selections = []
        document_by_id = {doc.id: doc for doc in documents}
        target_ids = set(document_ids)
        with transaction.atomic():
            existing_qs = AdminDocumentSelection.objects.filter(
                request=doc_request,
                section_type=section_type,
            )
            # Needs-list selections are scoped per print_group — only update this group.
            # Other need lists already sent for this opportunity stay intact.
            if section_type == "needs_list":
                existing_qs = existing_qs.filter(print_group=print_group)

            existing_by_doc_id = {sel.document_id: sel for sel in existing_qs}

            # Remove deselected documents for this scope.
            existing_qs.exclude(document_id__in=target_ids).delete()

            # Reuse existing row if present; otherwise create.
            for doc_id in document_ids:
                document = document_by_id.get(doc_id)
                if not document:
                    continue
                selection = existing_by_doc_id.get(doc_id)
                template_obj = (
                    template_by_doc_id.get(int(doc_id)) if requires_templates else None
                )
                if not selection:
                    selection = AdminDocumentSelection.objects.create(
                        request=doc_request,
                        section_type=section_type,
                        document=document,
                        print_group=print_group,
                        template=template_obj,
                    )
                elif requires_templates:
                    if selection.template_id != (template_obj.id if template_obj else None):
                        selection.template = template_obj
                        selection.save(update_fields=["template"])
                selections.append({
                    'id': selection.id,
                    'document_id': document.id,
                    'document_name': document.name,
                    'template_id': selection.template_id,
                })

        # After saving, update the configured GHL opportunity custom fields with:
        # 1) a numbered list of all selected document names for this request
        # 2) the upload link URL we send to the user (based on request_id)
        # (individual + needs list). Failures here should not block the API.
        try:
            from .ghl_service import (
                get_opportunity,
                create_contact_note,
                update_contact_custom_field,
                update_contact_note,
                update_opportunity_custom_fields,
            )

            # Collect all selected document names for this request
            all_selections = AdminDocumentSelection.objects.filter(
                request=doc_request,
                section_type__in=['individual', 'needs_list'],
            ).select_related('document').order_by('created_at')

            names = []
            seen = set()
            for sel in all_selections:
                name = (sel.document.name or "").strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                names.append(name)

            custom_fields = []
            upload_url = f"https://survey.flipfunding.com/{request_id}/upload/"

            # Needs List Items – only if we have at least one name
            if names and items_field_id:
                doc_list_value = "\n".join(
                    [f"{i}. {name}" for i, name in enumerate(names, start=1)]
                )
                custom_fields.append(
                    {
                        "id": items_field_id,
                        "field_value": doc_list_value,
                    }
                )

            # Needs List Url – always send if we have a field ID configured
            if url_field_id:
                custom_fields.append(
                    {
                        "id": url_field_id,
                        "field_value": upload_url,
                    }
                )

            if custom_fields:
                # request_id here is the GHL opportunity ID in your URLs
                update_opportunity_custom_fields(request_id, custom_fields, **ghl_kw)

            # Create or update GHL contact note with the same needs list data
            # (document list + upload link). Use saved note ID to update on subsequent changes.
            note_parts = []
            if names:
                doc_list_value = "\n".join(
                    [f"{i}. {name}" for i, name in enumerate(names, start=1)]
                )
                # note_parts.append("Needs List\n\n" + doc_list_value)
                note_parts.append(doc_list_value)
            note_parts.append("Upload link: " + upload_url)
            note_body = "\n\n".join(note_parts)

            contact_id = None
            try:
                opp_data = get_opportunity(request_id, **ghl_kw)
                opportunity = opp_data.get("opportunity") or {}
                contact_id = opportunity.get("contactId")
            except Exception as opp_err:
                logger.warning(
                    "Failed to fetch GHL opportunity for contact sync (request %s): %s",
                    request_id,
                    opp_err,
                    exc_info=True,
                )

            if contact_id:
                try:
                    if doc_request.ghl_needs_list_note_id:
                        update_contact_note(
                            contact_id,
                            doc_request.ghl_needs_list_note_id,
                            note_body,
                            **ghl_kw,
                        )
                    else:
                        result = create_contact_note(contact_id, note_body, **ghl_kw)
                        note_id = (result.get("note") or {}).get("id") or result.get("id")
                        if note_id:
                            doc_request.ghl_needs_list_note_id = note_id
                            doc_request.save(update_fields=["ghl_needs_list_note_id"])
                except Exception as note_err:
                    logger.warning(
                        "Failed to create/update GHL needs list note for request %s: %s",
                        request_id,
                        note_err,
                        exc_info=True,
                    )

                send_needs_field_id = _resolve_contact_custom_field_id(
                    ghl_ctx, "Send Needs List"
                )
                if send_needs_field_id:
                    try:
                        update_contact_custom_field(
                            contact_id,
                            send_needs_field_id,
                            note_body,
                            **ghl_kw,
                        )
                    except Exception as cf_err:
                        logger.warning(
                            "Failed to update GHL contact custom field Send Needs List for request %s: %s",
                            request_id,
                            cf_err,
                            exc_info=True,
                        )
        except Exception as e:
            logger.warning(
                "Failed to update GHL custom field for request %s: %s",
                request_id,
                e,
                exc_info=True,
            )

        _sync_needs_list_upload_status_to_ghl(request, doc_request, json_body=data)

        return JsonResponse({
            'success': True,
            'selections': selections,
            'count': len(selections)
        }, status=201)
    
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def upload_user_file(request, request_id, selection_id):
    """
    API endpoint to upload a file for a user document.
    POST /api/{request_id}/upload/{selection_id}/
    Body: multipart/form-data with 'file' field
    Uploads file to GHL (GoHighLevel) and stores url/fileId in model (no server storage).
    """
    try:
        from .ghl_service import upload_file as ghl_upload_file

        doc_request = DocumentRequest.objects.get(request_id=request_id)
        _link_doc_request_to_location(request, doc_request)
        selection = get_object_or_404(AdminDocumentSelection, id=selection_id, request=doc_request)

        if 'file' not in request.FILES:
            return JsonResponse({'error': 'No file provided'}, status=400)

        file = request.FILES['file']
        name = file.name or selection.document.name or 'document'

        # Upload to GHL; do not save file on server
        result = ghl_upload_file(file, name=name, **_ghl_upload_kwargs(request, doc_request=doc_request))

        upload = UserDocumentUpload.objects.create(
            admin_selection=selection,
            file=None,
            ghl_file_id=result.get('fileId'),
            ghl_file_url=result.get('url'),
            file_name=file.name,
        )

        _sync_needs_list_upload_status_to_ghl(request, doc_request)

        return JsonResponse({
            'success': True,
            'upload_id': upload.id,
            'file_url': upload.get_file_url(),
            'file_name': upload.get_file_name(),
            'uploaded_at': upload.uploaded_at.isoformat(),
        }, status=201)

    except DocumentRequest.DoesNotExist:
        return JsonResponse({'error': 'Request not found'}, status=404)
    except Exception as e:
        try:
            import requests
            if isinstance(e, requests.HTTPError) and e.response is not None:
                return JsonResponse({'error': f'GHL upload failed: {e.response.text[:500]}'}, status=502)
        except Exception:
            pass
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["DELETE"])
def delete_user_upload(request, request_id, upload_id):
    """
    API endpoint to delete a user upload.
    DELETE /api/{request_id}/upload/{upload_id}/delete/
    Deletes from GHL if ghl_file_id is set, then removes our record.
    Prevents deletion if the document has been accepted by admin.
    """
    try:
        from django.conf import settings
        from .ghl_service import delete_media as ghl_delete_media

        doc_request = DocumentRequest.objects.get(request_id=request_id)
        _link_doc_request_to_location(request, doc_request)
        upload = get_object_or_404(
            UserDocumentUpload,
            id=upload_id,
            admin_selection__request=doc_request
        )

        if upload.accepted:
            return JsonResponse({
                'error': 'Cannot delete an accepted document',
                'accepted': True
            }, status=403)

        if upload.ghl_file_id:
            del_kw = _ghl_media_delete_kwargs(request, doc_request=doc_request)
            if del_kw.get("alt_id"):
                try:
                    ghl_delete_media(upload.ghl_file_id, **del_kw)
                except Exception:
                    pass  # still delete our record if GHL delete fails
            else:
                alt_type = getattr(settings, 'GHL_ALT_TYPE', 'location')
                alt_id = getattr(settings, 'GHL_ALT_ID', '') or None
                if alt_id:
                    try:
                        ghl_delete_media(upload.ghl_file_id, alt_type=alt_type, alt_id=alt_id)
                    except Exception:
                        pass  # still delete our record if GHL delete fails

        deleted_id = upload.id
        upload.delete()

        _sync_needs_list_upload_status_to_ghl(request, doc_request)

        return JsonResponse({
            'success': True,
            'message': 'Upload deleted successfully',
            'upload_id': deleted_id
        })

    except DocumentRequest.DoesNotExist:
        return JsonResponse({'error': 'Request not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def accept_user_upload(request, request_id, upload_id):
    """
    API endpoint for admin to accept or reject a user upload.
    POST /api/{request_id}/admin/upload/{upload_id}/accept/
    Body: JSON with 'accepted' (boolean) and optional 'rejection_reason' (required when rejecting).
    """
    try:
        # Verify request exists
        doc_request = DocumentRequest.objects.get(request_id=request_id)
        _link_doc_request_to_location(request, doc_request)
        
        # Get the upload
        upload = get_object_or_404(
            UserDocumentUpload,
            id=upload_id,
            admin_selection__request=doc_request
        )
        
        data = json.loads(request.body)
        accepted = bool(data.get('accepted', False))
        reason = (data.get('rejection_reason') or '').strip()

        from django.utils import timezone
        now = timezone.now()

        if accepted:
            # Accept clears rejection feedback permanently
            upload.accepted = True
            upload.accepted_at = now
            upload.rejection_reason = ""
            upload.rejected_at = None
            upload.save(
                update_fields=[
                    "accepted",
                    "accepted_at",
                    "rejection_reason",
                    "rejected_at",
                    "updated_at",
                ]
            )
        else:
            if not reason:
                return JsonResponse(
                    {'error': 'Rejection reason is required'},
                    status=400,
                )
            upload.accepted = False
            upload.accepted_at = None
            upload.rejection_reason = reason
            upload.rejected_at = now
            upload.save(
                update_fields=[
                    "accepted",
                    "accepted_at",
                    "rejection_reason",
                    "rejected_at",
                    "updated_at",
                ]
            )
        
        return JsonResponse({
            'success': True,
            'message': f'Document {"accepted" if accepted else "rejected"} successfully',
            'upload_id': upload.id,
            'accepted': upload.accepted,
            'accepted_at': upload.accepted_at.isoformat() if upload.accepted_at else None,
            'rejection_reason': upload.rejection_reason or "",
            'rejected_at': upload.rejected_at.isoformat() if upload.rejected_at else None,
            'is_rejected': bool(upload.rejected_at) and not upload.accepted,
        })
    
    except DocumentRequest.DoesNotExist:
        return JsonResponse({'error': 'Request not found'}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["PUT", "PATCH", "DELETE"])
def user_upload_rejection_reason(request, request_id, upload_id):
    """
    Update or clear admin rejection reason on a user upload.
    PUT/PATCH /api/{request_id}/admin/upload/{upload_id}/rejection-reason/
      Body: { "rejection_reason": "..." }  (non-empty required for update)
    DELETE — clear the comment (and rejected status → pending again).
    """
    try:
        doc_request = DocumentRequest.objects.get(request_id=request_id)
        _link_doc_request_to_location(request, doc_request)
        upload = get_object_or_404(
            UserDocumentUpload,
            id=upload_id,
            admin_selection__request=doc_request,
        )

        from django.utils import timezone

        if request.method == "DELETE":
            upload.rejection_reason = ""
            upload.rejected_at = None
            if not upload.accepted:
                upload.accepted = False
                upload.accepted_at = None
            upload.save(
                update_fields=[
                    "rejection_reason",
                    "rejected_at",
                    "accepted",
                    "accepted_at",
                    "updated_at",
                ]
            )
            return JsonResponse({
                "success": True,
                "message": "Rejection comment removed",
                "upload_id": upload.id,
                "rejection_reason": "",
                "rejected_at": None,
                "accepted": upload.accepted,
                "is_rejected": False,
            })

        data = json.loads(request.body) if request.body else {}
        reason = (data.get("rejection_reason") or "").strip()
        if not reason:
            return JsonResponse({"error": "Rejection reason is required"}, status=400)

        upload.rejection_reason = reason
        upload.accepted = False
        upload.accepted_at = None
        if not upload.rejected_at:
            upload.rejected_at = timezone.now()
        upload.save(
            update_fields=[
                "rejection_reason",
                "rejected_at",
                "accepted",
                "accepted_at",
                "updated_at",
            ]
        )
        return JsonResponse({
            "success": True,
            "message": "Rejection reason updated",
            "upload_id": upload.id,
            "rejection_reason": upload.rejection_reason,
            "rejected_at": upload.rejected_at.isoformat() if upload.rejected_at else None,
            "accepted": False,
            "is_rejected": True,
        })
    except DocumentRequest.DoesNotExist:
        return JsonResponse({"error": "Request not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def account_document_library(request):
    """
    Per-subaccount document library (catalog masters + account-owned templates).

    GET /api/account/document-library/?location_id=
        Returns { document_ids: [...] } for documents available when picking lists
        (same rule as GET /api/documents/ with location_id and no request_id).

    POST /api/account/document-library/
        JSON: location_id, action, document_id
        - action "add": enable a shared catalog document for this subaccount.
        - action "remove": disable a shared catalog document (does not delete the master row).
        - action "delete_owned": permanently delete an account-only template
          (Document.owner_account must match location_id).
    """
    from accounts.models import GHLAuthCredentials

    if request.method == "GET":
        location_id = extract_location_id(request, None)
        if not location_id:
            return JsonResponse({"error": "location_id is required"}, status=400)
        account = GHLAuthCredentials.objects.filter(
            location_id=str(location_id).strip()
        ).first()
        if not account:
            return JsonResponse({"error": "Unknown location_id"}, status=404)
        ids = list(
            filter_documents_for_listing(
                Document.objects.all(), account=account, doc_request=None
            ).values_list("id", flat=True)
        )
        return JsonResponse({"document_ids": ids})

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    location_id = data.get("location_id") or extract_location_id(request, data)
    if not location_id:
        return JsonResponse({"error": "location_id is required"}, status=400)
    account = GHLAuthCredentials.objects.filter(
        location_id=str(location_id).strip()
    ).first()
    if not account:
        return JsonResponse({"error": "Unknown location_id"}, status=404)

    action = data.get("action")
    document_id = data.get("document_id")
    if not document_id or action not in ("add", "remove", "delete_owned"):
        return JsonResponse(
            {
                "error": (
                    "document_id and action are required; "
                    "action must be 'add', 'remove', or 'delete_owned'"
                )
            },
            status=400,
        )

    try:
        doc = Document.objects.get(id=int(document_id))
    except (Document.DoesNotExist, ValueError, TypeError):
        return JsonResponse({"error": "Document not found"}, status=404)

    if action == "add":
        if doc.request_id or doc.owner_account_id:
            return JsonResponse(
                {
                    "error": (
                        "Only shared catalog documents can be added via the library "
                        "(request and owner_account must be empty)"
                    )
                },
                status=400,
            )
        AccountDocumentLibrary.objects.get_or_create(account=account, document=doc)
        return JsonResponse({"success": True, "document_id": doc.id})

    if action == "remove":
        if doc.request_id or doc.owner_account_id:
            return JsonResponse(
                {
                    "error": (
                        "Only shared catalog documents can be removed from the library this way"
                    )
                },
                status=400,
            )
        AccountDocumentLibrary.objects.filter(account=account, document=doc).delete()
        return JsonResponse({"success": True, "document_id": doc.id})

    if doc.owner_account_id != account.id:
        return JsonResponse(
            {"error": "Not an account-owned template for this location"},
            status=403,
        )
    if doc.request_id:
        return JsonResponse(
            {"error": "Request-scoped documents cannot be deleted here"},
            status=400,
        )
    doc.delete()
    return JsonResponse({"success": True, "document_id": int(document_id)})


@csrf_exempt
@require_http_methods(["GET", "POST"])
def account_print_group_library(request):
    """
    Per-subaccount print group library (catalog masters + account-owned groups).

    GET /api/account/print-group-library/?location_id=
        Returns { print_group_ids: [...] }.

    POST JSON: location_id, action, print_group_id
        - action "add": enable a shared catalog print group for this subaccount.
        - action "remove": disable it (does not delete the master row).
        - action "delete_owned": delete a PrintGroup with owner_account matching location_id.
    """
    from accounts.models import GHLAuthCredentials

    if request.method == "GET":
        location_id = extract_location_id(request, None)
        if not location_id:
            return JsonResponse({"error": "location_id is required"}, status=400)
        account = GHLAuthCredentials.objects.filter(
            location_id=str(location_id).strip()
        ).first()
        if not account:
            return JsonResponse({"error": "Unknown location_id"}, status=404)
        ids = list(
            filter_print_groups_for_listing(
                PrintGroup.objects.all(), account=account, doc_request=None
            ).values_list("id", flat=True)
        )
        return JsonResponse({"print_group_ids": ids})

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    location_id = data.get("location_id") or extract_location_id(request, data)
    if not location_id:
        return JsonResponse({"error": "location_id is required"}, status=400)
    account = GHLAuthCredentials.objects.filter(
        location_id=str(location_id).strip()
    ).first()
    if not account:
        return JsonResponse({"error": "Unknown location_id"}, status=404)

    action = data.get("action")
    print_group_id = data.get("print_group_id")
    if not print_group_id or action not in ("add", "remove", "delete_owned"):
        return JsonResponse(
            {
                "error": (
                    "print_group_id and action are required; "
                    "action must be 'add', 'remove', or 'delete_owned'"
                )
            },
            status=400,
        )

    try:
        pg = PrintGroup.objects.get(id=int(print_group_id))
    except (PrintGroup.DoesNotExist, ValueError, TypeError):
        return JsonResponse({"error": "Print group not found"}, status=404)

    if action == "add":
        if pg.request_id or pg.owner_account_id:
            return JsonResponse(
                {
                    "error": (
                        "Only shared catalog print groups can be added "
                        "(request and owner_account must be empty)"
                    )
                },
                status=400,
            )
        AccountPrintGroupLibrary.objects.get_or_create(account=account, print_group=pg)
        return JsonResponse({"success": True, "print_group_id": pg.id})

    if action == "remove":
        if pg.request_id or pg.owner_account_id:
            return JsonResponse(
                {
                    "error": (
                        "Only shared catalog print groups can be removed from the library this way"
                    )
                },
                status=400,
            )
        AccountPrintGroupLibrary.objects.filter(account=account, print_group=pg).delete()
        return JsonResponse({"success": True, "print_group_id": pg.id})

    if pg.owner_account_id != account.id:
        return JsonResponse(
            {"error": "Not an account-owned print group for this location"},
            status=403,
        )
    if pg.request_id:
        return JsonResponse(
            {"error": "Request-scoped print groups cannot be deleted here"},
            status=400,
        )
    pg.delete()
    return JsonResponse({"success": True, "print_group_id": int(print_group_id)})


@csrf_exempt
@require_http_methods(["POST"])
def create_account_print_group(request):
    """
    Create a print group owned by a GHL subaccount (for Needs List catalog).
    POST /api/account/print-groups/create/
    Body: JSON location_id, name, description (optional)
    """
    from accounts.models import GHLAuthCredentials

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    location_id = data.get("location_id") or extract_location_id(request, data)
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()

    if not location_id:
        return JsonResponse({"error": "location_id is required"}, status=400)
    if not name:
        return JsonResponse({"error": "name is required"}, status=400)

    account = GHLAuthCredentials.objects.filter(
        location_id=str(location_id).strip()
    ).first()
    if not account:
        return JsonResponse({"error": "Unknown location_id"}, status=404)

    if PrintGroup.objects.filter(
        name=name, owner_account=account, request__isnull=True
    ).exists():
        return JsonResponse(
            {"error": "A print group with this name already exists for this location"},
            status=400,
        )

    print_group = PrintGroup.objects.create(
        name=name,
        description=description or f"Print group: {name}",
        owner_account=account,
        request=None,
    )
    return JsonResponse(
        {
            "success": True,
            "print_group": {
                "id": print_group.id,
                "name": print_group.name,
                "description": print_group.description,
            },
        },
        status=201,
    )


def _serialize_needs_list_template(tpl):
    return {
        "id": tpl.id,
        "name": tpl.name,
        "file_name": tpl.file_name or "",
        "ghl_file_id": tpl.ghl_file_id,
        "ghl_file_url": tpl.ghl_file_url,
        "created_at": tpl.created_at.isoformat() if tpl.created_at else None,
        "updated_at": tpl.updated_at.isoformat() if tpl.updated_at else None,
    }


@csrf_exempt
@require_http_methods(["GET", "POST"])
def needs_list_templates(request):
    """
    List or create Needs List template files for a location.
    GET  /api/account/needs-list-templates/?location_id=
    POST /api/account/needs-list-templates/  (multipart: location_id, name, file)
    Files upload to GHL Media folder templates_parent_id (not borrower upload folder).
    """
    from accounts.models import GHLAuthCredentials
    from .ghl_service import upload_file as ghl_upload_file

    if request.method == "GET":
        location_id = extract_location_id(request)
        if not location_id:
            return JsonResponse({"error": "location_id is required"}, status=400)
        account = GHLAuthCredentials.objects.filter(location_id=location_id).first()
        if not account:
            return JsonResponse({"error": "Unknown location_id"}, status=404)
        rows = NeedsListTemplate.objects.filter(account=account).order_by("name")
        return JsonResponse({
            "templates": [_serialize_needs_list_template(t) for t in rows],
            "templates_parent_id": (account.templates_parent_id or "").strip() or None,
        })

    # POST create
    location_id = (
        request.POST.get("location_id")
        or extract_location_id(request)
        or ""
    ).strip()
    name = (request.POST.get("name") or "").strip()
    if not location_id:
        return JsonResponse({"error": "location_id is required"}, status=400)
    if not name:
        return JsonResponse({"error": "name is required"}, status=400)
    if "file" not in request.FILES:
        return JsonResponse({"error": "file is required"}, status=400)

    account = GHLAuthCredentials.objects.filter(location_id=location_id).first()
    if not account:
        return JsonResponse({"error": "Unknown location_id"}, status=404)

    templates_folder = (account.templates_parent_id or "").strip()
    if not templates_folder:
        return JsonResponse(
            {
                "error": (
                    "Needs List Templates folder is not configured for this location "
                    "(set templates_parent_id on GHL credentials)."
                )
            },
            status=400,
        )

    file = request.FILES["file"]
    try:
        result = ghl_upload_file(
            file,
            name=file.name or name,
            parent_id=templates_folder,
            access_token=account.access_token,
        )
    except Exception as e:
        try:
            import requests
            if isinstance(e, requests.HTTPError) and e.response is not None:
                return JsonResponse(
                    {"error": f"GHL upload failed: {e.response.text[:500]}"},
                    status=502,
                )
        except Exception:
            pass
        return JsonResponse({"error": f"GHL upload failed: {e}"}, status=502)

    file_id = result.get("fileId")
    file_url = result.get("url")
    if not file_id or not file_url:
        return JsonResponse({"error": "GHL upload returned no file id/url"}, status=502)

    tpl = NeedsListTemplate.objects.create(
        account=account,
        name=name,
        ghl_file_id=file_id,
        ghl_file_url=file_url,
        file_name=file.name or name,
    )
    return JsonResponse(
        {"success": True, "template": _serialize_needs_list_template(tpl)},
        status=201,
    )


@csrf_exempt
@require_http_methods(["PUT", "PATCH", "DELETE"])
def needs_list_template_detail(request, template_id):
    """
    Update name / replace file, or delete a Needs List template.
    PUT/PATCH /api/account/needs-list-templates/<id>/
      multipart or JSON: location_id, name?, file? (optional replace)
    DELETE /api/account/needs-list-templates/<id>/?location_id=
    """
    from accounts.models import GHLAuthCredentials
    from .ghl_service import (
        upload_file as ghl_upload_file,
        delete_media,
    )

    location_id = extract_location_id(request)
    if request.content_type and "multipart/form-data" in request.content_type:
        location_id = (request.POST.get("location_id") or location_id or "").strip() or None
    elif request.body and request.method in ("PUT", "PATCH"):
        try:
            body = json.loads(request.body)
            location_id = (body.get("location_id") or location_id or "").strip() or None
        except json.JSONDecodeError:
            body = {}
    else:
        body = {}

    if not location_id:
        return JsonResponse({"error": "location_id is required"}, status=400)

    account = GHLAuthCredentials.objects.filter(location_id=location_id).first()
    if not account:
        return JsonResponse({"error": "Unknown location_id"}, status=404)

    tpl = get_object_or_404(NeedsListTemplate, id=template_id, account=account)

    if request.method == "DELETE":
        old_id = tpl.ghl_file_id
        tpl.delete()
        if old_id:
            try:
                delete_media(
                    old_id,
                    alt_type=(account.alt_type or "location"),
                    alt_id=(account.alt_id or account.location_id),
                    access_token=account.access_token,
                )
            except Exception as e:
                logger.warning("Failed to delete GHL template media %s: %s", old_id, e)
        return JsonResponse({"success": True, "deleted": template_id})

    # Update
    name = None
    if request.content_type and "multipart/form-data" in request.content_type:
        name = (request.POST.get("name") or "").strip() or None
        file = request.FILES.get("file")
    else:
        try:
            body = body if body else json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        name = (body.get("name") or "").strip() or None
        file = None

    update_fields = []
    if name:
        tpl.name = name
        update_fields.append("name")

    if file:
        templates_folder = (account.templates_parent_id or "").strip()
        if not templates_folder:
            return JsonResponse(
                {"error": "templates_parent_id is not configured for this location"},
                status=400,
            )
        try:
            result = ghl_upload_file(
                file,
                name=file.name or tpl.name,
                parent_id=templates_folder,
                access_token=account.access_token,
            )
        except Exception as e:
            return JsonResponse({"error": f"GHL upload failed: {e}"}, status=502)
        new_id = result.get("fileId")
        new_url = result.get("url")
        if not new_id or not new_url:
            return JsonResponse({"error": "GHL upload returned no file id/url"}, status=502)
        old_id = tpl.ghl_file_id
        tpl.ghl_file_id = new_id
        tpl.ghl_file_url = new_url
        tpl.file_name = file.name or tpl.file_name
        update_fields.extend(["ghl_file_id", "ghl_file_url", "file_name"])
        if old_id and old_id != new_id:
            try:
                delete_media(
                    old_id,
                    alt_type=(account.alt_type or "location"),
                    alt_id=(account.alt_id or account.location_id),
                    access_token=account.access_token,
                )
            except Exception as e:
                logger.warning("Failed to delete old GHL template media %s: %s", old_id, e)

    if not update_fields:
        return JsonResponse({"error": "Nothing to update (provide name and/or file)"}, status=400)

    update_fields.append("updated_at")
    tpl.save(update_fields=update_fields)
    return JsonResponse({"success": True, "template": _serialize_needs_list_template(tpl)})


def user_documents_view(request, request_id):
    """
    User view page to see all their uploaded documents and their status.
    URL: {request_id}/view/
    If no document list exists yet, shows a friendly "create the list first" page instead of 404.
    """
    try:
        doc_request = DocumentRequest.objects.get(request_id=request_id)
        _link_doc_request_to_location(request, doc_request)
    except DocumentRequest.DoesNotExist:
        return render(request, 'documents/request_not_found.html', {'request_id': request_id, 'is_user_facing': True})

    adhoc_docs, individual_docs, needs_list_docs = _build_request_document_data(doc_request)
    context = {
        'request_id': request_id,
        'adhoc_documents': adhoc_docs,
        'individual_documents': individual_docs,
        'needs_list_documents': needs_list_docs,
    }
    return render(request, 'documents/user_documents_view.html', context)
