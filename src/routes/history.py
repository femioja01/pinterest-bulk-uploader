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
            trigger = CronTrigger.from_crontab(cron_expr, timezone=tz) if len(parts) == 5 else None
            current_time = datetime.now(tz)

            for b in pending_batches:
                if b.scheduled_upload_at:
                    dt = b.scheduled_upload_at
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc).astimezone(tz)
                    else:
                        dt = dt.astimezone(tz)
                    schedule_map[b.id] = dt.strftime("%Y-%m-%d %H:%M %Z")
                elif trigger:
                    next_fire = trigger.get_next_fire_time(None, current_time)
                    if next_fire:
                        schedule_map[b.id] = next_fire.strftime("%Y-%m-%d %H:%M %Z")
                        current_time = next_fire + timedelta(seconds=1)
                    else:
                        schedule_map[b.id] = "Next scheduled cycle"
                else:
                    schedule_map[b.id] = "Next scheduled cycle"
        except Exception:
            for b in pending_batches:
                if b.scheduled_upload_at:
                    dt = b.scheduled_upload_at
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc).astimezone(tz)
                    else:
                        dt = dt.astimezone(tz)
                    schedule_map[b.id] = dt.strftime("%Y-%m-%d %H:%M %Z")
                else:
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
    view_mode: str = Query("active"),
    page: int = Query(1),
):
    # Normalize old 'grouped' mode to 'active'
    if view_mode == "grouped":
        view_mode = "active"

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
            raw_sched = b.scheduled_upload_at.strftime("%Y-%m-%dT%H:%M") if b.scheduled_upload_at else ""
            batch_list.append({
                "id": b.id,
                "created_at": b.created_at.strftime("%Y-%m-%d %H:%M") if b.created_at else "",
                "uploaded_at": b.uploaded_at.strftime("%Y-%m-%d %H:%M") if b.uploaded_at else "",
                "scheduled_time": scheduled_time,
                "raw_scheduled_time": raw_sched,
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

        active_groups = []
        archived_groups = []

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

                raw_sched = b.scheduled_upload_at.strftime("%Y-%m-%dT%H:%M") if b.scheduled_upload_at else ""
                b_details.append({
                    "id": b.id,
                    "filename": b.filename,
                    "pin_count": b.pin_count,
                    "status": b.status.value if b.status else "unknown",
                    "error_message": b.error_message or "",
                    "uploaded_at": b.uploaded_at.strftime("%Y-%m-%d %H:%M") if b.uploaded_at else "",
                    "scheduled_time": schedule_map.get(b.id, ""),
                    "raw_scheduled_time": raw_sched,
                })

            group_data = {
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
            }

            # If 100% done and no pending/processing slices left, place in archive
            if pct_done == 100 and pending_count == 0 and processing_count == 0:
                archived_groups.append(group_data)
            else:
                active_groups.append(group_data)

    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "active_page": "history",
            "batches": batch_list,
            "active_groups": active_groups,
            "archived_groups": archived_groups,
            "active_count": len(active_groups),
            "archived_count": len(archived_groups),
            "total_batches_count": total,
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


@router.get("/api/history/{batch_id}/dates", response_class=JSONResponse)
async def get_batch_dates(batch_id: int):
    """Retrieve all pin rows and their current Publish Dates from a batch CSV."""
    import csv
    from src.services.splitter import detect_delimiter

    factory = get_session_factory()
    with factory() as session:
        batch = session.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return JSONResponse({"error": "Batch not found"}, status_code=404)

        account = session.query(Account).filter(Account.id == batch.account_id).first()
        if not account:
            return JSONResponse({"error": "Account not found"}, status_code=404)

        account_name = str(account.name)
        batch_filename = str(batch.filename)
        batch_status = batch.status.value if batch.status else "unknown"

    dirs = get_account_dir(account_name)
    filepath = None
    for subdir in ["queue", "failed", "done", "pins"]:
        p = dirs / subdir / batch_filename
        if p.exists():
            filepath = p
            break

    if not filepath or not filepath.exists():
        return JSONResponse({"error": f"Batch file '{batch_filename}' not found on disk."}, status_code=404)

    delimiter = detect_delimiter(filepath)
    rows = []
    headers = []

    try:
        with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.reader(f, delimiter=delimiter)
            headers = next(reader, None)
            if not headers:
                return JSONResponse({"error": "CSV file is empty"}, status_code=400)

            pub_idx = -1
            title_idx = 0
            board_idx = 2

            for idx, h in enumerate(headers):
                clean_h = h.strip().lower()
                if clean_h in ["publish date", "publish_date", "schedule date", "date", "post date"]:
                    pub_idx = idx
                elif clean_h in ["title", "pin title", "headline", "name"]:
                    title_idx = idx
                elif clean_h in ["pinterest board", "board", "board name"]:
                    board_idx = idx

            for r_idx, row in enumerate(reader):
                if not any(cell.strip() for cell in row):
                    continue
                t = row[title_idx] if title_idx < len(row) else f"Pin #{r_idx + 1}"
                b = row[board_idx] if board_idx < len(row) else ""
                d = row[pub_idx] if (pub_idx != -1 and pub_idx < len(row)) else ""
                rows.append({
                    "index": r_idx,
                    "title": t,
                    "board": b,
                    "publish_date": d,
                })
    except Exception as e:
        logger.error(f"Error reading CSV dates: {e}")
        return JSONResponse({"error": f"Failed to read CSV: {str(e)}"}, status_code=500)

    dates_found = [r["publish_date"] for r in rows if r["publish_date"]]
    first_date = dates_found[0] if dates_found else ""
    last_date = dates_found[-1] if dates_found else ""

    return {
        "success": True,
        "batch_id": batch_id,
        "filename": batch_filename,
        "account_name": account_name,
        "status": batch_status,
        "total_pins": len(rows),
        "first_date": first_date,
        "last_date": last_date,
        "rows": rows,
    }


@router.post("/api/history/{batch_id}/update-dates", response_class=JSONResponse)
async def update_batch_dates(batch_id: int, data: dict = Body(...)):
    """Update the Publish Dates and times inside a batch CSV."""
    import csv
    from src.services.splitter import detect_delimiter

    factory = get_session_factory()
    with factory() as session:
        batch = session.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return JSONResponse({"error": "Batch not found"}, status_code=404)

        if batch.status == BatchStatus.DONE:
            return JSONResponse({"error": "Cannot edit dates of already completed/uploaded batch."}, status_code=400)

        account = session.query(Account).filter(Account.id == batch.account_id).first()
        if not account:
            return JSONResponse({"error": "Account not found"}, status_code=404)

        account_name = str(account.name)
        batch_filename = str(batch.filename)
        account_id = account.id

    dirs = get_account_dir(account_name)
    filepath = None
    for subdir in ["queue", "failed", "pins"]:
        p = dirs / subdir / batch_filename
        if p.exists():
            filepath = p
            break

    if not filepath or not filepath.exists():
        return JSONResponse({"error": f"Batch file '{batch_filename}' not found on disk."}, status_code=404)

    mode = data.get("mode", "auto")  # "auto", "shift", or "custom"
    new_dates = []

    delimiter = detect_delimiter(filepath)
    with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
        csv_data = list(csv.reader(f, delimiter=delimiter))

    if not csv_data:
        return JSONResponse({"error": "Batch CSV is empty"}, status_code=400)

    headers = csv_data[0]
    data_rows = csv_data[1:]
    total_pins = len(data_rows)

    if total_pins == 0:
        return JSONResponse({"error": "Batch CSV has no pin data rows"}, status_code=400)

    pub_idx = -1
    for idx, h in enumerate(headers):
        clean_h = h.strip().lower()
        if clean_h in ["publish date", "publish_date", "schedule date", "date", "post date"]:
            pub_idx = idx
            break

    if mode == "auto":
        # Auto schedule mode: start datetime + interval in minutes + daily window
        start_str = data.get("start_datetime", "").strip()
        interval_mins = int(data.get("interval_minutes", 120))
        daily_start = data.get("daily_start", "09:00").strip()
        daily_end = data.get("daily_end", "21:00").strip()

        if not start_str:
            return JSONResponse({"error": "Start date & time is required for auto-schedule mode."}, status_code=400)

        try:
            # Parse start_datetime (e.g. 2026-08-25T09:00 or 2026-08-25 09:00)
            clean_start = start_str.replace("T", " ")
            if len(clean_start) == 16:
                clean_start += ":00"
            current_dt = datetime.strptime(clean_start, "%Y-%m-%d %H:%M:%S")
        except Exception as e:
            return JSONResponse({"error": f"Invalid start date format '{start_str}': {e}"}, status_code=400)

        # Parse daily window hours/mins
        try:
            d_start_h, d_start_m = map(int, daily_start.split(":"))
            d_end_h, d_end_m = map(int, daily_end.split(":"))
        except Exception:
            d_start_h, d_start_m = 9, 0
            d_end_h, d_end_m = 21, 0

        for _ in range(total_pins):
            # Check if current_dt is past daily_end, if so roll to next day at daily_start
            window_end_today = current_dt.replace(hour=d_end_h, minute=d_end_m, second=0)
            if current_dt > window_end_today and interval_mins < 1440:
                # Roll to next day
                current_dt = (current_dt + timedelta(days=1)).replace(hour=d_start_h, minute=d_start_m, second=0)

            new_dates.append(current_dt.strftime("%Y-%m-%dT%H:%M:%S"))
            current_dt += timedelta(minutes=interval_mins)

    elif mode == "shift":
        # Shift mode: shift all existing dates by days/hours
        shift_days = int(data.get("shift_days", 0))
        shift_hours = int(data.get("shift_hours", 0))
        shift_delta = timedelta(days=shift_days, hours=shift_hours)

        for r_idx, row in enumerate(data_rows):
            existing_d = row[pub_idx] if (pub_idx != -1 and pub_idx < len(row)) else ""
            if existing_d:
                try:
                    clean_d = existing_d.replace("T", " ").strip()
                    if len(clean_d) == 10:
                        parsed_dt = datetime.strptime(clean_d, "%Y-%m-%d")
                    elif len(clean_d) == 16:
                        parsed_dt = datetime.strptime(clean_d, "%Y-%m-%d %H:%M")
                    else:
                        parsed_dt = datetime.strptime(clean_d[:19], "%Y-%m-%d %H:%M:%S")
                    new_dt = parsed_dt + shift_delta
                    new_dates.append(new_dt.strftime("%Y-%m-%dT%H:%M:%S"))
                except Exception:
                    new_dates.append(existing_d)
            else:
                new_dates.append("")

    elif mode == "custom":
        # Custom mode: array of dates provided per row
        custom_list = data.get("dates", [])
        if not custom_list or len(custom_list) != total_pins:
            return JSONResponse({"error": f"Provided {len(custom_list)} dates, but batch contains {total_pins} pins."}, status_code=400)
        for d_str in custom_list:
            clean_val = d_str.strip().replace(" ", "T")
            if clean_val and len(clean_val) == 16:
                clean_val += ":00"
            new_dates.append(clean_val)
    else:
        return JSONResponse({"error": f"Unknown mode '{mode}'"}, status_code=400)

    # Rewrite CSV file
    if pub_idx == -1:
        headers.append("Publish Date")
        pub_idx = len(headers) - 1

    updated_file_rows = [headers]
    for idx, row in enumerate(data_rows):
        while len(row) < len(headers):
            row.append("")
        if idx < len(new_dates) and new_dates[idx]:
            row[pub_idx] = new_dates[idx]
        updated_file_rows.append(row)

    try:
        with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(updated_file_rows)
    except Exception as e:
        logger.error(f"Failed to write updated CSV: {e}")
        return JSONResponse({"error": f"Failed to save CSV file: {str(e)}"}, status_code=500)

    with factory() as session:
        session.add(ActivityLog(
            account_id=account_id,
            event_type="dates_updated",
            message=f"Updated publish dates for {total_pins} pins in '{batch_filename}'",
        ))
        session.commit()

    date_sample = f"From {new_dates[0][:16].replace('T', ' ')} to {new_dates[-1][:16].replace('T', ' ')}" if new_dates and new_dates[0] else ""

    return {
        "success": True,
        "message": f"Successfully updated Publish Dates for {total_pins} pins in {batch_filename}! {date_sample}",
        "first_date": new_dates[0] if new_dates else "",
        "last_date": new_dates[-1] if new_dates else "",
    }


@router.post("/api/history/{batch_id}/reschedule-upload", response_class=JSONResponse)
async def reschedule_upload(batch_id: int, data: dict = Body(...)):
    """Reschedule the automated upload date & time for a batch, with optional auto-cascade to subsequent batches."""
    target_dt_str = data.get("target_datetime", "").strip()
    cascade = bool(data.get("cascade", False))
    interval_value = int(data.get("interval_value", 2))
    interval_unit = str(data.get("interval_unit", "days")).lower()

    if not target_dt_str:
        return JSONResponse({"error": "Target upload date and time is required."}, status_code=400)

    factory = get_session_factory()
    with factory() as session:
        tz_str = get_setting(session, "timezone", "Africa/Lagos")
        try:
            tz = zoneinfo.ZoneInfo(tz_str)
        except Exception:
            tz = zoneinfo.ZoneInfo("Africa/Lagos")

        try:
            clean_dt_str = target_dt_str.replace("T", " ")
            if len(clean_dt_str) == 16:
                clean_dt_str += ":00"
            naive_dt = datetime.strptime(clean_dt_str, "%Y-%m-%d %H:%M:%S")
            # Localize to account timezone and convert to UTC for storage
            local_dt = naive_dt.replace(tzinfo=tz)
            utc_start_dt = local_dt.astimezone(timezone.utc)
        except Exception as e:
            return JSONResponse({"error": f"Invalid datetime format '{target_dt_str}': {e}"}, status_code=400)

        batch = session.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return JSONResponse({"error": "Batch not found"}, status_code=404)

        if batch.status == BatchStatus.DONE:
            return JSONResponse({"error": "Cannot reschedule an already uploaded batch."}, status_code=400)

        account_id = batch.account_id
        batch_filename = str(batch.filename)
        orig_filename = batch.original_filename

        # Calculate interval delta
        if interval_unit == "hours":
            delta = timedelta(hours=interval_value)
        elif interval_unit == "minutes":
            delta = timedelta(minutes=interval_value)
        else:
            delta = timedelta(days=interval_value)

        if not cascade:
            # Single batch update
            batch.scheduled_upload_at = utc_start_dt
            session.add(ActivityLog(
                account_id=account_id,
                event_type="batch_rescheduled",
                message=f"Rescheduled batch '{batch_filename}' upload to {naive_dt.strftime('%Y-%m-%d %H:%M')} {tz_str}",
            ))
            session.commit()
            formatted_date = local_dt.strftime('%Y-%m-%d %H:%M %Z')
            return {"success": True, "message": f"Successfully scheduled upload for '{batch_filename}' on {formatted_date}!"}
        else:
            # Cascade to all subsequent pending batches
            query = session.query(Batch).filter(
                Batch.account_id == account_id,
                Batch.status == BatchStatus.PENDING,
            )
            if orig_filename:
                query = query.filter(Batch.original_filename == orig_filename)

            pending_batches = query.order_by(Batch.id.asc()).all()

            # Find the starting index
            start_index = 0
            for idx, b in enumerate(pending_batches):
                if b.id == batch_id:
                    start_index = idx
                    break

            affected_batches = pending_batches[start_index:]
            current_target = utc_start_dt
            for b in affected_batches:
                b.scheduled_upload_at = current_target
                current_target += delta

            session.add(ActivityLog(
                account_id=account_id,
                event_type="queue_rescheduled",
                message=f"Cascaded upload schedule for {len(affected_batches)} batches starting {naive_dt.strftime('%Y-%m-%d %H:%M')} (every {interval_value} {interval_unit})",
            ))
            session.commit()

            start_formatted = local_dt.strftime('%Y-%m-%d %H:%M')
            end_local = (current_target - delta).astimezone(tz).strftime('%Y-%m-%d %H:%M')
            return {
                "success": True,
                "message": f"Successfully rescheduled {len(affected_batches)} batch(es) in sequence from {start_formatted} to {end_local} ({tz_str})!",
                "count": len(affected_batches),
            }


@router.post("/api/history/reschedule-group", response_class=JSONResponse)
async def reschedule_group(data: dict = Body(...)):
    """Reschedule all pending batches for a master campaign group in sequence."""
    account_id = data.get("account_id")
    orig_filename = data.get("original_filename")
    target_dt_str = data.get("target_datetime", "").strip()
    interval_value = int(data.get("interval_value", 2))
    interval_unit = str(data.get("interval_unit", "days")).lower()

    if not target_dt_str:
        return JSONResponse({"error": "Target upload date and time is required."}, status_code=400)

    factory = get_session_factory()
    with factory() as session:
        tz_str = get_setting(session, "timezone", "Africa/Lagos")
        try:
            tz = zoneinfo.ZoneInfo(tz_str)
        except Exception:
            tz = zoneinfo.ZoneInfo("Africa/Lagos")

        try:
            clean_dt_str = target_dt_str.replace("T", " ")
            if len(clean_dt_str) == 16:
                clean_dt_str += ":00"
            naive_dt = datetime.strptime(clean_dt_str, "%Y-%m-%d %H:%M:%S")
            local_dt = naive_dt.replace(tzinfo=tz)
            utc_start_dt = local_dt.astimezone(timezone.utc)
        except Exception as e:
            return JSONResponse({"error": f"Invalid datetime format '{target_dt_str}': {e}"}, status_code=400)

        if interval_unit == "hours":
            delta = timedelta(hours=interval_value)
        elif interval_unit == "minutes":
            delta = timedelta(minutes=interval_value)
        else:
            delta = timedelta(days=interval_value)

        query = session.query(Batch).filter(
            Batch.account_id == account_id,
            Batch.status == BatchStatus.PENDING,
        )
        if orig_filename:
            query = query.filter(Batch.original_filename == orig_filename)

        pending_batches = query.order_by(Batch.id.asc()).all()

        if not pending_batches:
            return JSONResponse({"error": "No pending batches found in this group to reschedule."}, status_code=400)

        current_target = utc_start_dt
        for b in pending_batches:
            b.scheduled_upload_at = current_target
            current_target += delta

        session.add(ActivityLog(
            account_id=account_id,
            event_type="group_rescheduled",
            message=f"Rescheduled {len(pending_batches)} batches in '{orig_filename}' starting {naive_dt.strftime('%Y-%m-%d %H:%M')} (every {interval_value} {interval_unit})",
        ))
        session.commit()

        start_formatted = local_dt.strftime('%Y-%m-%d %H:%M')
        end_local = (current_target - delta).astimezone(tz).strftime('%Y-%m-%d %H:%M')
        return {
            "success": True,
            "message": f"Successfully rescheduled {len(pending_batches)} batch(es) in sequence from {start_formatted} to {end_local} ({tz_str})!",
            "count": len(pending_batches),
        }


