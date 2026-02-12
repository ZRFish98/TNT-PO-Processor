"""
Inventory Optimization page — extracted from original app.py page 4 with no
functional changes. Preserves all flagging, editing, and deletion logic.
"""
import pandas as pd
import streamlit as st

from backend.data_transformer import DataTransformer
from backend.inventory_optimizer import InventoryOptimizer


def render(settings: dict):
    st.title("📦 Inventory Optimization")

    if st.session_state["line_details"].empty:
        st.warning("No line details available. Complete the Transform step first.")
        if st.button("← Back to Transform"):
            st.session_state["current_page"] = "Transform & Review"
            st.rerun()
        return

    # Ensure required columns exist (migration safety)
    for col in ["store_on_hand", "hist_avg_sales"]:
        if col not in st.session_state["line_details"].columns:
            st.session_state["line_details"][col] = 0.0

    for col in ["product_image", "shortage_details"]:
        if col not in st.session_state["line_details"].columns:
            st.session_state["line_details"][col] = None

    # ── Run Optimization ──────────────────────────────────────────────────────
    col_btn, _ = st.columns([1, 4])
    with col_btn:
        if st.button("🚀 Run Optimization Engine"):
            transformer = DataTransformer(settings)
            optimizer = InventoryOptimizer(transformer)
            optimized, logs = optimizer.optimize_allocations(
                st.session_state["line_details"],
                pd.DataFrame(),   # historical_sales (no Supabase)
                pd.DataFrame(),   # store_inventory  (no Supabase)
            )
            st.session_state["line_details"] = optimized
            st.success("Optimization complete.")
            st.rerun()

    # ── Product Allocation Summary ────────────────────────────────────────────
    st.subheader("📊 Product Allocation Summary")
    st.caption("Products where total demand exceeds available inventory (and available > 0).")

    if not st.session_state["line_details"].empty:
        summary_data = []
        for warehouse in ["CE", "CW"]:
            wh_data = st.session_state["line_details"][
                st.session_state["line_details"]["warehouse"] == warehouse
            ].copy()
            if wh_data.empty:
                continue

            product_groups = wh_data.groupby("internal_reference").agg(
                product_uom_qty=("product_uom_qty", "sum"),
                odoo_available=("odoo_available", "first"),
                odoo_on_hand=("odoo_on_hand", "first"),
                product_name=("product_name", "first"),
                product_image=("product_image", "first"),
            ).reset_index()
            product_groups["warehouse"] = warehouse
            product_groups["needs_allocation"] = (
                (product_groups["product_uom_qty"] > product_groups["odoo_available"])
                & (product_groups["odoo_available"] > 0)
            )
            summary_data.append(product_groups)

        if summary_data:
            summary_df = pd.concat(summary_data, ignore_index=True)
            allocation_needed = summary_df[summary_df["needs_allocation"]].copy()

            if not allocation_needed.empty:
                allocation_needed["shortage"] = (
                    allocation_needed["product_uom_qty"] - allocation_needed["odoo_available"]
                )
                allocation_needed["product_image"] = allocation_needed["product_image"].apply(
                    lambda x: f"data:image/png;base64,{x}"
                    if pd.notna(x) and x and not str(x).startswith("data:")
                    else x
                )
                disp = allocation_needed[[
                    "warehouse", "product_image", "internal_reference",
                    "product_name", "product_uom_qty", "odoo_available", "shortage",
                ]].copy()
                disp.columns = ["Warehouse", "Image", "SKU", "Product", "Demand", "Available", "Shortage"]
                st.dataframe(
                    disp,
                    column_config={
                        "Image": st.column_config.ImageColumn("Image", width="small"),
                        "Demand": st.column_config.NumberColumn(format="%d"),
                        "Available": st.column_config.NumberColumn(format="%d"),
                        "Shortage": st.column_config.NumberColumn(format="%d"),
                    },
                    hide_index=True,
                    use_container_width=True,
                )
                st.warning(f"⚠️ {len(allocation_needed)} product(s) need allocation decisions.")
            else:
                st.success("✅ All products have sufficient inventory.")

    st.divider()

    # ── Warehouse tabs ─────────────────────────────────────────────────────────
    st.subheader("Review & Edit Allocations")
    tab_ce, tab_cw = st.tabs(["Canada East (CE)", "Canada West (CW)"])

    with tab_ce:
        _render_warehouse_tab("CE")

    with tab_cw:
        _render_warehouse_tab("CW")

    st.divider()
    if st.button("Next: Export →", type="primary"):
        st.session_state["current_page"] = "Export"
        st.rerun()


def _render_warehouse_tab(warehouse_code: str):
    df = st.session_state["line_details"][
        st.session_state["line_details"]["warehouse"] == warehouse_code
    ].copy()

    if df.empty:
        st.info(f"No orders for {warehouse_code}.")
        return

    editable_cols = ["price_unit", "product_uom_qty", "flagged"]
    display_cols = [
        "store_id", "store_name", "product_image",
        "odoo_available", "odoo_on_hand", "store_on_hand", "hist_avg_sales",
        "flag_reason", "shortage_details", "barcode", "internal_reference",
        "po_number", "product_name",
    ]
    full_cols = [
        "store_id", "store_name", "product_image", "price_unit",
        "product_uom_qty", "flagged", "odoo_available", "odoo_on_hand",
        "store_on_hand", "hist_avg_sales", "flag_reason", "shortage_details",
        "barcode", "internal_reference", "po_number", "product_name",
    ]

    # ── Filters ────────────────────────────────────────────────────────────────
    st.markdown("### 🔍 Filters")
    f1, f2, f3, f4 = st.columns([2, 1, 1, 1])

    with f1:
        selected_refs = st.multiselect(
            "Filter by SKU",
            options=sorted(df["internal_reference"].unique().tolist()),
            default=[],
            key=f"filter_ref_{warehouse_code}",
        )
    with f2:
        flag_filter = st.selectbox(
            "Flagged Status",
            ["All", "Flagged Only", "Not Flagged"],
            key=f"filter_flag_{warehouse_code}",
        )
    with f3:
        reasons = ["All"] + sorted(
            [str(r) for r in df["flag_reason"].dropna().unique() if r]
        )
        reason_filter = st.selectbox("Flag Reason", reasons, key=f"filter_reason_{warehouse_code}")
    with f4:
        st.write("")
        st.write("")
        if st.button("Clear Filters", key=f"clear_filters_{warehouse_code}"):
            st.rerun()

    # Apply filters
    filtered_df = df.copy()
    if selected_refs:
        filtered_df = filtered_df[filtered_df["internal_reference"].isin(selected_refs)]
    if flag_filter == "Flagged Only":
        filtered_df = filtered_df[filtered_df["flagged"] == True]
    elif flag_filter == "Not Flagged":
        filtered_df = filtered_df[filtered_df["flagged"] == False]
    if reason_filter != "All":
        filtered_df = filtered_df[filtered_df["flag_reason"] == reason_filter]

    if len(filtered_df) < len(df):
        st.info(f"Showing {len(filtered_df)} of {len(df)} items.")

    display_df = filtered_df[[c for c in full_cols if c in filtered_df.columns]].copy()
    if "product_image" in display_df.columns:
        display_df["product_image"] = display_df["product_image"].apply(
            lambda x: f"data:image/png;base64,{x}"
            if pd.notna(x) and x and not str(x).startswith("data:")
            else x
        )

    st.divider()
    edited_df = st.data_editor(
        display_df,
        key=f"editor_{warehouse_code}",
        num_rows="dynamic",
        height=800,
        column_config={
            "product_image": st.column_config.ImageColumn("Image", width="small"),
            "product_uom_qty": st.column_config.NumberColumn("Qty", min_value=0, step=1),
            "price_unit": st.column_config.NumberColumn("Price", min_value=0.01, format="$%.2f"),
            "flagged": st.column_config.CheckboxColumn("Flagged?"),
        },
        disabled=[c for c in display_df.columns if c not in editable_cols],
        hide_index=True,
        use_container_width=True,
    )

    # ── Action buttons ─────────────────────────────────────────────────────────
    act_col1, act_col2 = st.columns([1, 3])

    with act_col1:
        flagged_count = int(edited_df["flagged"].sum()) if "flagged" in edited_df.columns else 0
        if flagged_count > 0:
            if st.button(
                f"🗑️ Delete {flagged_count} Flagged Items",
                key=f"delete_flagged_{warehouse_code}",
            ):
                for col in editable_cols:
                    if col in edited_df.columns:
                        st.session_state["line_details"].loc[filtered_df.index, col] = edited_df[col].values
                drop_idx = edited_df[edited_df["flagged"] == True].index
                st.session_state["line_details"] = st.session_state["line_details"].drop(drop_idx)
                st.rerun()

    with act_col2:
        if st.button(f"💾 Save Changes ({warehouse_code})", key=f"save_{warehouse_code}"):
            for col in editable_cols:
                if col in edited_df.columns:
                    st.session_state["line_details"].loc[filtered_df.index, col] = edited_df[col].values
            st.success(f"✅ Changes saved for {warehouse_code}.")
            st.rerun()
