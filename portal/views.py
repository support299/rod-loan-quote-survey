from django.contrib.auth import get_user_model
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from . import services as portal_services
from .models import Loan, PortalProfile
from .serializers import (
    PortalProfileSerializer,
    PortalTokenObtainPairSerializer,
    RegisterSerializer,
)

User = get_user_model()


class IsPortalStaff(permissions.BasePermission):
    """Django staff/superusers or portal profiles with the staff role."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_staff or user.is_superuser:
            return True
        return portal_services.get_profile(user).role == PortalProfile.Role.STAFF


class RegisterView(generics.CreateAPIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = RegisterSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = serializer.save()
        portal_services.link_ghl_contact(profile)

        refresh = RefreshToken.for_user(profile.user)
        return Response(
            {
                'access': str(refresh.access_token),
                'refresh': str(refresh),
                'user': PortalProfileSerializer(profile).data,
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(TokenObtainPairView):
    permission_classes = [permissions.AllowAny]
    serializer_class = PortalTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200:
            email = (response.data.get('user') or {}).get('email')
            if email:
                try:
                    user = User.objects.get(email__iexact=email)
                    profile = portal_services.get_profile(user)
                    portal_services.link_ghl_contact(profile)
                    response.data['user'] = PortalProfileSerializer(profile).data
                except User.DoesNotExist:
                    pass
        return response


class RotatingTokenRefreshView(TokenRefreshView):
    permission_classes = [permissions.AllowAny]


class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get('refresh')
        if not refresh_token:
            return Response(
                {'detail': 'Refresh token is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except Exception:
            return Response(
                {'detail': 'Invalid or already blacklisted token.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({'detail': 'Logged out.'}, status=status.HTTP_200_OK)


class MeView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PortalProfileSerializer

    def get_object(self):
        profile = portal_services.get_profile(self.request.user)
        return portal_services.link_ghl_contact(profile)


class LinkContactView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        profile = portal_services.get_profile(request.user)
        profile = portal_services.link_ghl_contact(profile, force=True)
        return Response(
            {
                'user': PortalProfileSerializer(profile).data,
                'linked': bool(profile.ghl_contact_id),
            }
        )


class MyLoansView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        profile = portal_services.get_profile(request.user)
        loans = portal_services.list_loans_for_profile(profile)
        return Response({'loans': loans, 'count': len(loans)})


class LoanDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, opportunity_id):
        profile = portal_services.get_profile(request.user)
        detail = portal_services.get_loan_for_profile(profile, opportunity_id)
        if not detail:
            return Response({'detail': 'Loan not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(detail)


class LoanDocumentsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, opportunity_id):
        profile = portal_services.get_profile(request.user)
        detail = portal_services.get_loan_for_profile(profile, opportunity_id)
        if not detail and profile.role != PortalProfile.Role.STAFF:
            return Response({'detail': 'Loan not found.'}, status=status.HTTP_404_NOT_FOUND)
        docs = portal_services.list_documents_for_opportunity(opportunity_id)
        return Response(docs)


class AdminLoansView(APIView):
    permission_classes = [IsPortalStaff]

    def get(self, request):
        loans = portal_services.list_all_loans()
        stages = [{'key': value, 'label': label} for value, label in Loan.Status.choices]
        return Response({'loans': loans, 'count': len(loans), 'stages': stages})


class AdminLoanStatusView(APIView):
    permission_classes = [IsPortalStaff]

    def patch(self, request, opportunity_id):
        new_status = request.data.get('status')
        if new_status not in Loan.Status.values:
            return Response(
                {'detail': f'status must be one of: {", ".join(Loan.Status.values)}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        loan = portal_services.update_loan_status(opportunity_id, new_status)
        return Response(
            {
                'id': loan.opportunity_id,
                'status': loan.get_status_display(),
                'statusKey': loan.status,
                'stageName': loan.stage_name,
            }
        )


class AdminUsersView(APIView):
    permission_classes = [IsPortalStaff]

    def get(self, request):
        users = portal_services.list_all_users()
        return Response({'users': users, 'count': len(users)})
