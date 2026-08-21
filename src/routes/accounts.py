"""Account management routes — CRUD for Pinterest accounts with per-account schedule support,
1-click browser login launcher, and session state export."""

import json
import shutil
import threading
import time
from pathlib import Path
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from src.models.database import get_session_factory, get_setting, Account, ActivityLog
from src.config import ensure_account_dirs, get_account_dir, parse_playwright_proxy
from src.routes.schedule import _cron_to_human

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")

# In-memory tracking of active interactive login sessions
# account_id -> {"status": "in_progress" | "success" | "failed", "message": str, "timestamp": float, "force_save": bool}
_active_logins = {}


@router.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request):
    """Render the accounts management page."""
    factory = get_session_factory()
    with factory() as session:
        global_cron = get_setting(session, "upload_schedule", "0 9 */2 * *")
        accounts = session.query(Account).order_by(Account.created_at.desc()).all()
        account_list = []
        for a in accounts:
            state_path = get_account_dir(a.name) / "auth" / "state.json"
            has_session = state_path.exists()
            effective_cron = (a.schedule_cron or "").strip() or global_cron
            account_list.append({
                "id": a.id,
                "name": a.name,
                "proxy_url": a.proxy_url or "",
                "batch_size": a.batch_size,
                "schedule_cron": a.schedule_cron or "",
                "effective_schedule_human": _cron_to_human(effective_cron),
                "is_custom_schedule": bool((a.schedule_cron or "").strip()),
                "session_valid": a.session_valid,
                "has_session_file": has_session,
                "enabled": a.enabled,
            })

    return templates.TemplateResponse(
        request,
        "accounts.html",
        {
            "active_page": "accounts",
            "accounts": account_list,
            "global_cron": global_cron,
            "global_schedule_human": _cron_to_human(global_cron),
        }
    )


@router.post("/api/accounts", response_class=JSONResponse)
async def create_account(
    name: str = Form(...),
    proxy_url: str = Form(""),
    batch_size: int = Form(100),
    schedule_cron: str = Form(""),
):
    if not name or not name.strip():
        return JSONResponse({"error": "Account name is required"}, status_code=400)

    name = name.strip().lower().replace(" ", "-")
    batch_size = min(max(batch_size, 1), 100)
    schedule_cron = schedule_cron.strip() or None

    if schedule_cron:
        parts = schedule_cron.split()
        if len(parts) != 5:
            return JSONResponse({"error": "Invalid cron expression — must have 5 fields"}, status_code=400)

    factory = get_session_factory()
    with factory() as session:
        existing = session.query(Account).filter(Account.name == name).first()
        if existing:
            return JSONResponse({"error": f"Account '{name}' already exists"}, status_code=400)

        account = Account(
            name=name,
            proxy_url=proxy_url.strip() or None,
            batch_size=batch_size,
            schedule_cron=schedule_cron,
        )
        session.add(account)
        session.flush()

        session.add(ActivityLog(
            account_id=account.id,
            event_type="account_created",
            message=f"Account '{name}' created",
        ))
        session.commit()
        account_id = account.id

    ensure_account_dirs(name)

    try:
        from src.app import scheduler as app_scheduler
        if app_scheduler:
            from src.services.scheduler import sync_account_schedules
            sync_account_schedules(app_scheduler)
    except Exception:
        pass

    return JSONResponse({"success": True, "message": f"Account '{name}' created", "id": account_id})


@router.put("/api/accounts/{account_id}", response_class=JSONResponse)
@router.post("/api/accounts/{account_id}/update", response_class=JSONResponse)
async def update_account(
    account_id: int,
    name: str = Form(""),
    proxy_url: str = Form(""),
    batch_size: int = Form(100),
    schedule_cron: str = Form(""),
):
    batch_size = min(max(batch_size, 1), 100)
    schedule_cron = schedule_cron.strip() or None

    if schedule_cron:
        parts = schedule_cron.split()
        if len(parts) != 5:
            return JSONResponse({"error": "Invalid cron expression — must have 5 fields"}, status_code=400)

    factory = get_session_factory()
    with factory() as session:
        account = session.query(Account).filter(Account.id == account_id).first()
        if not account:
            return JSONResponse({"error": "Account not found"}, status_code=404)

        old_name = account.name
        if proxy_url is not None:
            account.proxy_url = proxy_url.strip() or None
        account.batch_size = batch_size
        account.schedule_cron = schedule_cron

        if name and name.strip() and name.strip().lower().replace(" ", "-") != old_name:
            new_name = name.strip().lower().replace(" ", "-")
            existing = session.query(Account).filter(Account.name == new_name).first()
            if existing:
                return JSONResponse({"error": f"Account '{new_name}' already exists"}, status_code=400)
            account.name = new_name
            old_dir = get_account_dir(old_name)
            new_dir = get_account_dir(new_name)
            if old_dir.exists():
                shutil.move(str(old_dir), str(new_dir))
            else:
                ensure_account_dirs(new_name)

        session.commit()

    try:
        from src.app import scheduler as app_scheduler
        if app_scheduler:
            from src.services.scheduler import sync_account_schedules
            sync_account_schedules(app_scheduler)
    except Exception:
        pass

    return JSONResponse({"success": True, "message": "Account updated"})


@router.delete("/api/accounts/{account_id}", response_class=JSONResponse)
async def delete_account(account_id: int):
    factory = get_session_factory()
    with factory() as session:
        account = session.query(Account).filter(Account.id == account_id).first()
        if not account:
            return JSONResponse({"error": "Account not found"}, status_code=404)

        account_name = account.name
        session.delete(account)
        session.commit()

    account_dir = get_account_dir(account_name)
    if account_dir.exists():
        shutil.rmtree(account_dir, ignore_errors=True)

    try:
        from src.app import scheduler as app_scheduler
        if app_scheduler:
            from src.services.scheduler import sync_account_schedules
            sync_account_schedules(app_scheduler)
    except Exception:
        pass

    return JSONResponse({"success": True, "message": f"Account '{account_name}' deleted"})


@router.post("/api/accounts/{account_id}/toggle", response_class=JSONResponse)
async def toggle_account(account_id: int):
    factory = get_session_factory()
    with factory() as session:
        account = session.query(Account).filter(Account.id == account_id).first()
        if not account:
            return JSONResponse({"error": "Account not found"}, status_code=404)

        account.enabled = not account.enabled
        session.commit()
        new_state = "enabled" if account.enabled else "disabled"

    try:
        from src.app import scheduler as app_scheduler
        if app_scheduler:
            from src.services.scheduler import sync_account_schedules
            sync_account_schedules(app_scheduler)
    except Exception:
        pass

    return JSONResponse({"success": True, "enabled": account.enabled, "message": f"Account {new_state}"})


@router.post("/api/accounts/{account_id}/session", response_class=JSONResponse)
async def upload_session(account_id: int, file: UploadFile = File(...)):
    factory = get_session_factory()
    with factory() as session:
        account = session.query(Account).filter(Account.id == account_id).first()
        if not account:
            return JSONResponse({"error": "Account not found"}, status_code=404)

        account_name = account.name

    if not file.filename.endswith(".json"):
        return JSONResponse({"error": "File must be a .json file"}, status_code=400)

    content = await file.read()
    try:
        json.loads(content)
    except json.JSONDecodeError:
        return JSONResponse({"error": "Invalid JSON file"}, status_code=400)

    dirs = ensure_account_dirs(account_name)
    state_path = dirs["auth"] / "state.json"
    state_path.write_bytes(content)

    factory = get_session_factory()
    with factory() as session:
        account = session.query(Account).filter(Account.name == account_name).first()
        if account:
            account.session_valid = True
            session.add(ActivityLog(
                account_id=account.id,
                event_type="session_uploaded",
                message=f"Session file uploaded for '{account_name}'",
            ))
            session.commit()

    return JSONResponse({"success": True, "message": "Session file uploaded successfully"})


@router.get("/api/accounts/{account_id}/download-session")
async def download_session_file(account_id: int):
    """Download the state.json session file for this account to use on a remote server."""
    factory = get_session_factory()
    with factory() as session:
        account = session.query(Account).filter(Account.id == account_id).first()
        if not account:
            return JSONResponse({"error": "Account not found"}, status_code=404)
        account_name = account.name

    state_path = get_account_dir(account_name) / "auth" / "state.json"
    if not state_path.exists():
        return JSONResponse({"error": "No session file found for this account. Please log in first."}, status_code=404)

    return FileResponse(
        path=str(state_path),
        filename=f"{account_name}_state.json",
        media_type="application/json",
    )


@router.post("/api/accounts/{account_id}/launch-login", response_class=JSONResponse)
async def launch_browser_login(account_id: int):
    """Launch a visual browser window on the local machine for one-click Pinterest login."""
    factory = get_session_factory()
    with factory() as session:
        account = session.query(Account).filter(Account.id == account_id).first()
        if not account:
            return JSONResponse({"error": "Account not found"}, status_code=404)
        account_name = account.name
        proxy_url = account.proxy_url

    if _active_logins.get(account_id, {}).get("status") == "in_progress":
        return JSONResponse({"success": True, "message": "Login browser is already open for this account."})

    _active_logins[account_id] = {
        "status": "in_progress",
        "message": "Browser launched. Please log in to Pinterest in the popup window...",
        "timestamp": time.time(),
        "force_save": False,
    }

    def _login_worker(acct_id: int, acct_name: str, proxy: str | None):
        from playwright.sync_api import sync_playwright, Error as PlaywrightError
        LOGIN_URL = "https://www.pinterest.com/login/"
        TIMEOUT_SECONDS = 300  # 5 minutes

        dirs = ensure_account_dirs(acct_name)
        state_file = dirs["auth"] / "state.json"

        launch_kwargs = {"headless": False}
        proxy_config = parse_playwright_proxy(proxy)
        if proxy_config:
            launch_kwargs["proxy"] = proxy_config

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(**launch_kwargs)
                context = browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page = context.new_page()
                page.goto(LOGIN_URL, wait_until="domcontentloaded")

                start_time = time.time()
                logged_in = False

                while time.time() - start_time < TIMEOUT_SECONDS:
                    # Check 1: User explicitly clicked "I'm Logged In — Save Session Now" in the UI
                    if _active_logins.get(acct_id, {}).get("force_save"):
                        logged_in = True
                        break

                    # Check 2: Cookie authentication indicator (_auth == "1")
                    try:
                        cookies = context.cookies()
                        cookie_map = {c["name"]: c["value"] for c in cookies}
                        # _auth is "1" only when the user is logged into Pinterest
                        if cookie_map.get("_auth") == "1":
                            logged_in = True
                            break
                    except Exception:
                        pass

                    # Check 3: Navigation away from login to homefeed/business/profile
                    try:
                        pages = context.pages
                        if not pages:
                            break

                        for p_curr in pages:
                            url = p_curr.url or ""
                            # If user is still on the login page or auth flow, do not trigger
                            if "/login" in url or "/auth" in url or "/password" in url or url == "about:blank":
                                continue

                            if "pinterest.com" in url:
                                if any(x in url for x in ["/homefeed", "/business", "/settings", "/today", "/ideas", "/created", "/saved"]):
                                    logged_in = True
                                    break
                                # If navigated to a user profile or board URL (e.g. pinterest.com/username)
                                if url.count("/") >= 3 and url != "https://www.pinterest.com/":
                                    logged_in = True
                                    break

                                # Check DOM for logged-in profile avatar
                                try:
                                    if p_curr.locator('[data-test-id="header-profile"], [aria-label="Your profile"], [data-test-id="header-avatar"], [data-test-id="business-profile-avatar"]').count() > 0:
                                        logged_in = True
                                        break
                                except Exception:
                                    pass

                        if logged_in:
                            break
                    except PlaywrightError:
                        # If window was closed, check if user was authenticated before closing
                        try:
                            cookies = context.cookies()
                            cookie_map = {c["name"]: c["value"] for c in cookies}
                            if cookie_map.get("_auth") == "1":
                                logged_in = True
                        except Exception:
                            pass
                        break

                    time.sleep(1)

                if not logged_in:
                    try:
                        browser.close()
                    except Exception:
                        pass
                    _active_logins[acct_id] = {
                        "status": "failed",
                        "message": "Login window was closed or timed out before login was detected.",
                        "timestamp": time.time(),
                        "force_save": False,
                    }
                    return

                # Allow cookies and localStorage tokens to settle
                time.sleep(2)
                context.storage_state(path=str(state_file))
                try:
                    browser.close()
                except Exception:
                    pass

                # Update database
                f = get_session_factory()
                with f() as db_session:
                    acct = db_session.query(Account).filter(Account.id == acct_id).first()
                    if acct:
                        acct.session_valid = True
                        db_session.add(ActivityLog(
                            account_id=acct.id,
                            event_type="session_captured",
                            message=f"Interactive login successful for '{acct_name}'",
                        ))
                        db_session.commit()

                _active_logins[acct_id] = {
                    "status": "success",
                    "message": f"Login detected! Session saved successfully for '{acct_name}'.",
                    "timestamp": time.time(),
                    "force_save": False,
                }

        except Exception as e:
            _active_logins[acct_id] = {
                "status": "failed",
                "message": f"Browser error: {str(e)}",
                "timestamp": time.time(),
                "force_save": False,
            }

    thread = threading.Thread(target=_login_worker, args=(account_id, account_name, proxy_url), daemon=True)
    thread.start()

    return JSONResponse({
        "success": True,
        "message": f"Browser opened for '{account_name}'. Please log in to Pinterest in the popup window.",
    })


@router.post("/api/accounts/{account_id}/force-save-session", response_class=JSONResponse)
async def force_save_session(account_id: int):
    """Trigger immediate capture and saving of session state from the open browser window."""
    if account_id in _active_logins:
        _active_logins[account_id]["force_save"] = True
        return JSONResponse({"success": True, "message": "Saving session state now..."})
    return JSONResponse({"error": "No active login browser found for this account"}, status_code=400)


@router.get("/api/accounts/{account_id}/login-status", response_class=JSONResponse)
async def check_login_progress(account_id: int):
    """Check the status of an in-progress interactive browser login."""
    info = _active_logins.get(account_id, {"status": "idle", "message": "No login in progress."})
    return JSONResponse(info)


@router.post("/api/accounts/{account_id}/check-session", response_class=JSONResponse)
async def check_session_status(account_id: int):
    factory = get_session_factory()
    with factory() as session:
        account = session.query(Account).filter(Account.id == account_id).first()
        if not account:
            return JSONResponse({"error": "Account not found"}, status_code=404)

        account_name = account.name
        proxy_url = account.proxy_url

    state_path = get_account_dir(account_name) / "auth" / "state.json"
    if not state_path.exists():
        return JSONResponse({"valid": False, "message": "No session file found. Click 'Log In (Browser)' or upload state.json."})

    try:
        from src.services.session import check_session
        is_valid = check_session(account_name, proxy_url)
    except Exception as e:
        return JSONResponse({"valid": False, "message": f"Error checking session: {str(e)}"})

    factory = get_session_factory()
    with factory() as session:
        account = session.query(Account).filter(Account.name == account_name).first()
        if account:
            account.session_valid = is_valid
            session.commit()

    return JSONResponse({
        "valid": is_valid,
        "message": "Session is valid" if is_valid else "Session expired — please re-login",
    })
