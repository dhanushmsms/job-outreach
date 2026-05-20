"""CLI runner — scrape, email, monitor, or full pipeline."""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_log_handlers = [logging.StreamHandler()]
try:
    import os as _os
    _os.makedirs("logs", exist_ok=True)
    _log_handlers.append(logging.FileHandler("logs/run.log"))
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger(__name__)

from src.config_loader import load_settings, load_user_configs
from src.emailer import send_cold_email, send_followup_email
from src.monitor import check_for_replies
from src.notifier import notify_reply_received, notify_cookie_expired
from src.reply_drafter import draft_reply
from src.scraper import scrape_all, scrape_linkedin_posts, _get_apify_token
from src.sheets import SheetsClient


def _check_cookie_health(settings: dict, users: list, countries_sample: list = None) -> bool:
    """
    Run a quick LinkedIn Posts probe across a sample of countries.
    Returns True if cookie is healthy, False if it looks expired.
    Sends a notification to all users if expired (fires only once per run).
    """
    cookie = settings.get("linkedin_cookie")
    if not cookie:
        return True  # no cookie configured — not our problem to alert on

    probe_countries = countries_sample or ["United Kingdom", "India"]
    apify_keys = settings.get("apify_keys", [])
    total_results = 0

    for country in probe_countries:
        token = _get_apify_token(apify_keys, country)
        if not token:
            continue
        results = scrape_linkedin_posts(
            country=country,
            roles=["Data Analyst"],
            token=token,
            max_results=5,
            linkedin_cookie=cookie,
        )
        total_results += len(results)

    if total_results == 0:
        logger.warning("LinkedIn Posts returned 0 results across probe countries — cookie may be expired")
        for user in users:
            notify_cookie_expired(user, settings)
        return False

    logger.info(f"LinkedIn cookie health check: OK ({total_results} results across probe)")
    return True


def _cross_share_contacts(sheet: SheetsClient, contact_dicts: list[dict], all_users: list, scraping_user: dict):
    """
    After scraping for one user, share contacts with every OTHER user whose
    target countries include the same country. Each person emails from their
    own account — so one contact can receive emails from multiple people.
    Dedup in add_contacts prevents sending twice from the same person.
    """
    for other_user in all_users:
        if other_user["name"] == scraping_user["name"]:
            continue
        other_countries = [c["name"].lower() for c in other_user.get("target_countries", [])]
        # Only share contacts whose country this other user also targets
        matching = [c for c in contact_dicts if c.get("country", "").lower() in other_countries]
        if matching:
            added = sheet.add_contacts(matching, other_user["name"])
            logger.info(
                f"[cross-share] {len(matching)} contacts from {scraping_user['name']} → "
                f"{other_user['name']} ({added} new)"
            )


def run_scrape(users, settings, countries_override=None, max_contacts=50):
    sheet = SheetsClient(settings["google_sheet_id"], settings["google_service_account_file"])

    # ── Cookie health check — runs once at the top of every scrape ────────────
    all_countries = countries_override or list({
        c["name"] for u in users for c in u.get("target_countries", [])
    })
    _check_cookie_health(settings, users, countries_sample=all_countries[:2])

    for user in users:
        countries = countries_override or [c["name"] for c in user.get("target_countries", [])]
        logger.info(f"[{user['name']}] Scraping countries: {countries}")
        for country_name in countries:
            country_cfg = next(
                (c for c in user.get("target_countries", []) if c["name"].lower() == country_name.lower()),
                {}
            )
            contacts = scrape_all(
                country=country_name,
                target_roles=user.get("target_roles", []),
                apify_keys=settings.get("apify_keys", []),
                needs_sponsorship=country_cfg.get("needs_sponsorship", False),
                max_results=max_contacts,
                linkedin_cookie=settings.get("linkedin_cookie"),
            )
            contact_dicts = [
                {"name": c.name, "email": c.email or "", "company": c.company,
                 "title": c.title, "country": c.country, "linkedin_url": c.linkedin_url or "",
                 "source": c.source, "role_type": c.role_type}
                for c in contacts
            ]
            added = sheet.add_contacts(contact_dicts, user["name"])
            logger.info(f"[{user['name']}] {country_name}: {len(contacts)} found, {added} new")

            # ── Share contacts with other users who also target this country ──
            _cross_share_contacts(sheet, contact_dicts, users, user)


def run_email(users, settings, dry_run=False):
    sheet = SheetsClient(settings["google_sheet_id"], settings["google_service_account_file"])
    for user in users:
        daily_limit = user.get("daily_email_limit", 20)
        daily_sent = sheet.get_daily_email_count(user["name"])
        remaining = max(0, daily_limit - daily_sent)
        if remaining == 0:
            logger.info(f"[{user['name']}] Daily limit reached, skipping")
            continue

        contacts = sheet.get_contacts_by_status("scraped", user["name"])[:remaining]
        logger.info(f"[{user['name']}] Sending to {len(contacts)} contacts")

        for contact in contacts:
            country_name = contact.get("Country", "")
            country_cfg = next(
                (c for c in user.get("target_countries", []) if c["name"].lower() == country_name.lower()),
                {}
            )

            if dry_run:
                logger.info(f"[DRY RUN] Would email {contact.get('Email')} at {contact.get('Company')}")
                continue

            msg_id = send_cold_email(
                contact={"name": contact.get("Name", ""), "email": contact.get("Email", ""),
                         "company": contact.get("Company", ""), "title": contact.get("Title", ""),
                         "country": country_name},
                user_config=user,
                settings=settings,
                country_config=country_cfg,
                sheets_client=sheet,
            )
            if msg_id:
                sheet.mark_emailed(contact.get("Email", ""), user["name"], msg_id)


def run_followups(users, settings):
    """Send one follow-up email to contacts who haven't replied after follow_up_days."""
    sheet = SheetsClient(settings["google_sheet_id"], settings["google_service_account_file"])
    for user in users:
        follow_up_days = user.get("follow_up_days", 7)
        contacts = sheet.get_followup_contacts(user["name"], follow_up_days)
        logger.info(f"[{user['name']}] {len(contacts)} contacts due for follow-up")
        for contact in contacts:
            country_name = contact.get("Country", "")
            country_cfg = next(
                (c for c in user.get("target_countries", []) if c["name"].lower() == country_name.lower()),
                {}
            )
            msg_id = send_followup_email(
                contact=contact,
                user_config=user,
                settings=settings,
                country_config=country_cfg,
            )
            if msg_id:
                sheet.mark_follow_up_sent(contact.get("Email", ""), user["name"])
                logger.info(f"[{user['name']}] Follow-up sent to {contact.get('Email')}")


def run_monitor(users, settings, since_hours=24):
    sheet = SheetsClient(settings["google_sheet_id"], settings["google_service_account_file"])
    for user in users:
        sent_ids = sheet.get_emailed_message_ids(user["name"])
        if not sent_ids:
            continue
        replies = check_for_replies(user, sent_ids, since_hours=since_hours)
        for reply in replies:
            sheet.mark_replied(reply.original_to, user["name"])
            notify_reply_received(reply, user, settings, all_users=users)
            draft_reply(reply, user, settings)
            logger.info(f"[{user['name']}] Reply from {reply.from_email} handled")


def main():
    parser = argparse.ArgumentParser(description="Job Outreach CLI")
    parser.add_argument("--mode", choices=["scrape", "email", "monitor", "full"], required=True)
    parser.add_argument("--users", default="all", help="Comma-separated user names or 'all'")
    parser.add_argument("--countries", default="", help="Comma-separated country names (overrides config)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--loop", action="store_true", help="Run monitor in a loop")
    parser.add_argument("--interval", type=int, default=15, help="Loop interval in minutes (with --loop)")
    args = parser.parse_args()

    settings = load_settings()
    all_users = load_user_configs()

    if args.users == "all":
        users = [u for u in all_users if u.get("enabled", True)]
    else:
        requested = [n.strip() for n in args.users.split(",")]
        users = [u for u in all_users if u["name"] in requested or u["email"] in requested]

    if not users:
        logger.error("No matching users found")
        sys.exit(1)

    countries = [c.strip() for c in args.countries.split(",") if c.strip()] or None

    if args.mode == "scrape":
        run_scrape(users, settings, countries)
    elif args.mode == "email":
        run_email(users, settings, dry_run=args.dry_run)
    elif args.mode == "monitor":
        if args.loop:
            logger.info(f"Starting monitor loop every {args.interval} minutes")
            while True:
                run_monitor(users, settings)
                time.sleep(args.interval * 60)
        else:
            run_monitor(users, settings)
    elif args.mode == "full":
        run_scrape(users, settings, countries)
        run_followups(users, settings)
        run_email(users, settings, dry_run=args.dry_run)
        run_monitor(users, settings)


if __name__ == "__main__":
    main()
