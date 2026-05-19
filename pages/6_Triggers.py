"""Triggers page — schedule automatic scraping, emailing, and monitoring."""

import sys
import subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
from src.config_loader import load_user_configs

st.set_page_config(page_title="Triggers", page_icon="⏰", layout="wide")
st.title("⏰ Triggers & Scheduling")

users = load_user_configs()
user_names = [u["name"] for u in users] if users else []

st.markdown("""
Set up automatic triggers so the workflow runs without you clicking anything.
Choose between a **cron job** (Mac/Linux, runs while your machine is on) or a **cloud schedule** (runs 24/7).
""")

st.markdown("---")

# ── Cron Generator ────────────────────────────────────────────────────────────
st.subheader("1. Cron Job (runs on your Mac)")

col1, col2 = st.columns(2)

with col1:
    trigger_mode = st.selectbox("What to run", [
        "Full pipeline (scrape + email + monitor)",
        "Scrape only",
        "Email only",
        "Monitor only",
    ])
    mode_map = {
        "Full pipeline (scrape + email + monitor)": "full",
        "Scrape only": "scrape",
        "Email only": "email",
        "Monitor only": "monitor",
    }

    selected_users = st.multiselect("For which profiles", user_names, default=user_names)
    users_arg = ",".join(selected_users) if selected_users else "all"

    countries_input = st.text_input("Countries (comma-separated, blank = all from config)", placeholder="United Kingdom,Ireland,Dubai")

with col2:
    schedule_type = st.selectbox("Schedule", [
        "Every day at 9am",
        "Every day at 8am",
        "Every Monday at 9am (weekly)",
        "Every 15 minutes (monitor loop)",
        "Every 30 minutes",
        "Custom cron expression",
    ])

    cron_map = {
        "Every day at 9am": "0 9 * * *",
        "Every day at 8am": "0 8 * * *",
        "Every Monday at 9am (weekly)": "0 9 * * 1",
        "Every 15 minutes (monitor loop)": "*/15 * * * *",
        "Every 30 minutes": "*/30 * * * *",
        "Custom cron expression": None,
    }
    cron_expr = cron_map[schedule_type]
    if cron_expr is None:
        cron_expr = st.text_input("Custom cron expression", placeholder="0 9 * * *")

project_dir = str(Path(__file__).parent.parent.resolve())
python_path = sys.executable
mode = mode_map[trigger_mode]
countries_flag = f"--countries \"{countries_input}\"" if countries_input.strip() else ""

cron_command = f"{python_path} {project_dir}/run.py --mode {mode} --users {users_arg} {countries_flag}".strip()
full_cron_line = f'{cron_expr} {cron_command} >> {project_dir}/logs/cron.log 2>&1'

st.markdown("**Generated cron line:**")
st.code(full_cron_line, language="bash")

with st.expander("How to add this to crontab"):
    st.markdown("""
**Step 1** — Open crontab in your terminal:
```bash
crontab -e
```
**Step 2** — Paste the line above at the bottom and save (`:wq` in vim, or `Ctrl+X` in nano).

**Step 3** — Verify it's saved:
```bash
crontab -l
```

**Check logs** at `logs/cron.log`
    """)

if st.button("📋 Copy cron line to clipboard"):
    st.code(full_cron_line)
    st.info("Copy the line above and paste it into your terminal with `crontab -e`")

st.markdown("---")

# ── Recommended Setup ─────────────────────────────────────────────────────────
st.subheader("2. Recommended Trigger Setup")

st.markdown("""
This setup covers a full automated workflow:

| Trigger | Schedule | What it does |
|---|---|---|
| Daily scrape + email | Every day at 9am | Finds new contacts and sends cold emails |
| Inbox monitor | Every 15 minutes | Checks for replies, notifies you, drafts response |

**Add both lines to crontab:**
""")

scrape_email_cron = f"0 9 * * * {python_path} {project_dir}/run.py --mode full --users all >> {project_dir}/logs/cron.log 2>&1"
monitor_cron = f"*/15 * * * * {python_path} {project_dir}/run.py --mode monitor --users all >> {project_dir}/logs/cron.log 2>&1"

st.code(f"{scrape_email_cron}\n{monitor_cron}", language="bash")

st.markdown("---")

# ── Manual Run ────────────────────────────────────────────────────────────────
st.subheader("3. Run Now (Manual Trigger)")

col_run1, col_run2, col_run3, col_run4 = st.columns(4)

def run_pipeline(mode, users_list):
    users_str = ",".join(users_list) if users_list else "all"
    cmd = [python_path, f"{project_dir}/run.py", "--mode", mode, "--users", users_str]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=project_dir)
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return "Timed out after 5 minutes. Check logs/run.log for progress."
    except Exception as e:
        return f"Error: {e}"

run_users = st.multiselect("Run for profiles", user_names, default=user_names, key="run_users")

with col_run1:
    if st.button("🔍 Run Scraper", use_container_width=True):
        with st.spinner("Scraping..."):
            output = run_pipeline("scrape", run_users)
        st.text_area("Output", output, height=200)

with col_run2:
    if st.button("📧 Run Emailer", use_container_width=True):
        with st.spinner("Sending emails..."):
            output = run_pipeline("email", run_users)
        st.text_area("Output", output, height=200)

with col_run3:
    if st.button("📬 Run Monitor", use_container_width=True):
        with st.spinner("Checking inbox..."):
            output = run_pipeline("monitor", run_users)
        st.text_area("Output", output, height=200)

with col_run4:
    if st.button("🚀 Run Full Pipeline", use_container_width=True, type="primary"):
        with st.spinner("Running full pipeline..."):
            output = run_pipeline("full", run_users)
        st.text_area("Output", output, height=200)

st.markdown("---")

# ── View Logs ─────────────────────────────────────────────────────────────────
st.subheader("4. Logs")
log_file = st.selectbox("View log file", ["logs/run.log", "logs/cron.log", "logs/app.log"])
if st.button("📄 Load Log"):
    log_path = Path(project_dir) / log_file
    if log_path.exists():
        with open(log_path) as f:
            lines = f.readlines()
        last_100 = "".join(lines[-100:])
        st.text_area("Last 100 lines", last_100, height=400)
    else:
        st.info("Log file not created yet.")
