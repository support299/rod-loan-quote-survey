from django.contrib.auth import get_user_model
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from . import services as portal_services
from .models import PortalProfile
from .serializers import (
    PortalProfileSerializer,
    PortalTokenObtainPairSerializer,
    RegisterSerializer,
)

User = get_user_model()


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
