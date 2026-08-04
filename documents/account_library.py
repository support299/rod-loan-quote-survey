"""
Per-GHL-subaccount (GHLAuthCredentials) visibility for catalog documents.

- Master catalog rows: Document with request=NULL and owner_account=NULL; visibility
  is controlled by AccountDocumentLibrary.
- Account-specific templates: Document.owner_account set; always visible to that account.
- Request-scoped rows: Document.request set; visible only in that document request's flows.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from django.db.models import Exists, OuterRef, Q, QuerySet

if TYPE_CHECKING:
    from accounts.models import GHLAuthCredentials

from .models import (
    AccountDocumentLibrary,
    AccountPrintGroupLibrary,
    Document,
    DocumentRequest,
    PrintGroup,
)


def resolve_account_for_request(
    http_request,
    json_body: Optional[dict] = None,
    doc_request: Optional[DocumentRequest] = None,
) -> Optional["GHLAuthCredentials"]:
    from accounts.models import GHLAuthCredentials

    from .ghl_credentials import extract_location_id

    location_id = extract_location_id(http_request, json_body)
    if location_id:
        return GHLAuthCredentials.objects.filter(location_id=location_id).first()
    if doc_request and getattr(doc_request, "ghl_account_id", None):
        return doc_request.ghl_account
    return None


def filter_documents_for_listing(
    qs: QuerySet,
    account: Optional["GHLAuthCredentials"] = None,
    doc_request: Optional[DocumentRequest] = None,
) -> QuerySet:
    catalog_q = Q(request__isnull=True)
    if account:
        lib = AccountDocumentLibrary.objects.filter(
            account=account, document_id=OuterRef("pk")
        )
        catalog_q &= Q(owner_account=account) | (
            Q(owner_account__isnull=True) & Exists(lib)
        )
    else:
        catalog_q &= Q(owner_account__isnull=True)

    if doc_request:
        return qs.filter(Q(request=doc_request) | catalog_q).distinct()
    if account:
        return qs.filter(catalog_q).distinct()
    return qs


def document_visible_to_account(
    document: Document,
    account: Optional["GHLAuthCredentials"],
    doc_request: Optional[DocumentRequest] = None,
) -> bool:
    if document.request_id:
        if not doc_request:
            return False
        return document.request_id == doc_request.id
    if not account:
        return document.owner_account_id is None
    if document.owner_account_id:
        return document.owner_account_id == account.id
    return AccountDocumentLibrary.objects.filter(
        account=account, document_id=document.id
    ).exists()


def seed_account_document_library(account) -> None:
    """Ensure every master catalog document is linked to this subaccount (idempotent)."""
    master_ids = Document.objects.filter(
        request__isnull=True, owner_account__isnull=True
    ).values_list("id", flat=True)
    rows = [
        AccountDocumentLibrary(account_id=account.id, document_id=did)
        for did in master_ids
    ]
    if rows:
        AccountDocumentLibrary.objects.bulk_create(rows, ignore_conflicts=True)


def sync_master_document_to_all_accounts(document: Document) -> None:
    """After a new global catalog document is created, attach it to every GHL subaccount."""
    if document.request_id or document.owner_account_id:
        return
    from accounts.models import GHLAuthCredentials

    acc_ids = GHLAuthCredentials.objects.values_list("id", flat=True)
    rows = [
        AccountDocumentLibrary(account_id=aid, document_id=document.id)
        for aid in acc_ids
    ]
    if rows:
        AccountDocumentLibrary.objects.bulk_create(rows, ignore_conflicts=True)


def sync_all_master_documents_to_all_libraries() -> None:
    """Bulk-sync: every master document × every account (for imports / ops)."""
    from accounts.models import GHLAuthCredentials

    acc_ids = list(GHLAuthCredentials.objects.values_list("id", flat=True))
    master_ids = list(
        Document.objects.filter(
            request__isnull=True, owner_account__isnull=True
        ).values_list("id", flat=True)
    )
    if not acc_ids or not master_ids:
        return
    rows = [
        AccountDocumentLibrary(account_id=aid, document_id=did)
        for aid in acc_ids
        for did in master_ids
    ]
    AccountDocumentLibrary.objects.bulk_create(rows, ignore_conflicts=True, batch_size=2000)


def filter_print_groups_for_listing(
    qs: QuerySet,
    account: Optional["GHLAuthCredentials"] = None,
    doc_request: Optional[DocumentRequest] = None,
) -> QuerySet:
    catalog_q = Q(request__isnull=True)
    if account:
        lib = AccountPrintGroupLibrary.objects.filter(
            account=account, print_group_id=OuterRef("pk")
        )
        catalog_q &= Q(owner_account=account) | (
            Q(owner_account__isnull=True) & Exists(lib)
        )
    else:
        catalog_q &= Q(owner_account__isnull=True)

    if doc_request:
        return qs.filter(Q(request=doc_request) | catalog_q).distinct()
    if account:
        return qs.filter(catalog_q).distinct()
    return qs


def print_group_visible_to_account(
    print_group: PrintGroup,
    account: Optional["GHLAuthCredentials"],
    doc_request: Optional[DocumentRequest] = None,
) -> bool:
    if print_group.request_id:
        if not doc_request:
            return False
        return print_group.request_id == doc_request.id
    if not account:
        return print_group.owner_account_id is None
    if print_group.owner_account_id:
        return print_group.owner_account_id == account.id
    return AccountPrintGroupLibrary.objects.filter(
        account=account, print_group_id=print_group.id
    ).exists()


def seed_account_print_group_library(account) -> None:
    """Ensure every master catalog print group is linked to this subaccount."""
    master_ids = PrintGroup.objects.filter(
        request__isnull=True, owner_account__isnull=True
    ).values_list("id", flat=True)
    rows = [
        AccountPrintGroupLibrary(account_id=account.id, print_group_id=gid)
        for gid in master_ids
    ]
    if rows:
        AccountPrintGroupLibrary.objects.bulk_create(rows, ignore_conflicts=True)


def sync_master_print_group_to_all_accounts(print_group: PrintGroup) -> None:
    if print_group.request_id or print_group.owner_account_id:
        return
    from accounts.models import GHLAuthCredentials

    acc_ids = GHLAuthCredentials.objects.values_list("id", flat=True)
    rows = [
        AccountPrintGroupLibrary(account_id=aid, print_group_id=print_group.id)
        for aid in acc_ids
    ]
    if rows:
        AccountPrintGroupLibrary.objects.bulk_create(rows, ignore_conflicts=True)


def sync_all_master_print_groups_to_all_libraries() -> None:
    from accounts.models import GHLAuthCredentials

    acc_ids = list(GHLAuthCredentials.objects.values_list("id", flat=True))
    master_ids = list(
        PrintGroup.objects.filter(
            request__isnull=True, owner_account__isnull=True
        ).values_list("id", flat=True)
    )
    if not acc_ids or not master_ids:
        return
    rows = [
        AccountPrintGroupLibrary(account_id=aid, print_group_id=gid)
        for aid in acc_ids
        for gid in master_ids
    ]
    AccountPrintGroupLibrary.objects.bulk_create(
        rows, ignore_conflicts=True, batch_size=2000
    )
