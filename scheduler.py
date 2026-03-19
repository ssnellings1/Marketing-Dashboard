"""
Background scheduler — runs sync jobs automatically.
Gmail is checked every hour; ad platforms every 6 hours.
"""
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)
_scheduler = None


def start_scheduler(app):
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    _scheduler = BackgroundScheduler(daemon=True)

    def run_gmail():
        with app.app_context():
            try:
                from integrations.email_parser import check_gmail
                check_gmail()
            except Exception as e:
                logger.error(f"Gmail sync error: {e}")

    def run_quickbooks():
        with app.app_context():
            try:
                from integrations.quickbooks import sync_quickbooks
                sync_quickbooks()
            except Exception as e:
                logger.error(f"QuickBooks sync error: {e}")

    def run_google_ads():
        with app.app_context():
            try:
                from integrations.google_ads import sync_google_ads
                sync_google_ads()
            except Exception as e:
                logger.error(f"Google Ads sync error: {e}")

    def run_meta_ads():
        with app.app_context():
            try:
                from integrations.meta_ads import sync_meta_ads
                sync_meta_ads()
            except Exception as e:
                logger.error(f"Meta Ads sync error: {e}")

    # Gmail: every hour
    _scheduler.add_job(run_gmail, IntervalTrigger(hours=1), id="gmail",
                       max_instances=1, coalesce=True)

    # Ad platforms: every 6 hours
    _scheduler.add_job(run_quickbooks, IntervalTrigger(hours=6), id="quickbooks",
                       max_instances=1, coalesce=True)
    _scheduler.add_job(run_google_ads, IntervalTrigger(hours=6), id="google_ads",
                       max_instances=1, coalesce=True)
    _scheduler.add_job(run_meta_ads,   IntervalTrigger(hours=6), id="meta_ads",
                       max_instances=1, coalesce=True)

    _scheduler.start()
    logger.info("Background scheduler started.")
