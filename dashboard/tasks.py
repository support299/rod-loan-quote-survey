import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def sync_dashboard():
    """Full GHL pull (01 + 02 pipelines), then re-evaluate exceptions."""
    from .issues import refresh_issues
    from .models import SyncRun
    from .sync import sync_all

    # One full sync at a time; a run stuck >30 min (dead worker) no longer blocks.
    if SyncRun.objects.filter(kind="full", status="running", started_at__gte=timezone.now() - timedelta(minutes=30)).exists():
        logger.info("Dashboard sync already running; skipped")
        return {"skipped": True}
    run = sync_all()
    issues = refresh_issues()
    return {"run": run.id, "stats": run.stats, "issues": issues}


@shared_task
def ingest_ghl_webhook(payload):
    from .sync import ingest_webhook

    return ingest_webhook(payload)


@shared_task
def refresh_dashboard_issues():
    from .issues import refresh_issues

    return refresh_issues()


@shared_task
def send_dashboard_reminders():
    from .issues import send_reminders

    return send_reminders()


@shared_task
def snapshot_forecast():
    """Preserve the daily month-end forecast (brief: keep daily forecast snapshots)."""
    from .metrics import load_dataset, local_today, month_end_forecast
    from .models import ForecastSnapshot

    ds = load_dataset(None, system=True)
    data = month_end_forecast(ds)
    data.pop("deal_ids", None)
    data.pop("missing_revenue_ids", None)
    data["taken_at"] = timezone.now().isoformat()
    day = local_today(ds.tz)
    ForecastSnapshot.objects.update_or_create(date=day, defaults={"data": data})
    return {"date": day.isoformat()}
