"""Scraper page — pick countries, run ScrapeGraphAI, add to sheet."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st

from src.browser import is_obscura_running, start_obscura, stop_obscura
from src.config_loader import load_settings, load_user_configs
from src.scraper import scrape_all
from src.sheets import SheetsClient

st.set_page_config(page_title="Scrape Contacts", page_icon="🔍", layout="wide")
st.title("🔍 Scrape Contacts")

# Show free sources info
with st.expander("ℹ️ Scrape sources (all free)", expanded=False):
    st.markdown("""
| Source | Free? | What it finds | Notes |
|---|---|---|---|
| **Adzuna API** | ✅ Free (100 calls/day) | Job listings + company names | Register at developer.adzuna.com |
| **Reed / CWJobs / Totaljobs** | ✅ Free | UK hiring managers | Via ScrapeGraphAI |
| **Naukri / Shine** | ✅ Free | India recruiters | Via ScrapeGraphAI |
| **Seek / TradeMe** | ✅ Free | NZ recruiters | Via ScrapeGraphAI |
| **Bayt / GulfTalent** | ✅ Free | Dubai recruiters | Via ScrapeGraphAI |
| **StepStone / EuroJobs** | ✅ Free | Europe hiring managers | Via ScrapeGraphAI |
| **Wellfound (AngelList)** | ✅ Free | Global startup hiring | Good for tech roles |
| **Hacker News Hiring** | ✅ Free (no key) | Tech companies hiring | Monthly thread |
| **GitHub** | ✅ Free (no key) | Engineering managers | 60 req/hour |
| **LinkedIn** | ✅ Free (fallback) | All roles | May get blocked |
| **Obscura** | ✅ Free | JS rendering layer | Run `bash install_obscura.sh` once |

**Obscura + ScrapeGraphAI together:** Obscura renders the full JavaScript page → ScrapeGraphAI + Claude extracts structured data from the rendered HTML. This handles React/SPA pages (LinkedIn, Indeed, Wellfound) that plain HTTP requests can't read.

**Only paid: Claude API** (~$5/month for everything — scraping + emails + reply drafts).
    """)

users = load_user_configs()
settings = load_settings()

if not users:
    st.warning("No user profiles configured.")
    st.stop()

user_names = [u["name"] for u in users]
selected_name = st.selectbox("Profile", user_names)
active_user = next(u for u in users if u["name"] == selected_name)

st.markdown("---")

# ── Obscura status bar ────────────────────────────────────────────────────────
obscura_col1, obscura_col2 = st.columns([3, 1])
with obscura_col1:
    if is_obscura_running():
        st.success("⚡ Obscura is running — full JavaScript rendering enabled (LinkedIn, Indeed, React sites)")
    else:
        st.warning("⚠️ Obscura not running — JS-heavy pages may not load fully")
with obscura_col2:
    if is_obscura_running():
        if st.button("Stop Obscura"):
            stop_obscura()
            st.rerun()
    else:
        if st.button("▶ Start Obscura", type="primary"):
            with st.spinner("Starting Obscura..."):
                ok = start_obscura()
            if ok:
                st.success("Obscura started!")
                st.rerun()
            else:
                st.error("Failed — run `bash install_obscura.sh` first")

st.markdown("---")

# Country selection with sponsorship flags
st.subheader("Target Countries")
target_countries = active_user.get("target_countries", [])

if not target_countries:
    st.warning("No target countries in this user's config.")
    st.stop()

# Let user pick countries and edit sponsorship/prompt inline
selected_countries = []
st.markdown("Select countries and review sponsorship settings:")

for i, country_cfg in enumerate(target_countries):
    with st.expander(f"🌍 {country_cfg['name']}", expanded=True):
        col_select, col_sponsor = st.columns([1, 2])
        with col_select:
            include = st.checkbox("Include in this run", value=True, key=f"include_{i}")
        with col_sponsor:
            needs_sponsor = st.checkbox(
                "Needs visa sponsorship",
                value=country_cfg.get("needs_sponsorship", False),
                key=f"sponsor_{i}",
            )
        custom_prompt = st.text_area(
            "Custom sponsorship message (shown in email)",
            value=country_cfg.get("custom_prompt", ""),
            key=f"prompt_{i}",
            height=60,
        )
        if include:
            selected_countries.append({
                "name": country_cfg["name"],
                "needs_sponsorship": needs_sponsor,
                "custom_prompt": custom_prompt,
            })

st.markdown("---")

# Additional career page URLs
st.subheader("Additional Career Page URLs (optional)")
career_urls_input = st.text_area(
    "One URL per line — company career pages to also scrape",
    height=100,
    placeholder="https://company.com/careers\nhttps://another.com/jobs",
)
career_urls = [u.strip() for u in career_urls_input.splitlines() if u.strip()]

col1, col2 = st.columns(2)
with col1:
    max_per_country = st.slider("Max contacts per country", 10, 200, 100)
with col2:
    run_parallel = st.checkbox("Run countries simultaneously", value=True)

st.markdown("---")

if not selected_countries:
    st.info("Select at least one country above.")
    st.stop()

if st.button("🚀 Start Scraping", type="primary"):
    try:
        sheet = SheetsClient(settings["google_sheet_id"], settings["google_service_account_file"])
    except Exception as e:
        st.error(f"Could not connect to Google Sheets: {e}")
        st.stop()

    total_added = 0
    progress = st.progress(0)
    status_box = st.empty()

    for idx, country_cfg in enumerate(selected_countries):
        country_name = country_cfg["name"]
        status_box.info(f"Scraping {country_name}...")

        with st.spinner(f"Scraping {country_name}..."):
            contacts = scrape_all(
                country=country_name,
                target_roles=active_user.get("target_roles", []),
                apify_keys=settings.get("apify_keys", []),
                career_page_urls=career_urls if career_urls else None,
                needs_sponsorship=country_cfg.get("needs_sponsorship", False),
                max_results=max_per_country,
            )

        contact_dicts = [
            {
                "name": c.name,
                "email": c.email or "",
                "company": c.company,
                "title": c.title,
                "country": c.country,
                "linkedin_url": c.linkedin_url or "",
                "source": c.source,
            }
            for c in contacts
        ]

        added = sheet.add_contacts(contact_dicts, active_user["name"])
        total_added += added

        # Show source breakdown
        sources = {}
        for c in contacts:
            sources[c.source] = sources.get(c.source, 0) + 1
        source_str = " | ".join(f"{s}: {n}" for s, n in sorted(sources.items()))
        st.success(f"✅ {country_name}: {len(contacts)} found, {added} new added to sheet"
                   + (f"  \n_Sources — {source_str}_" if source_str else ""))
        progress.progress((idx + 1) / len(selected_countries))

    status_box.empty()
    st.balloons()
    st.success(f"🎉 Done! Added **{total_added}** new contacts total across {len(selected_countries)} countries.")
    st.info("Go to the **Tracker** page to review, or head to **Send Emails** to start outreach.")
