"""
Transform & Review page — matches PO lines against Odoo products, calculates
SO references, and prepares order_summaries + line_details DataFrames.
"""
import streamlit as st
import pandas as pd

from backend.data_transformer import DataTransformer


def render(settings: dict):
    st.title("🔄 Transform & Review")

    if st.session_state["extracted_po_data"].empty:
        st.warning("No PO data loaded. Go to Dashboard and select POs to process.")
        if st.button("← Back to Dashboard"):
            st.session_state["current_page"] = "Dashboard"
            st.rerun()
        return

    # ── SO Reference (auto-fetch from Odoo) ───────────────────────────────────
    if not st.session_state["line_details"].empty:
        _show_results(settings)
        return

    client = st.session_state.get("odoo_client")

    st.subheader("📋 SO Reference")

    # Auto-fetch latest SO from Odoo if not yet fetched
    if not st.session_state["latest_so_auto_fetched"] and client:
        with st.spinner("Fetching latest SO number from Odoo..."):
            fetched = client.get_latest_so_number()
            if fetched:
                st.session_state["config_latest_so"] = fetched
                st.session_state["latest_so_auto_fetched"] = True

    so_col1, so_col2 = st.columns([2, 1])
    with so_col1:
        if st.session_state.get("latest_so_auto_fetched"):
            st.info(f"Auto-fetched latest SO: **OATS00{st.session_state['config_latest_so']}**")

        latest_so_input = st.number_input(
            "Latest SO Number in Odoo (override if needed)",
            min_value=1,
            step=1,
            value=st.session_state.get("config_latest_so") or 1,
            help=(
                "Enter the numeric part of the latest SO in Odoo "
                "(e.g. 3270 for OATS003270). "
                "New SO references are calculated as: Latest + Total_Orders + Index."
            ),
        )
        st.session_state["config_latest_so"] = latest_so_input

    with so_col2:
        if st.button("🔄 Re-fetch from Odoo"):
            if client:
                fetched = client.get_latest_so_number()
                if fetched:
                    st.session_state["config_latest_so"] = fetched
                    st.session_state["latest_so_auto_fetched"] = True
                    st.success(f"Fetched: OATS00{fetched}")
                else:
                    st.warning("Could not fetch — using manual value.")
            else:
                st.error("Not connected to Odoo.")

    st.divider()

    # ── Fetch & Transform ─────────────────────────────────────────────────────
    if st.button("⚡ Fetch Odoo Data & Transform", type="primary"):
        if not client:
            st.error("Connect to Odoo first (Settings page).")
            return
        if not st.session_state["config_latest_so"]:
            st.error("Enter the latest SO number above.")
            return

        with st.spinner("Fetching product data from Odoo..."):
            refs = (
                st.session_state["extracted_po_data"]["Internal Reference"]
                .unique()
                .astype(str)
                .tolist()
            )
            products = client.get_products(internal_references=refs)
            st.session_state["odoo_products_cache"] = products

        with st.spinner("Transforming PO data..."):
            _run_transform(settings)

        st.rerun()


def _run_transform(settings: dict):
    """Run DataTransformer with current session state inputs."""
    transformer = DataTransformer(settings)
    summary, details, logs = transformer.transform_data(
        st.session_state["extracted_po_data"],
        st.session_state["odoo_products_cache"],
        latest_so_number=st.session_state["config_latest_so"],
    )
    st.session_state["order_summaries"] = summary
    st.session_state["line_details"] = details
    st.session_state["transform_errors"] = logs


def _show_results(settings: dict):
    """Display transform results with Refresh + Re-transform controls."""
    client = st.session_state.get("odoo_client")

    # ── Refresh / Re-transform buttons ────────────────────────────────────────
    btn_col1, btn_col2, btn_col3 = st.columns(3)

    with btn_col1:
        if st.button("🔃 Refresh Odoo Data"):
            if not client:
                st.error("Connect to Odoo first.")
            else:
                with st.spinner("Re-fetching products from Odoo..."):
                    refs = (
                        st.session_state["extracted_po_data"]["Internal Reference"]
                        .unique()
                        .astype(str)
                        .tolist()
                    )
                    products = client.get_products(internal_references=refs)
                    st.session_state["odoo_products_cache"] = products
                st.success(f"Fetched {len(products)} products from Odoo.")

    with btn_col2:
        if st.button("♻️ Re-transform"):
            if not st.session_state.get("odoo_products_cache"):
                st.error("No cached Odoo data. Click 'Refresh Odoo Data' first.")
            else:
                with st.spinner("Re-transforming..."):
                    _run_transform(settings)
                st.success("Re-transform complete.")
                st.rerun()

    with btn_col3:
        if st.button("🗑️ Clear & Start Over"):
            st.session_state["order_summaries"] = pd.DataFrame()
            st.session_state["line_details"] = pd.DataFrame()
            st.session_state["odoo_products_cache"] = None
            st.session_state["latest_so_auto_fetched"] = False
            st.rerun()

    # ── SO reference info box ──────────────────────────────────────────────────
    if not st.session_state["order_summaries"].empty:
        latest_so = st.session_state.get("config_latest_so", 0)
        total_orders = len(st.session_state["order_summaries"])
        first_so = latest_so + total_orders + 1
        last_so = latest_so + total_orders + total_orders

        st.info(
            f"**SO Reference Calculation:**  "
            f"Latest SO in Odoo: OATS00{latest_so} | "
            f"Total orders: {total_orders} | "
            f"Range: OATS00{first_so} → OATS00{last_so}"
        )

    # ── Order summaries ────────────────────────────────────────────────────────
    st.subheader("Order Summaries")
    st.dataframe(st.session_state["order_summaries"], use_container_width=True)

    st.subheader("Line Details")
    st.dataframe(st.session_state["line_details"], use_container_width=True)

    if st.session_state["transform_errors"]:
        with st.expander(f"Transformation Logs ({len(st.session_state['transform_errors'])} messages)"):
            for log in st.session_state["transform_errors"]:
                st.write(log)

    st.divider()
    if st.button("Next: Inventory Optimization →", type="primary"):
        st.session_state["current_page"] = "Inventory Optimization"
        st.rerun()
