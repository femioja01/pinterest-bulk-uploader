"""Schedule management routes — global and per-account cron schedules."""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from src.models.database import get_session_factory, get_setting, set_setting, Account

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


def _cron_to_human(cron_expr: str) -> str:
    if not cron_expr:
        return "Global Default"

    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return cron_expr

    minute, hour, day, month, dow = parts

    if minute == "*" and hour == "*":
        return "Every minute"
    if minute == "0" and hour == "*":
        return "Every hour"
    if minute == "*/5" and hour == "*":
        return "Every 5 minutes"
    if minute == "*/10" and hour == "*":
        return "Every 10 minutes"
    if minute == "*/15" and hour == "*":
        return "Every 15 minutes"
    if minute == "*/30" and hour == "*":
        return "Every 30 minutes"

    time_str = ""
    if hour != "*" and minute != "*":
        h = int(hour) if hour.isdigit() else 0
        m = int(minute) if minute.isdigit() else 0
        ampm = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        time_str = f"{h12}:{m:02d} {ampm}"

    if day == "*" and month == "*" and dow == "*":
        return f"Every day at {time_str}" if time_str else cron_expr
    if day == "*" and month == "*" and dow == "1-5":
        return f"Weekdays at {time_str}" if time_str else "Weekdays"
    if day == "*/2" and month == "*" and dow == "*":
        return f"Every 2 days at {time_str}" if time_str else "Every 2 days"
    if day == "*/3" and month == "*" and dow == "*":
        return f"Every 3 days at {time_str}" if time_str else "Every 3 days"
    if day == "*/4" and month == "*" and dow == "*":
        return f"Every 4 days at {time_str}" if time_str else "Every 4 days"
    if day == "*/5" and month == "*" and dow == "*":
        return f"Every 5 days at {time_str}" if time_str else "Every 5 days"
    if day == "*/7" and month == "*" and dow == "*":
        return f"Every 7 days at {time_str}" if time_str else "Every 7 days"

    dow_names = {"0": "Sunday", "1": "Monday", "2": "Tuesday", "3": "Wednesday",
                 "4": "Thursday", "5": "Friday", "6": "Saturday", "7": "Sunday"}
    if dow in dow_names and day == "*" and month == "*":
        return f"Every {dow_names[dow]} at {time_str}" if time_str else f"Every {dow_names[dow]}"

    return f"Cron: {cron_expr}" + (f" (at {time_str})" if time_str else "")


def _get_next_runs(cron_expr: str, count: int = 5) -> list[str]:
    try:
        from apscheduler.triggers.cron import CronTrigger
        from datetime import datetime, timezone

        parts = cron_expr.strip().split()
        if len(parts) != 5:
            return []

        trigger = CronTrigger(
            minute=parts[0], hour=parts[1], day=parts[2],
            month=parts[3], day_of_week=parts[4],
        )

        runs = []
        next_time = datetime.now(timezone.utc)
        for _ in range(count):
            next_time = trigger.get_next_fire_time(None, next_time)
            if next_time:
                runs.append(next_time.strftime("%Y-%m-%d %H:%M %Z"))
                from datetime import timedelta
                next_time = next_time + timedelta(seconds=1)
            else:
                break
        return runs
    except Exception:
        return []


@router.get("/schedule", response_class=HTMLResponse)
async def schedule_page(request: Request):
    factory = get_session_factory()
    with factory() as session:
        global_cron = get_setting(session, "upload_schedule", "0 9 */2 * *")
        scheduler_enabled = get_setting(session, "scheduler_enabled", "true")
        accounts = session.query(Account).order_by(Account.created_at.asc()).all()

        account_schedules = []
        for a in accounts:
            effective_cron = (a.schedule_cron or "").strip() or global_cron
            is_custom = bool((a.schedule_cron or "").strip())
            next_runs = _get_next_runs(effective_cron, count=1)
            next_run_str = next_runs[0] if next_runs else "N/A"

            account_schedules.append({
                "id": a.id,
                "name": a.name,
                "enabled": a.enabled,
                "is_custom": is_custom,
                "cron": a.schedule_cron or "",
                "effective_cron": effective_cron,
                "human": _cron_to_human(effective_cron),
                "next_run": next_run_str,
            })

    global_human = _cron_to_human(global_cron)
    global_next_runs = _get_next_runs(global_cron, count=5)

    return templates.TemplateResponse(
        request,
        "schedule.html",
        {
            "active_page": "schedule",
            "global_cron": global_cron,
            "global_human": global_human,
            "global_next_runs": global_next_runs,
            "account_schedules": account_schedules,
            "scheduler_enabled": scheduler_enabled == "true",
        }
    )


@router.post("/api/schedule", response_class=JSONResponse)
async def update_global_schedule(cron_expression: str = Form(...)):
    cron_expression = cron_expression.strip()
    parts = cron_expression.split()
    if len(parts) != 5:
        return JSONResponse({"error": "Invalid cron expression — must have 5 fields"}, status_code=400)

    try:
        from apscheduler.triggers.cron import CronTrigger
        CronTrigger(
            minute=parts[0], hour=parts[1], day=parts[2],
            month=parts[3], day_of_week=parts[4],
        )
    except Exception as e:
        return JSONResponse({"error": f"Invalid cron expression: {str(e)}"}, status_code=400)

    factory = get_session_factory()
    with factory() as session:
        set_setting(session, "upload_schedule", cron_expression)

    try:
        from src.app import scheduler as app_scheduler
        if app_scheduler:
            from src.services.scheduler import update_schedule
            update_schedule(app_scheduler, cron_expression)
    except Exception:
        pass

    human = _cron_to_human(cron_expression)
    next_runs = _get_next_runs(cron_expression)

    return {
        "success": True,
        "message": f"Global default schedule updated: {human}",
        "human": human,
        "next_runs": next_runs,
    }


@router.post("/api/schedule/account/{account_id}", response_class=JSONResponse)
async def update_account_schedule_endpoint(account_id: int, cron_expression: str = Form("")):
    cron_expression = cron_expression.strip()

    if cron_expression:
        parts = cron_expression.split()
        if len(parts) != 5:
            return JSONResponse({"error": "Invalid cron expression — must have 5 fields"}, status_code=400)
        try:
            from apscheduler.triggers.cron import CronTrigger
            CronTrigger(
                minute=parts[0], hour=parts[1], day=parts[2],
                month=parts[3], day_of_week=parts[4],
            )
        except Exception as e:
            return JSONResponse({"error": f"Invalid cron expression: {str(e)}"}, status_code=400)

    factory = get_session_factory()
    with factory() as session:
        account = session.query(Account).filter_by(id=account_id).first()
        if not account:
            return JSONResponse({"error": "Account not found"}, status_code=404)

        account.schedule_cron = cron_expression if cron_expression else None
        session.commit()
        account_name = account.name

    try:
        from src.app import scheduler as app_scheduler
        if app_scheduler:
            from src.services.scheduler import update_account_schedule
            update_account_schedule(app_scheduler, account_id, cron_expression)
    except Exception:
        pass

    human = _cron_to_human(cron_expression) if cron_expression else "Using Global Default"
    return {
        "success": True,
        "message": f"Schedule for '{account_name}' updated: {human}",
        "human": human,
    }


@router.post("/api/schedule/toggle", response_class=JSONResponse)
async def toggle_scheduler():
    factory = get_session_factory()
    with factory() as session:
        current = get_setting(session, "scheduler_enabled", "true")
        new_value = "false" if current == "true" else "true"
        set_setting(session, "scheduler_enabled", new_value)

    try:
        from src.app import scheduler as app_scheduler
        if app_scheduler:
            if new_value == "true":
                app_scheduler.resume()
            else:
                app_scheduler.pause()
    except Exception:
        pass

    return {
        "success": True,
        "enabled": new_value == "true",
        "message": f"Scheduler {'enabled' if new_value == 'true' else 'paused'}",
    }
