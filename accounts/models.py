from django.db import models
from django.utils import timezone


class GHLAuthCredentials(models.Model):
    user_id = models.CharField(max_length=255)
    access_token = models.TextField()
    refresh_token = models.TextField()
    expires_in = models.IntegerField()
    scope = models.TextField(null=True, blank=True)
    user_type = models.CharField(max_length=50, null=True, blank=True)
    company_id = models.CharField(max_length=255, null=True, blank=True)
    location_id = models.CharField(max_length=255, null=True, blank=True)
    timezone = models.CharField(max_length=100, null=True, blank=True, default="America/Chicago")
    location_name = models.CharField(max_length=255, null=True, blank=True)
    business_email = models.EmailField(null=True, blank=True)
    business_phone = models.CharField(max_length=255, null=True, blank=True)
    parent_id = models.CharField(
        max_length=100,
        blank=True,
        help_text="GHL parentId for media upload-file (e.g. folder/location ID)",
    )
    templates_parent_id = models.CharField(
        max_length=100,
        blank=True,
        help_text="GHL media folder id for Needs List Templates (separate from borrower uploads)",
    )
    alt_type = models.CharField(
        max_length=50,
        default="location",
        help_text="For media update/delete API (e.g. 'location')",
    )
    alt_id = models.CharField(
        max_length=100,
        blank=True,
        help_text="For media update/delete API (e.g. location ID); leave blank to use location_id",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.location_name} - {self.location_id}"



class GHLCustomField(models.Model):
    """Store custom field names and their GHL IDs for each account"""
    FIELD_TYPE_CHOICES = [
        ('text', 'Text'),
        ('url', 'URL'),
        ('number', 'Number'),
        ('date', 'Date'),
        ('dropdown', 'Dropdown'),
        ('checkbox', 'Checkbox'),
    ]
    
    account = models.ForeignKey(
        GHLAuthCredentials,
        on_delete=models.CASCADE,
        related_name='custom_fields',
        help_text="The account this custom field belongs to"
    )
    field_name = models.CharField(
        max_length=255,
        help_text="Human-readable name of the custom field (e.g., 'Quote URL', 'Invoice URL')"
    )
    ghl_field_id = models.CharField(
        max_length=255,
        help_text="The GHL custom field ID used in API calls"
    )
    field_type = models.CharField(
        max_length=50,
        choices=FIELD_TYPE_CHOICES,
        default='text',
        help_text="Type of the custom field"
    )
    description = models.TextField(
        blank=True,
        null=True,
        help_text="Optional description of what this field is used for"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this custom field mapping is currently active"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'ghl_custom_fields'
        unique_together = ['account', 'ghl_field_id']
        ordering = ['field_name']
        indexes = [
            models.Index(fields=['account', 'is_active']),
            models.Index(fields=['ghl_field_id']),
        ]
    
    def __str__(self):
        return f"{self.account.user_id} - {self.field_name} ({self.ghl_field_id})"