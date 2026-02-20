"""Receive Invoice Verification — placeholder page."""

from typing import Dict

import streamlit as st


def render(settings: Dict) -> None:
    st.title("Receive Invoice Verification")
    st.info("This workflow is coming soon.")
    st.caption("Verify received invoices against purchase orders and flag discrepancies.")
