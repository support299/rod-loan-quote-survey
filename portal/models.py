from django.conf import settings
from django.db import models


class PortalProfile(models.Model):
    """Portal identity layered on Django auth.user — does not touch GHL flows."""

    class Role(models.TextChoices):
        BORROWER = 'borrower', 'Borrower'
        STAFF = 'staff', 'Staff'

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='portal_profile',
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.BORROWER,
    )
    phone = models.CharField(max_length=32, blank=True, default='')
    avatar_url = models.URLField(blank=True, default='')
    # Linked later when we sync GHL contacts — optional for login phase
    ghl_contact_id = models.CharField(max_length=64, blank=True, default='')
    ghl_location_id = models.CharField(max_length=64, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'portal_profiles'

    def __str__(self):
        return f'{self.user.email} ({self.role})'
