"""Pinterest session health check with stealth anti-detection."""

import logging
from pathlib import Path
from playwright.sync_api import sync_playwright
from src.config import PINTEREST_HOME_URL, ensure_account_dirs, parse_playwright_proxy
from src.services.uploader import CHROMIUM_STEALTH_ARGS, REALISTIC_USER_AGENT, STEALTH_INIT_SCRIPT

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
            launch_options = {
                "headless": True,
                "args": CHROMIUM_STEALTH_ARGS,
            }
            proxy_config = parse_playwright_proxy(proxy_url)
            if proxy_config:
                launch_options["proxy"] = proxy_config

            browser = p.chromium.launch(**launch_options)
            context = browser.new_context(
                storage_state=str(state_path),
                viewport={"width": 1440, "height": 900},
                user_agent=REALISTIC_USER_AGENT,
                locale="en-US",
            )
            context.add_init_script(STEALTH_INIT_SCRIPT)
            page = context.new_page()

            logger.info(f"Checking session for {account_name}...")
            page.goto(PINTEREST_HOME_URL, wait_until="domcontentloaded", timeout=30000)

            # If redirected to login, session has expired
            if "login" in page.url:
                logger.warning(f"Session for {account_name} expired (redirected to login).")
                context.close()
                browser.close()
                return False

            logger.info(f"Session for {account_name} is valid.")
            context.close()
            browser.close()
            return True
    except Exception as e:
        logger.error(f"Error checking session for {account_name}: {e}")
        return False
