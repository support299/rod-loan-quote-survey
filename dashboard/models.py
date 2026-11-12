from django.conf import settings
from django.db import models

from .constants import (
    DEFAULT_STAGE_PROBABILITIES,
    FUNDED_INDEX,
    ISSUE_TYPE_CHOICES,
    LOST_STATUSES,
    ROLE_CHOICES,
)


def _default_stage_probabilities():
    return dict(DEFAULT_STAGE_PROBABILITIES)


class CompanySettings(models.Model):
    """Singleton (pk=1) holding targets, thresholds and forecast assumptions."""

    timezone = models.CharField(max_length=64, default="America/New_York")
    # Rod: "$10M per month" — treated as funded loan volume (principal).
    monthly_volume_target = models.DecimalField(max_digits=14, decimal_places=2, default=10_000_000)
    monthly_revenue_target = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    default_ae_volume_target = models.DecimalField(max_digits=14, decimal_places=2, default=1_000_000)
    # Used when neither the loan's "AE Commission" field nor the AE's own rate is set.
    default_ae_commission_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    stall_days_default = models.PositiveIntegerField(default=7)
    stall_days_by_stage = models.JSONField(default=dict, blank=True)
    # Open deals idle longer than this are "dormant": listed separately at low severity
    # so ~1,100 long-idle 01-pipeline leads do not bury the real blockers.
    dormant_days = models.PositiveIntegerField(default=60)
    no_progress_days = models.PositiveIntegerField(default=14)
    incomplete_application_days = models.PositiveIntegerField(default=10)
    relationship_inactivity_days = models.PositiveIntegerField(default=90)
    stale_sync_minutes = models.PositiveIntegerField(default=60)
    stage_probabilities = models.JSONField(default=_default_stage_probabilities, blank=True)
    # First moment the dashboard started recording stage history.
    coverage_start = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Company settings"

    def __str__(self):
        return "Company settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def stall_days_for(self, stage_key):
        try:
            return int(self.stall_days_by_stage.get(stage_key, self.stall_days_default))
        except (TypeError, ValueError):
            return self.stall_days_default


class DashboardProfile(models.Model):
    """Dashboard role + link between a login and the matching GHL user / dropdown name."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="dashboard_profile")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    ghl_user_id = models.CharField(max_length=64, blank=True, default="")
    # Value used in the GHL "Account Executive" / "Loan Analysts" dropdowns, e.g. "Anna".
    ae_name = models.CharField(max_length=128, blank=True, default="")
    analyst_name = models.CharField(max_length=128, blank=True, default="")
    commission_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    monthly_volume_target = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    monthly_revenue_target = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.get_username()} ({self.role})"

    @property
    def display_name(self):
        return self.user.get_full_name() or self.ae_name or self.analyst_name or self.user.get_username()


class MonthlyTarget(models.Model):
    """Per-month override of the company (profile=None) or an AE's target."""

    month = models.DateField(help_text="First day of the month")
    profile = models.ForeignKey(DashboardProfile, null=True, blank=True, on_delete=models.CASCADE, related_name="targets")
    volume_target = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    revenue_target = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    class Meta:
        unique_together = [("month", "profile")]


class PipelineStage(models.Model):
    ghl_id = models.CharField(max_length=64, primary_key=True)
    pipeline_id = models.CharField(max_length=64, db_index=True)
    pipeline_name = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    position = models.IntegerField(default=0)
    stage_key = models.CharField(max_length=40, blank=True, default="")

    def __str__(self):
        return f"{self.pipeline_name} / {self.name}"


class GHLUser(models.Model):
    ghl_id = models.CharField(max_length=64, primary_key=True)
    name = models.CharField(max_length=255, blank=True, default="")
    email = models.CharField(max_length=255, blank=True, default="")
    role = models.CharField(max_length=64, blank=True, default="")
    synced_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name or self.ghl_id


class Deal(models.Model):
    """One GHL opportunity in the 01/02 pipelines = one loan."""

    ghl_id = models.CharField(max_length=64, primary_key=True)
    location_id = models.CharField(max_length=64, blank=True, default="")
    name = models.CharField(max_length=512, blank=True, default="")
    contact_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    contact_name = models.CharField(max_length=255, blank=True, default="")
    contact_email = models.CharField(max_length=255, blank=True, default="")
    contact_company = models.CharField(max_length=255, blank=True, default="")

    pipeline_id = models.CharField(max_length=64, blank=True, default="")
    pipeline_key = models.CharField(max_length=20, blank=True, default="")
    stage_id = models.CharField(max_length=64, blank=True, default="")
    stage_key = models.CharField(max_length=40, blank=True, default="", db_index=True)
    stage_name = models.CharField(max_length=255, blank=True, default="")
    stage_index = models.IntegerField(default=-1)
    furthest_stage_index = models.IntegerField(default=-1)
    status = models.CharField(max_length=20, blank=True, default="", db_index=True)
    in_scope = models.BooleanField(default=True, help_text="False once the deal left the 01/02 pipelines or was deleted")
    deleted = models.BooleanField(default=False)

    assigned_to = models.CharField(max_length=64, blank=True, default="")
    ae_name = models.CharField(max_length=128, blank=True, default="", db_index=True)
    analyst_name = models.CharField(max_length=128, blank=True, default="")
    analyst2_name = models.CharField(max_length=128, blank=True, default="")
    ae_at_application = models.CharField(max_length=128, blank=True, default="")
    ae_at_funding = models.CharField(max_length=128, blank=True, default="")

    loan_id = models.CharField(max_length=64, blank=True, default="")
    program = models.CharField(max_length=128, blank=True, default="")
    source = models.CharField(max_length=255, blank=True, default="")
    referral_source = models.CharField(max_length=255, blank=True, default="")
    broker_name = models.CharField(max_length=255, blank=True, default="")
    broker_email = models.CharField(max_length=255, blank=True, default="")
    entity_name = models.CharField(max_length=255, blank=True, default="")
    property_address = models.CharField(max_length=512, blank=True, default="")
    termsheet_status = models.CharField(max_length=64, blank=True, default="")
    lost_reason_id = models.CharField(max_length=64, blank=True, default="")
    ghl_loss_reason = models.CharField(max_length=255, blank=True, default="")
    ghl_loss_notes = models.TextField(blank=True, default="")

    total_loan_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    origination_fee = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    origination_points = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)
    processing_fee = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    underwriting_fee = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    broker_fee = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    ae_commission = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    expected_close_date = models.DateField(null=True, blank=True)

    ghl_created_at = models.DateTimeField(null=True, blank=True, db_index=True)
    ghl_updated_at = models.DateTimeField(null=True, blank=True)
    last_stage_change_at = models.DateTimeField(null=True, blank=True)
    last_status_change_at = models.DateTimeField(null=True, blank=True)
    funded_at = models.DateTimeField(null=True, blank=True, db_index=True)
    lost_at = models.DateTimeField(null=True, blank=True, db_index=True)

    custom_fields = models.JSONField(default=dict, blank=True)
    first_synced_at = models.DateTimeField(auto_now_add=True)
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-ghl_created_at"]

    def __str__(self):
        return self.name or self.ghl_id

    @property
    def is_funded(self):
        return self.funded_at is not None or self.stage_index >= FUNDED_INDEX

    @property
    def is_lost(self):
        return self.status in LOST_STATUSES and not self.is_funded

    @property
    def ghl_url(self):
        if not self.location_id:
            return ""
        return f"https://app.gohighlevel.com/v2/location/{self.location_id}/opportunities/list?opportunityId={self.ghl_id}"


class DealMeta(models.Model):
    """Dashboard-owned annotations; never overwritten by sync."""

    deal = models.OneToOneField(Deal, on_delete=models.CASCADE, related_name="meta", primary_key=True)
    loss_primary_reason = models.CharField(max_length=128, blank=True, default="")
    loss_secondary_reason = models.CharField(max_length=128, blank=True, default="")
    loss_notes = models.TextField(blank=True, default="")
    loss_outcome = models.CharField(max_length=40, blank=True, default="")
    competitor = models.CharField(max_length=255, blank=True, default="")
    recoverable = models.CharField(max_length=10, blank=True, default="", help_text="yes / no / maybe")
    paused = models.BooleanField(default=False)
    pause_reason = models.TextField(blank=True, default="")
    pause_review_date = models.DateField(null=True, blank=True)
    excluded = models.BooleanField(default=False, help_text="Test or duplicate record")
    exclude_reason = models.CharField(max_length=255, blank=True, default="")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)


class DealEvent(models.Model):
    """Audit trail of changes observed on a deal (stage, status, owner, ...)."""

    KIND_CHOICES = [
        ("created", "Created"),
        ("stage_change", "Stage change"),
        ("status_change", "Status change"),
        ("owner_change", "Owner change"),
        ("ae_change", "AE change"),
        ("analyst_change", "Analyst change"),
        ("close_date_change", "Expected close date change"),
        ("left_scope", "Left 01/02 pipelines"),
        ("deleted", "Deleted"),
    ]
    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=30, choices=KIND_CHOICES)
    from_value = models.CharField(max_length=255, blank=True, default="")
    to_value = models.CharField(max_length=255, blank=True, default="")
    occurred_at = models.DateTimeField(db_index=True)
    source = models.CharField(max_length=20, default="sync")
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["occurred_at", "id"]


class DealMilestone(models.Model):
    """First time a deal was observed entering a stage. Never inferred."""

    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="milestones")
    key = models.CharField(max_length=40)
    reached_at = models.DateTimeField(db_index=True)
    source = models.CharField(max_length=30, default="sync")

    class Meta:
        unique_together = [("deal", "key")]


class DealIssue(models.Model):
    """Operational exception (module 6). Resolved rows are kept as history."""

    STATUS_OPEN = "open"
    STATUS_SNOOZED = "snoozed"
    STATUS_RESOLVED = "resolved"
    STATUS_CHOICES = [(STATUS_OPEN, "Open"), (STATUS_SNOOZED, "Snoozed"), (STATUS_RESOLVED, "Resolved")]
    SEVERITY_CHOICES = [("critical", "Critical"), ("high", "High"), ("medium", "Medium"), ("low", "Low")]

    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="issues")
    issue_type = models.CharField(max_length=40, choices=ISSUE_TYPE_CHOICES, db_index=True)
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default="medium")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)
    owner = models.ForeignKey(DashboardProfile, null=True, blank=True, on_delete=models.SET_NULL, related_name="issues")
    owner_name = models.CharField(max_length=128, blank=True, default="")
    details = models.JSONField(default=dict, blank=True)
    days_overdue = models.IntegerField(default=0)
    next_action = models.TextField(blank=True, default="")
    deadline = models.DateField(null=True, blank=True)
    snoozed_until = models.DateTimeField(null=True, blank=True)
    snooze_reason = models.TextField(blank=True, default="")
    resolution_note = models.TextField(blank=True, default="")
    auto_resolved = models.BooleanField(default=False)
    detected_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-detected_at"]


class DealNote(models.Model):
    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    body = models.TextField()
    ghl_note_id = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class DealTask(models.Model):
    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True, default="")
    assignee = models.ForeignKey(DashboardProfile, null=True, on_delete=models.SET_NULL, related_name="tasks")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    issue = models.ForeignKey(DealIssue, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    due_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    reminded_at = models.DateTimeField(null=True, blank=True)
    ghl_task_id = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["completed_at", "due_at"]


class Notification(models.Model):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="dashboard_notifications")
    kind = models.CharField(max_length=40)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True, default="")
    deal = models.ForeignKey(Deal, null=True, blank=True, on_delete=models.CASCADE)
    issue = models.ForeignKey(DealIssue, null=True, blank=True, on_delete=models.CASCADE)
    task = models.ForeignKey(DealTask, null=True, blank=True, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class ActivityLog(models.Model):
    """Who did what in the dashboard (audit for corrections, resolutions, snoozes)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    deal = models.ForeignKey(Deal, null=True, blank=True, on_delete=models.CASCADE, related_name="activity")
    issue = models.ForeignKey(DealIssue, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=60)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class ForecastSnapshot(models.Model):
    """Daily copy of the month-end forecast so history can be compared later."""

    date = models.DateField(unique=True)
    data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]


class SyncRun(models.Model):
    KIND_CHOICES = [("full", "Full"), ("webhook", "Webhook")]
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    status = models.CharField(max_length=10, default="running")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    stats = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-started_at"]
