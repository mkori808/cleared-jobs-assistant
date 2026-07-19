"""Runs refresh.run_refresh() every 72 hours in the background."""
from apscheduler.schedulers.background import BackgroundScheduler
from . import refresh

REFRESH_INTERVAL_HOURS = 72

_scheduler = None


def start_scheduler(run_immediately: bool = True):
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        refresh.run_refresh,
        "interval",
        hours=REFRESH_INTERVAL_HOURS,
        id="job_refresh",
        next_run_time=None if not run_immediately else __import__("datetime").datetime.now(),
    )
    _scheduler.start()
    return _scheduler


def trigger_now():
    """Manually trigger an immediate refresh (used by the dashboard's 'Refresh now' button)."""
    if _scheduler:
        _scheduler.modify_job("job_refresh", next_run_time=__import__("datetime").datetime.now())
