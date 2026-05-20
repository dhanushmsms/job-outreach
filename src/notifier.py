"""Triple notifications — Telegram bot + WhatsApp (CallMeBot) + Gmail self-email via SMTP."""

import logging
import urllib.parse

import requests

from src.emailer import send_plain_email

logger = logging.getLogger(__name__)


def notify_whatsapp(message: str, phone: str, api_key: str) -> bool:
    """Send a WhatsApp message via CallMeBot free API.

    Setup (2 min, free — no account needed):
      1. Add +34 644 59 73 99 to your WhatsApp contacts as "CallMeBot"
      2. Send this message to that contact: 'I allow callmebot to send me messages'
      3. You'll receive your personal api_key via WhatsApp
      4. Set whatsapp_phone (with country code, e.g. 447700000000) and
         whatsapp_api_key in your user YAML
    """
    if not phone or not api_key or api_key.startswith("YOUR"):
        logger.warning("WhatsApp (CallMeBot) not configured — skipping")
        return False
    try:
        encoded = urllib.parse.quote(message)
        resp = requests.get(
            f"https://api.callmebot.com/whatsapp.php?phone={phone}&text={encoded}&apikey={api_key}",
            timeout=10,
        )
        if resp.status_code == 200:
            return True
        logger.warning("CallMeBot returned %s: %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        logger.error("WhatsApp notification failed: %s", e)
        return False


def notify_telegram(message: str, bot_token: str, chat_id: str) -> bool:
    if not bot_token or not chat_id or bot_token.startswith("YOUR"):
        logger.warning("Telegram not configured — skipping")
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error("Telegram notification failed: %s", e)
        return False


def notify_cookie_expired(user_config: dict, settings: dict) -> None:
    """Alert all users that the LinkedIn li_at cookie has expired and needs refreshing."""
    msg = (
        "⚠️ LinkedIn cookie expired!\n\n"
        "The LinkedIn Posts scraper returned 0 results — your li_at cookie has likely expired.\n\n"
        "To fix:\n"
        "1. Open LinkedIn in Chrome\n"
        "2. Press F12 → Console tab\n"
        "3. Paste: document.cookie.split(';').find(c => c.trim().startsWith('li_at')).trim()\n"
        "4. Send the new value to Claude to update settings.yaml\n\n"
        "Until fixed, LinkedIn Posts scraping is disabled (Jobs + Indeed still work)."
    )
    tg_msg = (
        "⚠️ *LinkedIn cookie expired!*\n\n"
        "LinkedIn Posts scraper returned 0 results — li\\_at cookie needs refreshing\\.\n\n"
        "Open LinkedIn → F12 → Console → run:\n"
        "`document.cookie.split(';').find(c => c.trim().startsWith('li_at')).trim()`\n\n"
        "Send the new value to Claude to update settings\\.yaml\\."
    )
    # Notify every configured user
    notify_telegram(
        message=tg_msg,
        bot_token=settings.get("telegram_bot_token", ""),
        chat_id=user_config.get("telegram_chat_id", ""),
    )
    notify_whatsapp(
        message=msg,
        phone=user_config.get("whatsapp_phone", ""),
        api_key=user_config.get("whatsapp_api_key", ""),
    )
    send_plain_email(
        to=user_config["email"],
        subject="[Job Outreach] ⚠️ LinkedIn cookie expired — action needed",
        body=msg,
        user_config=user_config,
    )
    logger.warning("LinkedIn cookie expiry notification sent to %s", user_config.get("email"))


def notify_reply_received(reply, user_config: dict, settings: dict, all_users: list = None) -> None:
    """
    Notify about a new reply.
    - The person whose email got the reply gets full notification + draft email
    - ALL other users get a Telegram ping so everyone stays in the loop
    """
    recipient_name = user_config.get("name", "").split()[0]

    plain_msg = (
        f"New reply for {recipient_name}!\n\n"
        f"From: {reply.from_name} <{reply.from_email}>\n"
        f"Subject: {reply.subject}\n\n"
        f"{reply.body_preview[:200]}\n\n"
        f"Check {recipient_name}'s Gmail — a draft reply is ready to review."
    )

    tg_msg = (
        f"📩 *New reply for {recipient_name}!*\n\n"
        f"*From:* {reply.from_name} <{reply.from_email}>\n"
        f"*Subject:* {reply.subject}\n\n"
        f"_{reply.body_preview[:200]}_"
    )

    # ── Notify the person whose email got the reply ────────────────────────────
    notify_telegram(
        message=tg_msg,
        bot_token=settings.get("telegram_bot_token", ""),
        chat_id=user_config.get("telegram_chat_id", ""),
    )
    notify_whatsapp(
        message=plain_msg,
        phone=user_config.get("whatsapp_phone", ""),
        api_key=user_config.get("whatsapp_api_key", ""),
    )
    email_body = (
        f"You received a reply from {reply.from_name} ({reply.from_email})\n\n"
        f"Subject: {reply.subject}\n\n"
        f"Preview:\n{reply.body_preview}\n\n"
        f"---\nA draft reply has been sent to {reply.from_email} — check your Sent folder "
        f"or review before sending from the Inbox Monitor page."
    )
    send_plain_email(
        to=user_config["email"],
        subject=f"[Job Outreach] Reply from {reply.from_name or reply.from_email}",
        body=email_body,
        user_config=user_config,
    )

    # ── Also notify all OTHER users (admin visibility) ─────────────────────────
    for other in (all_users or []):
        if other.get("email") == user_config.get("email"):
            continue  # already notified above
        notify_telegram(
            message=tg_msg,
            bot_token=settings.get("telegram_bot_token", ""),
            chat_id=other.get("telegram_chat_id", ""),
        )
        notify_whatsapp(
            message=plain_msg,
            phone=other.get("whatsapp_phone", ""),
            api_key=other.get("whatsapp_api_key", ""),
        )
