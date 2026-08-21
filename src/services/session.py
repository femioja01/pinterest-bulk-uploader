import logging
from pathlib import Path
from playwright.sync_api import sync_playwright
from src.config import PINTEREST_HOME_URL, get_account_dir, ensure_account_dirs, parse_playwright_proxy

logger = logging.getLogger(__name__)

def get_state_path(account_name: str) -> Path:
    dirs = ensure_account_dirs(account_name)
    return dirs["auth"] / "state.json"

def state_exists(account_name: str) -> bool:
    return get_state_path(account_name).exists()

def check_session(account_name: str, proxy_url: str | None = None) -> bool:
    state_path = get_state_path(account_name)
    if not state_path.exists():
        logger.warning(f"Session state not found for {account_name}")
        return False
        
    try:
        with sync_playwright() as p:
            browser_args = {}
            proxy_config = parse_playwright_proxy(proxy_url)
            if proxy_config:
                browser_args["proxy"] = proxy_config
                
            browser = p.chromium.launch(headless=True, **browser_args)
            context = browser.new_context(storage_state=str(state_path))
            page = context.new_page()
            
            logger.info(f"Checking session for {account_name}...")
            page.goto(PINTEREST_HOME_URL, wait_until="domcontentloaded", timeout=30000)
            
            # If we get redirected to login, session is expired
            if "login" in page.url:
                logger.warning(f"Session for {account_name} expired (redirected to login).")
                browser.close()
                return False
                
            logger.info(f"Session for {account_name} is valid.")
            browser.close()
            return True
    except Exception as e:
        logger.error(f"Error checking session for {account_name}: {e}")
        return False
