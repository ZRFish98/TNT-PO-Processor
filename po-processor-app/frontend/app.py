import json as _json_mod
import os
import sys
from datetime import datetime

# Allow Google OAuth to return more scopes than requested (e.g. previously granted)
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import yaml
from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.odoo_client import OdooClient
from backend.gmail_monitor import GmailMonitor
from database.connection import get_db_connection
from frontend.i18n import t

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Atiara Internal Tool",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="auto",
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

# ── Auto-generate credentials.json from env vars ─────────────────────────────
_gmail_client_id = os.getenv("GMAIL_CLIENT_ID")
_gmail_client_secret = os.getenv("GMAIL_CLIENT_SECRET")
_gmail_creds_path = os.getenv("GMAIL_CREDENTIALS_PATH", "/data/gmail/credentials.json")

if _gmail_client_id and _gmail_client_secret:
    _base_url = os.getenv("APP_BASE_URL", "http://localhost:8501")
    _creds_data = {
        "web": {
            "client_id": _gmail_client_id,
            "client_secret": _gmail_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": [_base_url.rstrip("/") + "/"],
        }
    }
    # Always write so credential updates via env vars take effect immediately
    _new_json = _json_mod.dumps(_creds_data, sort_keys=True)
    _needs_write = True
    if os.path.exists(_gmail_creds_path):
        try:
            with open(_gmail_creds_path) as _f:
                _needs_write = _json_mod.dumps(_json_mod.load(_f), sort_keys=True) != _new_json
        except Exception:
            _needs_write = True
    if _needs_write:
        os.makedirs(os.path.dirname(_gmail_creds_path), exist_ok=True)
        with open(_gmail_creds_path, "w") as _f:
            _f.write(_new_json)
        # Invalidate any stale token from a previous client ID
        _token_path = os.getenv("GMAIL_TOKEN_PATH", "/data/gmail/token.json")
        if os.path.exists(_token_path):
            os.remove(_token_path)

# ── Gmail Monitor singleton ────────────────────────────────────────────────────
@st.cache_resource
def get_gmail_monitor() -> GmailMonitor:
    _cw = set(settings.get("warehouse_mapping", {}).get("cw_stores", []))
    monitor = GmailMonitor(
        db_conn_factory=get_db_connection,
        cw_stores=_cw,
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
    # Export (SO ref + open orders are managed locally in p5_export.py)
    # Return Processor
    "return_uploaded_pdf": None,
    "return_page_pdfs": [],        # list[bytes] — per-page PDFs
    "return_extracted": [],        # list[dict]  — Gemini OCR results
    "return_summaries": pd.DataFrame(),
    "return_details": pd.DataFrame(),
    "return_extraction_errors": [],
    "return_unmatched": [],
    # Payment Reconciler
    "payment_pdf_bytes": None,
    "payment_statement": None,
    "payment_matched": False,
    "payment_matched_lines": [],
    "payment_bank_journals": None,
    "payment_batches_created": False,
    "payment_batch_results": None,
}

for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Language default ─────────────────────────────────────────────────────────
if "language" not in st.session_state:
    st.session_state["language"] = "en"

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

# ── Language switch via query param ───────────────────────────────────────────
_qp = st.query_params
if "set_lang" in _qp:
    _new_lang = _qp["set_lang"]
    if _new_lang in ("en", "zh"):
        st.session_state["language"] = _new_lang
    del st.query_params["set_lang"]
    st.rerun()

# ── OAuth callback handler ─────────────────────────────────────────────────────
# Google redirects back with ?code=... which may land in a NEW Streamlit session
# (the Flow object from session state is gone). Recreate it from credentials.json.
if "code" in _qp and os.path.exists(_gmail_creds_path):
    try:
        from google_auth_oauthlib.flow import Flow as _OAuthFlow

        _redirect_uri = os.getenv("APP_BASE_URL", "http://localhost:8501").rstrip("/") + "/"
        _flow = _OAuthFlow.from_client_secrets_file(
            _gmail_creds_path,
            scopes=["https://www.googleapis.com/auth/gmail.modify"],
            redirect_uri=_redirect_uri,
        )
        _monitor = get_gmail_monitor()
        _monitor.exchange_code_for_token(_flow, _qp["code"])
        st.session_state["gmail_oauth_flow"] = None
        st.session_state["gmail_auth_url"] = None
        st.query_params.clear()
        _monitor.start()
        st.success(t("app.oauth_success"))
        st.rerun()
    except Exception as _e:
        st.error(t("app.oauth_fail", error=_e))
        st.query_params.clear()

# ── Start Gmail Monitor eagerly (so it polls even before Settings is visited) ─
_gmail_monitor = get_gmail_monitor()

# ── Language toggle button (native Streamlit approach) ──────────────────────
_cur_lang = st.session_state.get("language", "en")
_lang_label = "\u4e2d\u6587" if _cur_lang == "en" else "EN"
_lang_target = "zh" if _cur_lang == "en" else "en"

# Use st.markdown with HTML/JS to create a clickable language toggle
# This runs in Streamlit's context, not an iframe, so navigation should work
st.markdown(
    f"""
    <div id="atiara-lang-toggle" style="position:fixed;top:14px;right:70px;z-index:999999;">
        <a href="?set_lang={_lang_target}"
           style="font-size:0.7rem;color:#FF367F;font-family:Poppins,sans-serif;
                  font-weight:600;text-decoration:none;cursor:pointer;
                  background:#111111;padding:4px 10px;border-radius:6px;
                  border:1px solid #222222;display:inline-block;">
            {_lang_label}
        </a>
    </div>
    """,
    unsafe_allow_html=True,
)

# ── Odoo connection indicator (fixed, top-right) ──────────────────────────────
_connected = st.session_state["odoo_connected"]
_clr = "#22c55e" if _connected else "#ef4444"
_dot_lbl = t("app.connected") if _connected else t("app.disconnected")

st.markdown(
    f"""
    <div style="position:fixed;top:14px;right:140px;z-index:999999;
                display:flex;align-items:center;gap:6px;
                background:#111111;padding:4px 10px;border-radius:6px;
                border:1px solid #222222;">
        <div style="width:8px;height:8px;border-radius:50%;background:{_clr};"></div>
        <span style="font-size:0.7rem;color:#737373;font-family:Poppins,sans-serif;font-weight:500;">
            {_dot_lbl}
        </span>
    </div>
    """,
    unsafe_allow_html=True,
)

# ── Header ─────────────────────────────────────────────────────────────────────
st.markdown(f"""
    <div style="display:flex; align-items:center; gap:12px; margin-bottom:0.75rem;">
        <div style="width:36px; height:36px; border-radius:50%;
                    background:#FF367F; display:flex; align-items:center;
                    justify-content:center; font-weight:700; font-size:14px;
                    color:#fff; font-family:'Poppins',sans-serif;">AT</div>
        <div>
            <h1 style="margin:0; line-height:1; font-size:1.2rem !important;
                       font-family:'Poppins',sans-serif; font-weight:600;
                       color:#ededed; -webkit-text-fill-color:#ededed;">
                {t('app.title')}</h1>
            <p style="color:#737373; font-size:0.68rem; margin:0;
                      font-family:'Poppins',sans-serif; letter-spacing:2px;
                      text-transform:uppercase;">
                {t('app.subtitle')}</p>
        </div>
    </div>
""", unsafe_allow_html=True)

# ── Multipage navigation ──────────────────────────────────────────────────────
import frontend.views.po_processor as _po_processor
import frontend.views.invoice_verification as _invoice_verification
import frontend.views.return_processor as _return_processor
import frontend.views.payment_reconciler as _payment_reconciler
import frontend.views.p1_settings as _p1_settings

_po_page = st.Page(
    lambda: _po_processor.render(settings),
    title=t("nav.po_processor"),
    icon=":material/shopping_cart:",
    url_path="po-processor",
    default=True,
)
_invoice_page = st.Page(
    lambda: _invoice_verification.render(settings),
    title=t("nav.invoice_verification"),
    icon=":material/receipt_long:",
    url_path="invoice-verification",
)
_return_page = st.Page(
    lambda: _return_processor.render(settings),
    title=t("nav.return_processor"),
    icon=":material/undo:",
    url_path="return-processor",
)
_payment_page = st.Page(
    lambda: _payment_reconciler.render(settings),
    title=t("nav.payment_reconciler"),
    icon=":material/account_balance:",
    url_path="payment-reconciler",
)
_settings_page = st.Page(
    lambda: _p1_settings.render(settings, _gmail_monitor),
    title=t("nav.settings"),
    icon=":material/settings:",
    url_path="settings",
)

pg = st.navigation({
    t("nav.workflows"): [_po_page, _invoice_page, _return_page, _payment_page],
    "": [_settings_page],
})
pg.run()
