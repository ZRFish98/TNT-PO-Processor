"""Receive Invoice Verification — placeholder page."""

from typing import Dict

import streamlit as st

from frontend.i18n import t


def render(settings: Dict) -> None:
    st.title(t("invoice.title"))
    st.info(t("invoice.coming_soon"))
    st.caption(t("invoice.caption"))
