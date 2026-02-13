import os
from datetime import datetime

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from database.connection import get_db_connection

load_dotenv()


def render(settings: dict, gmail_monitor) -> None:
    st.markdown("#### ⚙️ Settings")

    # ── Odoo Connection ────────────────────────────────────────────────────────
    with st.expander("Odoo Connection", expanded=True):
        odoo_url = st.text_input("Odoo URL", key="config_odoo_url")
        odoo_db = st.text_input("Database", key="config_odoo_db")
        odoo_user = st.text_input("Username", key="config_odoo_user")
        odoo_key = st.text_input(
            "API Key",
            type="password",
            value=os.getenv("ODOO_API_KEY", ""),
            key="config_odoo_key",
        )

        if st.button("Connect Odoo", type="primary", key="btn_connect_odoo"):
            from backend.odoo_client import OdooClient
            client = OdooClient(odoo_url, odoo_db, odoo_user, odoo_key)
            if client.connect():
                st.session_state["odoo_client"] = client
                st.session_state["odoo_connected"] = True
                st.session_state["odoo_last_connected"] = datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                st.success("Connected to Odoo")
                st.rerun()
            else:
                st.session_state["odoo_connected"] = False
                st.error("Connection failed. Check credentials.")

        if st.session_state["odoo_connected"]:
            st.success(
                f"Connected — last: {st.session_state.get('odoo_last_connected', 'unknown')}"
            )
            if st.button("Disconnect", key="btn_disconnect_odoo"):
                st.session_state["odoo_client"] = None
                st.session_state["odoo_connected"] = False
                st.rerun()
        else:
            st.caption("Not connected")

    # ── Gmail Integration ──────────────────────────────────────────────────────
    with st.expander("Gmail Integration", expanded=False):
        st.caption(
            "Monitors Gmail for T&T PO PDF attachments and adds them to the queue."
        )

        # Credentials status
        if not gmail_monitor.credentials_file_exists():
            st.warning(
                "credentials.json not found. Upload from Google Cloud Console."
            )
            uploaded = st.file_uploader(
                "Upload credentials.json",
                type=["json"],
                key="creds_uploader",
            )
            if uploaded:
                gmail_monitor.save_credentials_file(uploaded.read())
                st.success("credentials.json saved.")
                st.rerun()
        else:
            st.success("credentials.json present")
            if st.button("Remove credentials.json", key="btn_rm_creds"):
                gmail_monitor.credentials_path.unlink(missing_ok=True)
                st.rerun()

        # Auth status
        if gmail_monitor.is_authenticated():
            st.success("Gmail: Authorized")
        else:
            st.caption("Gmail: Not authorized")

        # OAuth flow
        if (
            gmail_monitor.credentials_file_exists()
            and not gmail_monitor.is_authenticated()
        ):
            base_url = os.getenv("APP_BASE_URL", "http://localhost:8501")
            redirect_uri = base_url.rstrip("/") + "/"

            if st.button("Authorize Gmail", key="btn_auth_gmail"):
                try:
                    auth_url, flow = gmail_monitor.get_authorization_url(redirect_uri)
                    st.session_state["gmail_oauth_flow"] = flow
                    st.session_state["gmail_auth_url"] = auth_url
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed: {e}")

            if st.session_state.get("gmail_auth_url"):
                st.info("Click below to authorize Gmail access.")
                st.markdown(
                    f'[Authorize Gmail]({st.session_state["gmail_auth_url"]})'
                )

        # Monitor controls
        if gmail_monitor.is_authenticated():
            gstatus = gmail_monitor.get_status()

            st.caption(
                f"**Status:** {'Running' if gstatus['running'] else 'Stopped'} · "
                f"**Processed:** {gstatus['emails_processed']} · "
                f"**Last poll:** {gstatus['last_poll_at'] or 'Never'}"
            )

            if gstatus["last_error"]:
                st.error(f"Error: {gstatus['last_error']}")

            if not gstatus["running"]:
                if st.button("Start Monitor", key="btn_start_gmail"):
                    gmail_monitor.start()
                    st.rerun()
            else:
                if st.button("Stop Monitor", key="btn_stop_gmail"):
                    gmail_monitor.stop()
                    st.rerun()

            interval = st.number_input(
                "Poll interval (sec)",
                min_value=60,
                max_value=3600,
                value=gmail_monitor.poll_interval,
                step=60,
                key="gmail_poll_interval",
            )
            if interval != gmail_monitor.poll_interval:
                gmail_monitor.poll_interval = interval

            if st.button("Revoke Gmail Access", key="btn_revoke_gmail"):
                gmail_monitor.revoke_token()
                st.rerun()

        st.caption(
            f"**Redirect URI:** `{os.getenv('APP_BASE_URL', 'http://localhost:8501')}/`"
        )

    # ── Database Explorer ──────────────────────────────────────────────────────
    with st.expander("Database Explorer", expanded=False):
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' ORDER BY tablename"
                    )
                    tables = [row[0] for row in cur.fetchall()]
        except Exception as e:
            st.error(f"DB error: {e}")
            return

        if not tables:
            st.info("No tables found.")
            return

        selected_table = st.selectbox("Table", tables, key="db_explorer_table")
        row_limit = st.number_input(
            "Row limit",
            min_value=10,
            max_value=500,
            value=50,
            step=25,
            key="db_explorer_limit",
        )

        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT COUNT(*) FROM {selected_table}")  # noqa: S608
                    total_rows = cur.fetchone()[0]

                df = pd.read_sql(
                    f"SELECT * FROM {selected_table} ORDER BY 1 DESC LIMIT %s",  # noqa: S608
                    conn,
                    params=(row_limit,),
                )
        except Exception as e:
            st.error(f"Query error: {e}")
            return

        st.caption(
            f"**{selected_table}** — {total_rows} rows "
            f"(showing {min(row_limit, total_rows)})"
        )
        st.dataframe(df, use_container_width=True, height=250)

        # Quick actions for staged_pos
        if selected_table == "staged_pos" and total_rows > 0:
            st.caption("**Quick Actions**")
            if st.button("Delete unprocessed", type="secondary", key="btn_del_unproc"):
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "DELETE FROM staged_pos WHERE status = 'unprocessed'"
                        )
                        deleted = cur.rowcount
                    conn.commit()
                st.success(f"Deleted {deleted} records.")
                st.rerun()

            if st.button("Delete ALL", type="secondary", key="btn_del_all"):
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM staged_pos")
                        deleted = cur.rowcount
                    conn.commit()
                st.success(f"Deleted {deleted} records.")
                st.rerun()
