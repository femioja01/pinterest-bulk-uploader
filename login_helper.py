#!/usr/bin/env python3
"""
Pinterest Login Helper
----------------------
Standalone script run locally on the user's machine to authenticate with Pinterest
in a headful browser and export the session storage state (cookies + localStorage)
to `data/accounts/<name>/auth/state.json`.

Usage:
    python login_helper.py --account <name> [--proxy <url>]
"""

import argparse
from pathlib import Path
import sys
import time
from playwright.sync_api import sync_playwright, Error as PlaywrightError


LOGIN_URL = "https://www.pinterest.com/login/"
LOGIN_TIMEOUT_SECONDS = 300  # 5 minutes
POLL_INTERVAL_SECONDS = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Log into Pinterest in a local browser and save session state for bulk uploading."
    )
    parser.add_argument(
        "--account",
        "-a",
        required=True,
        help="Account identifier name (e.g., my_account)",
    )
    parser.add_argument(
        "--proxy",
        "-p",
        default=None,
        help="Optional proxy URL (e.g., http://username:password@proxyserver:port)",
    )
    return parser.parse_args()


def setup_account_directories(base_dir: Path, account_name: str) -> Path:
    """Create data directories for the account and return the auth directory path."""
    account_dir = base_dir / "data" / "accounts" / account_name
    subdirs = ["auth", "pins", "queue", "done", "failed"]
    for subdir in subdirs:
        (account_dir / subdir).mkdir(parents=True, exist_ok=True)
    return account_dir / "auth"


def main():
    args = parse_args()
    base_dir = Path(__file__).resolve().parent
    auth_dir = setup_account_directories(base_dir, args.account)
    state_file = auth_dir / "state.json"

    print(f"\n[+] Initializing login session for account: '{args.account}'")
    print(f"[+] Account directory prepared at: {auth_dir.parent}")

    launch_kwargs = {
        "headless": False,
    }
    if args.proxy:
        print(f"[+] Configuring proxy: {args.proxy}")
        try:
            from src.config import parse_playwright_proxy
            proxy_cfg = parse_playwright_proxy(args.proxy)
            if proxy_cfg:
                launch_kwargs["proxy"] = proxy_cfg
        except Exception:
            launch_kwargs["proxy"] = {"server": args.proxy}

    try:
        with sync_playwright() as p:
            print("[+] Launching Chromium browser...")
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

            print(f"[+] Navigating to {LOGIN_URL} ...")
            page.goto(LOGIN_URL, wait_until="domcontentloaded")

            print("\nPlease log in to Pinterest in the browser window. The script will detect when you are logged in.\n")

            start_time = time.time()
            logged_in = False

            while time.time() - start_time < LOGIN_TIMEOUT_SECONDS:
                try:
                    current_url = page.url
                    # Pinterest navigates away from /login/ once login is successful
                    if current_url and "/login" not in current_url and current_url != "about:blank":
                        logged_in = True
                        break
                except PlaywrightError:
                    # Browser or page may have been closed
                    print("\n[-] Browser window was closed before login was completed.")
                    break

                time.sleep(POLL_INTERVAL_SECONDS)

            if not logged_in:
                print("\n[-] Error: Login timed out after 5 minutes or was cancelled.")
                try:
                    browser.close()
                except Exception:
                    pass
                sys.exit(1)

            print("\n[+] Login detected! Saving session state...")
            # Allow time for cookies and local storage tokens to settle
            time.sleep(3)

            context.storage_state(path=str(state_file))
            browser.close()

            print(f"\n[✓] Session successfully saved to:\n    {state_file.resolve()}\n")
            print("Upload this state.json file to the dashboard via the Accounts page, or copy it to the server.\n")

    except Exception as e:
        print(f"\n[-] An error occurred during login helper execution: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
