"""Playwright automated CSV uploader for Pinterest Bulk Pin Creation."""

import logging
import time
from pathlib import Path
from playwright.sync_api import sync_playwright
from src.config import PINTEREST_BULK_URL, ensure_account_dirs, parse_playwright_proxy

logger = logging.getLogger(__name__)


def upload_csv_to_pinterest(csv_path: Path, account_name: str, proxy_url: str | None = None) -> tuple[bool, str]:
    """Upload a CSV batch file to Pinterest via Playwright automation."""
    dirs = ensure_account_dirs(account_name)
    state_path = dirs["auth"] / "state.json"

    if not state_path.exists():
        logger.error(f"Cannot upload: Session state missing for account '{account_name}'")
        return False, "Session state missing"

    try:
        with sync_playwright() as p:
            browser_args = {}
            proxy_config = parse_playwright_proxy(proxy_url)
            if proxy_config:
                browser_args["proxy"] = proxy_config

            browser = p.chromium.launch(headless=True, **browser_args)
            context = browser.new_context(
                storage_state=str(state_path),
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            logger.info(f"Navigating to Pinterest Bulk Upload for '{account_name}'...")
            page.goto(PINTEREST_BULK_URL, wait_until="domcontentloaded", timeout=35000)

            # Check if session has expired
            if "login" in page.url:
                page.screenshot(path=str(dirs["failed"] / f"{csv_path.stem}_session_expired.png"))
                browser.close()
                logger.warning(f"Session expired for account '{account_name}' (redirected to login)")
                return False, "session_expired"

            # Wait for file inputs to be attached to the DOM (they may be styled hidden by Pinterest's UI)
            page.wait_for_selector('input[type="file"]', state="attached", timeout=30000)

            # Locate the file input that accepts CSV files
            input_element = page.locator('input[type="file"][accept*=".csv"]').first
            if not input_element.count():
                input_element = page.locator('input[type="file"]').first

            if not input_element.count():
                page.screenshot(path=str(dirs["failed"] / f"{csv_path.stem}_no_input.png"))
                browser.close()
                return False, "Could not find Pinterest CSV file input element"

            logger.info(f"Uploading batch '{csv_path.name}' to Pinterest for '{account_name}'...")
            input_element.set_input_files(str(csv_path.resolve()))

            # Wait for Pinterest to process the upload and render the success/error message
            time.sleep(10)

            page_text = page.locator("body").inner_text()
            page_text_lower = page_text.lower()

            if "success" in page_text_lower or "upload successful" in page_text_lower or "pins are being created" in page_text_lower:
                page.screenshot(path=str(dirs["done"] / f"{csv_path.stem}_success.png"))
                browser.close()
                logger.info(f"Successfully uploaded '{csv_path.name}' to Pinterest for '{account_name}'!")
                return True, "success"

            if "error" in page_text_lower or "invalid" in page_text_lower:
                page.screenshot(path=str(dirs["failed"] / f"{csv_path.stem}_failed.png"))
                browser.close()
                logger.error(f"Pinterest reported an error uploading '{csv_path.name}'")
                return False, "Pinterest reported an error processing this CSV"

            # Fallback: assume success if no error was displayed
            page.screenshot(path=str(dirs["done"] / f"{csv_path.stem}_uploaded.png"))
            browser.close()
            return True, "success"

    except Exception as e:
        logger.error(f"Exception during upload for '{account_name}': {e}")
        return False, str(e)
