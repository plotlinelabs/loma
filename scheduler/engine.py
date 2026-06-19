import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from observability.db import get_db

logger = logging.getLogger(__name__)

# APScheduler's from_crontab does NOT convert day-of-week numbering from
# standard cron convention (0=Sun,1=Mon,...,6=Sat) to Python/APScheduler
# convention (0=Mon,1=Tue,...,6=Sun).  We fix this by rewriting numeric
# day-of-week values to named days that APScheduler handles correctly.
_CRON_DOW_TO_NAME = {"0": "sun", "1": "mon", "2": "tue", "3": "wed",
                     "4": "thu", "5": "fri", "6": "sat", "7": "sun"}


def _fix_crontab_dow(expr: str) -> str:
    """Rewrite numeric day-of-week values in a crontab expression to named
    days so APScheduler interprets them correctly."""
    parts = expr.split()
    if len(parts) != 5:
        return expr
    dow = parts[4]
    # Handle ranges and lists: e.g. "1-5" → "mon-fri", "0,6" → "sun,sat"
    def _replace(token: str) -> str:
        return _CRON_DOW_TO_NAME.get(token, token)

    # Split on commas first, then handle ranges
    segments = []
    for segment in dow.split(","):
        if "-" in segment:
            bounds = segment.split("-", 1)
            segments.append(f"{_replace(bounds[0])}-{_replace(bounds[1])}")
        else:
            segments.append(_replace(segment))
    parts[4] = ",".join(segments)
    return " ".join(parts)

_scheduler: AsyncIOScheduler | None = None


async def init_scheduler():
    """Initialize the scheduler and load all active flows and tasks from MongoDB."""
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.start()

    db = get_db()
    if db is None:
        logger.warning("Observability DB not available — scheduler running with no jobs")
        return

    # Only load scheduled flows (not webhook-triggered ones).
    # Flows without trigger_type are legacy scheduled flows.
    flows = await db.flows.find({
        "status": "active",
        "$or": [
            {"trigger_type": {"$exists": False}},
            {"trigger_type": "scheduled"},
        ],
    }).to_list(None)
    for flow in flows:
        _add_job_for_flow(flow)

    # --- Usage monitoring (hourly) ---
    _scheduler.add_job(
        _run_usage_check,
        CronTrigger(minute=0, timezone="UTC"),
        id="usage_budget_check",
        replace_existing=True,
        misfire_grace_time=3600,
        max_instances=1,
    )
    logger.info("Usage check job registered (hourly at :00)")

    logger.info("Scheduler initialized with %d active flow(s)", len(flows))


def _add_job_for_flow(flow: dict):
    """Add an APScheduler job for a flow document."""
    from scheduler.executor import execute_flow

    flow_id = flow["flow_id"]

    try:
        if flow["schedule_type"] == "once":
            trigger = DateTrigger(
                run_date=flow["start_time"],
                timezone=flow.get("timezone", "UTC"),
            )
        else:
            trigger = CronTrigger.from_crontab(
                _fix_crontab_dow(flow["cron"]),
                timezone=flow.get("timezone", "UTC"),
            )
            if flow.get("end_time"):
                trigger.end_date = flow["end_time"]

        _scheduler.add_job(
            execute_flow,
            trigger=trigger,
            id=flow_id,
            args=[flow_id],
            replace_existing=True,
            misfire_grace_time=300,
            max_instances=1,
        )
        logger.info("Scheduled flow %s: %s", flow_id, flow.get("name", ""))
    except Exception:
        logger.exception("Failed to schedule flow %s", flow_id)


async def add_flow_to_scheduler(flow: dict):
    """Add a newly created/resumed flow to the live scheduler."""
    if _scheduler is None:
        logger.warning("Scheduler not initialized, cannot add flow %s", flow["flow_id"])
        return
    _add_job_for_flow(flow)


async def remove_flow_from_scheduler(flow_id: str):
    """Remove a flow from the live scheduler."""
    if _scheduler is None:
        return
    try:
        _scheduler.remove_job(flow_id)
        logger.info("Removed flow %s from scheduler", flow_id)
    except Exception:
        pass  # Job may not exist


def get_next_run_time(job_id: str):
    """Get the next scheduled run time for a job, or None."""
    if _scheduler is None:
        return None
    job = _scheduler.get_job(job_id)
    if job and job.next_run_time:
        return job.next_run_time
    return None


async def _run_usage_check():
    """Wrapper for the hourly usage budget check job."""
    try:
        from scheduler.usage_check import run_usage_check
        await run_usage_check()
    except Exception:
        logger.exception("[USAGE_CHECK] Hourly check failed")


async def shutdown_scheduler():
    """Graceful shutdown."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler shut down")
