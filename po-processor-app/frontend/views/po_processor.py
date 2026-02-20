"""T&T PO Processor — combined workflow page with internal tabs."""

from typing import Dict

import streamlit as st

from frontend.views import p2_queue, p4_inventory, p5_export

_TABS = ["Dashboard", "Transform & Review", "Export"]


def render(settings: Dict) -> None:
    st.title("T&T PO Processor")

    if "po_active_tab" not in st.session_state:
        st.session_state["po_active_tab"] = _TABS[0]

    active = st.radio(
        "Workflow step",
        _TABS,
        index=_TABS.index(st.session_state["po_active_tab"]),
        horizontal=True,
        key="_po_tab_radio",
        label_visibility="collapsed",
    )
    st.session_state["po_active_tab"] = active

    st.divider()

    if active == "Dashboard":
        p2_queue.render(settings)
    elif active == "Transform & Review":
        p4_inventory.render(settings)
    elif active == "Export":
        p5_export.render(settings)
