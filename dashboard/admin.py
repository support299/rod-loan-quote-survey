from django.contrib import admin

from .models import (
    ActivityLog,
    CompanySettings,
    DashboardProfile,
    Deal,
    DealEvent,
    DealIssue,
    DealMeta,
    DealMilestone,
    ForecastSnapshot,
    GHLUser,
    MonthlyTarget,
    PipelineStage,
    SyncRun,
)


@admin.register(DashboardProfile)
class DashboardProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "ae_name", "analyst_name", "ghl_user_id", "commission_pct")
    list_filter = ("role",)
    search_fields = ("user__username", "user__email", "ae_name", "analyst_name")


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = ("name", "stage_name", "status", "ae_name", "analyst_name", "total_loan_amount", "funded_at", "in_scope")
    list_filter = ("pipeline_key", "stage_key", "status", "in_scope")
    search_fields = ("ghl_id", "name", "contact_name", "loan_id")
    readonly_fields = [f.name for f in Deal._meta.fields]


@admin.register(DealEvent)
class DealEventAdmin(admin.ModelAdmin):
    list_display = ("deal", "kind", "from_value", "to_value", "occurred_at", "source")
    list_filter = ("kind", "source")
    search_fields = ("deal__ghl_id", "deal__name")


@admin.register(DealMilestone)
class DealMilestoneAdmin(admin.ModelAdmin):
    list_display = ("deal", "key", "reached_at", "source")
    list_filter = ("key", "source")


@admin.register(DealIssue)
class DealIssueAdmin(admin.ModelAdmin):
    list_display = ("deal", "issue_type", "severity", "status", "owner", "days_overdue", "detected_at")
    list_filter = ("issue_type", "severity", "status")


@admin.register(SyncRun)
class SyncRunAdmin(admin.ModelAdmin):
    list_display = ("kind", "status", "started_at", "finished_at")
    list_filter = ("kind", "status")


admin.site.register(CompanySettings)
admin.site.register(MonthlyTarget)
admin.site.register(PipelineStage)
admin.site.register(GHLUser)
admin.site.register(DealMeta)
admin.site.register(ForecastSnapshot)
admin.site.register(ActivityLog)
