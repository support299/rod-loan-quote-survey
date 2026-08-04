from django.contrib import admin
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


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'description', 'created_at']
    search_fields = ['name', 'description']
    list_filter = ['created_at']


@admin.register(PrintGroup)
class PrintGroupAdmin(admin.ModelAdmin):
    list_display = ['name', 'request', 'owner_account', 'created_at']
    search_fields = ['name', 'description']
    list_filter = ['created_at']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        (None, {'fields': ('name', 'description', 'request', 'owner_account')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )


@admin.register(AccountPrintGroupLibrary)
class AccountPrintGroupLibraryAdmin(admin.ModelAdmin):
    list_display = ['account', 'print_group', 'created_at']
    list_filter = ['created_at']
    search_fields = ['account__location_id', 'account__location_name', 'print_group__name']


@admin.register(AccountDocumentLibrary)
class AccountDocumentLibraryAdmin(admin.ModelAdmin):
    list_display = ['account', 'document', 'created_at']
    list_filter = ['created_at']
    search_fields = ['account__location_id', 'account__location_name', 'document__name']


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'request', 'owner_account', 'get_print_groups', 'created_at']
    list_filter = ['category', 'print_groups', 'created_at']
    search_fields = ['name', 'description', 'category__name']
    filter_horizontal = ['print_groups']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'description', 'category', 'request', 'owner_account')
        }),
        ('Print Groups', {
            'fields': ('print_groups',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def get_print_groups(self, obj):
        """Display print groups as a comma-separated list"""
        return ", ".join([pg.name for pg in obj.print_groups.all()])
    get_print_groups.short_description = 'Print Groups'


@admin.register(DocumentRequest)
class DocumentRequestAdmin(admin.ModelAdmin):
    list_display = ['request_id', 'created_at', 'updated_at', 'get_selections_count']
    search_fields = ['request_id']
    list_filter = ['created_at', 'updated_at']
    readonly_fields = ['created_at', 'updated_at']
    
    def get_selections_count(self, obj):
        return obj.admin_selections.count()
    get_selections_count.short_description = 'Selections'


@admin.register(AdminDocumentSelection)
class AdminDocumentSelectionAdmin(admin.ModelAdmin):
    list_display = ['request', 'section_type', 'document', 'print_group', 'created_at']
    list_filter = ['section_type', 'created_at', 'print_group']
    search_fields = ['request__request_id', 'document__name']
    readonly_fields = ['created_at']
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('request', 'document', 'print_group')


@admin.register(NeedsListTemplate)
class NeedsListTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'account', 'file_name', 'created_at', 'updated_at']
    list_filter = ['created_at']
    search_fields = ['name', 'file_name', 'account__location_id', 'account__location_name']
    readonly_fields = ['ghl_file_id', 'ghl_file_url', 'created_at', 'updated_at']


@admin.register(UserDocumentUpload)
class UserDocumentUploadAdmin(admin.ModelAdmin):
    list_display = ['admin_selection', 'file', 'uploaded_at', 'updated_at']
    list_filter = ['uploaded_at', 'updated_at']
    search_fields = ['admin_selection__document__name', 'file']
    readonly_fields = ['uploaded_at', 'updated_at']
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('admin_selection__document', 'admin_selection__request')


@admin.register(OpportunityCardSubmission)
class OpportunityCardSubmissionAdmin(admin.ModelAdmin):
    list_display = ['request_id', 'submitted_at']
    search_fields = ['request_id']
    list_filter = ['submitted_at']
    readonly_fields = ['request_id', 'form_data', 'submitted_at']
    fields = ['request_id', 'form_data', 'submitted_at']
