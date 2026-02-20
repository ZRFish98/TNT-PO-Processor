"""
Transform & Review — merged page combining Odoo data fetch / transformation
with inventory optimization, allocation review, and line-level editing.

Summaries recompute from non-flagged line_details so deletions and flags are
immediately reflected (and propagated to the Export page).
"""
import pandas as pd
import streamlit as st

from backend.data_transformer import DataTransformer
from backend.inventory_optimizer import InventoryOptimizer


# ── helpers ─────────────────────────────────────────────────────────────────────

def _run_transform(settings: dict) -> None:
    """Run DataTransformer with current session state inputs."""
    transformer = DataTransformer(settings)
    summary, details, logs = transformer.transform_data(
        st.session_state["extracted_po_data"],
        st.session_state["odoo_products_cache"],
    )
    st.session_state["order_summaries"] = summary
    st.session_state["line_details"] = details
    st.session_state["transform_errors"] = logs


def _compute_live_summaries(settings: dict) -> pd.DataFrame:
    """Rebuild order summaries from the current ``line_details``, excluding
    flagged rows.  Stores whose lines are all flagged / deleted will not
    appear — meaning no SO will be created for them downstream.
    """
    lines = st.session_state["line_details"]
    if lines.empty:
        return pd.DataFrame()

    active = lines[lines.get("flagged", False) != True].copy()
    if active.empty:
        return pd.DataFrame()

    tt_names = settings.get("tt_store_names", {})
    summaries: list[dict] = []

    for store_id in sorted(active["store_id"].unique()):
        group = active[active["store_id"] == store_id]
        wh = group["warehouse"].iloc[0] if "warehouse" in group.columns else "—"
        store_name = group["store_name"].iloc[0] if "store_name" in group.columns else ""
        official_name = tt_names.get(int(store_id), f"Store {store_id}")

        po_list = sorted(group["po_number"].unique().astype(str).tolist())
        order_date = group["order_date"].iloc[0] if "order_date" in group.columns else ""
        delivery_date = group["delivery_date"].iloc[0] if "delivery_date" in group.columns else ""

        summaries.append({
            "store_id": store_id,
            "store_name": store_name,
            "official_name": official_name,
            "warehouse": wh,
            "po_count": len(po_list),
            "po_numbers": ", ".join(po_list),
            "order_date": order_date,
            "delivery_date": delivery_date,
            "total_lines": len(group),
            "total_value": group["total_price"].sum() if "total_price" in group.columns else 0,
        })

    return pd.DataFrame(summaries)


# ── main render ─────────────────────────────────────────────────────────────────

def render(settings: dict):
    st.title("Transform & Review")

    # ── No line_details yet → show transform controls ─────────────────────────
    if st.session_state["line_details"].empty:
        if st.session_state["extracted_po_data"].empty:
            st.warning("No PO data loaded. Go to the Dashboard tab and select POs to process.")
            return

        client = st.session_state.get("odoo_client")

        st.subheader("Fetch Odoo Data & Transform")
        st.caption(
            "This will match each PO line against Odoo products, convert quantities "
            "from cases to units, and group by store."
        )

        if st.button("Fetch Odoo Data & Transform", type="primary"):
            if not client:
                st.error("Connect to Odoo first (Settings page).")
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
        return

    # ── line_details exists → full review UI ──────────────────────────────────

    # Ensure required columns exist (migration safety)
    for col in ["store_on_hand", "hist_avg_sales"]:
        if col not in st.session_state["line_details"].columns:
            st.session_state["line_details"][col] = 0.0

    for col in ["product_image", "shortage_details"]:
        if col not in st.session_state["line_details"].columns:
            st.session_state["line_details"][col] = None

    # ── Order Summaries (live, reflects flagged / deleted lines) ──────────────
    live_summaries = _compute_live_summaries(settings)
    st.session_state["order_summaries"] = live_summaries  # keep Export in sync

    st.subheader("Order Summaries")
    if live_summaries.empty:
        st.warning(
            "All order lines have been flagged or removed — nothing to export. "
            "Un-flag items or clear & start over."
        )
    else:
        # Metrics row
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Stores", len(live_summaries))
        m2.metric("POs", int(live_summaries["po_count"].sum()))
        m3.metric("Active Lines", int(live_summaries["total_lines"].sum()))
        m4.metric("Total Value", f"${live_summaries['total_value'].sum():,.2f}")

        disp_summary = live_summaries[[
            "store_id", "official_name", "warehouse", "po_count",
            "po_numbers", "order_date", "delivery_date",
            "total_lines", "total_value",
        ]].copy()
        disp_summary.columns = [
            "Store", "Customer", "WH", "POs",
            "PO Numbers", "Order Date", "Delivery Date",
            "Lines", "Value",
        ]
        st.dataframe(
            disp_summary,
            column_config={
                "Store": st.column_config.NumberColumn(format="%d", width="small"),
                "Value": st.column_config.NumberColumn(format="$%.2f"),
                "Lines": st.column_config.NumberColumn(format="%d", width="small"),
                "POs": st.column_config.NumberColumn(format="%d", width="small"),
            },
            hide_index=True,
            use_container_width=True,
        )

    # ── Unmatched SKUs (PDF lines with no Odoo product) ──────────────────────
    extracted = st.session_state.get("extracted_po_data", pd.DataFrame())
    line_det = st.session_state["line_details"]

    odoo_cache = st.session_state.get("odoo_products_cache")
    if not extracted.empty and odoo_cache:
        # SKUs present in PDF but absent from the Odoo product catalog
        pdf_skus = set(extracted["Internal Reference"].astype(str).unique())
        odoo_skus = {str(p.get("default_code", "")) for p in odoo_cache if p.get("default_code")}
        missing_skus = pdf_skus - odoo_skus

        if missing_skus:
            st.subheader("Unmatched SKUs")
            st.warning(
                f"**{len(missing_skus)} SKU(s) from the PDF could not be found in Odoo:** "
                + ", ".join(sorted(missing_skus))
            )

            # Build table of all affected PO lines
            unmatched_lines = extracted[
                extracted["Internal Reference"].astype(str).isin(missing_skus)
            ].copy()
            unmatched_disp = unmatched_lines[[
                "Store Name", "Store ID", "Internal Reference", "Description", "PO No.",
            ]].copy()
            unmatched_disp.columns = ["Store Name", "Store ID", "SKU", "Product Name", "PO #"]
            unmatched_disp = unmatched_disp.sort_values(["SKU", "Store ID"]).reset_index(drop=True)

            st.dataframe(
                unmatched_disp,
                column_config={
                    "Store ID": st.column_config.NumberColumn(format="%d", width="small"),
                    "PO #": st.column_config.NumberColumn(format="%d", width="small"),
                },
                hide_index=True,
                use_container_width=True,
            )

    # ── Zero Units Per Order warning ──────────────────────────────────────────
    odoo_products = st.session_state.get("odoo_products_cache")
    if odoo_products and not line_det.empty:
        products_df = pd.DataFrame(odoo_products)
        if not products_df.empty and "x_studio_tt_om_int" in products_df.columns:
            # Products where Units Per Order is 0, None, or False
            zero_upo = products_df[
                products_df["x_studio_tt_om_int"].apply(
                    lambda v: v is None or v is False or (isinstance(v, (int, float)) and v == 0)
                )
            ]
            if not zero_upo.empty:
                # Only warn about SKUs actually present in the current line_details
                active_skus = set(line_det["internal_reference"].astype(str).unique())
                zero_upo_active = zero_upo[
                    zero_upo["default_code"].astype(str).isin(active_skus)
                ]
                if not zero_upo_active.empty:
                    st.subheader("Units Per Order = 0")
                    st.warning(
                        f"**{len(zero_upo_active)} product(s) have `Units Per Order` "
                        f"(x_studio_tt_om_int) = 0 in Odoo.** "
                        "Quantity and price calculations default to 1 unit per order "
                        "when this field is 0 — results may be incorrect. "
                        "Please update these products in Odoo."
                    )
                    upo_disp = zero_upo_active[[
                        "default_code", "name", "barcode", "x_studio_tt_om_int",
                    ]].copy()
                    upo_disp.columns = ["SKU", "Product Name", "Barcode", "Units Per Order"]
                    st.dataframe(
                        upo_disp,
                        hide_index=True,
                        use_container_width=True,
                    )

    # Transform logs
    if st.session_state.get("transform_errors"):
        with st.expander(f"Transform Logs ({len(st.session_state['transform_errors'])} messages)"):
            for log in st.session_state["transform_errors"]:
                st.write(log)

    st.divider()

    # ── Utility buttons: Refresh / Re-transform / Clear ───────────────────────
    client = st.session_state.get("odoo_client")
    btn1, btn2, btn3, btn4 = st.columns(4)

    with btn1:
        if st.button("Refresh Odoo Data"):
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
                with st.spinner("Re-transforming..."):
                    _run_transform(settings)
                st.rerun()

    with btn2:
        if st.button("Re-transform"):
            if not st.session_state.get("odoo_products_cache"):
                st.error("No cached Odoo data. Click 'Refresh Odoo Data' first.")
            else:
                with st.spinner("Re-transforming..."):
                    _run_transform(settings)
                st.rerun()

    with btn3:
        if st.button("Clear & Start Over"):
            st.session_state["order_summaries"] = pd.DataFrame()
            st.session_state["line_details"] = pd.DataFrame()
            st.session_state["odoo_products_cache"] = None
            st.session_state["transform_errors"] = []
            # Clear export state
            for _k in ("export_latest_so", "export_so_fetched", "export_open_orders"):
                st.session_state.pop(_k, None)
            st.rerun()

    with btn4:
        if st.button("Run Optimization Engine", type="primary"):
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
    st.subheader("Product Allocation Summary")
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
                st.warning(f"{len(allocation_needed)} product(s) need allocation decisions.")
            else:
                st.success("All products have sufficient inventory.")

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
        st.session_state["po_active_tab"] = "Export"
        st.rerun()


def _render_warehouse_tab(warehouse_code: str):
    df = st.session_state["line_details"][
        st.session_state["line_details"]["warehouse"] == warehouse_code
    ].copy()

    if df.empty:
        st.info(f"No orders for {warehouse_code}.")
        return

    editable_cols = ["price_unit", "product_uom_qty", "flagged"]
    full_cols = [
        "store_id", "store_name", "product_image", "price_unit",
        "product_uom_qty", "flagged", "odoo_available", "odoo_on_hand",
        "store_on_hand", "hist_avg_sales", "flag_reason", "shortage_details",
        "barcode", "internal_reference", "po_number", "product_name",
    ]

    # ── Filters ────────────────────────────────────────────────────────────────
    st.markdown("### Filters")

    # Row 1: SKU, Barcode, Product Name
    f1, f2, f3 = st.columns([2, 2, 2])
    with f1:
        selected_refs = st.multiselect(
            "Filter by SKU",
            options=sorted(df["internal_reference"].unique().tolist()),
            default=[],
            key=f"filter_ref_{warehouse_code}",
        )
    with f2:
        barcode_options = sorted(
            [str(b) for b in df["barcode"].dropna().unique() if b]
        )
        selected_barcodes = st.multiselect(
            "Filter by Barcode",
            options=barcode_options,
            default=[],
            key=f"filter_barcode_{warehouse_code}",
        )
    with f3:
        product_name_search = st.text_input(
            "Filter by Product Name (contains)",
            value="",
            key=f"filter_product_name_{warehouse_code}",
        )

    # Row 2: Flagged Status, Flag Reason, action buttons
    f4, f5, f6, f7, f8 = st.columns([1.2, 1.2, 1, 1.3, 1.3])
    with f4:
        flag_filter = st.selectbox(
            "Flagged Status",
            ["All", "Flagged Only", "Not Flagged"],
            key=f"filter_flag_{warehouse_code}",
        )
    with f5:
        reasons = ["All"] + sorted(
            [str(r) for r in df["flag_reason"].dropna().unique() if r]
        )
        reason_filter = st.selectbox("Flag Reason", reasons, key=f"filter_reason_{warehouse_code}")
    with f6:
        st.write("")
        st.write("")
        if st.button("Clear Filters", key=f"clear_filters_{warehouse_code}"):
            st.rerun()
    with f7:
        st.write("")
        st.write("")
        delete_flagged_clicked = st.button(
            "Delete Flagged",
            key=f"delete_flagged_{warehouse_code}",
        )
    with f8:
        st.write("")
        st.write("")
        save_clicked = st.button(
            f"Save Changes ({warehouse_code})",
            key=f"save_{warehouse_code}",
            type="primary",
        )

    # Apply filters
    filtered_df = df.copy()
    if selected_refs:
        filtered_df = filtered_df[filtered_df["internal_reference"].isin(selected_refs)]
    if selected_barcodes:
        filtered_df = filtered_df[filtered_df["barcode"].astype(str).isin(selected_barcodes)]
    if product_name_search.strip():
        mask = filtered_df["product_name"].str.contains(
            product_name_search.strip(), case=False, na=False
        )
        filtered_df = filtered_df[mask]
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

    # ── Action button handlers ─────────────────────────────────────────────────
    # The editor may have fewer rows than filtered_df (user deleted rows inline)
    # so we must intersect indices before writing back.
    common_idx = filtered_df.index.intersection(edited_df.index)
    removed_idx = filtered_df.index.difference(edited_df.index)

    if delete_flagged_clicked:
        flagged_count = int(edited_df["flagged"].sum()) if "flagged" in edited_df.columns else 0
        if flagged_count > 0:
            # Write editable values back for rows still present
            for col in editable_cols:
                if col in edited_df.columns:
                    st.session_state["line_details"].loc[common_idx, col] = edited_df.loc[common_idx, col].values
            # Drop rows flagged in editor + rows removed inline
            drop_idx = edited_df.loc[edited_df["flagged"] == True].index.union(removed_idx)
            st.session_state["line_details"] = st.session_state["line_details"].drop(drop_idx)
            st.rerun()
        else:
            st.warning("No flagged items to delete.")

    if save_clicked:
        # Write editable values back for rows still present
        for col in editable_cols:
            if col in edited_df.columns:
                st.session_state["line_details"].loc[common_idx, col] = edited_df.loc[common_idx, col].values
        # Drop rows the user removed inline in the editor
        if not removed_idx.empty:
            st.session_state["line_details"] = st.session_state["line_details"].drop(removed_idx)
        st.success(f"Changes saved for {warehouse_code}.")
        st.rerun()
