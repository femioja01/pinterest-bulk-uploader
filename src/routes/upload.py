"""CSV upload routes — upload, preview, and queue CSVs."""

import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from src.models.database import get_session_factory, Account, Batch, BatchStatus, ActivityLog
from src.config import ensure_account_dirs
from src.services.splitter import validate_csv, split_csv, preview_csv, count_rows

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


@router.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    """Render the CSV upload page."""
    factory = get_session_factory()
    with factory() as session:
        accounts = session.query(Account).filter(Account.enabled == True).all()
        account_list = [{"id": a.id, "name": a.name, "batch_size": a.batch_size} for a in accounts]

    return templates.TemplateResponse(
        request,
        "upload.html",
        {
            "active_page": "upload",
            "accounts": account_list,
        }
    )


@router.post("/api/upload/preview", response_class=JSONResponse)
async def preview_upload(file: UploadFile = File(...), batch_size: int = Form(50)):
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        valid, error_msg = validate_csv(tmp_path)
        if not valid:
            return JSONResponse({"error": error_msg}, status_code=400)

        preview = preview_csv(tmp_path)
        row_count = preview["row_count"]
        batch_size = min(max(batch_size, 1), 100)
        batch_count = (row_count + batch_size - 1) // batch_size

        return {
            "row_count": row_count,
            "columns": preview["column_names"],
            "boards": preview["boards"],
            "sample_rows": preview["sample_rows"],
            "batch_count": batch_count,
            "batch_size": batch_size,
        }
    finally:
        tmp_path.unlink(missing_ok=True)


@router.post("/api/upload/queue", response_class=JSONResponse)
async def queue_upload(
    file: UploadFile = File(...),
    account_id: int = Form(...),
    batch_size: int = Form(50),
):
    factory = get_session_factory()
    with factory() as session:
        account = session.query(Account).filter(Account.id == account_id).first()
        if not account:
            return JSONResponse({"error": "Account not found"}, status_code=404)
        account_name = account.name

    dirs = ensure_account_dirs(account_name)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    master_filename = f"master_{timestamp}_{file.filename}"
    master_path = dirs["pins"] / master_filename
    content = await file.read()
    master_path.write_bytes(content)

    valid, error_msg = validate_csv(master_path)
    if not valid:
        master_path.unlink(missing_ok=True)
        return JSONResponse({"error": f"Invalid CSV: {error_msg}"}, status_code=400)

    batch_size = min(max(batch_size, 1), 100)
    batch_files = split_csv(master_path, batch_size, dirs["queue"])

    done_master = dirs["done"] / master_filename
    shutil.move(str(master_path), str(done_master))

    factory = get_session_factory()
    with factory() as session:
        account = session.query(Account).filter(Account.name == account_name).first()
        for bf in batch_files:
            row_count = count_rows(bf)
            batch = Batch(
                account_id=account.id,
                filename=bf.name,
                original_filename=file.filename,
                pin_count=row_count,
                status=BatchStatus.PENDING,
            )
            session.add(batch)

        session.add(ActivityLog(
            account_id=account.id,
            event_type="csv_split",
            message=f"Split {file.filename} → {len(batch_files)} batches ({batch_size} pins each)",
        ))
        session.commit()

    return {
        "success": True,
        "message": f"CSV split into {len(batch_files)} batches and queued for next scheduled run",
        "batch_count": len(batch_files),
    }


@router.post("/api/upload/now", response_class=JSONResponse)
async def upload_now(
    file: UploadFile = File(...),
    account_id: int = Form(...),
    batch_size: int = Form(50),
):
    factory = get_session_factory()
    with factory() as session:
        account = session.query(Account).filter(Account.id == account_id).first()
        if not account:
            return JSONResponse({"error": "Account not found"}, status_code=404)
        account_name = account.name

    dirs = ensure_account_dirs(account_name)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    master_filename = f"master_{timestamp}_{file.filename}"
    master_path = dirs["pins"] / master_filename
    content = await file.read()
    master_path.write_bytes(content)

    valid, error_msg = validate_csv(master_path)
    if not valid:
        master_path.unlink(missing_ok=True)
        return JSONResponse({"error": f"Invalid CSV: {error_msg}"}, status_code=400)

    batch_size = min(max(batch_size, 1), 100)
    batch_files = split_csv(master_path, batch_size, dirs["queue"])

    done_master = dirs["done"] / master_filename
    shutil.move(str(master_path), str(done_master))

    factory = get_session_factory()
    with factory() as session:
        account = session.query(Account).filter(Account.name == account_name).first()
        for bf in batch_files:
            row_count = count_rows(bf)
            batch = Batch(
                account_id=account.id,
                filename=bf.name,
                original_filename=file.filename,
                pin_count=row_count,
                status=BatchStatus.PENDING,
            )
            session.add(batch)

        session.add(ActivityLog(
            account_id=account.id,
            event_type="csv_split",
            message=f"Split {file.filename} → {len(batch_files)} batches for immediate upload",
        ))
        session.commit()

    def _run_upload():
        try:
            from src.services.scheduler import run_single_account
            run_single_account(account_name)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Background upload failed: {e}")

    thread = threading.Thread(target=_run_upload, daemon=True)
    thread.start()

    return {
        "success": True,
        "message": f"CSV split into {len(batch_files)} batches — uploading now in background",
        "batch_count": len(batch_files),
    }


@router.post("/api/upload/trigger/{account_id}", response_class=JSONResponse)
async def trigger_upload(account_id: int):
    factory = get_session_factory()
    with factory() as session:
        account = session.query(Account).filter(Account.id == account_id).first()
        if not account:
            return JSONResponse({"error": "Account not found"}, status_code=404)
        account_name = account.name

        pending = session.query(Batch).filter(
            Batch.account_id == account_id,
            Batch.status == BatchStatus.PENDING,
        ).count()

        if pending == 0:
            return JSONResponse({"error": "No pending batches for this account"}, status_code=400)

    def _run():
        try:
            from src.services.scheduler import run_single_account
            run_single_account(account_name)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Triggered upload failed: {e}")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return {"success": True, "message": f"Uploading {pending} pending batches for '{account_name}'..."}
