"""Telegram notification service for Pinterest Bulk Uploader."""

import logging
import httpx
from src.models.database import get_session_factory, get_setting

logger = logging.getLogger(__name__)


def _clean_token(token: str | None) -> str:
    """Clean and normalize a Telegram bot token."""
    if not token:
        return ""
    t = str(token).strip()
    if t.lower().startswith("bot") and ":" in t and not t.startswith("bot:"):
        t = t[3:]
    return t.strip()


def _clean_chat_id(chat_id: str | None) -> str:
    """Clean and normalize a Telegram chat ID."""
    if not chat_id:
        return ""
    return str(chat_id).strip()


async def send_telegram(message: str, bot_token: str, chat_id: str) -> tuple[bool, str]:
    token = _clean_token(bot_token)
    chat = _clean_chat_id(chat_id)

    if not token or not chat:
        logger.warning("Telegram bot token or chat ID is missing. Notification not sent.")
        return False, "Bot token or Chat ID is missing."

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat,
        "text": message,
        "parse_mode": "HTML",
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=12.0)
            if response.status_code == 200:
                return True, "Message sent successfully"

            try:
                res_data = response.json()
                desc = res_data.get("description", response.text)
            except Exception:
                desc = response.text

            logger.error(f"Telegram API error ({response.status_code}): {desc}")

            if "chat not found" in desc.lower():
                return False, "Chat not found! Please open your bot in Telegram and send /start to it first, and make sure your Chat ID is your numeric ID (e.g. from @userinfobot)."
            elif "unauthorized" in desc.lower():
                return False, "Invalid Bot Token! Please verify your token from @BotFather."
            elif "blocked" in desc.lower():
                return False, "The bot was blocked by the user. Unblock the bot on Telegram."

            return False, f"Telegram error: {desc}"

    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False, f"Network error connecting to Telegram: {str(e)}"


def _get_telegram_creds() -> tuple[str, str]:
    session_factory = get_session_factory()
    with session_factory() as db_session:
        bot_token = get_setting(db_session, "telegram_bot_token", "")
        chat_id = get_setting(db_session, "telegram_chat_id", "")
        return bot_token, chat_id


def _should_notify(setting_key: str) -> bool:
    session_factory = get_session_factory()
    with session_factory() as db_session:
        return get_setting(db_session, setting_key, "true").lower() == "true"


async def notify_upload_success(account_name: str, filename: str, pin_count: int):
    if not _should_notify("notify_on_success"):
        return
    msg = f"✅ <b>[{account_name}]</b> Uploaded {filename} ({pin_count} pins) successfully"
    token, chat_id = _get_telegram_creds()
    await send_telegram(msg, token, chat_id)


async def notify_upload_failed(account_name: str, filename: str, error: str):
    if not _should_notify("notify_on_failure"):
        return
    msg = f"❌ <b>[{account_name}]</b> Failed to upload {filename}: {error}"
    token, chat_id = _get_telegram_creds()
    await send_telegram(msg, token, chat_id)


async def notify_session_expired(account_name: str):
    if not _should_notify("notify_on_session_expiry"):
        return
    msg = f"🔑 <b>[{account_name}]</b> Pinterest session expired! Re-run login setup."
    token, chat_id = _get_telegram_creds()
    await send_telegram(msg, token, chat_id)


async def notify_csv_split(account_name: str, master_name: str, batch_count: int, batch_size: int):
    msg = f"🔄 <b>[{account_name}]</b> Split {master_name} → {batch_count} batches ({batch_size} pins each)"
    token, chat_id = _get_telegram_creds()
    await send_telegram(msg, token, chat_id)


async def notify_daily_summary(results: dict):
    if not _should_notify("notify_daily_summary"):
        return
    msg = "📊 <b>Daily Upload Summary</b>\n\n"
    for account, stats in results.items():
        msg += f"<b>{account}</b>: {stats.get('uploaded', 0)} batches uploaded, {stats.get('failed', 0)} failed.\n"
    token, chat_id = _get_telegram_creds()
    await send_telegram(msg, token, chat_id)


async def notify_low_queue(account_name: str, remaining_batches: int, remaining_pins: int):
    """Alert user via Telegram when an account's scheduled queue is running low or empty."""
    if not _should_notify("notify_on_low_queue"):
        return

    if remaining_batches <= 0:
        msg = (
            f"🚨 <b>[{account_name}] Queue is EMPTY!</b>\n\n"
            f"There are no scheduled pin batches left in the queue. "
            f"Upload a new Master CSV to keep automated pin posting active."
        )
    else:
        msg = (
            f"⚠️ <b>[{account_name}] Queue Running Low!</b>\n\n"
            f"Only <b>{remaining_batches} batch(es)</b> left in queue ({remaining_pins} pins remaining).\n"
            f"Upload a new Master CSV soon to prevent any interruption to your posting schedule."
        )

    token, chat_id = _get_telegram_creds()
    await send_telegram(msg, token, chat_id)


async def send_test_message(bot_token: str, chat_id: str) -> tuple[bool, str]:
    return await send_telegram("🔔 <b>Test Notification</b>\n\nYour Telegram bot is successfully connected to Pinterest Bulk Uploader!", bot_token, chat_id)
