import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def sync_loan_quote_survey_to_ghl(self, request_id, location_id=None):
    """
    Celery: ensure Loan Quote Survey contact custom fields exist, then write
    submission values onto the opportunity's contact.
    """
    from documents.survey_sync import sync_loan_quote_survey_submission

    try:
        return sync_loan_quote_survey_submission(request_id, location_id=location_id)
    except Exception as exc:
        logger.exception(
            "sync_loan_quote_survey_to_ghl failed for %s (location=%s)",
            request_id,
            location_id,
        )
        raise self.retry(exc=exc)


def enqueue_loan_quote_survey_ghl_sync(request_id, location_id=None):
    """
    Queue Celery task; if broker/worker unavailable, run sync inline so submit still works.
    """
    try:
        sync_loan_quote_survey_to_ghl.delay(request_id, location_id)
        logger.info("Queued Loan Quote Survey GHL sync for %s", request_id)
        return "queued"
    except Exception as e:
        logger.warning(
            "Celery enqueue failed for Loan Quote Survey sync (%s); running inline: %s",
            request_id,
            e,
        )
        from documents.survey_sync import sync_loan_quote_survey_submission

        sync_loan_quote_survey_submission(request_id, location_id=location_id)
        return "inline"
