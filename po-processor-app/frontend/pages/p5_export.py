"""
Export page — Append to existing Odoo SO (Direct API) or Create New SO (Excel download).
"""
import io
import json
from datetime import datetime

import pandas as pd
import streamlit as st

from database.connection import get_db_connection


def _to_excel(summaries: pd.DataFrame, lines: pd.DataFrame) -> bytes:
    """Generate Odoo import Excel (same logic as original app.py)."""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        if not summaries.empty:
            headers_df = pd.DataFrame({
                "Customer": summaries["official_name"],
                "Order Date": summaries["order_date"],
                "Delivery Date": summaries["delivery_date"],
                "Client Order Ref": summaries["po_numbers"],
            })
            headers_df.to_excel(writer, sheet_name="Sales Orders", index=False)

        if not lines.empty:
            valid_lines = lines[~lines["flagged"]].copy()
            valid_lines["store_display"] = (
                valid_lines["store_name"]
                + " - "
                + valid_lines["store_id"].astype(str).str.zfill(3)
            )
            lines_df = pd.DataFrame({
                "Order Reference": valid_lines["so_reference"],
                "Store": valid_lines["store_display"],
                "Product": valid_lines["barcode"],
                "Description": valid_lines["product_name"],
                "Quantity": valid_lines["product_uom_qty"],
                "Unit Price": valid_lines["price_unit"],
                "Lock Unit Price": True,
            })
            lines_df.to_excel(writer, sheet_name="Sales Order Lines", index=False)

    return output.getvalue()


def _mark_source_processed(source_po_ids: list):
    """Mark staged_pos records as processed after export."""
    if not source_po_ids:
        return
    sql = """
        UPDATE staged_pos
        SET status = 'processed', processed_at = NOW(), updated_at = NOW()
        WHERE id = ANY(%s) AND status = 'processing'
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (source_po_ids,))
            conn.commit()
    except Exception as e:
        st.warning(f"Could not mark source records as processed: {e}")


def render():
    st.title("🚀 Export")

    line_details = st.session_state.get("line_details", pd.DataFrame())
    order_summaries = st.session_state.get("order_summaries", pd.DataFrame())

    if line_details.empty:
        st.warning("No processed data found. Complete the Inventory Optimization step first.")
        if st.button("← Back to Inventory"):
            st.session_state["current_page"] = "Inventory Optimization"
            st.rerun()
        return

    # ── Summary stats ─────────────────────────────────────────────────────────
    st.subheader("Final Summary")
    total_orders = line_details["store_id"].nunique()
    total_lines = len(line_details[~line_details["flagged"]])
    total_value = (
        line_details[~line_details["flagged"]]["product_uom_qty"]
        * line_details[~line_details["flagged"]]["price_unit"]
    ).sum()

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Stores", total_orders)
    c2.metric("Export Lines (unflagged)", total_lines)
    c3.metric("Total Value", f"${total_value:,.2f}")

    st.divider()

    # ── Store selector ────────────────────────────────────────────────────────
    st.subheader("Select Store to Export")

    unique_stores = sorted(line_details["store_name"].unique().tolist())
    # Get official names for display
    store_name_map = {}
    if not order_summaries.empty:
        for _, row in order_summaries.iterrows():
            store_name_map[row["store_name"]] = row.get("official_name", row["store_name"])

    store_options = {store_name_map.get(s, s): s for s in unique_stores}

    selected_display = st.selectbox(
        "Store",
        options=list(store_options.keys()),
        key="export_store_selector",
    )
    selected_store_name = store_options[selected_display]
    official_name = selected_display

    # Filter line_details for selected store
    store_lines = line_details[
        (line_details["store_name"] == selected_store_name) & (~line_details["flagged"])
    ]
    store_summary = order_summaries[order_summaries["store_name"] == selected_store_name]

    st.caption(
        f"**{len(store_lines)}** unflagged lines for {official_name} | "
        f"Value: **${(store_lines['product_uom_qty'] * store_lines['price_unit']).sum():,.2f}**"
    )

    st.divider()

    # ── Append vs. Create ─────────────────────────────────────────────────────
    st.subheader("Export Strategy")

    export_mode = st.radio(
        "Choose action",
        ["Create New Sales Order", "Append to Existing Sales Order"],
        key="export_mode_radio",
        horizontal=True,
    )

    # ═══════════════════════════════════════════════════════════════════════════
    # PATH A: Create New SO → Excel download
    # ═══════════════════════════════════════════════════════════════════════════
    if export_mode == "Create New Sales Order":
        if not store_summary.empty:
            so_ref = store_summary.iloc[0].get("so_reference", "N/A")
            st.info(f"New SO will be created with reference: **{so_ref}**")

        st.markdown("Download the Excel file and import it into Odoo.")

        if not order_summaries.empty:
            excel_bytes = _to_excel(order_summaries, line_details)
            if st.download_button(
                label="📥 Download Odoo Import Excel",
                data=excel_bytes,
                file_name=f"odoo_import_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ):
                _mark_source_processed(st.session_state.get("source_po_ids", []))
                st.success("✅ Export complete. Source POs marked as processed.")
        else:
            st.warning("No order summaries to export.")

    # ═══════════════════════════════════════════════════════════════════════════
    # PATH B: Append to Existing SO → Direct Odoo API
    # ═══════════════════════════════════════════════════════════════════════════
    else:
        client = st.session_state.get("odoo_client")
        if not client:
            st.error("Connect to Odoo first (Settings page).")
            return

        # Fetch open SOs for this store
        fetch_col, _ = st.columns([1, 3])
        with fetch_col:
            if st.button("🔍 Load Open Sales Orders"):
                with st.spinner(f"Fetching open SOs for {official_name}..."):
                    open_orders = client.get_open_sales_orders(official_name)
                st.session_state["open_sales_orders"] = open_orders
                st.session_state["selected_so_id"] = None

        open_orders = st.session_state.get("open_sales_orders", [])

        if open_orders:
            st.markdown(f"**{len(open_orders)} open Sales Order(s)** for {official_name}:")

            # Build display DataFrame
            so_df = pd.DataFrame(open_orders)
            so_display = so_df[[c for c in [
                "id", "name", "state", "delivery_status",
                "client_order_ref", "create_date", "x_studio_delivery_date",
            ] if c in so_df.columns]].copy()

            so_display.insert(0, "Select", False)

            edited_so = st.data_editor(
                so_display,
                key="so_selector",
                num_rows="fixed",
                column_config={
                    "Select": st.column_config.CheckboxColumn("Select", width="small"),
                    "name": st.column_config.TextColumn("SO #"),
                    "state": st.column_config.TextColumn("State"),
                    "delivery_status": st.column_config.TextColumn("Delivery"),
                    "client_order_ref": st.column_config.TextColumn("PO Ref"),
                    "create_date": st.column_config.TextColumn("Created"),
                    "x_studio_delivery_date": st.column_config.TextColumn("Delivery Date"),
                },
                disabled=[c for c in so_display.columns if c != "Select"],
                hide_index=True,
                use_container_width=True,
            )

            selected_so_rows = edited_so[edited_so["Select"] == True]

            if len(selected_so_rows) > 1:
                st.warning("Select only ONE Sales Order to append to.")
            elif len(selected_so_rows) == 1:
                row_idx = selected_so_rows.index[0]
                so_id = int(so_df.loc[row_idx, "id"])
                so_name = so_df.loc[row_idx, "name"]
                st.session_state["selected_so_id"] = so_id

                st.info(f"Will append **{len(store_lines)} lines** to **{so_name}** (ID: {so_id})")

                # Preview lines
                with st.expander("Preview lines to append"):
                    preview = store_lines[["internal_reference", "product_name", "product_uom_qty", "price_unit"]].copy()
                    st.dataframe(preview, use_container_width=True)

                if st.button("📎 Append Lines to Odoo SO", type="primary"):
                    lines_payload = store_lines[
                        ["product_id", "product_uom_qty", "price_unit"]
                    ].to_dict(orient="records")

                    with st.spinner(f"Appending {len(lines_payload)} lines to {so_name}..."):
                        created_ids, failed_lines = client.append_lines_to_order(so_id, lines_payload)

                    if created_ids:
                        st.success(f"✅ Successfully appended {len(created_ids)} line(s) to {so_name}.")

                    if failed_lines:
                        st.error(f"❌ {len(failed_lines)} line(s) failed:")
                        for fl in failed_lines:
                            st.write(f"  Line {fl['index']}: {fl['error']}")

                    if created_ids:
                        _mark_source_processed(st.session_state.get("source_po_ids", []))
                        st.session_state["append_result"] = {
                            "created_ids": created_ids,
                            "failed_lines": failed_lines,
                            "so_name": so_name,
                        }

        elif st.session_state.get("open_sales_orders") is not None:
            st.info(
                f"No open (non-delivered, non-test) Sales Orders found for {official_name}. "
                "Switch to 'Create New Sales Order' or verify the store name in Odoo."
            )
        else:
            st.caption("Click 'Load Open Sales Orders' to see existing orders for this store.")
