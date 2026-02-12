import os
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def render(settings: dict, gmail_monitor):
    st.title("⚙️ Settings")

    # ── Odoo Connection ────────────────────────────────────────────────────────
    st.subheader("Odoo Connection")

    col1, col2 = st.columns(2)

    with col1:
        odoo_url = st.text_input("Odoo URL", key="config_odoo_url")
        odoo_db = st.text_input("Database", key="config_odoo_db")
        odoo_user = st.text_input("Username", key="config_odoo_user")
        odoo_key = st.text_input(
            "API Key",
            type="password",
            value=os.getenv("ODOO_API_KEY", ""),
            key="config_odoo_key",
        )

    with col2:
        st.write("")
        st.write("")
        if st.button("🔌 Connect Odoo", type="primary"):
            from backend.odoo_client import OdooClient
            client = OdooClient(odoo_url, odoo_db, odoo_user, odoo_key)
            if client.connect():
                st.session_state["odoo_client"] = client
                st.session_state["odoo_connected"] = True
                st.session_state["odoo_last_connected"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                st.success("✅ Connected to Odoo")
                st.rerun()
            else:
                st.session_state["odoo_connected"] = False
                st.error("❌ Connection failed. Check credentials.")

        if st.session_state["odoo_connected"]:
            st.success(f"✅ Connected — last: {st.session_state.get('odoo_last_connected', 'unknown')}")

            if st.button("🔄 Disconnect"):
                st.session_state["odoo_client"] = None
                st.session_state["odoo_connected"] = False
                st.rerun()
        else:
            st.error("❌ Not connected")

    st.divider()

    # ── Gmail Integration ──────────────────────────────────────────────────────
    st.subheader("Gmail Integration")
    st.caption("The Gmail monitor automatically checks for T&T PO PDF attachments and adds them to the queue.")

    # Step 1 — credentials.json upload
    creds_col, status_col = st.columns([2, 1])

    with creds_col:
        if not gmail_monitor.credentials_file_exists():
            st.warning("⚠️ credentials.json not found. Download it from Google Cloud Console (OAuth 2.0 Client ID → Desktop App) and upload below.")
            uploaded = st.file_uploader(
                "Upload credentials.json",
                type=["json"],
                key="creds_uploader",
            )
            if uploaded:
                gmail_monitor.save_credentials_file(uploaded.read())
                st.success("✅ credentials.json saved. You can now authorize Gmail.")
                st.rerun()
        else:
            st.success("✅ credentials.json present")

            if st.button("🗑️ Remove credentials.json"):
                gmail_monitor.credentials_path.unlink(missing_ok=True)
                st.rerun()

    with status_col:
        gstatus = gmail_monitor.get_status()
        if gstatus["authenticated"]:
            st.success("Gmail: Connected")
        else:
            st.error("Gmail: Not authorized")

    # Step 2 — OAuth flow
    if gmail_monitor.credentials_file_exists() and not gmail_monitor.is_authenticated():
        st.markdown("**Step 2:** Authorize access to your Gmail inbox.")

        # Determine redirect URI based on environment
        base_url = os.getenv("APP_BASE_URL", "http://localhost:8501")
        redirect_uri = base_url.rstrip("/") + "/"

        if st.button("🔑 Authorize Gmail"):
            try:
                auth_url, flow = gmail_monitor.get_authorization_url(redirect_uri)
                st.session_state["gmail_oauth_flow"] = flow
                st.session_state["gmail_auth_url"] = auth_url
                st.rerun()
            except Exception as e:
                st.error(f"Failed to generate auth URL: {e}")

        if st.session_state.get("gmail_auth_url"):
            st.info(
                "**Click the link below** to authorize Gmail access. "
                "After authorizing, you will be redirected back to this page automatically."
            )
            st.markdown(
                f'**[→ Click here to authorize Gmail]({st.session_state["gmail_auth_url"]})**'
            )

    # Step 3 — Monitor controls when authenticated
    if gmail_monitor.is_authenticated():
        gstatus = gmail_monitor.get_status()

        met1, met2, met3 = st.columns(3)
        met1.metric("Monitor Status", "Running" if gstatus["running"] else "Stopped")
        met2.metric("Emails Processed", gstatus["emails_processed"])
        met3.metric("Last Poll", gstatus["last_poll_at"] or "Never")

        if gstatus["last_error"]:
            st.error(f"Last error: {gstatus['last_error']}")

        btn_col1, btn_col2, btn_col3 = st.columns(3)

        with btn_col1:
            if not gstatus["running"]:
                if st.button("▶️ Start Monitor"):
                    gmail_monitor.start()
                    st.rerun()
            else:
                if st.button("⏹️ Stop Monitor"):
                    gmail_monitor.stop()
                    st.rerun()

        with btn_col2:
            interval = st.number_input(
                "Poll Interval (seconds)",
                min_value=60,
                max_value=3600,
                value=gmail_monitor.poll_interval,
                step=60,
            )
            if interval != gmail_monitor.poll_interval:
                gmail_monitor.poll_interval = interval

        with btn_col3:
            if st.button("🚫 Revoke Gmail Access"):
                gmail_monitor.revoke_token()
                st.rerun()

    st.divider()
    st.caption(
        "**OAuth Redirect URI to register in Google Cloud Console:** "
        f"`{os.getenv('APP_BASE_URL', 'http://localhost:8501')}/`"
    )
