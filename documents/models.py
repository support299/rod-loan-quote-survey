from django.db import models


class Category(models.Model):
    """
    Represents a document category (e.g., Assets, Credit, Entity, Income, Property).
    If request is set, the category is request-scoped (custom for that request only).
    """
    name = models.CharField(max_length=100, help_text="Category name (e.g., Assets, Credit, Entity)")
    description = models.TextField(blank=True, null=True, help_text="Optional description of the category")
    request = models.ForeignKey(
        'DocumentRequest',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='custom_categories',
        help_text="If set, this category is visible only for this request (custom ad hoc/individual/needs list category)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ['name']

    def __str__(self):
        return self.name


class PrintGroup(models.Model):
    """
    Represents a print group that documents can belong to.
    Examples: Profit & Loss, Conventional Refinance (W-2), FHA Refinance (W-2), VA Refinance (W-2), etc.
    If request is set, the print group is request-scoped (custom for that request only).
    """
    name = models.CharField(max_length=200, help_text="Print group name")
    description = models.TextField(blank=True, null=True, help_text="Optional description of the print group")
    request = models.ForeignKey(
        'DocumentRequest',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='custom_print_groups',
        help_text="If set, this print group is visible only for this request (custom needs list group)"
    )
    owner_account = models.ForeignKey(
        "accounts.GHLAuthCredentials",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="owned_print_groups",
        help_text="If set, this print group is only available for this GHL subaccount (master catalog otherwise).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Document(models.Model):
    """
    Represents a document that may be required for loan processing.
    Each document belongs to a category and can be associated with multiple print groups.
    If request is set, the document is request-scoped (custom doc for that request only).
    """
    name = models.CharField(
        max_length=300,
        help_text="Document name (e.g., Bank Statements, Gift Letter)"
    )
    description = models.TextField(
        help_text="Detailed description of what the document is and its purpose"
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name='documents',
        help_text="The category this document belongs to"
    )
    request = models.ForeignKey(
        'DocumentRequest',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='custom_documents',
        help_text="If set, this document is visible only for this request (custom ad hoc/individual/needs list doc)"
    )
    owner_account = models.ForeignKey(
        "accounts.GHLAuthCredentials",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="owned_catalog_documents",
        help_text="If set, this catalog-style document belongs only to this subaccount (not in shared master list).",
    )
    print_groups = models.ManyToManyField(
        PrintGroup,
        related_name='documents',
        blank=True,
        help_text="Print groups this document belongs to"
    )
    file = models.FileField(
        upload_to='documents/%Y/%m/%d/',
        blank=True,
        null=True,
        help_text="Uploaded document file"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['category']),
        ]

    def __str__(self):
        return self.name


class AccountDocumentLibrary(models.Model):
    """
    Which master catalog documents (request=NULL, owner_account=NULL) are enabled
    for a given GHL subaccount. Admins add/remove rows to customize the library per account.
    """

    account = models.ForeignKey(
        "accounts.GHLAuthCredentials",
        on_delete=models.CASCADE,
        related_name="document_library_entries",
    )
    document = models.ForeignKey(
        "Document",
        on_delete=models.CASCADE,
        related_name="account_library_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [["account", "document"]]
        verbose_name_plural = "Account document library entries"

    def __str__(self):
        return f"{self.account_id} → {self.document_id}"


class AccountPrintGroupLibrary(models.Model):
    """
    Which master catalog print groups (request=NULL, owner_account=NULL) are enabled
    per GHL subaccount—mirrors AccountDocumentLibrary for needs-list groupings.
    """

    account = models.ForeignKey(
        "accounts.GHLAuthCredentials",
        on_delete=models.CASCADE,
        related_name="print_group_library_entries",
    )
    print_group = models.ForeignKey(
        "PrintGroup",
        on_delete=models.CASCADE,
        related_name="account_library_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [["account", "print_group"]]
        verbose_name_plural = "Account print group library entries"

    def __str__(self):
        return f"{self.account_id} → {self.print_group_id}"


class DocumentRequest(models.Model):
    """
    Represents a document request session identified by a unique request ID from the URL.
    """
    request_id = models.CharField(max_length=255, unique=True, help_text="Unique identifier from URL (e.g., gfgwgvffrffgggrg)")
    ghl_account = models.ForeignKey(
        'accounts.GHLAuthCredentials',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='document_requests',
        help_text="Linked GHL account for this request (resolved by location_id).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    ghl_needs_list_note_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="GHL contact note ID for the needs list; when set, we update this note on subsequent changes instead of creating a new one",
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Request: {self.request_id}"


class AdminDocumentSelection(models.Model):
    """
    Stores admin's document selections for a specific request.
    """
    SECTION_CHOICES = [
        ('adhoc', 'AD HOC'),
        ('individual', 'Individual Documents'),
        ('needs_list', 'Needs List'),
    ]

    request = models.ForeignKey(
        DocumentRequest,
        on_delete=models.CASCADE,
        related_name='admin_selections',
        help_text="The document request this selection belongs to"
    )
    section_type = models.CharField(
        max_length=20,
        choices=SECTION_CHOICES,
        help_text="Type of section: adhoc, individual, or needs_list"
    )
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name='admin_selections',
        help_text="The document selected by admin"
    )
    print_group = models.ForeignKey(
        PrintGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='admin_selections',
        help_text="Print group (only for needs_list section type)"
    )
    template = models.ForeignKey(
        "NeedsListTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admin_selections",
        help_text="Template file the borrower should download for this requested document",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['request', 'section_type', 'document', 'print_group']
        ordering = ['section_type', 'created_at']

    def __str__(self):
        return f"{self.request.request_id} - {self.get_section_type_display()} - {self.document.name}"


class NeedsListTemplate(models.Model):
    """
    Reusable blank/template files for Needs List requests.
    Files live in GHL Media (templates folder); DB stores name + media ids/urls.
    """
    account = models.ForeignKey(
        "accounts.GHLAuthCredentials",
        on_delete=models.CASCADE,
        related_name="needs_list_templates",
        help_text="GHL subaccount that owns this template",
    )
    name = models.CharField(max_length=255, help_text="Display name shown to admin and borrower")
    ghl_file_id = models.CharField(max_length=100, help_text="GHL media fileId")
    ghl_file_url = models.URLField(max_length=500, help_text="GHL media URL for view/download")
    file_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Original uploaded file name",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["account", "name"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.account.location_id})"


class UserDocumentUpload(models.Model):
    """
    Stores user uploads for documents selected by admin.
    Files are uploaded to GHL (GoHighLevel); we store ghl_file_id and ghl_file_url.
    Legacy file field is optional for backwards compatibility.
    """
    admin_selection = models.ForeignKey(
        AdminDocumentSelection,
        on_delete=models.CASCADE,
        related_name='user_uploads',
        help_text="The admin selection this upload belongs to"
    )
    file = models.FileField(
        upload_to='user_uploads/%Y/%m/%d/',
        blank=True,
        null=True,
        help_text="Legacy: file on server (prefer GHL URL)"
    )
    ghl_file_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="GHL media fileId from upload-file API"
    )
    ghl_file_url = models.URLField(
        max_length=500,
        blank=True,
        null=True,
        help_text="GHL media URL from upload-file API (use for viewing)"
    )
    file_name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Original file name for display"
    )
    accepted = models.BooleanField(
        default=False,
        help_text="Whether the admin has accepted this document"
    )
    accepted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the document was accepted by admin"
    )
    rejection_reason = models.TextField(
        blank=True,
        default="",
        help_text="Admin reason shown to the borrower when the upload is rejected",
    )
    rejected_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the document was rejected by admin",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-uploaded_at']

    def get_file_url(self):
        """URL to view the file (GHL or legacy server)."""
        if self.ghl_file_url:
            return self.ghl_file_url
        if self.file:
            return self.file.url
        return None

    def get_file_name(self):
        """Display name for the file."""
        if self.file_name:
            return self.file_name
        if self.file:
            return self.file.name.split('/')[-1] if self.file.name else None
        return None

    def __str__(self):
        name = self.get_file_name() or "upload"
        return f"Upload for {self.admin_selection.document.name} - {name}"


class OpportunityCardSubmission(models.Model):
    """
    Stores a client's opportunity card registration form submission.
    Each unique request_id URL gets one submission (create or update on resubmit).
    """
    request_id = models.CharField(
        max_length=255,
        unique=True,
        help_text="Unique identifier from URL (same pattern as document request)"
    )
    form_data = models.JSONField(
        default=dict,
        help_text="All form fields as key-value pairs (street, city, purpose, etc.)"
    )
    submitted_at = models.DateTimeField(auto_now=True)
    ghl_note_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="GHL contact note ID; when set, we do not create another note on resubmit"
    )

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = "Opportunity Card Submission"
        verbose_name_plural = "Opportunity Card Submissions"

    def __str__(self):
        return f"Opportunity Card: {self.request_id}"
