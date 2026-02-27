"""
Export page — per-store Create New (Excel) or Append to existing SO (Odoo API).
SO references are predicted and assigned here, not in Transform.
"""
import io
from datetime import datetime
from typing import Any, Dict

import pandas as pd
import streamlit as st

from backend.data_transformer import DataTransformer
from database.connection import get_db_connection
from frontend.i18n import t


def _to_odoo_date(raw: str) -> str:
    """Convert a date string to Odoo's YYYY-MM-DD format.

    Handles MM/DD/YYYY and DD/MM/YYYY (falls back to dateutil).
    Returns the original string if parsing fails.
    """
    if not raw:
        return raw
    # Already in YYYY-MM-DD?
    if len(raw) >= 10 and raw[4] == "-":
        return raw[:10]
    from dateutil import parser as _dp
    try:
        return _dp.parse(raw, dayfirst=False).strftime("%Y-%m-%d")
    except Exception:
        return raw


# ── Helpers ───────────────────────────────────────────────────────────────────


def _add_promo_unit_price(df: pd.DataFrame) -> pd.DataFrame:
    """Add promo_unit_price column: price_unit for promotional lines, None otherwise.

    Uses object dtype to preserve None (avoid pandas NaN coercion).
    """
    df = df.copy()
    if "is_promotional" in df.columns:
        df["promo_unit_price"] = df.apply(
            lambda r: r["price_unit"] if r.get("is_promotional") is True else None,
            axis=1,
        ).astype(object)
    else:
        df["promo_unit_price"] = None
    return df


def _to_excel(summaries: pd.DataFrame, lines: pd.DataFrame) -> bytes:
    """Generate Odoo import Excel for 'Create New' stores only."""
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


def _mark_source_processed(source_po_ids: list) -> None:
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
        st.warning(t("export.warn.mark_fail", error=e))


# ── Main render ───────────────────────────────────────────────────────────────

def render(settings: dict) -> None:
    st.title(t("export.title"))

    line_details = st.session_state.get("line_details", pd.DataFrame())
    order_summaries = st.session_state.get("order_summaries", pd.DataFrame())

    if line_details.empty:
        st.warning(t("export.no_data"))
        return

    client = st.session_state.get("odoo_client")

    # ── Summary stats ─────────────────────────────────────────────────────────
    st.subheader(t("export.summary.title"))
    unflagged = line_details[~line_details["flagged"]]
    total_stores = unflagged["store_id"].nunique()
    total_lines = len(unflagged)
    total_value = (unflagged["product_uom_qty"] * unflagged["price_unit"]).sum()

    c1, c2, c3 = st.columns(3)
    c1.metric(t("export.metric.stores"), total_stores)
    c2.metric(t("export.metric.lines"), total_lines)
    c3.metric(t("export.metric.value"), f"${total_value:,.2f}")

    st.divider()

    # ── SO Reference section ──────────────────────────────────────────────────
    st.subheader(t("export.so_ref.title"))

    # Auto-fetch latest SO from Odoo
    if "export_latest_so" not in st.session_state:
        st.session_state["export_latest_so"] = None
        st.session_state["export_so_fetched"] = False

    if not st.session_state["export_so_fetched"] and client:
        with st.spinner(t("export.so_ref.spinner")):
            fetched = client.get_latest_so_number()
            if fetched:
                st.session_state["export_latest_so"] = fetched
                st.session_state["export_so_fetched"] = True

    so_col1, so_col2 = st.columns([2, 1])
    with so_col1:
        if st.session_state.get("export_so_fetched"):
            st.info(t("export.so_ref.auto", number=st.session_state["export_latest_so"]))

        latest_so_input = st.number_input(
            t("export.so_ref.label"),
            min_value=1,
            step=1,
            value=st.session_state.get("export_latest_so") or 1,
            help=t("export.so_ref.help"),
            key="export_so_input",
        )
        st.session_state["export_latest_so"] = latest_so_input

    with so_col2:
        if st.button(t("export.so_ref.btn_refetch")):
            if client:
                fetched = client.get_latest_so_number()
                if fetched:
                    st.session_state["export_latest_so"] = fetched
                    st.session_state["export_so_fetched"] = True
                    st.rerun()  # rerun so number_input picks up new value
                else:
                    st.warning(t("export.so_ref.fetch_fail"))
            else:
                st.error(t("export.so_ref.no_odoo"))

    st.divider()

    # ── Fetch open SOs for all stores ─────────────────────────────────────────
    if order_summaries.empty:
        st.warning(t("export.no_summaries"))
        return

    # Build official name list for bulk lookup
    tt_names = settings.get("tt_store_names", {})
    store_ids = sorted(order_summaries["store_id"].unique().tolist())

    # Get open orders from Odoo (cached in session state per export session)
    if "export_open_orders" not in st.session_state:
        st.session_state["export_open_orders"] = None

    if st.session_state["export_open_orders"] is None and client:
        partner_names = [
            tt_names.get(int(sid), f"Store {sid}")
            for sid in store_ids
        ]
        with st.spinner(t("export.open_orders.spinner")):
            open_orders_map = client.get_open_orders_for_stores(partner_names)
        st.session_state["export_open_orders"] = open_orders_map
    else:
        open_orders_map = st.session_state.get("export_open_orders") or {}

    if st.button(t("export.open_orders.btn_refresh")):
        if client:
            partner_names = [
                tt_names.get(int(sid), f"Store {sid}")
                for sid in store_ids
            ]
            with st.spinner(t("export.open_orders.spinner_refresh")):
                open_orders_map = client.get_open_orders_for_stores(partner_names)
            st.session_state["export_open_orders"] = open_orders_map
            st.rerun()
        else:
            st.error(t("export.open_orders.no_odoo"))

    # ── Build per-store summary table ─────────────────────────────────────────
    st.subheader(t("export.summary_table.title"))
    st.caption(t("export.summary_table.caption"))

    rows = []
    for _, row in order_summaries.iterrows():
        sid = row["store_id"]
        official_name = row.get("official_name", tt_names.get(int(sid), f"Store {sid}"))

        # Look up open order for this store
        open_so = open_orders_map.get(official_name)
        latest_so_name = open_so["name"] if open_so else "\u2014"
        latest_so_ref = open_so.get("client_order_ref", "") if open_so else ""

        # Build action options
        create_new_label = t("export.action.create_new")
        if open_so:
            action_options = [create_new_label, t("export.action.append", so_name=latest_so_name)]
        else:
            action_options = [create_new_label]

        # Default action
        default_action = action_options[0]

        rows.append({
            "Import": True,
            "store_id": int(sid),
            "store_name": official_name,
            "po_numbers": row.get("po_numbers", ""),
            "order_date": row.get("order_date", ""),
            "delivery_date": row.get("delivery_date", ""),
            "total_lines": int(row.get("total_lines", 0)),
            "total_value": float(row.get("total_value", 0)),
            "po_count": int(row.get("po_count", 0)),
            "latest_undelivered_so": latest_so_name,
            "latest_so_po_ref": latest_so_ref,
            "_open_so_id": open_so["id"] if open_so else None,
            "_action_options": action_options,
            "Action": default_action,
        })

    # Per-store layout with individual Action selectboxes
    col_widths = [0.4, 2.2, 1.2, 0.5, 1.5, 1.5, 0.5, 0.8]
    header_labels = [
        t("export.col.import"), t("export.col.action"),
        t("export.col.latest_so"), t("export.col.store_id"),
        t("export.col.store_name"), t("export.col.po_numbers"),
        t("export.col.lines"), t("export.col.value"),
    ]
    hdr = st.columns(col_widths)
    for col, label in zip(hdr, header_labels):
        col.markdown(f"**{label}**")

    create_new_stores = []
    append_stores = []
    create_new_label = t("export.action.create_new")

    for idx, row_data in enumerate(rows):
        cols = st.columns(col_widths)
        with cols[0]:
            import_checked = st.checkbox(
                "import",
                value=True,
                key=f"export_import_{row_data['store_id']}",
                label_visibility="collapsed",
            )
        with cols[1]:
            action = st.selectbox(
                "action",
                options=row_data["_action_options"],
                key=f"export_action_{row_data['store_id']}",
                label_visibility="collapsed",
            )
        cols[2].markdown(row_data["latest_undelivered_so"])
        cols[3].markdown(str(int(row_data["store_id"])))
        cols[4].markdown(row_data["store_name"])
        cols[5].markdown(str(row_data["po_numbers"]))
        cols[6].markdown(str(row_data["total_lines"]))
        cols[7].markdown(f"${row_data['total_value']:,.2f}")

        if not import_checked:
            continue

        sid = int(row_data["store_id"])
        if action == create_new_label:
            create_new_stores.append(sid)
        elif row_data["_open_so_id"]:
            append_stores.append({
                "store_id": sid,
                "so_id": int(row_data["_open_so_id"]),
                "so_name": row_data["latest_undelivered_so"],
                "existing_po_ref": str(row_data.get("latest_so_po_ref", "") or ""),
                "new_po_numbers": str(row_data.get("po_numbers", "")),
            })

    # Show predicted SO refs for Create New stores
    latest_so = st.session_state.get("export_latest_so") or 0
    if create_new_stores:
        so_ref_map = DataTransformer.generate_so_references(create_new_stores, latest_so)
        first_ref = min(so_ref_map.values())
        last_ref = max(so_ref_map.values())
        st.info(t("export.info.create_new", count=len(create_new_stores), first=first_ref, last=last_ref))
    else:
        so_ref_map = {}

    if append_stores:
        st.info(t("export.info.append", count=len(append_stores)))

    st.divider()

    # ── Execute ───────────────────────────────────────────────────────────────
    st.subheader(t("export.execute.title"))

    if not client:
        st.error(t("export.execute.no_odoo"))
    elif not create_new_stores and not append_stores:
        st.caption(t("export.execute.no_stores"))
    else:
        # Preview what will happen
        if create_new_stores:
            create_lines = line_details[
                line_details["store_id"].isin(create_new_stores) & ~line_details["flagged"]
            ]
            st.markdown(t("export.execute.create_preview", count=len(create_new_stores), lines=len(create_lines)))
        if append_stores:
            append_line_count = sum(
                len(line_details[
                    (line_details["store_id"] == item["store_id"]) & ~line_details["flagged"]
                ])
                for item in append_stores
            )
            st.markdown(t("export.execute.append_preview", count=len(append_stores), lines=append_line_count))

        btn_col, dl_col = st.columns([1, 1])

        with btn_col:
            selected_count = len(create_new_stores) + len(append_stores)
            import_clicked = st.button(
                t("export.btn.import", count=selected_count),
                type="primary",
                key="export_api_btn",
            )

        # Optional Excel download for Create New stores
        with dl_col:
            if create_new_stores:
                create_summaries = order_summaries[
                    order_summaries["store_id"].isin(create_new_stores)
                ].copy()
                create_lines_xl = line_details[
                    line_details["store_id"].isin(create_new_stores)
                ].copy()
                create_lines_xl["so_reference"] = create_lines_xl["store_id"].map(so_ref_map)
                excel_bytes = _to_excel(create_summaries, create_lines_xl)
                st.download_button(
                    label=t("export.btn.download_excel"),
                    data=excel_bytes,
                    file_name=f"odoo_import_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="export_excel_download",
                )

        if import_clicked:
            all_success = True

            # ── Create New SOs via API ────────────────────────────────────
            for sid in create_new_stores:
                store_summary = order_summaries[order_summaries["store_id"] == sid].iloc[0]
                official_name = store_summary.get("official_name", f"Store {sid}")
                po_numbers = store_summary.get("po_numbers", "")
                order_date = _to_odoo_date(str(store_summary.get("order_date", "")))
                delivery_date = _to_odoo_date(str(store_summary.get("delivery_date", "")))

                store_lines = line_details[
                    (line_details["store_id"] == sid) & (~line_details["flagged"])
                ]

                # Look up partner ID
                partner_id = client.get_partner_id_by_name(official_name)
                if not partner_id:
                    st.error(t("export.err.no_partner", name=official_name))
                    all_success = False
                    continue

                # Create the SO header
                with st.spinner(t("export.spinner.creating", name=official_name)):
                    try:
                        so_id, so_name = client.create_sales_order(
                            customer_id=partner_id,
                            date_order=order_date or None,
                            client_order_ref=po_numbers or None,
                        )
                        # Set delivery date if available
                        if delivery_date:
                            try:
                                client.models.execute_kw(
                                    client.db, client.uid, client.api_key,
                                    'sale.order', 'write',
                                    [[so_id], {'x_studio_delivery_date': delivery_date}]
                                )
                            except Exception:
                                pass  # Non-critical

                        st.success(t("export.msg.created", so_name=so_name, name=official_name))
                    except Exception as e:
                        st.error(t("export.err.create_fail", name=official_name, error=e))
                        all_success = False
                        continue

                # Add lines grouped by PO number with section headers
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                all_created_ids: list[int] = []
                all_failed_lines: list[dict] = []

                store_lines = _add_promo_unit_price(store_lines)

                with st.spinner(t("export.spinner.adding", count=len(store_lines), so_name=so_name)):
                    for po_num in sorted(store_lines["po_number"].unique()):
                        po_lines = store_lines[store_lines["po_number"] == po_num]
                        po_is_promo = po_lines["is_promotional"].fillna(False).any() if "is_promotional" in po_lines.columns else False
                        promo_tag = " [PROMO]" if po_is_promo else ""
                        section_text = f"PO {po_num}{promo_tag} — {now_str}"
                        client.create_section_line(so_id, section_text)

                        payload = po_lines[
                            ["product_id", "product_uom_qty", "price_unit", "promo_unit_price"]
                        ].to_dict(orient="records")
                        ids, fails = client.append_lines_to_order(so_id, payload)
                        all_created_ids.extend(ids)
                        all_failed_lines.extend(fails)

                created_ids = all_created_ids
                failed_lines = all_failed_lines

                if created_ids:
                    st.caption(t("export.msg.lines_added", count=len(created_ids), so_name=so_name))
                if failed_lines:
                    all_success = False
                    st.error(t("export.err.lines_failed", count=len(failed_lines), so_name=so_name))
                    for fl in failed_lines:
                        st.write(t("export.err.line_detail", index=fl["index"], error=fl["error"]))

            # ── Append to Existing SOs via API ────────────────────────────
            for item in append_stores:
                sid = item["store_id"]
                so_id = item["so_id"]
                so_name = item["so_name"]

                store_lines = line_details[
                    (line_details["store_id"] == sid) & (~line_details["flagged"])
                ]

                store_lines = _add_promo_unit_price(store_lines)

                # Add lines grouped by PO number with section headers
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                all_created_ids: list[int] = []
                all_failed_lines: list[dict] = []

                with st.spinner(t("export.spinner.appending", count=len(store_lines), so_name=so_name)):
                    for po_num in sorted(store_lines["po_number"].unique()):
                        po_lines = store_lines[store_lines["po_number"] == po_num]
                        po_is_promo = po_lines["is_promotional"].fillna(False).any() if "is_promotional" in po_lines.columns else False
                        promo_tag = " [PROMO]" if po_is_promo else ""
                        section_text = f"Add-on 加单 — PO {po_num}{promo_tag} — {now_str}"
                        client.create_section_line(so_id, section_text)

                        payload = po_lines[
                            ["product_id", "product_uom_qty", "price_unit", "promo_unit_price"]
                        ].to_dict(orient="records")
                        ids, fails = client.append_lines_to_order(so_id, payload)
                        all_created_ids.extend(ids)
                        all_failed_lines.extend(fails)

                created_ids = all_created_ids
                failed_lines = all_failed_lines

                if created_ids:
                    st.success(t("export.msg.appended", count=len(created_ids), so_name=so_name))
                if failed_lines:
                    all_success = False
                    st.error(t("export.err.lines_failed", count=len(failed_lines), so_name=so_name))
                    for fl in failed_lines:
                        st.write(t("export.err.line_detail", index=fl["index"], error=fl["error"]))

                # Update client_order_ref (append PO numbers)
                existing_ref = item["existing_po_ref"]
                new_pos = item["new_po_numbers"]
                if existing_ref and new_pos:
                    merged_ref = f"{existing_ref}, {new_pos}"
                elif new_pos:
                    merged_ref = new_pos
                else:
                    merged_ref = existing_ref

                if merged_ref:
                    ok = client.update_client_order_ref(so_id, merged_ref)
                    if ok:
                        st.caption(t("export.msg.ref_updated", so_name=so_name, ref=merged_ref))
                    else:
                        st.warning(t("export.warn.ref_fail", so_name=so_name))

            # Mark source POs as processed
            if all_success:
                _mark_source_processed(st.session_state.get("source_po_ids", []))
                st.success(t("export.msg.all_done"))

    # ── Reset for next batch ──────────────────────────────────────────────────
    st.divider()
    if st.button(t("export.btn.clear")):
        st.session_state.pop("export_open_orders", None)
        st.session_state.pop("export_latest_so", None)
        st.session_state.pop("export_so_fetched", None)
        st.rerun()
