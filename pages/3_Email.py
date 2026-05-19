"""Email page — review scraped contacts and send cold emails."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import streamlit as st

from src.config_loader import get_country_config, load_settings, load_user_configs
from src.emailer import generate_personalized_email, get_sponsorship_line, get_warmup_limit, send_cold_email
from src.sheets import SheetsClient

st.set_page_config(page_title="Send Emails", page_icon="📧", layout="wide")
st.title("📧 Send Cold Emails")

users = load_user_configs()
settings = load_settings()

if not users:
    st.warning("No user profiles configured.")
    st.stop()

user_names = [u["name"] for u in users]
selected_name = st.selectbox("Profile", user_names)
active_user = next(u for u in users if u["name"] == selected_name)

st.markdown("---")

try:
    sheet = SheetsClient(settings["google_sheet_id"], settings["google_service_account_file"])
    scraped_contacts = sheet.get_contacts_by_status("scraped", active_user["name"])
    daily_sent = sheet.get_daily_email_count(active_user["name"])
except Exception as e:
    st.error(f"Could not load contacts: {e}")
    st.stop()

daily_limit = active_user.get("daily_email_limit", 100)
effective_limit = get_warmup_limit(active_user, daily_sent)
remaining = max(0, effective_limit - daily_sent)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Pending (Scraped)", len(scraped_contacts))
col2.metric("Sent Today", daily_sent)
col3.metric("Today's Limit", effective_limit)
col4.metric("Remaining Today", remaining)

if active_user.get("warmup_mode") and effective_limit < daily_limit:
    st.info(f"🔥 Warm-up mode: sending up to **{effective_limit}** today (target: {daily_limit}). Increases by 20 each week to protect your account from spam flags.")

if not scraped_contacts:
    st.info("No scraped contacts waiting. Run the scraper first.")
    st.stop()

st.markdown("---")

# Filter by country
available_countries = sorted(set(c.get("Country", "") for c in scraped_contacts if c.get("Country")))
selected_countries = st.multiselect("Filter by country", available_countries, default=available_countries)

filtered = [c for c in scraped_contacts if c.get("Country", "") in selected_countries]

st.subheader(f"Contacts to email ({len(filtered)})")
st.dataframe(pd.DataFrame(filtered)[["Name", "Company", "Title", "Email", "Country", "Source"]], use_container_width=True)

st.markdown("---")
st.subheader("Email Preview (Claude-generated)")

if filtered:
    preview_contact = filtered[0]
    country_name = preview_contact.get("Country", "")
    country_cfg = get_country_config(active_user, country_name) or {}

    if st.button("✨ Generate preview email with Claude"):
        with st.spinner("Claude is writing a personalized email..."):
            try:
                subject, body = generate_personalized_email(
                    contact={
                        "name": preview_contact.get("Name", ""),
                        "email": preview_contact.get("Email", ""),
                        "company": preview_contact.get("Company", ""),
                        "title": preview_contact.get("Title", ""),
                        "country": country_name,
                    },
                    user_config=active_user,
                    country_config=country_cfg,
                    anthropic_api_key=settings.get("anthropic_api_key", ""),
                )
                st.markdown(f"**Subject:** {subject}")
                st.text_area("Body", body, height=220)
                sponsorship_note = country_cfg.get("custom_prompt", "")
                if sponsorship_note:
                    st.info(f"Visa/work note woven in: _{sponsorship_note}_")
            except Exception as e:
                st.error(f"Preview failed: {e}")
    else:
        st.caption("Click above to see a Claude-generated sample for the first contact.")

    resume_path = active_user.get("resume_pdf", "")
    if resume_path and Path(resume_path).exists():
        st.success(f"📎 Resume will be attached: {Path(resume_path).name}")
    else:
        st.warning(f"Resume PDF not found at: {resume_path}")

st.markdown("---")

col_limit, col_rate, col_dry = st.columns(3)
with col_limit:
    send_limit = st.number_input("Max emails to send this run", min_value=1, max_value=max(remaining, 1), value=min(remaining, remaining))
with col_rate:
    emails_per_hour = st.number_input(
        "Rate limit (emails/hour)",
        min_value=5, max_value=50,
        value=active_user.get("emails_per_hour", 15),
        help="Spread sends across the day. 15/hour = ~100 over 7 hours. Reduces spam risk."
    )
with col_dry:
    dry_run = st.checkbox("Dry run (preview only, don't send)", value=True)

if remaining == 0:
    st.warning(f"Daily limit of {daily_limit} reached. Come back tomorrow.")
    st.stop()

if st.button("📤 Send Emails", type="primary", disabled=(remaining == 0)):
    to_send = filtered[:send_limit]
    sent_count = 0
    failed_count = 0

    progress = st.progress(0)
    log_box = st.empty()
    logs = []

    for idx, contact in enumerate(to_send):
        country_name = contact.get("Country", "")
        country_cfg = get_country_config(active_user, country_name) or {}

        if dry_run:
            logs.append(f"[DRY RUN] Would email: {contact.get('Name')} <{contact.get('Email')}> at {contact.get('Company')}")
            sent_count += 1
        else:
            msg_id = send_cold_email(
                contact={
                    "name": contact.get("Name", ""),
                    "email": contact.get("Email", ""),
                    "company": contact.get("Company", ""),
                    "title": contact.get("Title", ""),
                    "country": country_name,
                },
                user_config=active_user,
                settings=settings,
                country_config=country_cfg,
            )
            if msg_id:
                sheet.mark_emailed(contact.get("Email", ""), active_user["name"], msg_id)
                logs.append(f"✅ Sent to {contact.get('Name')} <{contact.get('Email')}>")
                sent_count += 1
            else:
                logs.append(f"❌ Failed: {contact.get('Email')}")
                failed_count += 1

        log_box.text("\n".join(logs[-15:]))
        progress.progress((idx + 1) / len(to_send))

        # Rate limiting — pause between sends to avoid spam flags
        if not dry_run and sent_count > 0:
            import time
            delay = 3600 / emails_per_hour  # seconds between emails
            time.sleep(delay)

    if dry_run:
        st.info(f"Dry run complete — {sent_count} emails previewed. Uncheck 'Dry run' to send for real.")
    else:
        st.success(f"Done! ✅ {sent_count} sent, ❌ {failed_count} failed.")
