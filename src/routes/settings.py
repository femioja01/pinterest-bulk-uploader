"""Settings routes — Telegram config, batch cadence settings."""

import asyncio
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from src.models.database import get_session_factory, get_setting, set_setting

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Render the settings page."""
    factory = get_session_factory()
    with factory() as session:
        settings = {
            "telegram_bot_token": get_setting(session, "telegram_bot_token", ""),
            "telegram_chat_id": get_setting(session, "telegram_chat_id", ""),
            "default_batch_size": int(get_setting(session, "default_batch_size", "100")),
            "batches_per_run": int(get_setting(session, "batches_per_run", "1")),
            "low_queue_threshold": int(get_setting(session, "low_queue_threshold", "2")),
            "timezone": get_setting(session, "timezone", "Africa/Lagos"),
            "notify_on_success": get_setting(session, "notify_on_success", "true") == "true",
            "notify_on_failure": get_setting(session, "notify_on_failure", "true") == "true",
            "notify_on_session_expiry": get_setting(session, "notify_on_session_expiry", "true") == "true",
            "notify_on_low_queue": get_setting(session, "notify_on_low_queue", "true") == "true",
            "notify_daily_summary": get_setting(session, "notify_daily_summary", "true") == "true",
        }

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active_page": "settings",
            "settings": settings,
        }
    )


@router.post("/api/settings", response_class=JSONResponse)
async def save_settings(request: Request):
    """Save settings with safe partial update support."""
    content_type = request.headers.get("content-type", "")
    is_form = "form" in content_type.lower() or "urlencoded" in content_type.lower() or "multipart" in content_type.lower()

    if "application/json" in content_type:
        data = await request.json()
    else:
        form = await request.form()
        data = dict(form)

    def _is_true(val):
        return "true" if str(val).lower() in ("true", "on", "1", "yes") else "false"

    factory = get_session_factory()
    with factory() as session:
        # Update Bot Token if explicitly provided in request
        if "telegram_bot_token" in data or "telegram_token" in data:
            val = str(data.get("telegram_bot_token") or data.get("telegram_token") or "").strip()
            set_setting(session, "telegram_bot_token", val)

        # Update Chat ID if explicitly provided in request
        if "telegram_chat_id" in data:
            val = str(data.get("telegram_chat_id") or "").strip()
            set_setting(session, "telegram_chat_id", val)

        if "default_batch_size" in data:
            try:
                d_size = int(data.get("default_batch_size", 100))
                d_size = min(max(d_size, 1), 100)
                set_setting(session, "default_batch_size", str(d_size))
            except (ValueError, TypeError):
                pass

        if "batches_per_run" in data:
            try:
                b_run = max(int(data.get("batches_per_run", 1)), 1)
                set_setting(session, "batches_per_run", str(b_run))
            except (ValueError, TypeError):
                pass

        if "low_queue_threshold" in data:
            try:
                l_thresh = max(int(data.get("low_queue_threshold", 2)), 0)
                set_setting(session, "low_queue_threshold", str(l_thresh))
            except (ValueError, TypeError):
                pass

        if "timezone" in data:
            tz = str(data.get("timezone", "Africa/Lagos")).strip() or "Africa/Lagos"
            set_setting(session, "timezone", tz)

        # Notification toggles
        if is_form:
            set_setting(session, "notify_on_success", _is_true(data.get("notify_on_success")))
            set_setting(session, "notify_on_failure", _is_true(data.get("notify_on_failure")))
            set_setting(session, "notify_on_session_expiry", _is_true(data.get("notify_on_session_expiry")))
            set_setting(session, "notify_on_low_queue", _is_true(data.get("notify_on_low_queue")))
            set_setting(session, "notify_daily_summary", _is_true(data.get("notify_daily_summary")))
        else:
            if "notify_on_success" in data:
                set_setting(session, "notify_on_success", _is_true(data.get("notify_on_success")))
            if "notify_on_failure" in data:
                set_setting(session, "notify_on_failure", _is_true(data.get("notify_on_failure")))
            if "notify_on_session_expiry" in data:
                set_setting(session, "notify_on_session_expiry", _is_true(data.get("notify_on_session_expiry")))
            if "notify_on_low_queue" in data:
                set_setting(session, "notify_on_low_queue", _is_true(data.get("notify_on_low_queue")))
            if "notify_daily_summary" in data:
                set_setting(session, "notify_daily_summary", _is_true(data.get("notify_daily_summary")))

    return {"success": True, "message": "Settings saved successfully"}


@router.post("/api/settings/test-telegram", response_class=JSONResponse)
async def test_telegram(request: Request):
    bot_token = ""
    chat_id = ""

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            data = await request.json()
            bot_token = str(data.get("bot_token") or data.get("telegram_bot_token") or data.get("telegram_token") or "").strip()
            chat_id = str(data.get("chat_id") or data.get("telegram_chat_id") or "").strip()
        except Exception:
            pass
    elif "form" in content_type:
        try:
            form = await request.form()
            bot_token = str(form.get("bot_token") or form.get("telegram_bot_token") or form.get("telegram_token") or "").strip()
            chat_id = str(form.get("chat_id") or form.get("telegram_chat_id") or "").strip()
        except Exception:
            pass

    factory = get_session_factory()
    with factory() as session:
        if not bot_token:
            bot_token = get_setting(session, "telegram_bot_token", "")
        if not chat_id:
            chat_id = get_setting(session, "telegram_chat_id", "")

    if not bot_token or not chat_id:
        return JSONResponse({"error": "Bot token and Chat ID are required (enter and save them first or pass in form)."}, status_code=400)

    try:
        from src.services.notifier import send_test_message
        success, error_msg = await send_test_message(bot_token.strip(), chat_id.strip())
        if success:
            return {"success": True, "message": "Test message sent! Check your Telegram."}
        else:
            return JSONResponse({"error": f"Failed to send: {error_msg}"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"Error: {str(e)}"}, status_code=500)
