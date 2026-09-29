from django.contrib import admin

from .models import PortalProfile


@admin.register(PortalProfile)
class PortalProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'phone', 'ghl_contact_id', 'created_at')
    list_filter = ('role',)
    search_fields = ('user__email', 'user__first_name', 'user__last_name', 'phone', 'ghl_contact_id')
    raw_id_fields = ('user',)
