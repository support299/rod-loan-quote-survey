from django.urls import path
from . import views

urlpatterns = [
    # Request-based URLs (with request_id)
    path('<str:request_id>/opportunity-card/', views.opportunity_card_form, name='opportunity-card-form'),
    path('<str:request_id>/opportunity-submission/', views.opportunity_submission_view, name='opportunity-submission-view'),
    path('<str:request_id>/opportunity-submission/pdf/', views.download_opportunity_submission_pdf, name='download-opportunity-submission-pdf'),
    path('<str:request_id>/request/admin/', views.homepage, name='admin-homepage'),
    path('<str:request_id>/request/admin/library/', views.library_admin_page, name='library-admin'),
    path('<str:request_id>/request/admin/adhoc/', views.adhoc_page, name='adhoc'),
    path('<str:request_id>/request/admin/individual-documents/', views.individual_documents_page, name='individual-documents'),
    path('<str:request_id>/request/admin/needs-list/', views.needs_list_page, name='needs-list'),
    path('<str:request_id>/request/admin/uploads/', views.admin_user_uploads_view, name='admin-user-uploads'),
    path('<str:request_id>/upload/', views.user_upload_page, name='user-upload'),
    path('<str:request_id>/download-pdf/', views.download_request_pdf, name='download-request-pdf'),
    path('<str:request_id>/view/', views.user_documents_view, name='user-documents-view'),
    
    # API endpoints
    path('api/categories/', views.get_categories, name='categories'),
    path('api/categories/create/', views.create_category, name='create-category'),
    path('api/categories/<int:category_id>/', views.delete_category, name='delete-category'),
    path('api/account/document-library/', views.account_document_library, name='account-document-library'),
    path('api/account/print-group-library/', views.account_print_group_library, name='account-print-group-library'),
    path('api/account/print-groups/create/', views.create_account_print_group, name='create-account-print-group'),
    path('api/account/needs-list-templates/', views.needs_list_templates, name='needs-list-templates'),
    path(
        'api/account/needs-list-templates/<int:template_id>/',
        views.needs_list_template_detail,
        name='needs-list-template-detail',
    ),
    path('api/documents/', views.get_documents, name='documents'),
    path('api/documents/create/', views.create_document, name='create-document'),
    path('api/documents/<int:document_id>/upload/', views.upload_document_file, name='upload-document'),
    path('api/print-groups/', views.get_print_groups, name='print-groups'),
    path('api/<str:request_id>/admin/adhoc/create/', views.create_adhoc_document, name='create-adhoc-document'),
    path('api/<str:request_id>/admin/adhoc/<int:selection_id>/delete/', views.delete_adhoc_document, name='delete-adhoc-document'),
    path('api/<str:request_id>/admin/individual/create/', views.create_individual_document, name='create-individual-document'),
    path('api/<str:request_id>/admin/needs-list/print-group/create/', views.create_needs_list_print_group, name='create-needs-list-print-group'),
    path('api/<str:request_id>/admin/needs-list/document/create/', views.create_needs_list_document, name='create-needs-list-document'),
    path('api/<str:request_id>/admin/selections/', views.save_admin_selections, name='save-admin-selections'),
    path('api/<str:request_id>/upload/<int:selection_id>/', views.upload_user_file, name='upload-user-file'),
    path('api/<str:request_id>/upload/<int:upload_id>/delete/', views.delete_user_upload, name='delete-user-upload'),
    path('api/<str:request_id>/admin/upload/<int:upload_id>/accept/', views.accept_user_upload, name='accept-user-upload'),
    path(
        'api/<str:request_id>/admin/upload/<int:upload_id>/rejection-reason/',
        views.user_upload_rejection_reason,
        name='user-upload-rejection-reason',
    ),
]
