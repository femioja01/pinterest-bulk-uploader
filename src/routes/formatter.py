"""Pinterest Bulk Pin CSV Formatter Routes.

Provides endpoints to:
1. Inspect master pin spreadsheets
2. Format & download Pinterest Official Bulk CSVs with automated Publish Dates
3. Format & send directly to Account upload queues
"""

import io
import re
import csv
import shutil
import logging
import zoneinfo
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from src.models.database import get_session_factory, Account, Batch, BatchStatus, ActivityLog, get_setting
from src.config import ensure_account_dirs
from src.services.splitter import split_csv, detect_delimiter
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

    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    return templates.TemplateResponse(
        request,
        "formatter.html",
        {
            "active_page": "formatter",
            "accounts": account_list,
            "default_start_date": tomorrow,
        },
    )


@router.post("/api/formatter/inspect", response_class=JSONResponse)
async def inspect_csv_endpoint(
    file: UploadFile | None = File(None),
    raw_text: str | None = Form(None),
):
    """Inspect master CSV or pasted text for required columns and detected weeks."""
    try:
        if file and file.filename:
            content = await file.read()
        elif raw_text and raw_text.strip():
            content = raw_text.strip()
        else:
            return JSONResponse({"error": "No CSV file uploaded or pasted data provided"}, status_code=400)

        info = inspect_master_csv(content)
        return info
    except Exception as e:
        logger.error(f"Error inspecting CSV: {e}")
        return JSONResponse({"error": f"Failed to inspect data: {str(e)}"}, status_code=400)


@router.post("/api/formatter/convert")
async def convert_csv_endpoint(
    file: UploadFile | None = File(None),
    raw_text: str | None = Form(None),
    target_template: str = Form("pinterest"),
    start_week: str = Form(""),
    end_week: str = Form(""),
    specific_weeks: str = Form(""),
    schedule_publish_dates: bool = Form(False),
    publish_start_date: str = Form(""),
    publish_pins_per_day: int = Form(25),
    publish_daily_start: str = Form("08:00"),
    publish_daily_end: str = Form("22:00"),
    custom_filename: str = Form(""),
):
    """Format master CSV or pasted text into Official Pinterest or Publer Bulk CSV format and return as download."""
    try:
        def _get_val(val, default):
            if hasattr(val, "default"):
                return val.default if val.default is not ... else default
            return val if val is not None else default

        custom_name_val = str(_get_val(custom_filename, "")).strip()

        if file and file.filename:
            content = await file.read()
            orig_name = file.filename or "master_pins.csv"
        elif raw_text and raw_text.strip():
            content = raw_text.strip()
            orig_name = "pasted_pins.csv"
        else:
            return JSONResponse({"error": "No CSV file uploaded or pasted data provided"}, status_code=400)

        if custom_name_val:
            clean_name = custom_name_val
            if not clean_name.lower().endswith(".csv"):
                clean_name += ".csv"
            orig_name = clean_name

        start_week_val = str(_get_val(start_week, "")).strip()
        end_week_val = str(_get_val(end_week, "")).strip()
        spec_weeks_val = str(_get_val(specific_weeks, "")).strip()
        schedule_publish_dates = bool(_get_val(schedule_publish_dates, False))
        publish_start_date = str(_get_val(publish_start_date, "")).strip()
        publish_pins_per_day = int(_get_val(publish_pins_per_day, 25))
        publish_daily_start = str(_get_val(publish_daily_start, "08:00")).strip()
        publish_daily_end = str(_get_val(publish_daily_end, "22:00")).strip()

        s_week = int(start_week_val) if start_week_val.isdigit() else None
        e_week = int(end_week_val) if end_week_val.isdigit() else None
        spec_weeks = None
        if spec_weeks_val:
            spec_weeks = [int(w.strip()) for w in spec_weeks_val.split(",") if w.strip().isdigit()]

        out_df, qa_report = format_master_csv(
            content,
            target_template=target_template,
            start_week=s_week,
            end_week=e_week,
            specific_weeks=spec_weeks,
            schedule_publish_dates=schedule_publish_dates,
            publish_start_date=publish_start_date,
            publish_pins_per_day=publish_pins_per_day,
            publish_daily_start=publish_daily_start,
            publish_daily_end=publish_daily_end,
        )

        output = io.StringIO()
        out_df.to_csv(output, index=False)
        csv_bytes = output.getvalue().encode("utf-8-sig")

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
            week_tag = "_Bulk"

        if schedule_publish_dates:
            week_tag += "_Scheduled"

        tmpl_name = "Publer" if target_template.lower() == "publer" else "Pinterest"
        out_filename = f"{clean_base}{week_tag}_{tmpl_name}.csv"

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
    file: UploadFile | None = File(None),
    raw_text: str | None = Form(None),
    account_id: int = Form(...),
    batch_size: int = Form(50),
    start_week: str = Form(""),
    end_week: str = Form(""),
    specific_weeks: str = Form(""),
    schedule_publish_dates: bool = Form(False),
    publish_start_date: str = Form(""),
    publish_pins_per_day: int = Form(25),
    publish_daily_start: str = Form("08:00"),
    publish_daily_end: str = Form("22:00"),
    queue_schedule_mode: str = Form("default"),
    queue_upload_time: str = Form("09:00"),
    queue_first_date: str = Form(""),
    queue_interval_days: int = Form(2),
    custom_filename: str = Form(""),
):
    """Format master CSV or pasted text and immediately split & queue for an account."""
    factory = get_session_factory()
    with factory() as session:
        account = session.query(Account).filter(Account.id == account_id).first()
        if not account:
            return JSONResponse({"error": "Account not found"}, status_code=404)
        account_name = account.name

    try:
        def _get_val(val, default):
            if hasattr(val, "default"):
                return val.default if val.default is not ... else default
            return val if val is not None else default

        custom_name_val = str(_get_val(custom_filename, "")).strip()

        if file and file.filename:
            content = await file.read()
            orig_filename = file.filename
        elif raw_text and raw_text.strip():
            content = raw_text.strip()
            orig_filename = "pasted_pins.csv"
        else:
            return JSONResponse({"error": "No CSV file uploaded or pasted data provided"}, status_code=400)

        if custom_name_val:
            clean_name = custom_name_val
            if not clean_name.lower().endswith(".csv"):
                clean_name += ".csv"
            orig_filename = clean_name

        start_week_val = str(_get_val(start_week, "")).strip()
        end_week_val = str(_get_val(end_week, "")).strip()
        spec_weeks_val = str(_get_val(specific_weeks, "")).strip()
        schedule_publish_dates = bool(_get_val(schedule_publish_dates, False))
        publish_start_date = str(_get_val(publish_start_date, "")).strip()
        publish_pins_per_day = int(_get_val(publish_pins_per_day, 25))
        publish_daily_start = str(_get_val(publish_daily_start, "08:00")).strip()
        publish_daily_end = str(_get_val(publish_daily_end, "22:00")).strip()
        queue_schedule_mode = str(_get_val(queue_schedule_mode, "default")).strip()
        queue_upload_time = str(_get_val(queue_upload_time, "09:00")).strip()
        queue_first_date = str(_get_val(queue_first_date, "")).strip()
        queue_interval_days = int(_get_val(queue_interval_days, 2))

        s_week = int(start_week_val) if start_week_val.isdigit() else None
        e_week = int(end_week_val) if end_week_val.isdigit() else None
        spec_weeks = None
        if spec_weeks_val:
            spec_weeks = [int(w.strip()) for w in spec_weeks_val.split(",") if w.strip().isdigit()]

        out_df, qa_report = format_master_csv(
            content,
            start_week=s_week,
            end_week=e_week,
            specific_weeks=spec_weeks,
            schedule_publish_dates=schedule_publish_dates,
            publish_start_date=publish_start_date,
            publish_pins_per_day=publish_pins_per_day,
            publish_daily_start=publish_daily_start,
            publish_daily_end=publish_daily_end,
        )

        dirs = ensure_account_dirs(account_name)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        clean_acct = re.sub(r"[^a-zA-Z0-9_\-]", "_", account_name)
        orig_clean = (orig_filename or "master").rsplit(".", 1)[0]
        formatted_master_filename = f"master_{timestamp}_{orig_clean}_Official.csv"
        master_path = dirs["pins"] / formatted_master_filename

        # Write formatted CSV to pins dir
        out_df.to_csv(master_path, index=False, encoding="utf-8-sig")

        # Split into batches with account & timestamp prefix
        batch_size = min(max(batch_size, 1), 100)
        batch_prefix = f"{clean_acct}_batch_{timestamp}"
        batch_files = split_csv(master_path, batch_size, dirs["queue"], prefix=batch_prefix)

        if not batch_files or len(batch_files) == 0:
            return JSONResponse({"error": "Failed to split CSV: No batch files were generated."}, status_code=400)

        done_master = dirs["done"] / formatted_master_filename
        shutil.move(str(master_path), str(done_master))

        # Insert records into DB with accurate pin counts and scheduled upload times
        now_utc = datetime.now(timezone.utc)
        with factory() as session:
            account = session.query(Account).filter(Account.name == account_name).first()
            tz_str = get_setting(session, "timezone", "Africa/Lagos")
            try:
                tz = zoneinfo.ZoneInfo(tz_str)
            except Exception:
                tz = zoneinfo.ZoneInfo("Africa/Lagos")

            # Parse custom upload time of day
            target_hour = 9
            target_minute = 0
            if queue_upload_time and ":" in queue_upload_time:
                try:
                    parts = queue_upload_time.strip().split(":")
                    target_hour = max(0, min(23, int(parts[0])))
                    target_minute = max(0, min(59, int(parts[1])))
                except Exception:
                    target_hour = 9
                    target_minute = 0

            # Base date for unscheduled pins if custom schedule mode is selected
            manual_start_date = None
            interval_days = max(1, queue_interval_days)
            if queue_schedule_mode == "custom" and not schedule_publish_dates:
                if queue_first_date and queue_first_date.strip():
                    try:
                        manual_start_date = datetime.strptime(queue_first_date.strip(), "%Y-%m-%d").date()
                    except Exception:
                        manual_start_date = datetime.now(tz).date() + timedelta(days=1)
                else:
                    manual_start_date = datetime.now(tz).date() + timedelta(days=1)

            for idx, bf in enumerate(batch_files):
                pin_count = 0
                first_publish_date_str = None
                delim = detect_delimiter(bf)
                with open(bf, "r", encoding="utf-8-sig") as f:
                    reader = csv.reader(f, delimiter=delim)
                    hdr = next(reader, None)
                    pub_idx = hdr.index("Publish Date") if hdr and "Publish Date" in hdr else -1
                    for row in reader:
                        if any(cell.strip() for cell in row):
                            pin_count += 1
                            if first_publish_date_str is None and pub_idx >= 0 and len(row) > pub_idx:
                                val = row[pub_idx].strip()
                                if val:
                                    first_publish_date_str = val

                batch_upload_at = None
                if queue_schedule_mode == "custom":
                    if first_publish_date_str:
                        # Auto-interval: derive date from first pin in batch, set to chosen upload time of day
                        try:
                            date_part = first_publish_date_str.split("T")[0]
                            p_date = datetime.strptime(date_part, "%Y-%m-%d")
                            naive_dt = p_date.replace(hour=target_hour, minute=target_minute, second=0)
                            local_dt = naive_dt.replace(tzinfo=tz)
                            batch_upload_at = local_dt.astimezone(timezone.utc)
                        except Exception as e:
                            logger.warning(f"Could not parse publish date '{first_publish_date_str}' for batch {bf.name}: {e}")
                    elif manual_start_date:
                        # Manual cadence: start_date + idx * interval_days at chosen upload time of day
                        curr_date = manual_start_date + timedelta(days=idx * interval_days)
                        naive_dt = datetime.combine(curr_date, datetime.min.time()).replace(
                            hour=target_hour, minute=target_minute, second=0
                        )
                        local_dt = naive_dt.replace(tzinfo=tz)
                        batch_upload_at = local_dt.astimezone(timezone.utc)
                else:
                    # Account default cadence
                    batch_upload_at = None

                batch = Batch(
                    account_id=account.id,
                    filename=bf.name,
                    original_filename=orig_filename,
                    pin_count=max(0, pin_count),
                    status=BatchStatus.PENDING,
                    created_at=now_utc,
                    scheduled_upload_at=batch_upload_at,
                )
                session.add(batch)

            if queue_schedule_mode == "custom":
                sched_info = f" (scheduled at {target_hour:02d}:{target_minute:02d} {tz_str})"
            elif schedule_publish_dates:
                sched_info = " (with auto-generated publish dates, account cadence)"
            else:
                sched_info = ""

            discarded = qa_report.get("discarded_rows_count", 0)
            discard_info = f" (discarded {discarded} incomplete rows)" if discarded > 0 else ""
            log = ActivityLog(
                account_id=account.id,
                event_type="formatted_queue",
                message=f"Formatted & queued {orig_filename}{sched_info}{discard_info} -> {len(batch_files)} batches ({len(out_df)} pins)",
            )
            session.add(log)
            session.commit()

        time_info = f" scheduled for upload at {target_hour:02d}:{target_minute:02d} ({tz_str})" if queue_schedule_mode == "custom" else " using account default schedule"
        return {
            "success": True,
            "message": f"Successfully formatted {len(out_df)} pins{discard_info} and queued {len(batch_files)} batches of up to {batch_size} pins for '{account_name}'{time_info}!",
            "total_pins": len(out_df),
            "batch_count": len(batch_files),
            "account_id": account_id,
            "qa_report": qa_report,
        }
    except Exception as e:
        logger.error(f"Error converting and queueing CSV: {e}")
        return JSONResponse({"error": f"Failed: {str(e)}"}, status_code=400)
