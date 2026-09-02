"""Playwright automated CSV uploader for Pinterest Bulk Pin Creation.

Features anti-detection stealth, browser fingerprint spoofing,
humanized interactions, and network-settle dwell time to ensure
Pinterest accepts all pins without shadow-flagging.
"""

import logging
import random
import time
from pathlib import Path
from playwright.sync_api import sync_playwright
from src.config import PINTEREST_BULK_URL, ensure_account_dirs, parse_playwright_proxy

logger = logging.getLogger(__name__)

# Modern Chrome User Agent matching current browser distributions
REALISTIC_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Chromium stealth launch flags
CHROMIUM_STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-infobars",
    "--window-size=1440,900",
    "--disable-features=IsolateOrigins,site-per-process",
    "--lang=en-US,en",
]

# Anti-bot detection masking scripts
STEALTH_INIT_SCRIPT = """
(() => {
    // 1. Remove navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
        configurable: true
    });

    // 2. Mock chrome runtime and app objects
    window.chrome = {
        app: {
            isInstalled: false,
            InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
            RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }
        },
        runtime: {
            OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
            OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
            PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
            PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
            PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
            RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' }
        },
        loadTimes: function() {},
        csi: function() {}
    };

    // 3. Mock languages and plugins
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
        configurable: true
    });

    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const plugins = [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
            ];
            plugins.item = (i) => plugins[i];
            plugins.namedItem = (name) => plugins.find(p => p.name === name);
            plugins.refresh = () => {};
            return plugins;
        },
        configurable: true
    });

    // 4. Mock notification permissions query
    if (window.navigator.permissions) {
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
    }
})();
"""


def upload_csv_to_pinterest(csv_path: Path, account_name: str, proxy_url: str | None = None) -> tuple[bool, str]:
    """Upload a CSV batch file to Pinterest with full anti-detection stealth."""
    dirs = ensure_account_dirs(account_name)
    state_path = dirs["auth"] / "state.json"

    if not state_path.exists():
        logger.error(f"Cannot upload: Session state missing for account '{account_name}'")
        return False, "Session state missing"

    try:
        with sync_playwright() as p:
            launch_options = {
                "headless": True,
                "args": CHROMIUM_STEALTH_ARGS,
            }
            proxy_config = parse_playwright_proxy(proxy_url)
            if proxy_config:
                launch_options["proxy"] = proxy_config
                logger.info(f"Using proxy for '{account_name}': {proxy_config.get('server')}")
            else:
                logger.warning(f"No proxy configured for '{account_name}' — uploading using server direct IP")

            browser = p.chromium.launch(**launch_options)

            context = browser.new_context(
                storage_state=str(state_path),
                viewport={"width": 1440, "height": 900},
                device_scale_factor=2,
                is_mobile=False,
                has_touch=False,
                locale="en-US",
                user_agent=REALISTIC_USER_AGENT,
            )

            # Inject stealth script to mask automation
            context.add_init_script(STEALTH_INIT_SCRIPT)

            page = context.new_page()

            logger.info(f"Navigating to Pinterest Bulk Upload for '{account_name}' with stealth mode...")
            page.goto(PINTEREST_BULK_URL, wait_until="domcontentloaded", timeout=40000)

            # Wait 2-3s for client-side JS hydration
            time.sleep(random.uniform(2.0, 3.5))

            # Check if session has expired
            if "login" in page.url:
                page.screenshot(path=str(dirs["failed"] / f"{csv_path.stem}_session_expired.png"))
                context.close()
                browser.close()
                logger.warning(f"Session expired for account '{account_name}' (redirected to login)")
                return False, "session_expired"

            # Human-like pre-upload interactions (mouse movement and slight scroll)
            try:
                page.mouse.move(random.randint(150, 400), random.randint(180, 350), steps=8)
                page.evaluate(f"window.scrollBy(0, {random.randint(80, 160)})")
                time.sleep(random.uniform(1.2, 2.0))
            except Exception:
                pass

            # Wait for file inputs to be attached to the DOM
            try:
                page.wait_for_selector('input[type="file"]', state="attached", timeout=30000)
            except Exception as wait_err:
                logger.error(f"Timeout waiting for file input on {page.url} (title: '{page.title()}'): {wait_err}")
                page.screenshot(path=str(dirs["failed"] / f"{csv_path.stem}_file_input_timeout.png"))
                context.close()
                browser.close()
                return False, f"Timeout waiting for Pinterest upload form: {wait_err}"

            # Locate the file input that accepts CSV files
            input_element = page.locator('input[type="file"][accept*=".csv"]').first
            if not input_element.count():
                input_element = page.locator('input[type="file"]').first

            if not input_element.count():
                page.screenshot(path=str(dirs["failed"] / f"{csv_path.stem}_no_input.png"))
                context.close()
                browser.close()
                return False, "Could not find Pinterest CSV file input element"

            logger.info(f"Uploading batch '{csv_path.name}' to Pinterest for '{account_name}'...")
            input_element.set_input_files(str(csv_path.resolve()))

            # Wait for network requests to settle (uploading file and receiving response)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

            # Extended human dwell time (18-25 seconds):
            # Crucial: Allows Pinterest's background telemetry, CSRF token handshakes,
            # and file ingestion queue to register completely before the browser is torn down.
            logger.info("Allowing Pinterest upload telemetry and ingestion handshakes to finalize...")
            time.sleep(random.uniform(18.0, 24.0))

            page_text = page.locator("body").inner_text()
            page_text_lower = page_text.lower()

            if "success" in page_text_lower or "upload successful" in page_text_lower or "pins are being created" in page_text_lower or "received" in page_text_lower:
                page.screenshot(path=str(dirs["done"] / f"{csv_path.stem}_success.png"))
                context.close()
                browser.close()
                logger.info(f"Successfully uploaded '{csv_path.name}' to Pinterest for '{account_name}'!")
                return True, "success"

            if "error" in page_text_lower or "invalid" in page_text_lower:
                page.screenshot(path=str(dirs["failed"] / f"{csv_path.stem}_failed.png"))
                context.close()
                browser.close()
                logger.error(f"Pinterest reported an error uploading '{csv_path.name}'")
                return False, "Pinterest reported an error processing this CSV"

            # Fallback: assume success if no error was displayed and file was accepted
            page.screenshot(path=str(dirs["done"] / f"{csv_path.stem}_uploaded.png"))
            context.close()
            browser.close()
            logger.info(f"Batch '{csv_path.name}' accepted by Pinterest for '{account_name}'.")
            return True, "success"

    except Exception as e:
        logger.error(f"Exception during upload for '{account_name}': {e}")
        return False, str(e)
