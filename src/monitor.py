"""
Gmail inbox monitor — IMAP with App Password.
No OAuth, no credential files. Just email + App Password.
Checks for replies to sent cold emails and returns new Reply objects.
"""

import email
import imaplib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.header import decode_header
from typing import Optional

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993


@dataclass
class Reply:
    from_email: str
    from_name: str
    subject: str
    body_preview: str
    thread_id: str        # In-Reply-To header value (our sent message ID)
    message_id: str       # This message's ID
    received_at: str
    original_to: str      # The contact email we originally emailed


# ── IMAP helpers ───────────────────────────────────────────────────────────────

def _imap_connect(user_config: dict) -> imaplib.IMAP4_SSL:
    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    imap.login(user_config["email"], user_config["gmail_app_password"])
    return imap


def _decode_header_value(value: str) -> str:
    parts = decode_header(value or "")
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)


def _get_body(msg: email.message.Message) -> str:
    """Extract plain text body from email message."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in cd:
                try:
                    charset = part.get_content_charset() or "utf-8"
                    return part.get_payload(decode=True).decode(charset, errors="replace")
                except Exception:
                    continue
    else:
        try:
            charset = msg.get_content_charset() or "utf-8"
            return msg.get_payload(decode=True).decode(charset, errors="replace")
        except Exception:
            pass
    return ""


def _parse_from(from_header: str) -> tuple[str, str]:
    """Parse 'Name <email>' into (name, email)."""
    from_header = _decode_header_value(from_header)
    if "<" in from_header:
        name = from_header.split("<")[0].strip().strip('"')
        addr = from_header.split("<")[1].rstrip(">").strip()
    else:
        name = ""
        addr = from_header.strip()
    return name, addr


# ── Main monitoring function ───────────────────────────────────────────────────

def check_for_replies(
    user_config: dict,
    sent_message_ids: dict[str, str],   # {our_msg_id: contact_email}
    since_hours: int = 24,
) -> list[Reply]:
    """
    Check Gmail inbox via IMAP for replies to our cold emails.

    sent_message_ids maps the ID we stored (email_timestamp) to the contact's email.
    We identify replies by checking the sender email against contacts we emailed.
    """
    if not sent_message_ids:
        return []

    # Build a set of contact emails we're watching for replies from
    watched_emails = {v.lower() for v in sent_message_ids.values()}
    # Reverse map: contact_email → our_msg_id
    contact_to_msgid = {v.lower(): k for k, v in sent_message_ids.items()}

    try:
        imap = _imap_connect(user_config)
    except imaplib.IMAP4.error as e:
        logger.error("IMAP login failed for %s — check App Password: %s", user_config["email"], e)
        return []
    except Exception as e:
        logger.error("IMAP connection failed: %s", e)
        return []

    replies: list[Reply] = []

    try:
        imap.select("INBOX")

        # Search for messages since N hours ago
        since_date = (datetime.now() - timedelta(hours=since_hours)).strftime("%d-%b-%Y")
        _, msg_nums = imap.search(None, f'(SINCE "{since_date}" UNSEEN)')

        if not msg_nums[0]:
            logger.info("[%s] No new messages in last %dh", user_config["name"], since_hours)
            return []

        num_list = msg_nums[0].split()
        logger.info("[%s] Checking %d new messages", user_config["name"], len(num_list))

        for num in num_list:
            try:
                _, data = imap.fetch(num, "(RFC822)")
                raw = data[0][1]
                msg = email.message_from_bytes(raw)

                from_name, from_addr = _parse_from(msg.get("From", ""))

                # Only process if this sender is someone we cold-emailed
                if from_addr.lower() not in watched_emails:
                    continue

                subject   = _decode_header_value(msg.get("Subject", ""))
                date_str  = msg.get("Date", "")
                body      = _get_body(msg)
                msg_id    = msg.get("Message-ID", "")
                in_reply  = msg.get("In-Reply-To", "")

                body_preview = body[:300].replace("\n", " ").strip()
                original_to  = contact_to_msgid.get(from_addr.lower(), from_addr.lower())

                replies.append(Reply(
                    from_email=from_addr,
                    from_name=from_name,
                    subject=subject,
                    body_preview=body_preview,
                    thread_id=in_reply or msg_id,
                    message_id=msg_id,
                    received_at=date_str,
                    original_to=from_addr,   # the contact who replied
                ))

                # Mark as seen so we don't re-notify
                imap.store(num, "+FLAGS", "\\Seen")
                logger.info("Reply from %s (%s)", from_addr, subject)

            except Exception as e:
                logger.warning("Error parsing message %s: %s", num, e)
                continue

    finally:
        try:
            imap.logout()
        except Exception:
            pass

    logger.info("[%s] %d new replies found", user_config["name"], len(replies))
    return replies


def test_imap_connection(user_config: dict) -> tuple[bool, str]:
    """Test IMAP connection. Returns (success, message)."""
    try:
        imap = _imap_connect(user_config)
        imap.select("INBOX")
        imap.logout()
        return True, "Connected successfully"
    except imaplib.IMAP4.error:
        return False, "Authentication failed — check App Password and that IMAP is enabled in Gmail settings"
    except Exception as e:
        return False, str(e)
