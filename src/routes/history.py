"""Upload history routes — view, filter, retry, cancel, and manage scheduled batches and master campaigns."""

from datetime import datetime, timezone, timedelta
from collections import defaultdict
import zoneinfo
import shutil
import threading
import logging
import asyncio
from fastapi import APIRouter, Request, Query, Body
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from src.models.database import get_session_factory, get_setting, Account, Batch, BatchStatus, ActivityLog
from src.config import get_account_dir

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


def _get_batch_schedule_map(session) -> dict[int, str]:
    """Calculate the projected scheduled upload date and time for all pending batches."""
    from apscheduler.triggers.cron import CronTrigger

    global_cron = get_setting(session, "upload_schedule", "0 9 */2 * *")
    tz_str = get_setting(session, "timezone", "Africa/Lagos")
    try:
        tz = zoneinfo.ZoneInfo(tz_str)
    except Exception:
        tz = zoneinfo.ZoneInfo("Africa/Lagos")

    accounts = session.query(Account).all()
    schedule_map = {}

    for account in accounts:
        cron_expr = (account.schedule_cron or "").strip() or global_cron
        pending_batches = (
            session.query(Batch)
            .filter(Batch.account_id == account.id, Batch.status == BatchStatus.PENDING)
            .order_by(Batch.created_at.asc())
            .all()
        )
        if not pending_batches:
            continue

        try:
            parts = cron_expr.strip().split()
            if len(parts) == 5:
                trigger = CronTrigger.from_crontab(cron_expr, timezone=tz)
                current_time = datetime.now(tz)
                for b in pending_batches:
                    next_fire = trigger.get_next_fire_time(None, current_time)
                    if next_fire:
                        schedule_map[b.id] = next_fire.strftime("%Y-%m-%d %H:%M %Z")
                        current_time = next_fire + timedelta(seconds=1)
                    else:
                        schedule_map[b.id] = "Next scheduled cycle"
            else:
                for b in pending_batches:
                    schedule_map[b.id] = "Next scheduled cycle"
        except Exception:
            for b in pending_batches:
                schedule_map[b.id] = "Next scheduled cycle"

    return schedule_map


def _upload_batch_worker(batch_id: int, account_name: str, batch_filename: str):
    """Background worker that executes Playwright upload for a single specific batch."""
    from src.services.uploader import upload_csv_to_pinterest
    from src.services.notifier import notify_upload_success, notify_upload_failed

    factory = get_session_factory()
    dirs = get_account_dir(account_name)
    queue_file = dirs / "queue" / batch_filename
    failed_file = dirs / "failed" / batch_filename

    if failed_file.exists() and not queue_file.exists():
        shutil.move(str(failed_file), str(queue_file))

    with factory() as session:
        account = session.query(Account).filter(Account.name == account_name).first()
        proxy_url = account.proxy_url if account else None

    if not queue_file.exists():
        with factory() as session:
            b = session.query(Batch).filter(Batch.id == batch_id).first()
            if b:
                b.status = BatchStatus.FAILED
                b.error_message = "Batch file not found in queue directory"
                session.commit()
        return

    logger.info(f"Starting Upload Now worker for batch '{batch_filename}' ({account_name})...")
    success, error_msg = upload_csv_to_pinterest(queue_file, account_name, proxy_url)

    with factory() as session:
        b = session.query(Batch).filter(Batch.id == batch_id).first()
        if not b:
            return

        if success:
            b.status = BatchStatus.DONE
            b.uploaded_at = datetime.now(timezone.utc)
            b.error_message = None
            session.commit()

            done_file = dirs / "done" / batch_filename
            if queue_file.exists():
                shutil.move(str(queue_file), str(done_file))

            session.add(ActivityLog(
                account_id=b.account_id,
                event_type="batch_uploaded",
                message=f"Uploaded '{batch_filename}' ({b.pin_count} pins) via Upload Now",
            ))
            session.commit()

            try:
                asyncio.run(notify_upload_success(account_name, batch_filename, b.pin_count))
            except Exception as e:
                logger.error(f"Telegram notification error: {e}")
        else:
            b.status = BatchStatus.FAILED
            b.error_message = error_msg
            session.commit()

            if queue_file.exists():
                shutil.move(str(queue_file), str(failed_file))

            session.add(ActivityLog(
                account_id=b.account_id,
                event_type="upload_failed",
                message=f"Upload failed for '{batch_filename}': {error_msg}",
            ))
            session.commit()

            try:
                asyncio.run(notify_upload_failed(account_name, batch_filename, error_msg))
            except Exception as e:
                logger.error(f"Telegram notification error: {e}")

        # Check remaining queue level and notify if low
        remaining_pending = (
            session.query(Batch)
            .filter_by(account_id=b.account_id, status=BatchStatus.PENDING)
            .all()
        )
        remaining_count = len(remaining_pending)
        remaining_pins = sum(x.pin_count for x in remaining_pending)
        try:
            threshold = int(get_setting(session, "low_queue_threshold", "2"))
        except Exception:
            threshold = 2

        if remaining_count <= threshold:
            from src.services.notifier import notify_low_queue
            try:
                asyncio.run(notify_low_queue(account_name, remaining_count, remaining_pins))
            except Exception as e:
                logger.error(f"Telegram low queue notification error: {e}")


@router.get("/history", response_class=HTMLResponse)
async def history_page(
    request: Request,
    account_id: int = Query(0),
    status: str = Query(""),
    view_mode: str = Query("grouped"),
    page: int = Query(1),
):
    per_page = 25
    factory = get_session_factory()
    with factory() as session:
        accounts = session.query(Account).all()
        account_list = [{"id": a.id, "name": a.name} for a in accounts]
        account_name_map = {a.id: a.name for a in accounts}
        schedule_map = _get_batch_schedule_map(session)

        # Base query with filters
        query = session.query(Batch)
        if account_id:
            query = query.filter(Batch.account_id == account_id)
        if status:
            try:
                batch_status = BatchStatus(status)
                query = query.filter(Batch.status == batch_status)
            except ValueError:
                pass

        total = query.count()
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))

        # Flat Batches for flat view
        flat_batches = (
            query
            .order_by(Batch.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        batch_list = []
        for b in flat_batches:
            acct_name = account_name_map.get(b.account_id, "Unknown")
            scheduled_time = schedule_map.get(b.id, "")
            batch_list.append({
                "id": b.id,
                "created_at": b.created_at.strftime("%Y-%m-%d %H:%M") if b.created_at else "",
                "uploaded_at": b.uploaded_at.strftime("%Y-%m-%d %H:%M") if b.uploaded_at else "",
                "scheduled_time": scheduled_time,
                "account_name": acct_name,
                "account_id": b.account_id,
                "filename": b.filename,
                "original_filename": b.original_filename or "",
                "pin_count": b.pin_count,
                "status": b.status.value if b.status else "unknown",
                "error_message": b.error_message or "",
            })

        # Build Master Groups (all matching batches grouped by master upload)
        all_matching_batches = query.order_by(Batch.created_at.desc()).all()
        groups_dict = defaultdict(list)
        for b in all_matching_batches:
            created_minute = b.created_at.strftime("%Y-%m-%d %H:%M") if b.created_at else ""
            key = (b.account_id, b.original_filename or "Bulk Upload", created_minute)
            groups_dict[key].append(b)

        master_groups = []
        for idx, ((acct_id, orig_name, created_str), b_list) in enumerate(groups_dict.items()):
            total_batches = len(b_list)
            total_pins = sum(b.pin_count for b in b_list)
            done_count = sum(1 for b in b_list if b.status == BatchStatus.DONE)
            failed_count = sum(1 for b in b_list if b.status == BatchStatus.FAILED)
            pending_count = sum(1 for b in b_list if b.status == BatchStatus.PENDING)
            processing_count = sum(1 for b in b_list if b.status == BatchStatus.PROCESSING)
            cancelled_count = sum(1 for b in b_list if b.status == BatchStatus.CANCELLED)

            pct_done = int((done_count / total_batches) * 100) if total_batches > 0 else 0

            # Sort batches naturally by filename
            sorted_b = sorted(b_list, key=lambda x: x.filename)
            b_details = []
            first_pending_id = None

            for b in sorted_b:
                if b.status == BatchStatus.PENDING and first_pending_id is None:
                    first_pending_id = b.id

                b_details.append({
                    "id": b.id,
                    "filename": b.filename,
                    "pin_count": b.pin_count,
                    "status": b.status.value if b.status else "unknown",
                    "error_message": b.error_message or "",
                    "uploaded_at": b.uploaded_at.strftime("%Y-%m-%d %H:%M") if b.uploaded_at else "",
                    "scheduled_time": schedule_map.get(b.id, ""),
                })

            master_groups.append({
                "group_id": f"group_{idx}_{acct_id}",
                "account_id": acct_id,
                "account_name": account_name_map.get(acct_id, "Unknown"),
                "original_filename": orig_name,
                "created_at": created_str,
                "total_batches": total_batches,
                "total_pins": total_pins,
                "done_count": done_count,
                "failed_count": failed_count,
                "pending_count": pending_count,
                "processing_count": processing_count,
                "cancelled_count": cancelled_count,
                "pct_done": pct_done,
                "first_pending_id": first_pending_id,
                "batches": b_details,
            })

    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "active_page": "history",
            "batches": batch_list,
            "master_groups": master_groups,
            "accounts": account_list,
            "filter_account_id": account_id,
            "filter_status": status,
            "view_mode": view_mode,
            "page": page,
            "total_pages": total_pages,
            "total": total,
        }
    )


@router.post("/api/history/{batch_id}/upload-now", response_class=JSONResponse)
async def upload_now_batch(batch_id: int):
    """Trigger immediate Playwright upload for a specific batch."""
    factory = get_session_factory()
    with factory() as session:
        batch = session.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return JSONResponse({"error": "Batch not found"}, status_code=404)

        if batch.status == BatchStatus.DONE:
            return JSONResponse({"error": "This batch is already completed and uploaded."}, status_code=400)

        if batch.status == BatchStatus.PROCESSING:
            return JSONResponse({"error": "This batch is currently uploading."}, status_code=400)

        batch_filename = str(batch.filename)
        account = session.query(Account).filter(Account.id == batch.account_id).first()
        if not account:
            return JSONResponse({"error": "Account not found"}, status_code=404)

        account_name = str(account.name)
        dirs = get_account_dir(account_name)
        queue_file = dirs / "queue" / batch_filename
        failed_file = dirs / "failed" / batch_filename

        # Ensure file is in queue/ folder
        if failed_file.exists() and not queue_file.exists():
            shutil.move(str(failed_file), str(queue_file))

        batch.status = BatchStatus.PROCESSING
        batch.error_message = None
        session.commit()

    thread = threading.Thread(
        target=_upload_batch_worker,
        args=(batch_id, account_name, batch_filename),
        daemon=True,
    )
    thread.start()

    return {"success": True, "message": f"🚀 Uploading batch '{batch_filename}' to Pinterest now..."}


@router.post("/api/history/{batch_id}/cancel", response_class=JSONResponse)
async def cancel_batch(batch_id: int):
    """Cancel a scheduled pending batch and remove it from the active upload queue."""
    factory = get_session_factory()
    with factory() as session:
        batch = session.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return JSONResponse({"error": "Batch not found"}, status_code=404)

        if batch.status == BatchStatus.DONE:
            return JSONResponse({"error": "Cannot cancel a batch that is already uploaded to Pinterest."}, status_code=400)

        batch_filename = str(batch.filename)
        account = session.query(Account).filter(Account.id == batch.account_id).first()
        account_name = account.name if account else "Unknown"

        # Move file out of queue/ to failed/ so scheduler ignores it
        if account:
            dirs = get_account_dir(account.name)
            queue_file = dirs / "queue" / batch_filename
            failed_file = dirs / "failed" / batch_filename
            if queue_file.exists():
                shutil.move(str(queue_file), str(failed_file))

        batch.status = BatchStatus.CANCELLED
        batch.error_message = "Cancelled by user"

        session.add(ActivityLog(
            account_id=account.id if account else None,
            event_type="batch_cancelled",
            message=f"Cancelled batch '{batch_filename}' for account '{account_name}'",
        ))
        session.commit()

    return {"success": True, "message": f"Batch {batch_filename} has been cancelled and removed from queue."}


@router.post("/api/history/{batch_id}/requeue", response_class=JSONResponse)
async def requeue_batch(batch_id: int):
    """Re-queue a cancelled or failed batch back to pending for scheduled upload."""
    factory = get_session_factory()
    with factory() as session:
        batch = session.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return JSONResponse({"error": "Batch not found"}, status_code=404)

        if batch.status == BatchStatus.DONE:
            return JSONResponse({"error": "Batch already uploaded"}, status_code=400)

        batch_filename = str(batch.filename)
        account = session.query(Account).filter(Account.id == batch.account_id).first()
        if not account:
            return JSONResponse({"error": "Account not found"}, status_code=404)

        dirs = get_account_dir(account.name)
        queue_file = dirs / "queue" / batch_filename
        failed_file = dirs / "failed" / batch_filename

        if failed_file.exists():
            shutil.move(str(failed_file), str(queue_file))

        batch.status = BatchStatus.PENDING
        batch.error_message = None
        batch.uploaded_at = None

        session.add(ActivityLog(
            account_id=account.id,
            event_type="batch_requeued",
            message=f"Re-queued batch '{batch_filename}' for scheduled upload",
        ))
        session.commit()

    return {"success": True, "message": f"Batch {batch_filename} re-queued for scheduled upload!"}


@router.post("/api/history/{batch_id}/retry", response_class=JSONResponse)
async def retry_batch(batch_id: int):
    """Immediately retry a failed or cancelled batch."""
    return await upload_now_batch(batch_id)


@router.post("/api/history/cancel-group", response_class=JSONResponse)
async def cancel_group(data: dict = Body(...)):
    """Cancel all pending batches in a master upload group."""
    account_id = data.get("account_id")
    original_filename = data.get("original_filename")

    if not account_id:
        return JSONResponse({"error": "Missing account_id"}, status_code=400)

    factory = get_session_factory()
    cancelled_count = 0

    with factory() as session:
        query = session.query(Batch).filter(
            Batch.account_id == account_id,
            Batch.status == BatchStatus.PENDING,
        )
        if original_filename:
            query = query.filter(Batch.original_filename == original_filename)

        pending_batches = query.all()
        if not pending_batches:
            return {"success": True, "message": "No pending batches to cancel."}

        account = session.query(Account).filter(Account.id == account_id).first()
        account_name = account.name if account else "Unknown"
        dirs = get_account_dir(account_name)

        for batch in pending_batches:
            batch_filename = str(batch.filename)
            queue_file = dirs / "queue" / batch_filename
            failed_file = dirs / "failed" / batch_filename
            if queue_file.exists():
                shutil.move(str(queue_file), str(failed_file))

            batch.status = BatchStatus.CANCELLED
            batch.error_message = "Cancelled by user"
            cancelled_count += 1

        session.add(ActivityLog(
            account_id=account_id,
            event_type="group_cancelled",
            message=f"Cancelled {cancelled_count} pending batch(es) for '{original_filename or 'Queue'}'",
        ))
        session.commit()

    return {"success": True, "message": f"Successfully cancelled {cancelled_count} scheduled batch(es)."}


@router.post("/api/history/requeue-group", response_class=JSONResponse)
async def requeue_group(data: dict = Body(...)):
    """Re-queue all cancelled or failed batches in a master upload group."""
    account_id = data.get("account_id")
    original_filename = data.get("original_filename")

    if not account_id:
        return JSONResponse({"error": "Missing account_id"}, status_code=400)

    factory = get_session_factory()
    requeued_count = 0

    with factory() as session:
        query = session.query(Batch).filter(
            Batch.account_id == account_id,
            Batch.status.in_([BatchStatus.CANCELLED, BatchStatus.FAILED]),
        )
        if original_filename:
            query = query.filter(Batch.original_filename == original_filename)

        inactive_batches = query.all()
        if not inactive_batches:
            return {"success": True, "message": "No cancelled or failed batches to re-queue."}

        account = session.query(Account).filter(Account.id == account_id).first()
        account_name = account.name if account else "Unknown"
        dirs = get_account_dir(account_name)

        for batch in inactive_batches:
            batch_filename = str(batch.filename)
            queue_file = dirs / "queue" / batch_filename
            failed_file = dirs / "failed" / batch_filename
            if failed_file.exists():
                shutil.move(str(failed_file), str(queue_file))

            batch.status = BatchStatus.PENDING
            batch.error_message = None
            batch.uploaded_at = None
            requeued_count += 1

        session.add(ActivityLog(
            account_id=account_id,
            event_type="group_requeued",
            message=f"Re-queued {requeued_count} batch(es) for '{original_filename or 'Queue'}'",
        ))
        session.commit()

    return {"success": True, "message": f"Successfully re-queued {requeued_count} batch(es) for scheduled upload!"}


@router.post("/api/history/{batch_id}/delete", response_class=JSONResponse)
async def delete_batch(batch_id: int):
    """Delete a batch entirely from database and remove its file from disk."""
    factory = get_session_factory()
    with factory() as session:
        batch = session.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return JSONResponse({"error": "Batch not found"}, status_code=404)

        batch_filename = str(batch.filename)
        account = session.query(Account).filter(Account.id == batch.account_id).first()
        if account:
            dirs = get_account_dir(account.name)
            for subdir in ["queue", "done", "failed"]:
                f_path = dirs / subdir / batch_filename
                if f_path.exists():
                    f_path.unlink(missing_ok=True)

        session.delete(batch)
        session.commit()

    return {"success": True, "message": "Batch deleted successfully."}


@router.post("/api/history/retry-all-failed", response_class=JSONResponse)
async def retry_all_failed():
    factory = get_session_factory()
    retried = 0

    with factory() as session:
        failed_batches = session.query(Batch).filter(Batch.status == BatchStatus.FAILED).all()
        accounts_to_run = set()

        for batch in failed_batches:
            account = session.query(Account).filter(Account.id == batch.account_id).first()
            if not account:
                continue

            batch_filename = str(batch.filename)
            failed_dir = get_account_dir(account.name) / "failed"
            queue_dir = get_account_dir(account.name) / "queue"
            failed_file = failed_dir / batch_filename
            queue_file = queue_dir / batch_filename

            if failed_file.exists():
                shutil.move(str(failed_file), str(queue_file))

            batch.status = BatchStatus.PENDING
            batch.error_message = None
            batch.uploaded_at = None
            accounts_to_run.add(account.name)
            retried += 1

        session.commit()

    for acct_name in accounts_to_run:
        def _run(name=acct_name):
            try:
                from src.services.scheduler import run_single_account
                run_single_account(name)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Retry all failed: {e}")

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    return {"success": True, "message": f"Retrying {retried} failed batches..."}


@router.get("/api/history/{batch_id}/download")
async def download_batch(batch_id: int):
    factory = get_session_factory()
    with factory() as session:
        batch = session.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return JSONResponse({"error": "Batch not found"}, status_code=404)

        batch_filename = str(batch.filename)
        account = session.query(Account).filter(Account.id == batch.account_id).first()
        if not account:
            return JSONResponse({"error": "Account not found"}, status_code=404)

        for subdir in ["queue", "done", "failed"]:
            filepath = get_account_dir(account.name) / subdir / batch_filename
            if filepath.exists():
                return FileResponse(filepath, filename=batch_filename, media_type="text/csv")

    return JSONResponse({"error": "File not found on disk"}, status_code=404)
