"""Scheduler module for automated upload cycles.

Supports per-account independent scheduling with APScheduler and built-in thread runner fallback.
Allows each account to have its own schedule cadence (e.g. Account A every 2 days, Account B every 5 days).
"""

import logging
import asyncio
import threading
import time
from datetime import datetime, timezone

from src.models.database import get_session_factory, Account, Batch, BatchStatus, get_setting
from src.services.session import check_session
from src.services.uploader import upload_csv_to_pinterest
from src.services.notifier import notify_upload_success, notify_upload_failed, notify_session_expired, notify_daily_summary
from src.config import ensure_account_dirs

logger = logging.getLogger(__name__)


def _get_account_job_id(account_id: int) -> str:
    return f"account_job_{account_id}"


class SimpleScheduler:
    """Lightweight self-contained background scheduler supporting multi-account crons."""
    def __init__(self):
        self.running = False
        self.timezone = "Africa/Lagos"
        self._thread = None
        self._stop_event = threading.Event()
        self.last_run_times = {}

    def start(self):
        self.running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Native background scheduler started")

    def shutdown(self, wait=False):
        self.running = False
        self._stop_event.set()
        if self._thread and wait:
            self._thread.join(timeout=5)

    def pause(self):
        self.running = False

    def resume(self):
        self.running = True

    def _run_loop(self):
        while not self._stop_event.is_set():
            if self.running:
                now = datetime.now()
                session_factory = get_session_factory()
                with session_factory() as session:
                    global_cron = get_setting(session, "upload_schedule", "0 9 */2 * *")
                    accounts = session.query(Account).filter_by(enabled=True).all()

                    for acct in accounts:
                        cron_expr = (acct.schedule_cron or "").strip() or global_cron
                        if self._matches_cron(now, cron_expr):
                            key = f"{acct.id}_{now.strftime('%Y-%m-%d %H:%M')}"
                            if key not in self.last_run_times:
                                self.last_run_times[key] = True
                                # Clean old keys
                                if len(self.last_run_times) > 100:
                                    self.last_run_times.clear()
                                try:
                                    run_single_account(acct.name, max_batches=1)
                                except Exception as e:
                                    logger.error(f"Error executing upload for {acct.name}: {e}")

            time.sleep(20)

    def _matches_cron(self, dt: datetime, expr: str) -> bool:
        try:
            parts = expr.strip().split()
            if len(parts) != 5:
                return False
            min_p, hr_p, dom_p, mon_p, dow_p = parts

            def _match(val: int, pattern: str) -> bool:
                if pattern == "*":
                    return True
                if "/" in pattern:
                    _, step = pattern.split("/")
                    return val % int(step) == 0
                if "," in pattern:
                    return val in [int(x) for x in pattern.split(",") if x.isdigit()]
                if "-" in pattern:
                    start, end = pattern.split("-")
                    return int(start) <= val <= int(end)
                return val == int(pattern) if pattern.isdigit() else False

            cron_dow = (dt.weekday() + 1) % 7

            return (
                _match(dt.minute, min_p)
                and _match(dt.hour, hr_p)
                and _match(dt.day, dom_p)
                and _match(dt.month, mon_p)
                and _match(cron_dow, dow_p)
            )
        except Exception:
            return False


def create_scheduler():
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        return BackgroundScheduler()
    except ImportError:
        return SimpleScheduler()


def sync_account_schedules(scheduler):
    """Synchronize all per-account scheduled jobs in APScheduler."""
    if not hasattr(scheduler, "add_job"):
        return

    session_factory = get_session_factory()
    with session_factory() as session:
        global_cron = get_setting(session, "upload_schedule", "0 9 */2 * *")
        tz = get_setting(session, "timezone", "Africa/Lagos")
        accounts = session.query(Account).all()

        from apscheduler.triggers.cron import CronTrigger

        # Remove deleted / disabled account jobs
        existing_job_ids = {j.id for j in scheduler.get_jobs()}

        for account in accounts:
            job_id = _get_account_job_id(account.id)
            if not account.enabled:
                if job_id in existing_job_ids:
                    try:
                        scheduler.remove_job(job_id)
                    except Exception:
                        pass
                continue

            cron_expr = (account.schedule_cron or "").strip() or global_cron
            try:
                trigger = CronTrigger.from_crontab(cron_expr, timezone=tz)
                scheduler.add_job(
                    run_single_account,
                    args=[account.name, 1],
                    trigger=trigger,
                    id=job_id,
                    name=f"Upload for {account.name}",
                    replace_existing=True,
                )
                logger.info(f"Scheduled job for '{account.name}' with cron: {cron_expr}")
            except Exception as e:
                logger.error(f"Failed to schedule job for '{account.name}' ({cron_expr}): {e}")


def start_scheduler(scheduler):
    """Start the scheduler and register all per-account schedules."""
    if hasattr(scheduler, "add_job"):
        sync_account_schedules(scheduler)
        try:
            scheduler.start()
            logger.info("APScheduler started with per-account jobs.")
        except Exception as e:
            logger.error(f"Failed to start APScheduler: {e}")
    else:
        scheduler.start()


def update_account_schedule(scheduler, account_id: int, cron_expression: str):
    """Update or create a specific account's schedule job."""
    if hasattr(scheduler, "add_job"):
        sync_account_schedules(scheduler)


def update_schedule(scheduler, cron_expression: str):
    """Update global default schedule and re-sync accounts that use the default."""
    if hasattr(scheduler, "add_job"):
        sync_account_schedules(scheduler)


def run_upload_cycle():
    """Fallback full-cycle runner for all enabled accounts."""
    session_factory = get_session_factory()
    with session_factory() as session:
        accounts = session.query(Account).filter_by(enabled=True).all()
        for acct in accounts:
            run_single_account(acct.name, max_batches=1)


def run_single_account(account_name: str, max_batches: int = 1):
    """Run upload for a single account. Drips up to max_batches (default 1) from the queue."""
    logger.info(f"Executing scheduled/manual upload for account '{account_name}' (max {max_batches} batch)...")
    session_factory = get_session_factory()

    with session_factory() as db_session:
        account = db_session.query(Account).filter_by(name=account_name, enabled=True).first()
        if not account:
            logger.warning(f"Account '{account_name}' not found or disabled. Skipping.")
            return

        if not check_session(account.name, account.proxy_url):
            account.session_valid = False
            db_session.commit()
            asyncio.run(notify_session_expired(account.name))
            return

        account.session_valid = True
        db_session.commit()

        pending_batches = (
            db_session.query(Batch)
            .filter_by(account_id=account.id, status=BatchStatus.PENDING)
            .order_by(Batch.created_at.asc())
            .limit(max_batches)
            .all()
        )

        if not pending_batches:
            logger.info(f"No pending batches in queue for account '{account_name}'.")
            return

        for batch in pending_batches:
            batch.status = BatchStatus.PROCESSING
            db_session.commit()

            dirs = ensure_account_dirs(account.name)
            batch_file = dirs["queue"] / batch.filename

            if not batch_file.exists():
                batch.status = BatchStatus.FAILED
                batch.error_message = "File not found on disk"
                db_session.commit()
                continue

            success, error = upload_csv_to_pinterest(batch_file, account.name, account.proxy_url)

            if success:
                batch.status = BatchStatus.DONE
                batch.uploaded_at = datetime.now(timezone.utc)
                db_session.commit()

                target = dirs["done"] / batch.filename
                batch_file.rename(target)

                asyncio.run(notify_upload_success(account.name, batch.filename, batch.pin_count))
            else:
                batch.status = BatchStatus.FAILED
                batch.error_message = error
                db_session.commit()

                if error == "session_expired":
                    account.session_valid = False
                    db_session.commit()
                    asyncio.run(notify_session_expired(account.name))
                    break

                target = dirs["failed"] / batch.filename
                batch_file.rename(target)

                asyncio.run(notify_upload_failed(account.name, batch.filename, error))

        # Check remaining queue level and alert if low
        remaining_pending = (
            db_session.query(Batch)
            .filter_by(account_id=account.id, status=BatchStatus.PENDING)
            .all()
        )
        remaining_count = len(remaining_pending)
        remaining_pins = sum(b.pin_count for b in remaining_pending)
        try:
            threshold = int(get_setting(db_session, "low_queue_threshold", "2"))
        except Exception:
            threshold = 2

        if remaining_count <= threshold:
            from src.services.notifier import notify_low_queue
            logger.warning(f"Account '{account.name}' queue running low: {remaining_count} batches left ({remaining_pins} pins).")
            asyncio.run(notify_low_queue(account.name, remaining_count, remaining_pins))


def get_account_next_run(scheduler, account_id: int) -> str | None:
    """Get formatted next run time for an account."""
    if hasattr(scheduler, "get_job"):
        job = scheduler.get_job(_get_account_job_id(account_id))
        if job and job.next_run_time:
            return job.next_run_time.strftime("%Y-%m-%d %H:%M %Z")
    return None
