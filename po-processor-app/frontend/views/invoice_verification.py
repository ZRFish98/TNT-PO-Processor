"""Invoice Verification — Upload signed invoices/tester sheets, validate against Odoo, attach & complete delivery."""

import os
from typing import Any, Dict

import pandas as pd
import streamlit as st

from frontend.i18n import t

# Internal tab keys (language-independent)
_TAB_KEYS = ["upload", "review", "attach"]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _picking_sort_key(picking: Dict) -> int:
    """Sort pickings by delivery step order: Pick=0, Pack=1, Out=2."""
    code = picking.get("picking_type_code", "")
    raw = picking.get("picking_type_id")
    name = raw[1] if isinstance(raw, (list, tuple)) and len(raw) > 1 else ""
    name_lower = name.lower()

    if code == "internal" and "pick" in name_lower:
        return 0
    elif code == "internal" and "pack" in name_lower:
        return 1
    elif code == "outgoing":
        return 2
    return 3


def _picking_step_label(picking: Dict) -> str:
    """Get human-readable step label for a picking."""
    name = ""
    if isinstance(picking.get("picking_type_id"), (list, tuple)):
        name = picking["picking_type_id"][1]
    name_lower = name.lower()

    if "pick" in name_lower:
        return "Pick"
    elif "pack" in name_lower:
        return "Pack"
    elif "out" in name_lower or picking.get("picking_type_code") == "outgoing":
        return "Out"
    return name or picking.get("picking_type_code", "?")


def _state_icon(state: str) -> str:
    """Map picking state to a status icon."""
    return {
        "done": "\u2705",       # green check
        "assigned": "\u23f3",   # hourglass (ready)
        "waiting": "\u23f3",
        "confirmed": "\u23f3",
        "draft": "\u26aa",      # white circle
        "cancel": "\u274c",     # red X
    }.get(state, "\u26aa")


def _state_label(state: str) -> str:
    """Map picking state to a human label."""
    return {
        "done": t("invoice.state.done"),
        "assigned": t("invoice.state.ready"),
        "waiting": t("invoice.state.waiting"),
        "confirmed": t("invoice.state.confirmed"),
        "draft": t("invoice.state.draft"),
        "cancel": t("invoice.state.cancelled"),
    }.get(state, state)


def _order_state_label(state: str) -> str:
    """Map sale.order state to a human label."""
    return {
        "draft": t("invoice.order_state.quotation"),
        "sent": t("invoice.order_state.sent"),
        "sale": t("invoice.order_state.confirmed"),
        "done": t("invoice.order_state.locked"),
        "cancel": t("invoice.order_state.cancelled"),
    }.get(state, state)


def _delivery_status_label(status: str) -> str:
    """Map delivery_status to a human label."""
    return {
        "no": t("invoice.delivery.nothing"),
        "partial": t("invoice.delivery.partial"),
        "full": t("invoice.delivery.full"),
        "pending": t("invoice.delivery.pending"),
    }.get(status or "no", status or "—")


def _doc_type_label(doc_type: str) -> str:
    """Map doc_type to display label."""
    return {
        "invoice": t("invoice.doc_type.invoice"),
        "tnt_invoice": t("invoice.doc_type.tnt_invoice"),
        "tester_sheet": t("invoice.doc_type.tester_sheet"),
    }.get(doc_type, doc_type)


# ── Tab 1: Upload & Process ─────────────────────────────────────────────────


def _render_upload() -> None:
    """Tab 1: Upload & Process scanned invoices/tester sheets."""
    st.caption(t("invoice.upload.caption"))

    uploaded = st.file_uploader(
        t("invoice.upload.label"),
        type=["pdf"],
        accept_multiple_files=True,
        key="invoice_pdf_uploader",
    )

    if uploaded:
        # Process new uploads (avoid re-splitting on rerun)
        current_names = {f.name for f in uploaded}
        prev_names = st.session_state.get("invoice_uploaded_names", set())

        if current_names != prev_names:
            from backend.invoice_extractor import InvoiceExtractor

            all_page_pdfs: list[bytes] = []
            for f in uploaded:
                pdf_bytes = f.read()
                pages = InvoiceExtractor.split_pdf(pdf_bytes)
                pages = [InvoiceExtractor.compress_page_pdf(p) for p in pages]
                all_page_pdfs.extend(pages)

            st.session_state["invoice_page_pdfs"] = all_page_pdfs
            st.session_state["invoice_uploaded_names"] = current_names
            # Clear previous results
            st.session_state["invoice_page_data"] = []
            st.session_state["invoice_orders"] = {}
            st.session_state["invoice_odoo_data"] = {}

    page_pdfs = st.session_state.get("invoice_page_pdfs", [])
    if page_pdfs:
        st.info(t("invoice.upload.page_count", count=len(page_pdfs)))

    # Process button
    if page_pdfs:
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        if not gemini_key:
            st.warning(t("invoice.error_no_gemini_key"))
            return

        if st.button(t("invoice.upload.btn_process"), type="primary", key="btn_process_invoices"):
            from backend.invoice_extractor import InvoiceExtractor

            extractor = InvoiceExtractor(api_key=gemini_key)
            total = len(page_pdfs)
            progress = st.progress(0, text=t("invoice.upload.processing", current=1, total=total))

            page_data: list[dict] = []
            errors: list[dict] = []

            for i, page_pdf in enumerate(page_pdfs):
                progress.progress(
                    max(0.0, min(1.0, i / total)),
                    text=t("invoice.upload.processing", current=i + 1, total=total),
                )
                try:
                    data = extractor.extract_page(page_pdf, page_number=i + 1)
                    page_data.append(data)
                except Exception as e:
                    errors.append({"page": i + 1, "error": str(e)})
                    page_data.append({
                        "page_number": i + 1,
                        "ref_number": None,
                        "doc_type": "unknown",
                        "is_last_page": False,
                        "total_amount": None,
                        "page_content_summary": "extraction failed",
                    })

            progress.progress(1.0)

            # Group pages by order
            orders = InvoiceExtractor.group_pages_by_order(page_data)

            st.session_state["invoice_page_data"] = page_data
            st.session_state["invoice_orders"] = orders
            st.session_state["invoice_odoo_data"] = {}

            progress.empty()
            st.success(t("invoice.upload.done", orders=len(orders), pages=len(page_data), errors=len(errors)))

            if errors:
                with st.expander(t("invoice.upload.errors")):
                    for err in errors:
                        st.error(f"Page {err['page']}: {err['error']}")

            st.rerun()

    # Show existing results
    orders = st.session_state.get("invoice_orders", {})
    if orders:
        st.subheader(t("invoice.upload.summary_title"))

        rows = []
        for ref, order in orders.items():
            rows.append({
                t("invoice.col.ref"): order["ref_number"],
                t("invoice.col.customer"): order.get("customer_name") or "—",
                t("invoice.col.po_ref"): order.get("po_ref") or "—",
                t("invoice.col.doc_type"): _doc_type_label(order["doc_type"]),
                t("invoice.col.pages"): order["page_count"],
                t("invoice.col.total"): f"${order['total_amount']:,.2f}" if order["total_amount"] else "—",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # Next button
        def _go_to_review() -> None:
            st.session_state["invoice_active_tab"] = "review"
            st.session_state["_invoice_tab_radio"] = t("invoice.tab.review")

        st.button(
            t("invoice.upload.btn_next"),
            key="btn_invoice_next_review",
            on_click=_go_to_review,
            type="primary",
        )

    # Clear button
    if st.session_state.get("invoice_page_pdfs"):
        if st.button(t("invoice.upload.btn_clear"), key="btn_clear_invoices"):
            for k in ("invoice_page_pdfs", "invoice_uploaded_names", "invoice_page_data",
                       "invoice_orders", "invoice_odoo_data"):
                st.session_state.pop(k, None)
            st.rerun()


# ── Tab 2: Review & Verify ──────────────────────────────────────────────────


def _render_review(settings: Dict) -> None:
    """Tab 2: Review extracted data and verify against Odoo."""
    orders = st.session_state.get("invoice_orders", {})
    if not orders:
        st.info(t("invoice.review.no_data"))
        return

    client = st.session_state.get("odoo_client")
    if not client or not st.session_state.get("odoo_connected"):
        st.error(t("invoice.review.not_connected"))
        return

    # Fetch Odoo data button
    if st.button(t("invoice.review.btn_fetch"), type="primary", key="btn_fetch_odoo_invoices"):
        with st.spinner(t("invoice.review.fetching")):
            odoo_data = _fetch_odoo_data_for_orders(client, orders)
            st.session_state["invoice_odoo_data"] = odoo_data
        st.rerun()

    odoo_data = st.session_state.get("invoice_odoo_data", {})

    # Display per-order cards
    for ref, order in orders.items():
        odoo = odoo_data.get(ref, {})
        _render_order_review_card(ref, order, odoo)

    # Next button
    if odoo_data:
        def _go_to_attach() -> None:
            st.session_state["invoice_active_tab"] = "attach"
            st.session_state["_invoice_tab_radio"] = t("invoice.tab.attach")

        st.button(
            t("invoice.review.btn_next"),
            key="btn_invoice_next_attach",
            on_click=_go_to_attach,
            type="primary",
        )


def _fetch_odoo_data_for_orders(
    client: Any, orders: Dict[str, dict],
) -> Dict[str, dict]:
    """Fetch Odoo sales order + delivery data for each detected order."""
    result: Dict[str, dict] = {}

    for ref, order in orders.items():
        ref_number = order["ref_number"]
        if ref_number.startswith("Unknown_"):
            result[ref] = {"error": t("invoice.review.err_no_ref")}
            continue

        so_data = client.get_order_with_delivery_details(ref_number)
        if not so_data:
            result[ref] = {"error": t("invoice.review.err_so_not_found", ref=ref_number)}
            continue

        # Determine warehouse type from warehouse_id
        wh_id = so_data.get("warehouse_id")
        wh_name = ""
        wh_type = "CE"  # default 3-step
        if isinstance(wh_id, (list, tuple)):
            wh_name = wh_id[1] if len(wh_id) > 1 else ""
            # Check if warehouse name contains "West" or "CW"
            wh_lower = wh_name.lower()
            if "west" in wh_lower or "cw" in wh_lower:
                wh_type = "CW"

        # Fetch delivery pickings
        picking_ids = so_data.get("picking_ids", [])
        pickings = client.get_delivery_pickings(picking_ids) if picking_ids else []
        # Sort by delivery step order
        pickings.sort(key=_picking_sort_key)

        # Customer name
        partner = so_data.get("partner_id")
        partner_name = partner[1] if isinstance(partner, (list, tuple)) else str(partner)

        result[ref] = {
            "so_id": so_data["id"],
            "so_name": so_data["name"],
            "amount_total": so_data.get("amount_total", 0),
            "state": so_data.get("state", ""),
            "delivery_status": so_data.get("delivery_status", ""),
            "partner_name": partner_name,
            "warehouse_name": wh_name,
            "warehouse_type": wh_type,
            "delivery_date": so_data.get("x_studio_delivery_date") or "—",
            "date_order": so_data.get("date_order") or "—",
            "pickings": pickings,
        }

    return result


def _render_order_review_card(ref: str, order: dict, odoo: dict) -> None:
    """Render a single order's review card with amount comparison."""
    error = odoo.get("error")
    icon = "\u274c" if error else "\u2705"

    with st.expander(
        f"{icon} {ref} — {_doc_type_label(order['doc_type'])} ({order['page_count']} pg)",
        expanded=not error,
    ):
        if error:
            st.error(error)
            return

        col1, col2, col3 = st.columns(3)

        with col1:
            st.markdown(f"**{t('invoice.review.customer')}:** {odoo.get('partner_name', '—')}")
            st.markdown(f"**{t('invoice.review.warehouse')}:** {odoo.get('warehouse_name', '—')} ({odoo.get('warehouse_type', '—')})")
            st.markdown(f"**{t('invoice.review.order_state')}:** {_order_state_label(odoo.get('state', ''))}")

        with col2:
            st.markdown(f"**{t('invoice.review.delivery_status')}:** {_delivery_status_label(odoo.get('delivery_status', ''))}")
            st.markdown(f"**{t('invoice.review.delivery_date')}:** {odoo.get('delivery_date', '—')}")
            st.markdown(f"**{t('invoice.review.order_date')}:** {odoo.get('date_order', '—')}")

        with col3:
            scanned_total = order.get("total_amount")
            odoo_total = odoo.get("amount_total", 0)

            if scanned_total is not None:
                match = abs(scanned_total - odoo_total) < 0.01
                color = "green" if match else "red"
                match_icon = "\u2705" if match else "\u274c"
                match_text = t("invoice.review.match_yes") if match else t("invoice.review.match_no")
                st.markdown(f"**{t('invoice.review.scanned_total')}:** ${scanned_total:,.2f}")
                st.markdown(f"**{t('invoice.review.odoo_total')}:** ${odoo_total:,.2f}")
                st.markdown(f"**{t('invoice.review.match')}:** :{color}[{match_icon} {match_text}]")
            else:
                st.markdown(f"**{t('invoice.review.scanned_total')}:** —")
                st.markdown(f"**{t('invoice.review.odoo_total')}:** ${odoo_total:,.2f}")
                st.caption(t("invoice.review.no_total_tester"))

        # Delivery steps visual
        pickings = odoo.get("pickings", [])
        if pickings:
            st.divider()
            st.markdown(f"**{t('invoice.review.delivery_steps')}:**")
            step_cols = st.columns(len(pickings))
            for i, picking in enumerate(pickings):
                with step_cols[i]:
                    label = _picking_step_label(picking)
                    icon = _state_icon(picking.get("state", ""))
                    state = _state_label(picking.get("state", ""))
                    st.markdown(f"{icon} **{label}**")
                    st.caption(f"{picking.get('name', '')} — {state}")


# ── Tab 3: Attach & Complete ────────────────────────────────────────────────


def _render_attach() -> None:
    """Tab 3: Attach PDFs to Odoo and validate delivery steps."""
    orders = st.session_state.get("invoice_orders", {})
    odoo_data = st.session_state.get("invoice_odoo_data", {})

    if not orders:
        st.info(t("invoice.attach.no_data"))
        return

    if not odoo_data:
        st.warning(t("invoice.attach.not_validated"))
        return

    client = st.session_state.get("odoo_client")
    if not client or not st.session_state.get("odoo_connected"):
        st.error(t("invoice.attach.not_connected"))
        return

    # Summary table with checkboxes
    st.subheader(t("invoice.attach.summary_title"))

    valid_refs: list[str] = []
    for ref, order in orders.items():
        odoo = odoo_data.get(ref, {})
        if odoo.get("error"):
            continue

        scanned_total = order.get("total_amount")
        odoo_total = odoo.get("amount_total", 0)
        amount_match = abs(scanned_total - odoo_total) < 0.01 if scanned_total is not None else True

        pickings = odoo.get("pickings", [])
        all_done = all(p.get("state") == "done" for p in pickings) if pickings else False

        col_check, col_ref, col_cust, col_wh, col_total, col_status, col_delivery = st.columns([0.3, 1.2, 1.5, 0.8, 1.0, 1.0, 1.5])

        with col_check:
            checked = st.checkbox("", value=True, key=f"attach_check_{ref}", label_visibility="collapsed")
        with col_ref:
            st.markdown(f"**{ref}**")
        with col_cust:
            st.caption(odoo.get("partner_name", "—"))
        with col_wh:
            st.caption(f"{odoo.get('warehouse_type', '—')}")
        with col_total:
            if scanned_total is not None:
                color = "green" if amount_match else "red"
                st.markdown(f":{color}[${scanned_total:,.2f}]")
            else:
                st.caption("—")
        with col_status:
            st.caption(_order_state_label(odoo.get("state", "")))
        with col_delivery:
            # Delivery steps inline
            parts = []
            for p in pickings:
                icon = _state_icon(p.get("state", ""))
                label = _picking_step_label(p)
                parts.append(f"{icon}{label}")
            st.caption(" → ".join(parts) if parts else "—")

        if checked:
            valid_refs.append(ref)

    st.divider()

    # Per-order detail expanders
    for ref in valid_refs:
        order = orders[ref]
        odoo = odoo_data.get(ref, {})
        if odoo.get("error"):
            continue

        with st.expander(f"{ref} — {odoo.get('partner_name', '')}", expanded=False):
            _render_order_attach_detail(ref, order, odoo)

    # Batch action buttons
    st.divider()
    btn_col1, btn_col2 = st.columns(2)

    with btn_col1:
        if st.button(
            t("invoice.attach.btn_attach", count=len(valid_refs)),
            type="primary",
            disabled=len(valid_refs) == 0,
            key="btn_attach_invoices",
        ):
            _attach_pdfs_to_odoo(client, valid_refs, orders, odoo_data)

    with btn_col2:
        if st.button(
            t("invoice.attach.btn_validate", count=len(valid_refs)),
            type="primary",
            disabled=len(valid_refs) == 0,
            key="btn_validate_deliveries",
        ):
            _validate_deliveries(client, valid_refs, odoo_data)


def _render_order_attach_detail(ref: str, order: dict, odoo: dict) -> None:
    """Render detailed view for a single order in the Attach tab."""
    col1, col2 = st.columns(2)

    with col1:
        st.markdown(f"**{t('invoice.review.order_state')}:** {_order_state_label(odoo.get('state', ''))}")
        st.markdown(f"**{t('invoice.review.delivery_status')}:** {_delivery_status_label(odoo.get('delivery_status', ''))}")
        st.markdown(f"**{t('invoice.review.delivery_date')}:** {odoo.get('delivery_date', '—')}")
        st.markdown(f"**{t('invoice.review.order_date')}:** {odoo.get('date_order', '—')}")

    with col2:
        scanned_total = order.get("total_amount")
        odoo_total = odoo.get("amount_total", 0)
        st.markdown(f"**{t('invoice.review.scanned_total')}:** {'${:,.2f}'.format(scanned_total) if scanned_total else '—'}")
        st.markdown(f"**{t('invoice.review.odoo_total')}:** ${odoo_total:,.2f}")
        st.markdown(f"**{t('invoice.col.doc_type')}:** {_doc_type_label(order['doc_type'])}")
        st.markdown(f"**{t('invoice.review.warehouse')}:** {odoo.get('warehouse_name', '—')}")

    # Delivery pickings table
    pickings = odoo.get("pickings", [])
    if pickings:
        st.markdown(f"**{t('invoice.review.delivery_steps')}:**")
        for picking in pickings:
            icon = _state_icon(picking.get("state", ""))
            label = _picking_step_label(picking)
            state = _state_label(picking.get("state", ""))
            date_done = picking.get("date_done") or "—"
            st.markdown(f"  {icon} **{label}** — {picking.get('name', '')} — {state} — {date_done}")


def _attach_pdfs_to_odoo(
    client: Any,
    refs: list[str],
    orders: Dict[str, dict],
    odoo_data: Dict[str, dict],
) -> None:
    """Attach merged PDFs to Odoo sales orders."""
    from backend.invoice_extractor import InvoiceExtractor

    page_pdfs = st.session_state.get("invoice_page_pdfs", [])
    ok = 0
    fail = 0

    for ref in refs:
        order = orders[ref]
        odoo = odoo_data.get(ref, {})
        so_id = odoo.get("so_id")

        if not so_id:
            st.warning(t("invoice.attach.err_no_so", ref=ref))
            fail += 1
            continue

        with st.spinner(t("invoice.attach.attaching_one", ref=ref)):
            # Merge this order's pages into a single PDF
            indices = order.get("page_indices", [])
            order_pages = [page_pdfs[i] for i in indices if i < len(page_pdfs)]

            if not order_pages:
                st.warning(t("invoice.attach.err_no_pages", ref=ref))
                fail += 1
                continue

            merged_pdf = InvoiceExtractor.merge_pages(order_pages)

            # Determine filename based on doc type
            doc_type = order.get("doc_type", "invoice")
            prefix = {
                "invoice": "Invoice",
                "tnt_invoice": "T&T_Invoice",
                "tester_sheet": "Tester_Sheet",
            }.get(doc_type, "Document")
            filename = f"{prefix}_{ref}.pdf"

            # Attach to SO
            att_id = client.attach_pdf_to_record(
                res_model="sale.order",
                res_id=so_id,
                filename=filename,
                pdf_bytes=merged_pdf,
            )

            if att_id:
                # Post chatter message
                client.post_chatter_message(
                    res_model="sale.order",
                    res_id=so_id,
                    body=f"Signed {_doc_type_label(doc_type)} attached — {ref}",
                    attachment_ids=[att_id],
                )
                st.success(t("invoice.attach.attached_ok", ref=ref))
                ok += 1
            else:
                st.error(t("invoice.attach.attached_fail", ref=ref))
                fail += 1

    st.info(t("invoice.attach.attach_done", ok=ok, fail=fail))


def _validate_deliveries(
    client: Any,
    refs: list[str],
    odoo_data: Dict[str, dict],
) -> None:
    """Validate delivery pickings for selected orders."""
    ok = 0
    fail = 0

    for ref in refs:
        odoo = odoo_data.get(ref, {})
        pickings = odoo.get("pickings", [])

        if not pickings:
            st.caption(t("invoice.attach.no_pickings", ref=ref))
            continue

        with st.spinner(t("invoice.attach.validating_one", ref=ref)):
            for picking in pickings:
                picking_id = picking.get("id")
                if not picking_id or picking.get("state") in ("done", "cancel"):
                    continue

                success, detail = client.validate_delivery_picking(picking_id)
                label = _picking_step_label(picking)

                if success:
                    st.caption(f"\u2705 {ref} — {label}: {detail}")
                    ok += 1
                else:
                    st.warning(f"\u274c {ref} — {label}: {detail}")
                    fail += 1

    st.info(t("invoice.attach.validate_done", ok=ok, fail=fail))


# ── Main render ──────────────────────────────────────────────────────────────


def render(settings: Dict) -> None:
    """Main render function for Invoice Verification."""
    st.title(t("invoice.title"))

    if "invoice_active_tab" not in st.session_state:
        st.session_state["invoice_active_tab"] = _TAB_KEYS[0]

    _tab_labels = [
        t("invoice.tab.upload"),
        t("invoice.tab.review"),
        t("invoice.tab.attach"),
    ]
    _key_to_label = dict(zip(_TAB_KEYS, _tab_labels))
    _label_to_key = dict(zip(_tab_labels, _TAB_KEYS))

    if "_invoice_tab_radio" not in st.session_state:
        current_key = st.session_state["invoice_active_tab"]
        st.session_state["_invoice_tab_radio"] = _key_to_label.get(current_key, _tab_labels[0])

    active_label = st.radio(
        t("invoice.select_tab"),
        _tab_labels,
        horizontal=True,
        key="_invoice_tab_radio",
        label_visibility="collapsed",
    )
    st.session_state["invoice_active_tab"] = _label_to_key.get(active_label, _TAB_KEYS[0])

    st.divider()

    active_key = st.session_state["invoice_active_tab"]
    if active_key == "upload":
        _render_upload()
    elif active_key == "review":
        _render_review(settings)
    elif active_key == "attach":
        _render_attach()
