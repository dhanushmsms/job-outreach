"""Inbox Monitor page — check for replies, notify, draft responses."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st

from src.config_loader import load_settings, load_user_configs
from src.monitor import check_for_replies
from src.notifier import notify_reply_received
from src.reply_drafter import draft_reply
from src.sheets import SheetsClient

st.set_page_config(page_title="Inbox Monitor", page_icon="📬", layout="wide")
st.title("📬 Inbox Monitor")

users = load_user_configs()
settings = load_settings()

if not users:
    st.warning("No user profiles configured.")
    st.stop()

user_names = [u["name"] for u in users]

st.subheader("Monitor Settings")
col1, col2, col3 = st.columns(3)
with col1:
    selected_users = st.multiselect("Check inboxes for", user_names, default=user_names)
with col2:
    since_hours = st.slider("Look back (hours)", 1, 168, 24)
with col3:
    auto_draft = st.checkbox("Auto-draft Claude reply", value=True)
    send_notifications = st.checkbox("Send notifications", value=True)

st.markdown("---")

if st.button("🔍 Check for Replies Now", type="primary"):
    if not selected_users:
        st.warning("Select at least one profile.")
        st.stop()

    all_replies = []

    for user_name in selected_users:
        active_user = next(u for u in users if u["name"] == user_name)

        try:
            sheet = SheetsClient(settings["google_sheet_id"], settings["google_service_account_file"])
            sent_ids = sheet.get_emailed_message_ids(active_user["name"])
        except Exception as e:
            st.error(f"Sheet error for {user_name}: {e}")
            continue

        if not sent_ids:
            st.info(f"{user_name}: No sent emails tracked yet.")
            continue

        with st.spinner(f"Checking inbox for {user_name}..."):
            try:
                replies = check_for_replies(active_user, sent_ids, since_hours=since_hours)
            except Exception as e:
                st.error(f"Gmail error for {user_name}: {e}")
                continue

        if not replies:
            st.info(f"✅ {user_name}: No new replies in the last {since_hours}h.")
            continue

        st.success(f"🎉 {user_name}: {len(replies)} new reply/replies!")

        for reply in replies:
            with st.expander(f"💬 Reply from {reply.from_name} <{reply.from_email}> — {reply.subject}", expanded=True):
                col_info, col_preview = st.columns([1, 2])
                with col_info:
                    st.markdown(f"**From:** {reply.from_name}")
                    st.markdown(f"**Email:** {reply.from_email}")
                    st.markdown(f"**Received:** {reply.received_at}")
                    st.markdown(f"**Re: emailed contact:** {reply.original_to}")
                with col_preview:
                    st.markdown("**Message preview:**")
                    st.text(reply.body_preview)

                # Update sheet
                try:
                    sheet.mark_replied(reply.original_to, active_user["name"])
                    st.caption("✅ Sheet updated → 'replied'")
                except Exception as e:
                    st.warning(f"Could not update sheet: {e}")

                # Send notifications
                if send_notifications:
                    try:
                        notify_reply_received(reply, active_user, settings)
                        st.caption("🔔 Notification sent (Telegram + Gmail)")
                    except Exception as e:
                        st.warning(f"Notification failed: {e}")

                # Draft reply
                if auto_draft:
                    with st.spinner("Drafting reply with Claude..."):
                        try:
                            draft_content = draft_reply(reply, active_user, settings)
                            if draft_content:
                                st.markdown("**📝 Draft reply (saved to Gmail Drafts):**")
                                st.text_area("Draft", draft_content, height=150, key=f"draft_{reply.message_id}")
                            else:
                                st.warning("Could not generate draft.")
                        except Exception as e:
                            st.warning(f"Draft failed: {e}")

            all_replies.append((user_name, reply))

    if not all_replies:
        st.info("No new replies found across selected profiles.")

st.markdown("---")
st.subheader("Auto-Monitor (Background)")
st.markdown(
    "To run the monitor automatically every N minutes, run this in your terminal:\n"
    "```bash\n"
    "python run.py --mode monitor --users all --loop\n"
    "```"
)
