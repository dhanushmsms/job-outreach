"""Settings page — manage user profiles and global config."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
import streamlit as st

from src.config_loader import load_settings, load_user_configs, save_user_config

st.set_page_config(page_title="Settings", page_icon="⚙️", layout="wide")
st.title("⚙️ Settings")

users = load_user_configs()
settings = load_settings()

# ── Global Settings ────────────────────────────────────────────────────────────
st.subheader("Global Settings")
st.markdown("Edit `config/settings.yaml` to change these values.")

with st.expander("View current settings.yaml"):
    st.json({k: ("***" if "key" in k.lower() or "token" in k.lower() else v) for k, v in settings.items()})

st.markdown("---")

# ── User Profiles ──────────────────────────────────────────────────────────────
st.subheader("User Profiles")

tab_list, tab_new = st.tabs(["Manage Existing", "Create New Profile"])

with tab_list:
    if not users:
        st.info("No user profiles yet. Copy one of the `.example.yaml` files in `config/users/` and fill it in.")
    else:
        for user in users:
            with st.expander(f"👤 {user['name']} — {user['email']}", expanded=False):
                st.markdown(f"**Config file:** `{user.get('_config_file', '—')}`")

                col1, col2 = st.columns(2)
                with col1:
                    new_name = st.text_input("Full Name", value=user["name"], key=f"name_{user['email']}")
                    new_summary = st.text_area("Resume Summary", value=user.get("resume_summary", ""), key=f"summary_{user['email']}", height=80)
                    new_limit = st.number_input("Daily Email Limit", value=user.get("daily_email_limit", 20), key=f"limit_{user['email']}")
                with col2:
                    new_roles = st.text_area(
                        "Target Roles (one per line)",
                        value="\n".join(user.get("target_roles", [])),
                        key=f"roles_{user['email']}",
                        height=100,
                    )
                    new_resume = st.text_input("Resume PDF path", value=user.get("resume_pdf", ""), key=f"resume_{user['email']}")
                    new_tg = st.text_input("Telegram Chat ID", value=user.get("telegram_chat_id", ""), key=f"tg_{user['email']}")

                st.markdown("**Target Countries**")
                country_configs = user.get("target_countries", [])
                updated_countries = []
                for i, cc in enumerate(country_configs):
                    c1, c2, c3 = st.columns([2, 1, 3])
                    with c1:
                        c_name = st.text_input("Country", value=cc["name"], key=f"cname_{user['email']}_{i}")
                    with c2:
                        c_sponsor = st.checkbox("Needs Sponsorship", value=cc.get("needs_sponsorship", False), key=f"csponsor_{user['email']}_{i}")
                    with c3:
                        c_prompt = st.text_input("Custom Message", value=cc.get("custom_prompt", ""), key=f"cprompt_{user['email']}_{i}")
                    updated_countries.append({"name": c_name, "needs_sponsorship": c_sponsor, "custom_prompt": c_prompt})

                # Add country
                with st.form(key=f"add_country_{user['email']}"):
                    st.markdown("**Add a new country**")
                    nc1, nc2, nc3 = st.columns([2, 1, 3])
                    with nc1:
                        new_country_name = st.text_input("Country Name", key=f"nc_name_{user['email']}")
                    with nc2:
                        new_country_sponsor = st.checkbox("Needs Sponsorship", key=f"nc_sponsor_{user['email']}")
                    with nc3:
                        new_country_prompt = st.text_input("Custom Message", key=f"nc_prompt_{user['email']}")
                    add_submitted = st.form_submit_button("➕ Add Country")
                    if add_submitted and new_country_name:
                        updated_countries.append({
                            "name": new_country_name,
                            "needs_sponsorship": new_country_sponsor,
                            "custom_prompt": new_country_prompt,
                        })
                        st.success(f"Added {new_country_name}")

                if st.button(f"💾 Save Changes", key=f"save_{user['email']}"):
                    user["name"] = new_name
                    user["resume_summary"] = new_summary
                    user["daily_email_limit"] = new_limit
                    user["target_roles"] = [r.strip() for r in new_roles.splitlines() if r.strip()]
                    user["resume_pdf"] = new_resume
                    user["telegram_chat_id"] = new_tg
                    user["target_countries"] = updated_countries
                    try:
                        save_user_config(user)
                        st.success("Saved!")
                        st.cache_data.clear()
                    except Exception as e:
                        st.error(f"Save failed: {e}")

with tab_new:
    st.markdown("Fill in the form below to create a new user profile.")
    with st.form("new_user_form"):
        np_name = st.text_input("Full Name")
        np_email = st.text_input("Gmail Address")
        np_summary = st.text_area("Resume Summary (2-3 sentences)", height=80)
        np_roles = st.text_area("Target Roles (one per line)", height=80)
        np_resume = st.text_input("Resume PDF path (e.g. resumes/yourname_resume.pdf)")
        np_tg = st.text_input("Telegram Chat ID (optional)")
        np_limit = st.number_input("Daily Email Limit", value=20, min_value=1)
        np_submitted = st.form_submit_button("Create Profile")

        if np_submitted:
            if not np_name or not np_email:
                st.error("Name and email are required.")
            else:
                slug = np_name.lower().replace(" ", "_")
                new_path = Path("config/users") / f"{slug}.yaml"
                cfg = {
                    "name": np_name,
                    "email": np_email,
                    "resume_summary": np_summary,
                    "resume_pdf": np_resume,
                    "target_roles": [r.strip() for r in np_roles.splitlines() if r.strip()],
                    "target_countries": [],
                    "gmail_credentials_file": f"credentials/{slug}_credentials.json",
                    "gmail_token_file": f"credentials/{slug}_token.json",
                    "telegram_chat_id": np_tg,
                    "daily_email_limit": int(np_limit),
                    "follow_up_days": 7,
                    "enabled": True,
                }
                with open(new_path, "w") as f:
                    yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
                st.success(f"Profile created: `{new_path}`")
                st.info("Now add target countries and upload Gmail credentials. Then restart the app.")
                st.cache_data.clear()

st.markdown("---")

# ── Resume Upload ──────────────────────────────────────────────────────────────
st.subheader("Update Resumes")
st.markdown("Upload a new PDF to replace an existing resume. The file will be saved to `resumes/` and the config updated automatically.")

if users:
    resume_user = st.selectbox("Select profile to update resume for", [u["name"] for u in users], key="resume_user_select")
    active_resume_user = next(u for u in users if u["name"] == resume_user)

    current_resume = active_resume_user.get("resume_pdf", "")
    if current_resume and Path(current_resume).exists():
        st.caption(f"Current resume: `{current_resume}`")
        with open(current_resume, "rb") as f:
            st.download_button("⬇️ Download current resume", f.read(), file_name=Path(current_resume).name, mime="application/pdf")
    else:
        st.warning(f"No resume found at: `{current_resume}`")

    uploaded = st.file_uploader("Upload new resume PDF", type=["pdf"], key="resume_upload")
    if uploaded and st.button("💾 Save New Resume"):
        slug = resume_user.lower().replace(" ", "_").split()[0]
        save_path = Path("resumes") / f"{slug}_cv.pdf"
        with open(save_path, "wb") as f:
            f.write(uploaded.read())
        active_resume_user["resume_pdf"] = str(save_path)
        try:
            save_user_config(active_resume_user)
            st.success(f"Resume updated for {resume_user} → `{save_path}`")
            st.cache_data.clear()
        except Exception as e:
            st.error(f"Saved file but could not update config: {e}")

st.markdown("---")
st.subheader("Setup Checklist")

checks = {
    "settings.yaml configured": bool(settings.get("google_sheet_id") and not str(settings.get("google_sheet_id","")).startswith("YOUR")),
    "Claude API key set": bool(settings.get("anthropic_api_keys") or settings.get("anthropic_api_key")),
    "At least one user profile": len(users) > 0,
    "Google service account file": Path(settings.get("google_service_account_file", "")).exists(),
    "All users have App Password": all(
        u.get("gmail_app_password") and not str(u.get("gmail_app_password","")).startswith("YOUR")
        for u in users
    ) if users else False,
}

for label, ok in checks.items():
    st.markdown(f"{'✅' if ok else '❌'} {label}")

st.markdown("---")
st.subheader("Test Gmail Connection")
st.markdown("Verify each person's App Password works before sending emails.")

if users:
    test_user_name = st.selectbox("Test profile", [u["name"] for u in users], key="test_gmail_user")
    test_user = next(u for u in users if u["name"] == test_user_name)

    col_smtp, col_imap = st.columns(2)
    with col_smtp:
        if st.button("Test SMTP (sending)"):
            from src.emailer import send_plain_email
            ok = send_plain_email(
                to=test_user["email"],
                subject="[Job Outreach] SMTP test",
                body="Your Gmail SMTP connection is working correctly.",
                user_config=test_user,
            )
            if ok:
                st.success(f"✅ SMTP works — test email sent to {test_user['email']}")
            else:
                st.error("❌ SMTP failed — check App Password in the yaml config")

    with col_imap:
        if st.button("Test IMAP (monitoring)"):
            from src.monitor import test_imap_connection
            ok, msg = test_imap_connection(test_user)
            if ok:
                st.success(f"✅ IMAP works — inbox accessible for {test_user['email']}")
            else:
                st.error(f"❌ IMAP failed — {msg}")
