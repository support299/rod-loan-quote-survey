from django.urls import path

from .views import (
    AdminLoansView,
    AdminLoanStatusView,
    AdminUsersView,
    LinkContactView,
    LoanDetailView,
    LoanDocumentsView,
    LoginView,
    LogoutView,
    MeView,
    MyLoansView,
    RegisterView,
    RotatingTokenRefreshView,
)

urlpatterns = [
    path('auth/register/', RegisterView.as_view(), name='portal-register'),
    path('auth/login/', LoginView.as_view(), name='portal-login'),
    path('auth/refresh/', RotatingTokenRefreshView.as_view(), name='portal-refresh'),
    path('auth/logout/', LogoutView.as_view(), name='portal-logout'),
    path('auth/me/', MeView.as_view(), name='portal-me'),
    path('auth/link-contact/', LinkContactView.as_view(), name='portal-link-contact'),
    path('loans/', MyLoansView.as_view(), name='portal-loans'),
    path('loans/<str:opportunity_id>/', LoanDetailView.as_view(), name='portal-loan-detail'),
    path(
        'loans/<str:opportunity_id>/documents/',
        LoanDocumentsView.as_view(),
        name='portal-loan-documents',
    ),
    path('admin/loans/', AdminLoansView.as_view(), name='portal-admin-loans'),
    path(
        'admin/loans/<str:opportunity_id>/',
        AdminLoanStatusView.as_view(),
        name='portal-admin-loan-status',
    ),
    path('admin/users/', AdminUsersView.as_view(), name='portal-admin-users'),
]
