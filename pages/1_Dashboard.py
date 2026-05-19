"""Tracker page — live Google Sheets CRM view with filters."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import streamlit as st

from src.config_loader import load_settings, load_user_configs
from src.sheets import SheetsClient, STATUS_EMAILED, STATUS_REPLIED, STATUS_SCRAPED

st.set_page_config(page_title="Tracker", page_icon="📊", layout="wide")

col_title, col_sheet = st.columns([4, 1])
with col_title:
    st.title("📊 Contact Tracker")
with col_sheet:
    settings_tmp = load_settings()
    _sid = settings_tmp.get("google_sheet_id", "")
    if _sid:
        st.link_button("📋 Open Google Sheet",
                       f"https://docs.google.com/spreadsheets/d/{_sid}",
                       use_container_width=True)

users = load_user_configs()
settings = load_settings()

if not users:
    st.warning("No user profiles configured.")
    st.stop()

user_names = [u["name"] for u in users]
col_user, col_status, col_country = st.columns(3)

with col_user:
    selected_name = st.selectbox("Profile", user_names, key="tracker_user")
with col_status:
    status_filter = st.multiselect(
        "Status",
        ["scraped", "emailed", "replied", "responded", "not_interested", "bounced"],
        default=[],
    )
with col_country:
    country_filter = st.multiselect("Country", [], key="country_filter_placeholder")

active_user = next(u for u in users if u["name"] == selected_name)

if st.button("🔄 Refresh"):
    st.cache_data.clear()

try:
    sheet = SheetsClient(settings["google_sheet_id"], settings["google_service_account_file"])
    contacts = sheet.get_all_contacts(active_user["name"])
except Exception as e:
    st.error(f"Could not connect to Google Sheets: {e}")
    st.stop()

if not contacts:
    st.info("No contacts yet. Run the scraper first.")
    st.stop()

df = pd.DataFrame(contacts)

# Populate country filter dynamically
countries = sorted(df["Country"].dropna().unique().tolist()) if "Country" in df.columns else []
country_filter = st.multiselect("Country", countries, key="country_filter_real")

if status_filter:
    df = df[df["Status"].isin(status_filter)]
if country_filter:
    df = df[df["Country"].isin(country_filter)]

# Stats row
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Showing", len(df))
c2.metric("Emailed", len(df[df["Status"] == "emailed"]) if "Status" in df.columns else 0)
c3.metric("Replied", len(df[df["Status"] == "replied"]) if "Status" in df.columns else 0)
c4.metric("Not Interested", len(df[df["Status"] == "not_interested"]) if "Status" in df.columns else 0)
c5.metric("Bounced", len(df[df["Status"] == "bounced"]) if "Status" in df.columns else 0)

st.markdown("---")

# Color-code status
def color_status(val):
    colors = {
        "scraped": "background-color: #f0f0f0",
        "emailed": "background-color: #cce5ff",
        "replied": "background-color: #d4edda",
        "responded": "background-color: #c3e6cb",
        "not_interested": "background-color: #f8d7da",
        "bounced": "background-color: #fff3cd",
    }
    return colors.get(val, "")

styled = df.style.applymap(color_status, subset=["Status"] if "Status" in df.columns else [])
st.dataframe(styled, use_container_width=True, height=500)

# Export
csv = df.to_csv(index=False)
st.download_button("⬇️ Export CSV", csv, file_name=f"{selected_name}_contacts.csv", mime="text/csv")

# Manual status update
st.markdown("---")
st.subheader("Update Contact Status")

col_email, col_new_status, col_notes = st.columns([2, 1, 2])
with col_email:
    update_email = st.text_input("Contact Email")
with col_new_status:
    new_status = st.selectbox("New Status", ["emailed", "replied", "responded", "not_interested", "bounced"])
with col_notes:
    notes = st.text_input("Notes (optional)")

if st.button("Update Status"):
    if update_email:
        ok = sheet.update_status(update_email, new_status, active_user["name"], notes)
        if ok:
            st.success(f"Updated {update_email} → {new_status}")
            st.cache_data.clear()
        else:
            st.error(f"Email not found: {update_email}")
    else:
        st.warning("Enter an email address")
