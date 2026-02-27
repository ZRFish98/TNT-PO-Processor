"""
Transform & Review — merged page combining Odoo data fetch / transformation
with inventory optimization, allocation review, and line-level editing.

Summaries recompute from non-flagged line_details so deletions and flags are
immediately reflected (and propagated to the Export page).
"""
import pandas as pd
import streamlit as st

from backend.bq_lost_lines import record_lost_lines
from backend.data_transformer import DataTransformer
from backend.inventory_optimizer import InventoryOptimizer
from frontend.i18n import t


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
        wh = group["warehouse"].iloc[0] if "warehouse" in group.columns else "\u2014"
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
    st.title(t("transform.title"))

    # ── No line_details yet → show transform controls ─────────────────────────
    if st.session_state["line_details"].empty:
        if st.session_state["extracted_po_data"].empty:
            st.warning(t("transform.no_data"))
            return

        client = st.session_state.get("odoo_client")

        st.subheader(t("transform.fetch.title"))
        st.caption(t("transform.fetch.caption"))

        if st.button(t("transform.fetch.btn"), type="primary"):
            if not client:
                st.error(t("transform.fetch.no_odoo"))
                return

            with st.spinner(t("transform.fetch.spinner_products")):
                refs = (
                    st.session_state["extracted_po_data"]["Internal Reference"]
                    .unique()
                    .astype(str)
                    .tolist()
                )
                products = client.get_products(internal_references=refs)
                st.session_state["odoo_products_cache"] = products

            with st.spinner(t("transform.fetch.spinner_transform")):
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

    if "is_promotional" not in st.session_state["line_details"].columns:
        st.session_state["line_details"]["is_promotional"] = False

    # ── Order Summaries (live, reflects flagged / deleted lines) ──────────────
    live_summaries = _compute_live_summaries(settings)
    st.session_state["order_summaries"] = live_summaries  # keep Export in sync

    st.subheader(t("transform.summaries.title"))
    if live_summaries.empty:
        st.warning(t("transform.summaries.empty"))
    else:
        # Metrics row
        m1, m2, m3, m4 = st.columns(4)
        m1.metric(t("transform.metric.stores"), len(live_summaries))
        m2.metric(t("transform.metric.pos"), int(live_summaries["po_count"].sum()))
        m3.metric(t("transform.metric.lines"), int(live_summaries["total_lines"].sum()))
        m4.metric(t("transform.metric.value"), f"${live_summaries['total_value'].sum():,.2f}")

        disp_summary = live_summaries[[
            "store_id", "official_name", "warehouse", "po_count",
            "po_numbers", "order_date", "delivery_date",
            "total_lines", "total_value",
        ]].copy()
        disp_summary.columns = [
            t("transform.col.store"), t("transform.col.customer"),
            t("transform.col.wh"), t("transform.col.pos"),
            t("transform.col.po_numbers"), t("transform.col.order_date"),
            t("transform.col.delivery_date"), t("transform.col.lines"),
            t("transform.col.value"),
        ]
        st.dataframe(
            disp_summary,
            column_config={
                t("transform.col.store"): st.column_config.NumberColumn(format="%d", width="small"),
                t("transform.col.value"): st.column_config.NumberColumn(format="$%.2f"),
                t("transform.col.lines"): st.column_config.NumberColumn(format="%d", width="small"),
                t("transform.col.pos"): st.column_config.NumberColumn(format="%d", width="small"),
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
            st.subheader(t("transform.unmatched.title"))
            st.warning(
                t("transform.unmatched.warning",
                  count=len(missing_skus),
                  skus=", ".join(sorted(missing_skus)))
            )

            # Build table of all affected PO lines
            unmatched_lines = extracted[
                extracted["Internal Reference"].astype(str).isin(missing_skus)
            ].copy()
            unmatched_disp = unmatched_lines[[
                "Store Name", "Store ID", "Internal Reference", "Description", "PO No.",
            ]].copy()
            unmatched_disp.columns = [
                t("transform.unmatched.col.store_name"),
                t("transform.unmatched.col.store_id"),
                t("transform.unmatched.col.sku"),
                t("transform.unmatched.col.product_name"),
                t("transform.unmatched.col.po"),
            ]
            unmatched_disp = unmatched_disp.sort_values(
                [t("transform.unmatched.col.sku"), t("transform.unmatched.col.store_id")]
            ).reset_index(drop=True)

            st.dataframe(
                unmatched_disp,
                column_config={
                    t("transform.unmatched.col.store_id"): st.column_config.NumberColumn(format="%d", width="small"),
                    t("transform.unmatched.col.po"): st.column_config.NumberColumn(format="%d", width="small"),
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
                    st.subheader(t("transform.zero_upo.title"))
                    st.warning(t("transform.zero_upo.warning", count=len(zero_upo_active)))
                    upo_disp = zero_upo_active[[
                        "default_code", "name", "barcode", "x_studio_tt_om_int",
                    ]].copy()
                    upo_disp.columns = [
                        t("transform.zero_upo.col.sku"),
                        t("transform.zero_upo.col.product_name"),
                        t("transform.zero_upo.col.barcode"),
                        t("transform.zero_upo.col.upo"),
                    ]
                    st.dataframe(
                        upo_disp,
                        hide_index=True,
                        use_container_width=True,
                    )

    # Transform logs
    if st.session_state.get("transform_errors"):
        with st.expander(t("transform.logs.title", count=len(st.session_state["transform_errors"]))):
            for log in st.session_state["transform_errors"]:
                st.write(log)

    st.divider()

    # ── Utility buttons: Refresh / Re-transform / Clear ───────────────────────
    client = st.session_state.get("odoo_client")
    btn1, btn2, btn3, btn4 = st.columns(4)

    with btn1:
        if st.button(t("transform.btn.refresh")):
            if not client:
                st.error(t("transform.btn.refresh_no_odoo"))
            else:
                with st.spinner(t("transform.spinner.refetch")):
                    refs = (
                        st.session_state["extracted_po_data"]["Internal Reference"]
                        .unique()
                        .astype(str)
                        .tolist()
                    )
                    products = client.get_products(internal_references=refs)
                    st.session_state["odoo_products_cache"] = products
                with st.spinner(t("transform.spinner.retransform")):
                    _run_transform(settings)
                st.rerun()

    with btn2:
        if st.button(t("transform.btn.retransform")):
            if not st.session_state.get("odoo_products_cache"):
                st.error(t("transform.btn.retransform_no_cache"))
            else:
                with st.spinner(t("transform.spinner.retransform")):
                    _run_transform(settings)
                st.rerun()

    with btn3:
        if st.button(t("transform.btn.clear")):
            st.session_state["order_summaries"] = pd.DataFrame()
            st.session_state["line_details"] = pd.DataFrame()
            st.session_state["odoo_products_cache"] = None
            st.session_state["transform_errors"] = []
            # Clear export state
            for _k in ("export_latest_so", "export_so_fetched", "export_open_orders"):
                st.session_state.pop(_k, None)
            st.rerun()

    with btn4:
        if st.button(t("transform.btn.optimize"), type="primary"):
            transformer = DataTransformer(settings)
            optimizer = InventoryOptimizer(transformer)
            optimized, logs = optimizer.optimize_allocations(
                st.session_state["line_details"],
                pd.DataFrame(),   # historical_sales (no Supabase)
                pd.DataFrame(),   # store_inventory  (no Supabase)
            )
            st.session_state["line_details"] = optimized
            st.success(t("transform.msg.optimized"))
            st.rerun()

    # ── Product Allocation Summary ────────────────────────────────────────────
    st.subheader(t("transform.allocation.title"))
    st.caption(t("transform.allocation.caption"))

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
                disp.columns = [
                    t("transform.allocation.col.warehouse"),
                    t("transform.allocation.col.image"),
                    t("transform.allocation.col.sku"),
                    t("transform.allocation.col.product"),
                    t("transform.allocation.col.demand"),
                    t("transform.allocation.col.available"),
                    t("transform.allocation.col.shortage"),
                ]
                st.dataframe(
                    disp,
                    column_config={
                        t("transform.allocation.col.image"): st.column_config.ImageColumn(
                            t("transform.allocation.col.image"), width="small"
                        ),
                        t("transform.allocation.col.demand"): st.column_config.NumberColumn(format="%d"),
                        t("transform.allocation.col.available"): st.column_config.NumberColumn(format="%d"),
                        t("transform.allocation.col.shortage"): st.column_config.NumberColumn(format="%d"),
                    },
                    hide_index=True,
                    use_container_width=True,
                )
                st.warning(t("transform.allocation.warning", count=len(allocation_needed)))
            else:
                st.success(t("transform.allocation.ok"))

    st.divider()

    # ── Warehouse tabs ─────────────────────────────────────────────────────────
    st.subheader(t("transform.review.title"))
    tab_ce, tab_cw = st.tabs([t("transform.tab.ce"), t("transform.tab.cw")])

    with tab_ce:
        _render_warehouse_tab("CE")

    with tab_cw:
        _render_warehouse_tab("CW")

    st.divider()
    if st.button(t("transform.btn.next_export"), type="primary"):
        st.session_state["po_active_tab"] = "export"
        st.session_state["_po_tab_changed"] = True
        st.rerun()


def _render_warehouse_tab(warehouse_code: str):
    df = st.session_state["line_details"][
        st.session_state["line_details"]["warehouse"] == warehouse_code
    ].copy()

    if df.empty:
        st.info(t("transform.wh.empty", warehouse=warehouse_code))
        return

    editable_cols = ["price_unit", "product_uom_qty", "flagged", "is_promotional"]
    full_cols = [
        "store_id", "store_name", "product_image", "is_promotional", "price_unit",
        "product_uom_qty", "flagged", "odoo_available", "odoo_on_hand",
        "store_on_hand", "hist_avg_sales", "flag_reason", "shortage_details",
        "barcode", "internal_reference", "po_number", "product_name",
    ]

    # ── Filters ────────────────────────────────────────────────────────────────
    st.markdown(t("transform.filter.title"))

    # Row 1: SKU, Barcode, Product Name
    f1, f2, f3 = st.columns([2, 2, 2])
    with f1:
        selected_refs = st.multiselect(
            t("transform.filter.sku"),
            options=sorted(df["internal_reference"].unique().tolist()),
            default=[],
            key=f"filter_ref_{warehouse_code}",
        )
    with f2:
        barcode_options = sorted(
            [str(b) for b in df["barcode"].dropna().unique() if b]
        )
        selected_barcodes = st.multiselect(
            t("transform.filter.barcode"),
            options=barcode_options,
            default=[],
            key=f"filter_barcode_{warehouse_code}",
        )
    with f3:
        product_name_search = st.text_input(
            t("transform.filter.product_name"),
            value="",
            key=f"filter_product_name_{warehouse_code}",
        )

    # Row 2: Flagged Status, Flag Reason, action buttons
    f4, f5, f6, f7, f8 = st.columns([1.2, 1.2, 1, 1.3, 1.3])
    with f4:
        flag_options = [
            t("transform.filter.flagged.all"),
            t("transform.filter.flagged.yes"),
            t("transform.filter.flagged.no"),
        ]
        flag_filter = st.selectbox(
            t("transform.filter.flagged"),
            flag_options,
            key=f"filter_flag_{warehouse_code}",
        )
    with f5:
        reasons = [t("transform.filter.reason.all")] + sorted(
            [str(r) for r in df["flag_reason"].dropna().unique() if r]
        )
        reason_filter = st.selectbox(
            t("transform.filter.reason"), reasons, key=f"filter_reason_{warehouse_code}"
        )
    with f6:
        st.write("")
        st.write("")
        if st.button(t("transform.btn.clear_filters"), key=f"clear_filters_{warehouse_code}"):
            st.rerun()
    with f7:
        st.write("")
        st.write("")
        delete_flagged_clicked = st.button(
            t("transform.btn.delete_flagged"),
            key=f"delete_flagged_{warehouse_code}",
        )
    with f8:
        st.write("")
        st.write("")
        save_clicked = st.button(
            t("transform.btn.save", warehouse=warehouse_code),
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
    if flag_filter == t("transform.filter.flagged.yes"):
        filtered_df = filtered_df[filtered_df["flagged"] == True]
    elif flag_filter == t("transform.filter.flagged.no"):
        filtered_df = filtered_df[filtered_df["flagged"] == False]
    if reason_filter != t("transform.filter.reason.all"):
        filtered_df = filtered_df[filtered_df["flag_reason"] == reason_filter]

    if len(filtered_df) < len(df):
        st.info(t("transform.filter.showing", filtered=len(filtered_df), total=len(df)))

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
            "product_image": st.column_config.ImageColumn(t("transform.col.image"), width="small"),
            "is_promotional": st.column_config.CheckboxColumn(t("transform.col.promo"), width="small"),
            "product_uom_qty": st.column_config.NumberColumn(t("transform.col.qty"), min_value=0, step=1),
            "price_unit": st.column_config.NumberColumn(t("transform.col.price"), min_value=0.01, format="$%.2f"),
            "flagged": st.column_config.CheckboxColumn(t("transform.col.flagged")),
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
            lost = st.session_state["line_details"].loc[
                st.session_state["line_details"].index.intersection(drop_idx)
            ]
            record_lost_lines(lost, "delete_flagged", warehouse_code)
            st.session_state["line_details"] = st.session_state["line_details"].drop(drop_idx)
            st.rerun()
        else:
            st.warning(t("transform.msg.no_flagged"))

    if save_clicked:
        # Write editable values back for rows still present
        for col in editable_cols:
            if col in edited_df.columns:
                st.session_state["line_details"].loc[common_idx, col] = edited_df.loc[common_idx, col].values
        # Drop rows the user removed inline in the editor
        if not removed_idx.empty:
            lost = st.session_state["line_details"].loc[
                st.session_state["line_details"].index.intersection(removed_idx)
            ]
            record_lost_lines(lost, "save_removed", warehouse_code)
            st.session_state["line_details"] = st.session_state["line_details"].drop(removed_idx)
        st.success(t("transform.msg.saved", warehouse=warehouse_code))
        st.rerun()
