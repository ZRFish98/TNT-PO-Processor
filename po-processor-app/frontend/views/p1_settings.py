import os
from datetime import datetime

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from database.connection import get_db_connection
from frontend.i18n import t

load_dotenv()


def render(settings: dict, gmail_monitor) -> None:
    st.title(t("settings.title"))

    # ── Odoo Connection ────────────────────────────────────────────────────────
    with st.expander(t("settings.odoo.title"), expanded=True):
        odoo_url = st.text_input(t("settings.odoo.url"), key="config_odoo_url")
        odoo_db = st.text_input(t("settings.odoo.db"), key="config_odoo_db")
        odoo_user = st.text_input(t("settings.odoo.user"), key="config_odoo_user")
        odoo_key = st.text_input(
            t("settings.odoo.key"),
            type="password",
            value=os.getenv("ODOO_API_KEY", ""),
            key="config_odoo_key",
        )

        if st.button(t("settings.odoo.btn_connect"), type="primary", key="btn_connect_odoo"):
            from backend.odoo_client import OdooClient
            client = OdooClient(odoo_url, odoo_db, odoo_user, odoo_key)
            if client.connect():
                st.session_state["odoo_client"] = client
                st.session_state["odoo_connected"] = True
                st.session_state["odoo_last_connected"] = datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                st.success(t("settings.odoo.connected"))
                st.rerun()
            else:
                st.session_state["odoo_connected"] = False
                st.error(t("settings.odoo.connect_fail"))

        if st.session_state["odoo_connected"]:
            st.success(
                t("settings.odoo.status",
                  timestamp=st.session_state.get("odoo_last_connected", "unknown"))
            )
            if st.button(t("settings.odoo.btn_disconnect"), key="btn_disconnect_odoo"):
                st.session_state["odoo_client"] = None
                st.session_state["odoo_connected"] = False
                st.rerun()
        else:
            st.caption(t("settings.odoo.not_connected"))

    # ── Gmail Integration ──────────────────────────────────────────────────────
    with st.expander(t("settings.gmail.title"), expanded=False):
        st.caption(t("settings.gmail.caption"))

        # Credentials status
        if not gmail_monitor.credentials_file_exists():
            st.warning(t("settings.gmail.no_creds"))
            uploaded = st.file_uploader(
                t("settings.gmail.upload"),
                type=["json"],
                key="creds_uploader",
            )
            if uploaded:
                gmail_monitor.save_credentials_file(uploaded.read())
                st.success(t("settings.gmail.saved"))
                st.rerun()
        else:
            st.success(t("settings.gmail.present"))
            if st.button(t("settings.gmail.btn_remove"), key="btn_rm_creds"):
                gmail_monitor.credentials_path.unlink(missing_ok=True)
                st.rerun()

        # Auth status
        if gmail_monitor.is_authenticated():
            st.success(t("settings.gmail.authorized"))
        else:
            st.caption(t("settings.gmail.not_authorized"))

        # OAuth flow
        if (
            gmail_monitor.credentials_file_exists()
            and not gmail_monitor.is_authenticated()
        ):
            base_url = os.getenv("APP_BASE_URL", "http://localhost:8501")
            redirect_uri = base_url.rstrip("/") + "/"

            if st.button(t("settings.gmail.btn_auth"), key="btn_auth_gmail"):
                try:
                    auth_url, flow = gmail_monitor.get_authorization_url(redirect_uri)
                    st.session_state["gmail_oauth_flow"] = flow
                    st.session_state["gmail_auth_url"] = auth_url
                    st.rerun()
                except Exception as e:
                    st.error(t("settings.gmail.auth_fail", error=e))

            if st.session_state.get("gmail_auth_url"):
                st.info(t("settings.gmail.auth_prompt"))
                st.markdown(
                    f'[{t("settings.gmail.auth_link")}]({st.session_state["gmail_auth_url"]})'
                )

        # Monitor controls
        if gmail_monitor.is_authenticated():
            gstatus = gmail_monitor.get_status()

            running_label = (
                t("settings.gmail.status_running") if gstatus["running"]
                else t("settings.gmail.status_stopped")
            )
            last_poll = gstatus["last_poll_at"] or t("settings.gmail.status_never")
            st.caption(
                t("settings.gmail.status",
                  status=running_label,
                  count=gstatus["emails_processed"],
                  last_poll=last_poll)
            )

            if gstatus["last_error"]:
                st.error(t("settings.gmail.error", error=gstatus["last_error"]))

            if not gstatus["running"]:
                if st.button(t("settings.gmail.btn_start"), key="btn_start_gmail"):
                    gmail_monitor.start()
                    st.rerun()
            else:
                if st.button(t("settings.gmail.btn_stop"), key="btn_stop_gmail"):
                    gmail_monitor.stop()
                    st.rerun()

            interval = st.number_input(
                t("settings.gmail.poll_interval"),
                min_value=60,
                max_value=3600,
                value=gmail_monitor.poll_interval,
                step=60,
                key="gmail_poll_interval",
            )
            if interval != gmail_monitor.poll_interval:
                gmail_monitor.poll_interval = interval

            if st.button(t("settings.gmail.btn_revoke"), key="btn_revoke_gmail"):
                gmail_monitor.revoke_token()
                st.rerun()

        st.caption(
            t("settings.gmail.redirect_uri",
              url=os.getenv("APP_BASE_URL", "http://localhost:8501") + "/")
        )

    # ── Database Explorer ──────────────────────────────────────────────────────
    with st.expander(t("settings.db.title"), expanded=False):
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' ORDER BY tablename"
                    )
                    tables = [row[0] for row in cur.fetchall()]
        except Exception as e:
            st.error(t("settings.db.error", error=e))
            return

        if not tables:
            st.info(t("settings.db.no_tables"))
            return

        selected_table = st.selectbox(t("settings.db.table"), tables, key="db_explorer_table")
        row_limit = st.number_input(
            t("settings.db.row_limit"),
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
            st.error(t("settings.db.query_error", error=e))
            return

        st.caption(
            t("settings.db.info",
              table=selected_table,
              total=total_rows,
              showing=min(row_limit, total_rows))
        )
        st.dataframe(df, use_container_width=True, height=250)

        # Quick actions for staged_pos
        if selected_table == "staged_pos" and total_rows > 0:
            st.caption(t("settings.db.quick_actions"))
            if st.button(t("settings.db.btn_del_unproc"), type="secondary", key="btn_del_unproc"):
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "DELETE FROM staged_pos WHERE status = 'unprocessed'"
                        )
                        deleted = cur.rowcount
                    conn.commit()
                st.success(t("settings.db.deleted", count=deleted))
                st.rerun()

            if st.button(t("settings.db.btn_del_all"), type="secondary", key="btn_del_all"):
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM staged_pos")
                        deleted = cur.rowcount
                    conn.commit()
                st.success(t("settings.db.deleted", count=deleted))
                st.rerun()
