import os
import sys
from datetime import datetime

import pandas as pd
import streamlit as st
import yaml
from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.odoo_client import OdooClient
from backend.gmail_monitor import GmailMonitor
from database.connection import get_db_connection

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="T&T PO Processor",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ────────────────────────────────────────────────────────────────────────
def _load_css():
    for path in ("frontend/style.css", os.path.join(os.path.dirname(__file__), "style.css")):
        try:
            with open(path) as f:
                st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
            return
        except FileNotFoundError:
            continue

_load_css()

# ── Settings ───────────────────────────────────────────────────────────────────
@st.cache_resource
def load_settings():
    for path in ("config/settings.yaml", os.path.join(os.path.dirname(__file__), "../config/settings.yaml")):
        try:
            with open(path) as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            continue
    return {}

settings = load_settings()

# ── Gmail Monitor singleton ────────────────────────────────────────────────────
@st.cache_resource
def get_gmail_monitor() -> GmailMonitor:
    monitor = GmailMonitor(
        db_conn_factory=get_db_connection,
        poll_interval_seconds=int(os.getenv("GMAIL_POLL_INTERVAL_SECONDS", 300)),
        token_path=os.getenv("GMAIL_TOKEN_PATH", "/data/gmail/token.json"),
        credentials_path=os.getenv("GMAIL_CREDENTIALS_PATH", "/data/gmail/credentials.json"),
    )
    if monitor.is_authenticated():
        monitor.start()
    return monitor

# ── Session state defaults ─────────────────────────────────────────────────────
_DEFAULTS = {
    # Odoo connection
    "odoo_client": None,
    "odoo_connected": False,
    "odoo_last_connected": None,
    "config_odoo_url": os.getenv("ODOO_URL", settings.get("odoo", {}).get("default_url", "")),
    "config_odoo_db": os.getenv("ODOO_DB", settings.get("odoo", {}).get("default_db", "")),
    "config_odoo_user": os.getenv("ODOO_USERNAME", settings.get("odoo", {}).get("default_user", "official@atiara.ca")),
    # Gmail OAuth (transient — only alive during the OAuth redirect flow)
    "gmail_oauth_flow": None,
    "gmail_auth_url": None,
    # Queue
    "selected_po_ids": [],
    "source_po_ids": [],
    # Pipeline
    "extracted_po_data": pd.DataFrame(),
    "po_errors": [],
    "odoo_products_cache": None,
    "order_summaries": pd.DataFrame(),
    "line_details": pd.DataFrame(),
    "transform_errors": [],
    # SO reference
    "config_latest_so": None,
    "latest_so_auto_fetched": False,
    # Export
    "export_mode": "create_new",
    "open_sales_orders": [],
    "selected_so_id": None,
    "append_result": None,
    # Navigation
    "current_page": "Dashboard",
}

for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Auto-connect Odoo on first load ───────────────────────────────────────────
if not st.session_state["odoo_connected"]:
    _api_key = os.getenv("ODOO_API_KEY", "")
    _url = st.session_state["config_odoo_url"]
    _db = st.session_state["config_odoo_db"]
    _user = st.session_state["config_odoo_user"]

    if _url and _db and _user and _api_key:
        try:
            _client = OdooClient(_url, _db, _user, _api_key)
            if _client.connect():
                st.session_state["odoo_client"] = _client
                st.session_state["odoo_connected"] = True
                st.session_state["odoo_last_connected"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass  # Will show as disconnected; user can reconnect from Settings

# ── OAuth callback handler ─────────────────────────────────────────────────────
_qp = st.query_params
if "code" in _qp and st.session_state.get("gmail_oauth_flow") is not None:
    try:
        _monitor = get_gmail_monitor()
        _monitor.exchange_code_for_token(st.session_state["gmail_oauth_flow"], _qp["code"])
        st.session_state["gmail_oauth_flow"] = None
        st.session_state["gmail_auth_url"] = None
        st.query_params.clear()
        _monitor.start()
        st.success("✅ Gmail authorized and monitoring started!")
        st.rerun()
    except Exception as _e:
        st.error(f"OAuth callback failed: {_e}")
        st.query_params.clear()

# ── Global Odoo signal light (fixed, visible on every page) ───────────────────
_connected = st.session_state["odoo_connected"]
_clr = "#00FF88" if _connected else "#FF4444"
_lbl = "CONNECTED" if _connected else "DISCONNECTED"
st.markdown(
    f"""
    <div style="position:fixed; top:60px; right:20px; z-index:9999;
                display:flex; align-items:center; gap:6px;
                background:rgba(0,0,0,0.55); padding:5px 12px;
                border-radius:20px; backdrop-filter:blur(6px);
                border:1px solid {_clr}33;">
        <div style="width:10px; height:10px; border-radius:50%;
                    background:{_clr}; box-shadow:0 0 8px {_clr};"></div>
        <span style="font-size:0.68rem; color:{_clr};
                     font-family:'JetBrains Mono',monospace;
                     letter-spacing:1px;">{_lbl}</span>
    </div>
    """,
    unsafe_allow_html=True,
)

# ── Header ─────────────────────────────────────────────────────────────────────
st.markdown("""
    <div style="display:flex; align-items:center; margin-bottom:1.5rem;">
        <div style="background:linear-gradient(135deg,#00D1FF 0%,#007AFF 100%);
                    padding:12px; border-radius:12px; margin-right:15px;
                    box-shadow:0 0 20px rgba(0,209,255,0.4);">
            <span style="font-size:24px;">🛒</span>
        </div>
        <div>
            <h1 style="margin:0; padding:0; line-height:1;">T&T PO PROCESSOR</h1>
            <p style="color:#666; font-size:0.9rem; margin:0;
                      font-family:'JetBrains Mono',monospace;">
                SUPPLY CHAIN AUTOMATION ENGINE v2.0
            </p>
        </div>
    </div>
""", unsafe_allow_html=True)

# ── Sidebar navigation ─────────────────────────────────────────────────────────
st.sidebar.markdown("""
    <div style="margin-bottom:2rem;">
        <h3 style="margin:0; font-size:1.1rem; letter-spacing:2px;
                   color:#00D1FF; font-family:'Outfit',sans-serif;">NAVIGATOR</h3>
        <div style="height:2px; width:30px;
                    background:linear-gradient(90deg,#00D1FF,#a100ff);
                    margin-top:5px;"></div>
    </div>
""", unsafe_allow_html=True)

PAGES = ["Settings", "Dashboard", "Transform & Review", "Inventory Optimization", "Export"]

if st.session_state["current_page"] not in PAGES:
    st.session_state["current_page"] = "Dashboard"

page = st.sidebar.radio(
    "Go to",
    PAGES,
    index=PAGES.index(st.session_state["current_page"]),
)
st.session_state["current_page"] = page

# ── Wizard progress indicator ─────────────────────────────────────────────────
_idx = PAGES.index(page)
_cols = st.columns(len(PAGES))
for _i, _p in enumerate(PAGES):
    with _cols[_i]:
        if _i < _idx:
            st.markdown(
                f'<div class="nav-indicator" style="background:rgba(0,255,163,0.1);'
                f'border-color:#00FFA3;color:#00FFA3">✓ {_p}</div>',
                unsafe_allow_html=True,
            )
        elif _i == _idx:
            st.markdown(
                f'<div class="nav-indicator nav-indicator-active">{_p}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="nav-indicator" style="opacity:0.4;">{_p}</div>',
                unsafe_allow_html=True,
            )

st.divider()

# ── Route to page module ───────────────────────────────────────────────────────
if page == "Settings":
    from frontend.pages.p1_settings import render
    render(settings, get_gmail_monitor())

elif page == "Dashboard":
    from frontend.pages.p2_queue import render
    render()

elif page == "Transform & Review":
    from frontend.pages.p3_transform import render
    render(settings)

elif page == "Inventory Optimization":
    from frontend.pages.p4_inventory import render
    render(settings)

elif page == "Export":
    from frontend.pages.p5_export import render
    render()
