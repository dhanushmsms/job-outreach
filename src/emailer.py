"""
Gmail sender — SMTP with App Password.
No OAuth, no Cloud Console, no credential files.
Each user just needs their Gmail + 16-char App Password.
"""

import base64
import logging
import os
import smtplib
import time
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import re

import anthropic

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# ── Contact validation ─────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

# Generic/role-based inboxes that are valid targets even without a personal name
_GENERIC_EMAIL_PREFIXES = {
    "careers", "jobs", "recruitment", "hr", "hiring", "talent",
    "apply", "applications", "resourcing", "people", "info",
    "contact", "hello", "team", "adjustments", "work",
}

def validate_contact(contact: dict) -> tuple[bool, str]:
    """
    Check name/email consistency before sending.
    Returns (is_valid, reason_if_invalid).

    Rules:
    1. Email must be present and valid format.
    2. If email is personal (not generic inbox), name must not be empty.
    3. If email contains a name pattern (e.g. john.smith@ or j.smith@),
       at least one token must loosely match a word in the contact name.
    4. Name must not be a placeholder like "Hiring Manager" alone with a
       personal email — that suggests a scrape mismatch.
    """
    email = (contact.get("email") or contact.get("Email") or "").strip()
    name  = (contact.get("name")  or contact.get("Name")  or "").strip()

    # Rule 1 — valid email format
    if not email or not _EMAIL_RE.match(email):
        return False, f"Invalid or missing email: '{email}'"

    local = email.split("@")[0].lower()
    local_tokens = set(re.split(r'[.\-_+]', local))

    # Rule 2 — generic inboxes are fine without a personal name
    if local_tokens & _GENERIC_EMAIL_PREFIXES:
        # Generic inbox — acceptable even with no name; will greet as "there"
        return True, ""

    # Rule 3 — personal email but no name at all
    if not name:
        return False, f"Personal email '{email}' but contact name is empty — possible scrape mismatch"

    # Rule 4 — placeholder-only names with personal email
    placeholder_names = {"hiring manager", "recruiter", "hr", "talent", "unknown", "n/a"}
    if name.lower() in placeholder_names:
        return False, f"Name is generic placeholder '{name}' with personal email '{email}' — skipping to avoid mismatch"

    # Rule 5 — loose name/email token match for personal emails
    # Only flag if email has 2+ tokens that look like name parts (not e.g. "sales123")
    name_tokens = set(re.split(r'[\s.\-_]', name.lower()))
    name_tokens = {t for t in name_tokens if len(t) > 1}
    email_name_tokens = {t for t in local_tokens if len(t) > 1 and not t.isdigit()}

    if len(email_name_tokens) >= 2:
        # Email looks like firstname.lastname — check at least 1 token overlaps
        # or first letter of first token matches first letter of a name token
        overlap = name_tokens & email_name_tokens
        initial_match = any(
            any(nt.startswith(et[0]) for nt in name_tokens)
            for et in email_name_tokens
        )
        if not overlap and not initial_match:
            return False, (
                f"Name '{name}' doesn't match email pattern '{email}' — "
                f"possible scrape mismatch (name tokens: {name_tokens}, email tokens: {email_name_tokens})"
            )

    return True, ""


# ── SMTP connection ────────────────────────────────────────────────────────────

def _smtp_send(msg: MIMEMultipart, user_config: dict) -> bool:
    """Send a MIMEMultipart message via Gmail SMTP."""
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(user_config["email"], user_config["gmail_app_password"])
            server.send_message(msg)
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error(
            "Gmail auth failed for %s — check App Password is correct and 2FA is enabled",
            user_config["email"],
        )
        return False
    except Exception as e:
        logger.error("SMTP send failed for %s: %s", user_config["email"], e)
        return False


def _build_message(
    to: str,
    subject: str,
    body: str,
    sender_name: str,
    sender_email: str,
    resume_pdf: Optional[str] = None,
) -> MIMEMultipart:
    msg = MIMEMultipart()
    msg["From"] = f"{sender_name} <{sender_email}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    if resume_pdf and os.path.exists(resume_pdf):
        with open(resume_pdf, "rb") as f:
            attachment = MIMEApplication(f.read(), _subtype="pdf")
        attachment.add_header(
            "Content-Disposition", "attachment", filename=Path(resume_pdf).name
        )
        msg.attach(attachment)
    elif resume_pdf:
        logger.warning("Resume PDF not found: %s", resume_pdf)

    return msg


# ── Warm-up logic ──────────────────────────────────────────────────────────────

def get_warmup_limit(user_config: dict, daily_sent_today: int) -> int:
    """
    Warm-up mode: start at 20/day, add 20 each week until daily_email_limit.
    Protects Gmail account from spam flags.
    """
    hard_limit = user_config.get("daily_email_limit", 100)
    if not user_config.get("warmup_mode", False):
        return hard_limit

    slug = user_config.get("email", "user").split("@")[0]
    state_file = Path(f"credentials/{slug}_warmup.txt")
    try:
        if state_file.exists():
            start_date = datetime.fromisoformat(state_file.read_text().strip())
            weeks_elapsed = (datetime.now() - start_date).days // 7
            return min(20 + weeks_elapsed * 20, hard_limit)
        else:
            state_file.parent.mkdir(exist_ok=True)
            state_file.write_text(datetime.now().isoformat())
            return 20
    except Exception:
        return 20


# ── Claude email generation ────────────────────────────────────────────────────

def get_sponsorship_line(country_config: dict) -> str:
    custom = country_config.get("custom_prompt", "").strip()
    if custom:
        return custom
    if country_config.get("needs_sponsorship"):
        return "Please note that I would require visa/work permit sponsorship to work in this country."
    return ""


def generate_personalized_email(
    contact: dict,
    user_config: dict,
    country_config: dict,
    anthropic_api_key,           # str or list[str]
    email_model: str = "claude-haiku-4-5-20251001",
) -> tuple[str, str]:
    """Use Claude to write a unique personalized cold email. Returns (subject, body)."""

    sponsorship_note = get_sponsorship_line(country_config)
    roles_str = ", ".join(user_config.get("target_roles", ["the role"]))
    first_name = (contact.get("name") or "").split()[0] or "there"

    # Company handling — never expose a placeholder in the email
    company = (contact.get("company") or "").strip()
    has_company = bool(company and company.lower() not in ("unknown", "n/a", ""))
    company_line = f"- Company: {company}" if has_company else ""
    company_rule = (
        "- Mention the company name naturally once"
        if has_company
        else "- Do NOT mention a company name — write as if reaching out speculatively to a recruiter. Never use placeholders like [Company], [Your Company], or brackets of any kind."
    )

    role_type = contact.get("role_type", "")
    role_type_context = ""
    if role_type == "contract":
        role_type_context = "- This is for a CONTRACT or FREELANCE role — mention you are open to contract work."
    elif role_type == "sponsorship":
        role_type_context = "- This role offers visa sponsorship — express specific enthusiasm about this."

    prompt = f"""Write a short, warm, professional cold email from a job seeker to a hiring manager or recruiter.

Sender:
- Name: {user_config['name']}
- Background: {user_config.get('resume_summary', '')}
- Target roles: {roles_str}
- Applying in: {contact.get('country', '')}
{"- Visa/work status: " + sponsorship_note if sponsorship_note else ""}
{role_type_context}

Recipient:
- First name: {first_name}
{company_line}
- Title: {contact.get('title', 'Hiring Manager')}

Rules:
- 4-5 sentences max, no fluff
{company_rule}
- Reference 1-2 specific skills matching {roles_str}
- End with a low-pressure ask for a 15-minute call
- Sound human and direct — not templated
- Never start with "I hope this email finds you well"
- Never use square brackets or angle brackets anywhere in the email
- Weave visa/PSW note in as a positive if present
- Mention resume is attached

Return EXACTLY:
SUBJECT: <subject line>
BODY:
<email body starting with "Hi {first_name},">"""

    # Support single key or list (rotation)
    if isinstance(anthropic_api_key, list):
        from src.key_rotator import KeyRotator
        rotator = KeyRotator("anthropic_email", anthropic_api_key)
        active_key = rotator.get_working()
    else:
        active_key = anthropic_api_key

    client = anthropic.Anthropic(api_key=active_key)

    try:
        response = client.messages.create(
            model=email_model,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        if ("rate" in str(e).lower() or "429" in str(e)) and isinstance(anthropic_api_key, list):
            from src.key_rotator import KeyRotator
            rotator = KeyRotator("anthropic_email", anthropic_api_key)
            rotator.mark_failed(active_key)
            next_key = rotator.get_working()
            client = anthropic.Anthropic(api_key=next_key)
            response = client.messages.create(
                model=email_model,
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
        else:
            raise

    if not response.content:
        raise ValueError("Claude returned empty response")
    raw = response.content[0].text.strip()
    lines = raw.splitlines()
    subject, body_lines, in_body = "", [], False

    for line in lines:
        if line.startswith("SUBJECT:") and not in_body:
            subject = line.replace("SUBJECT:", "").strip()
        elif line.startswith("BODY:"):
            in_body = True
        elif in_body:
            body_lines.append(line)

    body = "\n".join(body_lines).strip() or raw
    if not subject:
        subject = f"Exploring {roles_str} Opportunities at {contact.get('company', 'your company')}"

    return subject, body


# ── Main send function ─────────────────────────────────────────────────────────

def send_cold_email(
    contact: dict,
    user_config: dict,
    settings: dict,
    country_config: dict = None,
    sheets_client=None,   # optional SheetsClient — used to flag mismatches in sheet
) -> Optional[str]:
    """
    Generate a Claude-personalized email and send via Gmail SMTP.
    Returns a unique message ID string on success, None on failure.
    """
    country_config = country_config or {}
    api_keys = settings.get("anthropic_api_keys") or settings.get("anthropic_api_key", "")

    # ── Validate name/email match before doing anything ────────────────────────
    valid, reason = validate_contact(contact)
    if not valid:
        logger.warning("Skipping contact — %s", reason)
        # Highlight the row orange in the sheet so it's easy to spot and review
        if sheets_client:
            email = contact.get("email") or contact.get("Email", "")
            sheets_client.flag_mismatch(email, user_config["name"], reason)
        return None

    try:
        subject, body = generate_personalized_email(
            contact=contact,
            user_config=user_config,
            country_config=country_config,
            anthropic_api_key=api_keys,
            email_model=settings.get("email_model", "claude-haiku-4-5-20251001"),
        )
    except Exception as e:
        logger.error("Email generation failed for %s: %s", contact.get("email"), e)
        return None

    msg = _build_message(
        to=contact["email"],
        subject=subject,
        body=body,
        sender_name=user_config["name"],
        sender_email=user_config["email"],
        resume_pdf=user_config.get("resume_pdf"),
    )

    ok = _smtp_send(msg, user_config)
    if ok:
        # Generate a stable message ID from email + timestamp
        msg_id = f"{contact['email']}_{int(time.time())}"
        logger.info("Sent to %s (id=%s)", contact["email"], msg_id)
        return msg_id

    return None


def send_followup_email(
    contact: dict,
    user_config: dict,
    settings: dict,
    country_config: dict = None,
) -> Optional[str]:
    """Send a single follow-up email to a contact who hasn't replied."""
    country_config = country_config or {}
    api_keys = settings.get("anthropic_api_keys") or settings.get("anthropic_api_key", "")

    # ── Validate name/email match before doing anything ────────────────────────
    valid, reason = validate_contact(contact)
    if not valid:
        logger.warning("Skipping follow-up — %s", reason)
        return None

    first_name = (contact.get("Name") or contact.get("name") or "").split()[0] or "there"
    company = contact.get("Company") or contact.get("company", "your company")
    roles_str = ", ".join(user_config.get("target_roles", ["the role"]))

    prompt = f"""Write a very short, warm follow-up email from {user_config['name']} to {first_name} at {company}.

Context: {user_config['name']} sent a cold email about {roles_str} opportunities a week ago and hasn't heard back.

Rules:
- 2-3 sentences only
- Don't be pushy or apologetic
- Reference that you reached out last week
- Keep it light and human
- End with a simple question like "Would this week work for a quick call?"

Return EXACTLY:
SUBJECT: Re: <brief subject>
BODY:
<email body>"""

    if isinstance(api_keys, list):
        from src.key_rotator import KeyRotator
        active_key = KeyRotator("anthropic_followup", api_keys).get_working()
    else:
        active_key = api_keys

    try:
        client = anthropic.Anthropic(api_key=active_key)
        response = client.messages.create(
            model=settings.get("email_model", "claude-haiku-4-5-20251001"),
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        lines = raw.splitlines()
        subject, body_lines, in_body = "", [], False
        for line in lines:
            if line.startswith("SUBJECT:") and not in_body:
                subject = line.replace("SUBJECT:", "").strip()
            elif line.startswith("BODY:"):
                in_body = True
            elif in_body:
                body_lines.append(line)
        body = "\n".join(body_lines).strip() or raw
        if not subject:
            subject = f"Following up — {roles_str} at {company}"
    except Exception as e:
        logger.error("Follow-up generation failed: %s", e)
        return None

    email_addr = contact.get("Email") or contact.get("email", "")
    if not email_addr:
        return None

    msg = _build_message(
        to=email_addr,
        subject=subject,
        body=body,
        sender_name=user_config["name"],
        sender_email=user_config["email"],
        resume_pdf=user_config.get("resume_pdf"),
    )
    ok = _smtp_send(msg, user_config)
    if ok:
        msg_id = f"followup_{email_addr}_{int(time.time())}"
        logger.info("Follow-up sent to %s", email_addr)
        return msg_id
    return None


def send_plain_email(
    to: str,
    subject: str,
    body: str,
    user_config: dict,
) -> bool:
    """Send a plain email (notifications, self-alerts). Returns True on success."""
    msg = _build_message(
        to=to,
        subject=subject,
        body=body,
        sender_name=user_config["name"],
        sender_email=user_config["email"],
    )
    return _smtp_send(msg, user_config)
