"""Application configuration — loads from environment and provides defaults."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
ACCOUNTS_DIR = DATA_DIR / "accounts"
DB_PATH = DATA_DIR / "app.db"

# App
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-to-a-random-string")
TZ = os.getenv("TZ", "Africa/Lagos")

# Defaults (can be overridden from dashboard, stored in DB)
DEFAULT_UPLOAD_SCHEDULE = os.getenv("UPLOAD_SCHEDULE", "0 9 */2 * *")
DEFAULT_BATCH_SIZE = int(os.getenv("DEFAULT_BATCH_SIZE", "100"))
BATCH_DELAY_SECONDS = int(os.getenv("BATCH_DELAY_SECONDS", "30"))

# Pinterest
PINTEREST_BULK_URL = "https://www.pinterest.com/settings/bulk-create-pins/"
PINTEREST_LOGIN_URL = "https://www.pinterest.com/login/"
PINTEREST_HOME_URL = "https://www.pinterest.com/"

# CSV columns expected by Pinterest bulk upload
PINTEREST_CSV_COLUMNS = [
    "Title", "Media URL", "Pinterest Board", "Thumbnail",
    "Description", "Link", "Publish Date", "Keywords"
]


def get_account_dir(account_name: str) -> Path:
    """Get the data directory for a specific account."""
    return ACCOUNTS_DIR / account_name


def ensure_account_dirs(account_name: str) -> dict[str, Path]:
    """Create and return all subdirectories for an account."""
    base = get_account_dir(account_name)
    dirs = {
        "auth": base / "auth",
        "pins": base / "pins",
        "queue": base / "queue",
        "done": base / "done",
        "failed": base / "failed",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def parse_playwright_proxy(proxy_str: str | None) -> dict | None:
    """Parse proxy strings in multiple formats for Playwright Chromium.

    Supported formats:
    - IP:PORT:USER:PASS  (e.g., '9.142.15.214:6370:pwgylvly:hxbo32275cd2')
    - http://IP:PORT:USER:PASS
    - http://USER:PASS@IP:PORT or USER:PASS@IP:PORT
    - IP:PORT or http://IP:PORT
    - socks5://...

    Returns:
        dict suitable for playwright `proxy` argument, e.g.:
        {"server": "http://9.142.15.214:6370", "username": "...", "password": "..."}
        or None if no proxy is configured.
    """
    if not proxy_str or not str(proxy_str).strip():
        return None

    p = str(proxy_str).strip()

    # Extract protocol prefix if present
    protocol = "http"
    for prefix in ["http://", "https://", "socks5://"]:
        if p.lower().startswith(prefix):
            protocol = prefix.replace("://", "")
            p = p[len(prefix):]
            break

    # Format 1: IP:PORT:USER:PASS (4 colon-separated parts)
    parts = p.split(":")
    if len(parts) == 4:
        host, port, user, pwd = parts
        return {
            "server": f"{protocol}://{host}:{port}",
            "username": user,
            "password": pwd,
        }

    # Format 2: USER:PASS@HOST:PORT
    if "@" in p:
        auth, host_port = p.split("@", 1)
        if ":" in auth:
            user, pwd = auth.split(":", 1)
        else:
            user, pwd = auth, ""
        return {
            "server": f"{protocol}://{host_port}",
            "username": user,
            "password": pwd,
        }

    # Format 3: HOST:PORT
    if len(parts) == 2:
        host, port = parts
        return {"server": f"{protocol}://{host}:{port}"}

    return {"server": f"{protocol}://{p}"}
