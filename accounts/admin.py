from django.contrib import admin
from accounts.models import GHLAuthCredentials, GHLCustomField


# Register your models here.
admin.site.register(GHLAuthCredentials)
admin.site.register(GHLCustomField)