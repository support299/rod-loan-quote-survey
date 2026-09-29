from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import update_last_login
from django.contrib.auth.password_validation import validate_password
from rest_framework import exceptions, serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

from .models import PortalProfile

User = get_user_model()


class PortalProfileSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source='user.email', read_only=True)
    first_name = serializers.CharField(source='user.first_name', read_only=True)
    last_name = serializers.CharField(source='user.last_name', read_only=True)
    full_name = serializers.SerializerMethodField()
    is_admin = serializers.SerializerMethodField()

    class Meta:
        model = PortalProfile
        fields = (
            'email',
            'first_name',
            'last_name',
            'full_name',
            'role',
            'is_admin',
            'phone',
            'avatar_url',
            'ghl_contact_id',
            'ghl_location_id',
        )

    def get_full_name(self, obj):
        name = f'{obj.user.first_name} {obj.user.last_name}'.strip()
        return name or obj.user.email.split('@')[0]

    def get_is_admin(self, obj):
        return bool(obj.user.is_staff or obj.user.is_superuser)


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default='')
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default='')
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True, default='')

    def validate_email(self, value):
        email = value.strip().lower()
        if User.objects.filter(email__iexact=email).exists() or User.objects.filter(username__iexact=email).exists():
            raise serializers.ValidationError('An account with this email already exists.')
        return email

    def validate_password(self, value):
        validate_password(value)
        return value

    def create(self, validated_data):
        email = validated_data['email']
        user = User.objects.create_user(
            username=email,
            email=email,
            password=validated_data['password'],
            first_name=validated_data.get('first_name', ''),
            last_name=validated_data.get('last_name', ''),
        )
        return PortalProfile.objects.create(
            user=user,
            role=PortalProfile.Role.BORROWER,
            phone=validated_data.get('phone', ''),
        )


class PortalTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Login with email + password. Username in DB equals email."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop(self.username_field, None)
        self.fields['email'] = serializers.EmailField(write_only=True)

    def validate(self, attrs):
        email = attrs.get('email', '').strip().lower()
        password = attrs.get('password')

        authenticate_kwargs = {
            User.USERNAME_FIELD: email,
            'password': password,
        }
        request = self.context.get('request')
        if request is not None:
            authenticate_kwargs['request'] = request

        user = authenticate(**authenticate_kwargs)
        if not api_settings.USER_AUTHENTICATION_RULE(user):
            raise exceptions.AuthenticationFailed(
                self.error_messages['no_active_account'],
                'no_active_account',
            )

        self.user = user
        refresh = RefreshToken.for_user(user)
        data = {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }
        if api_settings.UPDATE_LAST_LOGIN:
            update_last_login(None, user)

        profile, _ = PortalProfile.objects.get_or_create(
            user=user,
            defaults={'role': PortalProfile.Role.BORROWER},
        )
        data['user'] = PortalProfileSerializer(profile).data
        return data
