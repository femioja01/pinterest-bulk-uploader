"""Pinterest Bulk Pin CSV Formatter Routes.

Provides endpoints to:
1. Inspect master pin spreadsheets
2. Format & download Pinterest Official Bulk CSVs
3. Format & send directly to Account upload queues
"""

import io
import shutil
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from src.models.database import get_session_factory, Account, Batch, BatchStatus, ActivityLog
from src.config import ensure_account_dirs
from src.services.splitter import split_csv
from src.services.formatter import inspect_master_csv, format_master_csv

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


@router.get("/formatter", response_class=HTMLResponse)
async def formatter_page(request: Request):
    """Render the Pinterest Bulk CSV Formatter page."""
    factory = get_session_factory()
    with factory() as session:
        accounts = session.query(Account).filter(Account.enabled == True).all()
        account_list = [{"id": a.id, "name": a.name, "batch_size": a.batch_size} for a in accounts]

    return templates.TemplateResponse(
        request,
        "formatter.html",
        {
            "active_page": "formatter",
            "accounts": account_list,
        },
    )


@router.post("/api/formatter/inspect", response_class=JSONResponse)
async def inspect_csv_endpoint(file: UploadFile = File(...)):
    """Inspect master CSV for required columns and detected weeks."""
    try:
        content = await file.read()
        info = inspect_master_csv(content)
        return info
    except Exception as e:
        logger.error(f"Error inspecting CSV: {e}")
        return JSONResponse({"error": f"Failed to read CSV: {str(e)}"}, status_code=400)


@router.post("/api/formatter/convert")
async def convert_csv_endpoint(
    file: UploadFile = File(...),
    start_week: str = Form(""),
    end_week: str = Form(""),
    specific_weeks: str = Form(""),
):
    """Format master CSV into Official Pinterest Bulk CSV format and return as download."""
    try:
        content = await file.read()

        s_week = int(start_week) if start_week.strip().isdigit() else None
        e_week = int(end_week) if end_week.strip().isdigit() else None
        spec_weeks = None
        if specific_weeks.strip():
            spec_weeks = [int(w.strip()) for w in specific_weeks.split(",") if w.strip().isdigit()]

        out_df, qa_report = format_master_csv(
            content,
            start_week=s_week,
            end_week=e_week,
            specific_weeks=spec_weeks,
        )

        output = io.StringIO()
        out_df.to_csv(output, index=False)
        csv_bytes = output.getvalue().encode("utf-8-sig")

        # Generate descriptive output filename
        orig_name = file.filename or "master_pins.csv"
        clean_base = orig_name.rsplit(".", 1)[0]
        if spec_weeks:
            week_tag = f"_Weeks_{'_'.join(map(str, spec_weeks))}"
        elif s_week and e_week:
            week_tag = f"_Week{s_week}_to_Week{e_week}"
        elif s_week:
            week_tag = f"_From_Week{s_week}"
        elif e_week:
            week_tag = f"_Up_to_Week{e_week}"
        else:
            week_tag = "_Official_Bulk"

        out_filename = f"{clean_base}{week_tag}_Pinterest.csv"

        return StreamingResponse(
            io.BytesIO(csv_bytes),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{out_filename}"'},
        )
    except Exception as e:
        logger.error(f"Error formatting CSV: {e}")
        return JSONResponse({"error": f"Formatting failed: {str(e)}"}, status_code=400)


@router.post("/api/formatter/convert-and-queue", response_class=JSONResponse)
async def convert_and_queue_endpoint(
    file: UploadFile = File(...),
    account_id: int = Form(...),
    batch_size: int = Form(50),
    start_week: str = Form(""),
    end_week: str = Form(""),
    specific_weeks: str = Form(""),
):
    """Format master CSV and immediately split & queue for an account."""
    factory = get_session_factory()
    with factory() as session:
        account = session.query(Account).filter(Account.id == account_id).first()
        if not account:
            return JSONResponse({"error": "Account not found"}, status_code=404)
        account_name = account.name

    try:
        content = await file.read()

        s_week = int(start_week) if start_week.strip().isdigit() else None
        e_week = int(end_week) if end_week.strip().isdigit() else None
        spec_weeks = None
        if specific_weeks.strip():
            spec_weeks = [int(w.strip()) for w in specific_weeks.split(",") if w.strip().isdigit()]

        out_df, qa_report = format_master_csv(
            content,
            start_week=s_week,
            end_week=e_week,
            specific_weeks=spec_weeks,
        )

        dirs = ensure_account_dirs(account_name)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        orig_clean = (file.filename or "master").rsplit(".", 1)[0]
        formatted_master_filename = f"master_{timestamp}_{orig_clean}_Official.csv"
        master_path = dirs["pins"] / formatted_master_filename

        # Write formatted CSV to pins dir
        out_df.to_csv(master_path, index=False, encoding="utf-8-sig")

        # Split into batches
        batch_size = min(max(batch_size, 1), 100)
        batch_files = split_csv(master_path, batch_size, dirs["queue"])

        done_master = dirs["done"] / formatted_master_filename
        shutil.move(str(master_path), str(done_master))

        # Insert records into DB
        with factory() as session:
            account = session.query(Account).filter(Account.name == account_name).first()
            for bf in batch_files:
                with open(bf, "r", encoding="utf-8-sig") as f:
                    pin_count = sum(1 for line in f if line.strip()) - 1
                batch = Batch(
                    account_id=account.id,
                    filename=bf.name,
                    original_filename=file.filename,
                    pin_count=max(0, pin_count),
                    status=BatchStatus.PENDING,
                )
                session.add(batch)

            log = ActivityLog(
                account_id=account.id,
                event_type="formatted_queue",
                message=f"Formatted & queued {file.filename} -> {len(batch_files)} batches ({len(out_df)} pins)",
            )
            session.add(log)
            session.commit()

        return {
            "success": True,
            "message": f"Successfully formatted {len(out_df)} pins and queued {len(batch_files)} batches of up to {batch_size} pins for '{account_name}'!",
            "total_pins": len(out_df),
            "batch_count": len(batch_files),
            "qa_report": qa_report,
        }
    except Exception as e:
        logger.error(f"Error converting and queueing CSV: {e}")
        return JSONResponse({"error": f"Failed: {str(e)}"}, status_code=400)
