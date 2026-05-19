"""Claude AI reply drafter — drafts a reply and emails it to the user for review."""

import logging
from typing import Optional

import anthropic

from src.emailer import send_plain_email

logger = logging.getLogger(__name__)


def draft_reply(
    reply,
    user_config: dict,
    settings: dict,
) -> Optional[str]:
    """
    Use Claude to draft a reply to a recruiter's response.
    Emails the draft to the user themselves so they can copy, edit, and send.
    Returns the draft body text.
    """
    api_keys = settings.get("anthropic_api_keys") or settings.get("anthropic_api_key", "")
    if isinstance(api_keys, list):
        from src.key_rotator import KeyRotator
        active_key = KeyRotator("anthropic_reply", api_keys).get_working()
    else:
        active_key = api_keys

    first_name = reply.from_name.split()[0] if reply.from_name else "there"

    prompt = f"""You are helping {user_config['name']} write a professional reply to a recruiter or hiring manager.

The reply they received:
From: {reply.from_name} ({reply.from_email})
Subject: {reply.subject}
Message: {reply.body_preview}

About {user_config['name']}:
{user_config.get('resume_summary', '')}

Write a warm, professional, concise reply (3-5 sentences):
- Acknowledge what they said with genuine enthusiasm
- Reinforce interest in the role/company
- Suggest a specific next step (call, share availability, ask for details)
- Sound natural and human, not templated

Return only the email body. Start directly — no subject line needed."""

    try:
        client = anthropic.Anthropic(api_key=active_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        draft_body = response.content[0].text.strip()
        full_draft = f"Hi {first_name},\n\n{draft_body}\n\nBest regards,\n{user_config['name']}"

        # Email the draft to the user so they can review and forward/reply
        review_body = (
            f"Claude drafted the reply below for {reply.from_name} ({reply.from_email}).\n"
            f"Review it, edit if needed, then copy and send from your Gmail.\n\n"
            f"{'─' * 50}\n"
            f"To: {reply.from_email}\n"
            f"Subject: Re: {reply.subject}\n\n"
            f"{full_draft}\n"
            f"{'─' * 50}"
        )
        send_plain_email(
            to=user_config["email"],
            subject=f"[Draft Reply] Re: {reply.subject}",
            body=review_body,
            user_config=user_config,
        )

        logger.info("Draft reply emailed to %s for review", user_config["email"])
        return full_draft

    except Exception as e:
        logger.error("Failed to draft reply: %s", e)
        return None
