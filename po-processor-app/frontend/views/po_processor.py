"""T&T PO Processor — combined workflow page with internal tabs."""

from typing import Dict

import streamlit as st

from frontend.i18n import t
from frontend.views import p2_queue, p4_inventory, p5_export

# Internal keys (language-independent) for tab state tracking
_TAB_KEYS = ["dashboard", "transform", "export"]


def render(settings: Dict) -> None:
    st.title(t("po.title"))

    if "po_active_tab" not in st.session_state:
        st.session_state["po_active_tab"] = _TAB_KEYS[0]

    # Build translated labels, keeping key-based mapping
    _tab_labels = [
        t("po.tab.dashboard"),
        t("po.tab.transform"),
        t("po.tab.export"),
    ]
    _key_to_label = dict(zip(_TAB_KEYS, _tab_labels))
    _label_to_key = dict(zip(_tab_labels, _TAB_KEYS))

    current_key = st.session_state["po_active_tab"]
    current_label = _key_to_label.get(current_key, _tab_labels[0])

    # Sync radio widget when tab was changed programmatically
    if st.session_state.pop("_po_tab_changed", False):
        st.session_state["_po_tab_radio"] = current_label

    active_label = st.radio(
        t("po.workflow_step"),
        _tab_labels,
        horizontal=True,
        key="_po_tab_radio",
        label_visibility="collapsed",
    )
    st.session_state["po_active_tab"] = _label_to_key.get(active_label, _TAB_KEYS[0])

    st.divider()

    active_key = st.session_state["po_active_tab"]
    if active_key == "dashboard":
        p2_queue.render(settings)
    elif active_key == "transform":
        p4_inventory.render(settings)
    elif active_key == "export":
        p5_export.render(settings)
