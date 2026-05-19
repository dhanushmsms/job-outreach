"""Job Outreach Automation — Streamlit entry point."""

import logging
import sys
from pathlib import Path

import streamlit as st
import yaml

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/app.log"),
    ],
)

st.set_page_config(
    page_title="Job Outreach",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── User profile selector ──────────────────────────────────────────────────────
from src.config_loader import load_user_configs, load_settings

@st.cache_data(ttl=30)
def _load_configs():
    try:
        return load_user_configs(), load_settings()
    except Exception as e:
        return [], {}

users, settings = _load_configs()

st.sidebar.title("💼 Job Outreach")
st.sidebar.markdown("---")

if not users:
    st.sidebar.warning("No user profiles found. Copy an `.example.yaml` and fill it in.")
    st.sidebar.code("config/users/user1.example.yaml → config/users/user1.yaml")
    active_user = None
else:
    user_names = [u["name"] for u in users]
    selected_name = st.sidebar.selectbox("👤 Active Profile", user_names)
    active_user = next(u for u in users if u["name"] == selected_name)
    st.session_state["active_user"] = active_user
    st.session_state["settings"] = settings

    st.sidebar.markdown(f"**Email:** {active_user['email']}")
    countries = [c["name"] for c in active_user.get("target_countries", [])]
    st.sidebar.markdown(f"**Countries:** {', '.join(countries) or '—'}")

st.sidebar.markdown("---")
st.sidebar.markdown("**Pages**")
st.sidebar.page_link("app.py", label="🏠 Home")
st.sidebar.page_link("pages/1_Dashboard.py", label="📊 Tracker")
st.sidebar.page_link("pages/2_Scraper.py", label="🔍 Scrape Contacts")
st.sidebar.page_link("pages/3_Email.py", label="📧 Send Emails")
st.sidebar.page_link("pages/4_Monitor.py", label="📬 Inbox Monitor")
st.sidebar.page_link("pages/5_Settings.py", label="⚙️ Settings")
st.sidebar.page_link("pages/6_Triggers.py", label="⏰ Triggers")

st.sidebar.markdown("---")
_sheet_id = settings.get("google_sheet_id", "")
if _sheet_id:
    _sheet_url = f"https://docs.google.com/spreadsheets/d/{_sheet_id}"
    st.sidebar.link_button("📋 Open Google Sheet", _sheet_url, use_container_width=True)

# ── Home page ──────────────────────────────────────────────────────────────────
st.title("💼 Job Outreach Automation")

if not active_user:
    st.info("Set up a user profile to get started. See the sidebar for instructions.")
    st.stop()

col1, col2, col3, col4 = st.columns(4)

try:
    from src.sheets import SheetsClient
    sheet = SheetsClient(settings["google_sheet_id"], settings["google_service_account_file"])
    all_contacts = sheet.get_all_contacts(active_user["name"])

    scraped = sum(1 for c in all_contacts if c.get("Status") == "scraped")
    emailed = sum(1 for c in all_contacts if c.get("Status") == "emailed")
    replied = sum(1 for c in all_contacts if c.get("Status") == "replied")
    total = len(all_contacts)

    col1.metric("Total Contacts", total)
    col2.metric("Scraped", scraped)
    col3.metric("Emailed", emailed)
    col4.metric("Replied", replied)
except Exception as e:
    col1.metric("Total Contacts", "—")
    col2.metric("Scraped", "—")
    col3.metric("Emailed", "—")
    col4.metric("Replied", "—")
    st.warning(f"Could not load sheet stats: {e}")

st.markdown("---")
st.markdown("""
### Quick Start
1. **Scrape** → Go to *Scrape Contacts*, pick countries, run the scraper
2. **Email** → Go to *Send Emails*, review contacts, send cold emails
3. **Monitor** → Go to *Inbox Monitor* to check for replies and get notifications
4. **Track** → View the full CRM on the *Tracker* page
""")
