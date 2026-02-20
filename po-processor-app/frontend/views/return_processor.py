"""Return Processor — placeholder page."""

from typing import Dict

import streamlit as st


def render(settings: Dict) -> None:
    st.title("Return Processor")
    st.info("This workflow is coming soon.")
    st.caption("Process product returns and generate return authorizations.")
