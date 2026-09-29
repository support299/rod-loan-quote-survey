"""
Portal loan helpers — read-only over existing OpportunityCardSubmission + GHL.
Does not create opportunities or change survey/doc flows.
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation

from django.db.models import Q
from django.utils import timezone

from accounts.models import GHLAuthCredentials
from documents.ghl_service import lookup_duplicate_contact
from documents.models import (
    AdminDocumentSelection,
    DocumentRequest,
    OpportunityCardSubmission,
)
from documents.survey_opportunity import (
    get_default_ghl_account,
    get_opportunity_pipeline_info,
)

from .models import Loan, PortalProfile

logger = logging.getLogger(__name__)

# High-level tracker shown in the SPA (maps many GHL stage names → these keys)
PORTAL_STAGE_DEFS = [
    {'key': 'application', 'label': 'Application'},
    {'key': 'processing', 'label': 'Processing'},
    {'key': 'underwriting', 'label': 'Underwriting'},
    {'key': 'approval', 'label': 'Approval'},
    {'key': 'closing', 'label': 'Closing'},
    {'key': 'funded', 'label': 'Funded'},
]


def get_profile(user) -> PortalProfile:
    profile, _ = PortalProfile.objects.get_or_create(
        user=user,
        defaults={'role': PortalProfile.Role.BORROWER},
    )
    return profile


def _account_for_profile(profile: PortalProfile):
    if profile.ghl_location_id:
        account = (
            GHLAuthCredentials.objects.filter(location_id=profile.ghl_location_id)
            .exclude(access_token='')
            .exclude(access_token__isnull=True)
            .first()
        )
        if account:
            return account
    return get_default_ghl_account()


def link_ghl_contact(profile: PortalProfile, force: bool = False) -> PortalProfile:
    """
    Match portal user email to GHL contact via duplicate search.
    Saves ghl_contact_id + ghl_location_id on the profile.
    """
    if profile.ghl_contact_id and not force:
        return profile

    email = (profile.user.email or '').strip().lower()
    if not email:
        return profile

    account = _account_for_profile(profile)
    if not account:
        logger.warning('No GHL account available to link portal user %s', email)
        return profile

    try:
        contact = lookup_duplicate_contact(
            account.location_id,
            email=email,
            access_token=account.access_token,
        )
    except Exception:
        logger.exception('GHL duplicate contact lookup failed for %s', email)
        return profile

    if not contact or not contact.get('id'):
        return profile

    profile.ghl_contact_id = contact['id']
    profile.ghl_location_id = account.location_id or profile.ghl_location_id
    if not profile.phone and contact.get('phone'):
        profile.phone = str(contact.get('phone') or '')[:32]
    profile.save(
        update_fields=['ghl_contact_id', 'ghl_location_id', 'phone', 'updated_at']
    )
    return profile


def _money(value) -> str:
    if value is None or value == '':
        return '—'
    raw = str(value).strip()
    if not raw:
        return '—'
    cleaned = re.sub(r'[^\d.\-]', '', raw.replace(',', ''))
    try:
        amount = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return raw if raw.startswith('$') else f'${raw}'
    return f'${amount:,.0f}'


def _guess_key_from_text(text: str) -> str:
    t = (text or '').lower()
    if 'fund' in t:
        return 'funded'
    if 'clos' in t:
        return 'closing'
    if 'approv' in t or 'termsheet' in t or 'term sheet' in t or 'commitment' in t:
        return 'approval'
    if 'underwrit' in t or 'under review' in t:
        return 'underwriting'
    if 'process' in t or 'qc review' in t or 'document upload' in t:
        return 'processing'
    if 'quick app' in t or 'application' in t:
        return 'application'
    return 'application'


def _portal_stage_key(stage_name: str, pipeline_name: str = '') -> str:
    combined = f'{pipeline_name} {stage_name}'
    pipe = (pipeline_name or '').lower()
    if '02 processing' in pipe or 'processing pipeline' in pipe:
        # Default processing pipeline stages toward processing/underwriting
        key = _guess_key_from_text(stage_name)
        if key == 'application':
            return 'processing'
        return key
    return _guess_key_from_text(combined)


def build_loan_stages(current: str) -> list[dict]:
    keys = [s['key'] for s in PORTAL_STAGE_DEFS]
    try:
        current_idx = keys.index(current)
    except ValueError:
        current_idx = 0

    stages = []
    for i, spec in enumerate(PORTAL_STAGE_DEFS):
        if i < current_idx:
            state = 'complete'
        elif i == current_idx:
            state = 'current'
        elif i == current_idx + 1:
            state = 'upcoming'
        else:
            state = 'locked'
        stages.append({**spec, 'state': state})
    return stages


def _loan_amount_from_form(form_data: dict) -> str:
    for key in (
        'purchase_price',
        'lot_purchase_price',
        'payoff',
        'existing_loan_balance',
        'as_is_value',
        'current_property_value',
        'estimated_property_value',
    ):
        val = (form_data or {}).get(key)
        if val not in (None, ''):
            return _money(val)
    return '—'


def _health_from_selections(selections) -> str:
    waiting_on_borrower = False
    needs_attention = False
    for sel in selections:
        uploads = list(sel.user_uploads.all())
        if not uploads:
            waiting_on_borrower = True
            continue
        latest = uploads[0]  # ordering = -uploaded_at
        if latest.rejection_reason or latest.rejected_at:
            needs_attention = True
    if needs_attention:
        return 'Needs Attention'
    if waiting_on_borrower:
        return 'Waiting on You'
    return 'On Track'


def _health_by_request(request_ids: list[str]) -> dict[str, str]:
    """Deal health for many loans in two queries (selections + their uploads)."""
    by_request = {}
    try:
        selections = (
            AdminDocumentSelection.objects.filter(request__request_id__in=request_ids)
            .select_related('request')
            .prefetch_related('user_uploads')
        )
        for sel in selections:
            by_request.setdefault(sel.request.request_id, []).append(sel)
    except Exception:
        logger.exception('health check failed for %s', request_ids)
    return {rid: _health_from_selections(by_request.get(rid, [])) for rid in request_ids}


def _health_for_request(request_id: str) -> str:
    return _health_by_request([request_id])[request_id]


def _submissions_for_email(email: str):
    email = (email or '').strip().lower()
    if not email:
        return OpportunityCardSubmission.objects.none()
    # JSON email match (case variants) + legacy username emails
    return OpportunityCardSubmission.objects.filter(
        Q(form_data__email__iexact=email)
    ).order_by('-submitted_at')


def get_or_sync_loan(opportunity_id: str, account=None) -> Loan:
    """
    Return the portal Loan row, reading pipeline info from GHL only the first time.
    A failed GHL read leaves ghl_synced_at empty so the next request retries.
    """
    loan, _ = Loan.objects.get_or_create(opportunity_id=opportunity_id)
    if not loan.ghl_synced_at:
        _sync_loan_from_ghl(loan, account or get_default_ghl_account())
    return loan


def _loans_by_id(request_ids: list[str], account=None) -> dict[str, Loan]:
    """Bulk version of get_or_sync_loan: one query for existing rows, GHL only for unsynced."""
    loans = {loan.opportunity_id: loan for loan in Loan.objects.filter(opportunity_id__in=request_ids)}
    missing = [rid for rid in request_ids if rid not in loans]
    if missing:
        Loan.objects.bulk_create([Loan(opportunity_id=rid) for rid in missing], ignore_conflicts=True)
        loans.update(
            {loan.opportunity_id: loan for loan in Loan.objects.filter(opportunity_id__in=missing)}
        )

    unsynced = [loan for loan in loans.values() if not loan.ghl_synced_at]
    if unsynced:
        account = account or get_default_ghl_account()
        for loan in unsynced:
            _sync_loan_from_ghl(loan, account)
    return loans


def _sync_loan_from_ghl(loan: Loan, account) -> None:
    if not account:
        return
    try:
        info = get_opportunity_pipeline_info(
            loan.opportunity_id,
            account=account,
            access_token=account.access_token,
        )
    except Exception:
        logger.warning('Could not load GHL pipeline for opportunity %s', loan.opportunity_id, exc_info=True)
        return
    if not info:
        return

    loan.stage_name = info.get('stage_name') or ''
    loan.pipeline_name = info.get('pipeline_name') or ''
    loan.opportunity_name = ((info.get('opportunity') or {}).get('name') or '')[:255]
    loan.ghl_contact_id = info.get('contact_id') or ''
    if loan.stage_name:
        loan.status = _portal_stage_key(loan.stage_name, loan.pipeline_name)
    loan.ghl_synced_at = timezone.now()
    loan.save()


def update_loan_status(opportunity_id: str, status: str) -> Loan:
    """Set the portal status (DB only — GHL is not updated)."""
    loan = get_or_sync_loan(opportunity_id)
    loan.status = status
    # Borrower dashboard shows stage_name, so keep it in step with the new status
    loan.stage_name = Loan.Status(status).label
    loan.save(update_fields=['status', 'stage_name', 'updated_at'])
    return loan


def serialize_loan_card(
    submission: OpportunityCardSubmission, loan: Loan, health: str | None = None
) -> dict:
    form = submission.form_data or {}

    address = (
        (form.get('subject_property_address') or '').strip()
        or loan.opportunity_name
        or 'Loan application'
    )

    return {
        'id': submission.request_id,
        'opportunityId': submission.request_id,
        'address': address,
        'status': loan.get_status_display(),
        'statusKey': loan.status,
        'loanAmount': _loan_amount_from_form(form),
        'rateLock': '—',
        'nextPaymentDate': '—',
        'totalDue': '—',
        'estimatedEndDate': '—',
        'health': health or _health_for_request(submission.request_id),
        'loanType': (form.get('loan_type') or '').strip() or '—',
        'entityName': (form.get('entity_name') or '').strip() or '',
        'pipelineName': loan.pipeline_name,
        'stageName': loan.stage_name,
    }


def list_loans_for_profile(profile: PortalProfile) -> list[dict]:
    profile = link_ghl_contact(profile)
    email = (profile.user.email or '').strip().lower()
    submissions = list(_submissions_for_email(email))
    ids = [sub.request_id for sub in submissions]
    loans = _loans_by_id(ids, _account_for_profile(profile))
    health = _health_by_request(ids)
    return [
        serialize_loan_card(sub, loans[sub.request_id], health[sub.request_id])
        for sub in submissions
    ]


def list_all_loans() -> list[dict]:
    """Every loan for the admin pipeline, with borrower contact from the form."""
    submissions = list(OpportunityCardSubmission.objects.order_by('-submitted_at'))
    ids = [sub.request_id for sub in submissions]
    loans_by_id = _loans_by_id(ids)
    health = _health_by_request(ids)
    loans = []
    for sub in submissions:
        form = sub.form_data or {}
        card = serialize_loan_card(sub, loans_by_id[sub.request_id], health[sub.request_id])
        card['borrowerName'] = (form.get('full_name') or '').strip()
        card['borrowerEmail'] = (form.get('email') or '').strip()
        loans.append(card)
    return loans


def get_loan_for_profile(profile: PortalProfile, opportunity_id: str) -> dict | None:
    profile = link_ghl_contact(profile)
    email = (profile.user.email or '').strip().lower()
    sub = OpportunityCardSubmission.objects.filter(request_id=opportunity_id).first()
    if not sub:
        return None

    form_email = ((sub.form_data or {}).get('email') or '').strip().lower()
    if form_email != email and profile.role != PortalProfile.Role.STAFF:
        # Staff can view any; borrowers only own email
        return None

    loan = get_or_sync_loan(opportunity_id, _account_for_profile(profile))
    card = serialize_loan_card(sub, loan)
    form = sub.form_data or {}

    urgent = None
    docs = list_documents_for_opportunity(opportunity_id)
    for doc in docs.get('documentsNeeded') or []:
        if doc.get('status') in ('Rejected', 'Needs Revision') or (
            doc.get('status') == 'Pending Review' and doc.get('awaitingUpload')
        ):
            urgent = {
                'title': f"Upload {doc.get('name')}" if doc.get('awaitingUpload') else doc.get('name'),
                'subtitle': doc.get('status'),
            }
            if doc.get('awaitingUpload'):
                urgent = {
                    'title': f"Upload {doc.get('name')}",
                    'subtitle': 'Action needed',
                }
            break

    summary = {
        'id': opportunity_id,
        'loanName': f"Loan {opportunity_id[:8]}",
        'address': card['address'],
        'loanAmount': card['loanAmount'],
        'interestRate': '—',
        'ltv': '—',
        'arv': _money(form.get('arv') or form.get('final_value')),
        'loanType': card['loanType'],
        'term': '—',
        'closingDate': '—',
        'maturityDate': '—',
        'monthlyPayment': '—',
        'constructionHoldback': _money(form.get('construction_budget') or form.get('rehab_budget')),
    }

    assigned = None
    ae = (form.get('account_executive') or '').strip()
    if ae and ae.upper() != 'N/A':
        assigned = {
            'name': ae,
            'title': 'Account Executive',
            'avatar': '',
        }

    return {
        'loan': card,
        'currentStatus': {
            'label': card['status'],
            'badge': 'Action Needed' if card['health'] == 'Waiting on You' else None,
        },
        'loanStages': build_loan_stages(loan.status),
        'urgentAction': urgent,
        'assignedOfficer': assigned,
        'recentActivity': [],
        'summary': summary,
        'documents': docs,
    }


def _upload_status(selection: AdminDocumentSelection) -> tuple[str, bool]:
    uploads = list(selection.user_uploads.all())
    if not uploads:
        return 'Pending Review', True
    latest = uploads[0]  # related ordering newest first
    if latest.rejection_reason or latest.rejected_at:
        return 'Needs Revision', True
    if latest.accepted:
        return 'Uploaded', False
    return 'Pending Review', False


def list_documents_for_opportunity(opportunity_id: str) -> dict:
    doc_req = DocumentRequest.objects.filter(request_id=opportunity_id).first()
    if not doc_req:
        return {'documentsNeeded': [], 'documentsSent': []}

    selections = (
        AdminDocumentSelection.objects.filter(request=doc_req)
        .select_related('document', 'document__blank_template', 'template')
        .prefetch_related('user_uploads')
    )

    needed = []
    for sel in selections:
        status_label, awaiting = _upload_status(sel)
        name = sel.document.name if sel.document_id else 'Document'
        needed.append(
            {
                'id': sel.id,
                'name': name,
                'status': status_label,
                'awaitingUpload': awaiting,
                'selectionId': sel.id,
            }
        )

    sent = []
    for sel in selections:
        tpl = sel.template or (
            sel.document.blank_template if sel.document_id else None
        )
        if not tpl:
            continue
        sent.append(
            {
                'id': f'tpl-{tpl.id}-{sel.id}',
                'name': tpl.name or (sel.document.name if sel.document_id else 'Document'),
                'sentDate': tpl.created_at.strftime('%b %d, %Y') if tpl.created_at else '—',
                'url': tpl.ghl_file_url or '',
            }
        )

    return {'documentsNeeded': needed, 'documentsSent': sent}


def list_all_users() -> list[dict]:
    """Every portal/Django user for the admin Users page (3 queries total)."""
    from collections import Counter

    from django.contrib.auth import get_user_model

    loan_counts = Counter(
        (email or '').strip().lower()
        for email in OpportunityCardSubmission.objects.values_list('form_data__email', flat=True)
    )

    users = []
    for user in get_user_model().objects.select_related('portal_profile').order_by('-date_joined'):
        profile = getattr(user, 'portal_profile', None)
        email = (user.email or '').strip().lower()
        users.append(
            {
                'id': user.id,
                'email': user.email,
                'fullName': f'{user.first_name} {user.last_name}'.strip(),
                'phone': profile.phone if profile else '',
                'role': profile.role if profile else PortalProfile.Role.BORROWER,
                'isAdmin': bool(user.is_staff or user.is_superuser),
                'isActive': user.is_active,
                'ghlLinked': bool(profile and profile.ghl_contact_id),
                'loanCount': loan_counts.get(email, 0) if email else 0,
                'dateJoined': user.date_joined,
                'lastLogin': user.last_login,
            }
        )
    return users
