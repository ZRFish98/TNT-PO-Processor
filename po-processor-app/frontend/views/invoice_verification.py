"""Invoice Verification — Upload signed invoices, validate against Odoo, attach as proof of delivery."""

import base64
import io
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from pypdf import PdfReader, PdfWriter

from frontend.i18n import t

# Internal tab keys (language-independent)
_TAB_KEYS = ["upload", "review", "attach"]


def _compress_pdf(pdf_bytes: bytes, quality: int = 2) -> bytes:
    """Compress PDF using pypdf (quality: 0=max, 4=min)."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    for page in reader.pages:
        page.compress_content_streams(level=quality)
        writer.add_page(page)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _split_pdf_by_invoice(pdf_bytes: bytes) -> List[Tuple[bytes, Dict[str, Any]]]:
    """Split a multi-invoice PDF into individual invoices using Gemini OCR.

    Returns list of (pdf_bytes, metadata) where metadata contains:
        - invoice_number: str
        - total_amount: float or None
        - page_count: int
    """
    try:
        import google.genai as genai
    except ImportError:
        st.error("google-genai package not installed")
        return []

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        st.error(t("invoice.error_no_gemini_key"))
        return []

    # Convert PDF to images for OCR
    try:
        from pdf2image import convert_from_bytes
    except ImportError:
        st.error("pdf2image package not installed")
        return []

    images = convert_from_bytes(pdf_bytes, dpi=150)

    # OCR each page to detect invoice numbers
    client = genai.Client(api_key=api_key)
    page_data = []

    for i, img in enumerate(images):
        img_bytes = io.BytesIO()
        img.save(img_bytes, format="JPEG", quality=85)
        img_bytes.seek(0)

        prompt = """Extract the following from this invoice page:
1. Invoice number (look for "Invoice #", "Invoice No.", "SO#", etc.)
2. Total amount (look for "Total", "Amount Due", "Grand Total", etc.)

Return ONLY a JSON object with this exact format:
{"invoice_number": "SO12345", "total_amount": 1234.56}

If you cannot find the invoice number, return: {"invoice_number": null, "total_amount": null}
If you cannot find the total, set total_amount to null.
"""

        try:
            response = client.models.generate_content(
                model="gemini-2.0-flash-exp",
                contents=[
                    prompt,
                    {"mime_type": "image/jpeg", "data": img_bytes.getvalue()}
                ]
            )

            # Parse JSON response
            import json
            text = response.text.strip()
            # Remove markdown code blocks if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            data = json.loads(text)
            page_data.append({
                "page_num": i,
                "invoice_number": data.get("invoice_number"),
                "total_amount": data.get("total_amount"),
            })
        except Exception as e:
            st.warning(f"OCR failed for page {i+1}: {e}")
            page_data.append({
                "page_num": i,
                "invoice_number": None,
                "total_amount": None,
            })

    # Group pages by invoice number
    invoice_groups = []
    current_invoice = None
    current_pages = []

    for pd_item in page_data:
        inv_num = pd_item["invoice_number"]

        if inv_num and inv_num != current_invoice:
            # Start of new invoice
            if current_pages:
                invoice_groups.append({
                    "invoice_number": current_invoice,
                    "pages": current_pages,
                })
            current_invoice = inv_num
            current_pages = [pd_item]
        elif inv_num == current_invoice or (inv_num is None and current_invoice):
            # Continuation of current invoice
            current_pages.append(pd_item)
        else:
            # No invoice number detected and no current invoice
            if not invoice_groups:
                # First page, start new group
                current_invoice = None
                current_pages = [pd_item]

    # Add last group
    if current_pages:
        invoice_groups.append({
            "invoice_number": current_invoice,
            "pages": current_pages,
        })

    # Split PDF into individual invoices
    reader = PdfReader(io.BytesIO(pdf_bytes))
    result = []

    for group in invoice_groups:
        writer = PdfWriter()
        total_amount = None

        for page_info in group["pages"]:
            page_num = page_info["page_num"]
            writer.add_page(reader.pages[page_num])
            # Use the first non-null total amount found
            if total_amount is None and page_info["total_amount"]:
                total_amount = page_info["total_amount"]

        output = io.BytesIO()
        writer.write(output)
        pdf_data = output.getvalue()

        # Compress
        pdf_data = _compress_pdf(pdf_data, quality=2)

        metadata = {
            "invoice_number": group["invoice_number"] or f"Unknown_{len(result)+1}",
            "total_amount": total_amount,
            "page_count": len(group["pages"]),
        }

        result.append((pdf_data, metadata))

    return result


def render(settings: Dict) -> None:
    """Main render function for Invoice Verification."""
    st.title(t("invoice.title"))

    # Initialize session state
    if "invoice_uploaded_pdf" not in st.session_state:
        st.session_state["invoice_uploaded_pdf"] = None
    if "invoice_split_pdfs" not in st.session_state:
        st.session_state["invoice_split_pdfs"] = []  # list of (bytes, metadata)
    if "invoice_summaries" not in st.session_state:
        st.session_state["invoice_summaries"] = pd.DataFrame()  # invoice-level summary
    if "invoice_validation_errors" not in st.session_state:
        st.session_state["invoice_validation_errors"] = []
    if "invoice_active_tab" not in st.session_state:
        st.session_state["invoice_active_tab"] = "upload"

    # Tab selection with radio (language-aware)
    tabs_display = {k: t(f"invoice.tab.{k}") for k in _TAB_KEYS}

    if "_invoice_tab_radio" not in st.session_state:
        st.session_state["_invoice_tab_radio"] = tabs_display[st.session_state["invoice_active_tab"]]

    selected_display = st.radio(
        t("invoice.select_tab"),
        options=list(tabs_display.values()),
        key="_invoice_tab_radio",
        horizontal=True,
        label_visibility="collapsed",
    )

    # Map back to internal key
    display_to_key = {v: k for k, v in tabs_display.items()}
    active_tab = display_to_key[selected_display]
    st.session_state["invoice_active_tab"] = active_tab

    # Render active tab
    if active_tab == "upload":
        _render_upload_tab()
    elif active_tab == "review":
        _render_review_tab(settings)
    elif active_tab == "attach":
        _render_attach_tab()


def _render_upload_tab() -> None:
    """Tab 1: Upload and split invoices."""
    st.header(t("invoice.upload.header"))
    st.markdown(t("invoice.upload.instructions"))

    uploaded = st.file_uploader(
        t("invoice.upload.label"),
        type=["pdf"],
        key="invoice_uploader",
    )

    if uploaded:
        pdf_bytes = uploaded.read()
        st.session_state["invoice_uploaded_pdf"] = pdf_bytes

        st.success(t("invoice.upload.success", name=uploaded.name, size=len(pdf_bytes) // 1024))

        if st.button(t("invoice.upload.btn_split"), type="primary", key="btn_split_invoices"):
            with st.spinner(t("invoice.upload.splitting")):
                split_result = _split_pdf_by_invoice(pdf_bytes)

                if split_result:
                    st.session_state["invoice_split_pdfs"] = split_result
                    st.success(t("invoice.upload.split_success", count=len(split_result)))

                    # Create summary DataFrame
                    summaries = []
                    for i, (pdf_data, meta) in enumerate(split_result):
                        summaries.append({
                            "index": i,
                            "invoice_number": meta["invoice_number"],
                            "total_amount": meta["total_amount"],
                            "page_count": meta["page_count"],
                            "pdf_size_kb": len(pdf_data) // 1024,
                        })

                    st.session_state["invoice_summaries"] = pd.DataFrame(summaries)
                    st.rerun()
                else:
                    st.error(t("invoice.upload.split_fail"))

    # Show split invoices
    if st.session_state["invoice_split_pdfs"]:
        st.subheader(t("invoice.upload.split_preview"))

        df = st.session_state["invoice_summaries"]
        st.dataframe(df, use_container_width=True)

        # Preview each invoice
        for i, (pdf_data, meta) in enumerate(st.session_state["invoice_split_pdfs"]):
            with st.expander(f"📄 {meta['invoice_number']} ({meta['page_count']} pages)"):
                st.download_button(
                    t("invoice.upload.btn_download"),
                    data=pdf_data,
                    file_name=f"{meta['invoice_number']}.pdf",
                    mime="application/pdf",
                    key=f"dl_invoice_{i}",
                )

                # Show PDF preview
                pdf_b64 = base64.b64encode(pdf_data).decode()
                st.markdown(
                    f'<iframe src="data:application/pdf;base64,{pdf_b64}" '
                    f'width="100%" height="400px"></iframe>',
                    unsafe_allow_html=True,
                )

        # Next button
        def _go_to_review() -> None:
            st.session_state["invoice_active_tab"] = "review"
            st.session_state["_invoice_tab_radio"] = t("invoice.tab.review")

        st.button(
            t("invoice.upload.btn_next"),
            key="btn_next_review",
            on_click=_go_to_review,
            type="primary",
        )


def _render_review_tab(settings: Dict) -> None:
    """Tab 2: Review and validate against Odoo."""
    st.header(t("invoice.review.header"))

    if not st.session_state["invoice_split_pdfs"]:
        st.warning(t("invoice.review.no_data"))
        return

    client = st.session_state.get("odoo_client")
    if not client or not st.session_state.get("odoo_connected"):
        st.error(t("invoice.review.not_connected"))
        return

    st.markdown(t("invoice.review.instructions"))

    # Validate button
    if st.button(t("invoice.review.btn_validate"), type="primary", key="btn_validate_all"):
        with st.spinner(t("invoice.review.validating")):
            _validate_invoices_against_odoo(client)
            st.rerun()

    # Show validation results
    if not st.session_state["invoice_summaries"].empty:
        df = st.session_state["invoice_summaries"]

        # Display with color coding
        st.dataframe(
            df.style.apply(lambda row: _style_validation_row(row), axis=1),
            use_container_width=True,
        )

        # Show errors
        if st.session_state["invoice_validation_errors"]:
            st.error(t("invoice.review.errors_found"))
            for err in st.session_state["invoice_validation_errors"]:
                st.warning(f"⚠️ {err}")

        # Next button
        def _go_to_attach() -> None:
            st.session_state["invoice_active_tab"] = "attach"
            st.session_state["_invoice_tab_radio"] = t("invoice.tab.attach")

        st.button(
            t("invoice.review.btn_next"),
            key="btn_next_attach",
            on_click=_go_to_attach,
            type="primary",
        )


def _validate_invoices_against_odoo(client) -> None:
    """Validate each invoice against Odoo sales orders."""
    summaries = st.session_state["invoice_summaries"].to_dict("records")
    split_pdfs = st.session_state["invoice_split_pdfs"]
    errors = []

    for i, summary in enumerate(summaries):
        inv_num = summary["invoice_number"]
        extracted_amount = summary["total_amount"]

        # Search for SO in Odoo by name or client_order_ref
        try:
            # Try searching by name first
            so_ids = client.models.execute_kw(
                client.db, client.uid, client.api_key,
                'sale.order', 'search',
                [[['name', '=', inv_num]]],
                {'limit': 1}
            )

            if not so_ids:
                # Try client_order_ref
                so_ids = client.models.execute_kw(
                    client.db, client.uid, client.api_key,
                    'sale.order', 'search',
                    [[['client_order_ref', 'ilike', inv_num]]],
                    {'limit': 1}
                )

            if not so_ids:
                summaries[i]["odoo_so_id"] = None
                summaries[i]["odoo_amount"] = None
                summaries[i]["amount_match"] = False
                summaries[i]["delivery_status"] = "Not Found"
                summaries[i]["validation_status"] = "❌ SO Not Found"
                errors.append(f"{inv_num}: Sales Order not found in Odoo")
                continue

            so_id = so_ids[0]

            # Get SO details
            so_data = client.models.execute_kw(
                client.db, client.uid, client.api_key,
                'sale.order', 'read',
                [so_id],
                {'fields': ['name', 'amount_total', 'picking_ids', 'state']}
            )[0]

            odoo_amount = so_data['amount_total']
            amount_match = abs(extracted_amount - odoo_amount) < 0.01 if extracted_amount else False

            # Check delivery status
            picking_ids = so_data.get('picking_ids', [])
            delivery_status = "No Delivery"

            if picking_ids:
                pickings = client.models.execute_kw(
                    client.db, client.uid, client.api_key,
                    'stock.picking', 'read',
                    [picking_ids],
                    {'fields': ['state']}
                )

                states = [p['state'] for p in pickings]
                if all(s == 'done' for s in states):
                    delivery_status = "✅ Delivered"
                elif any(s == 'done' for s in states):
                    delivery_status = "⚠️ Partial"
                else:
                    delivery_status = "⏳ Pending"

            # Determine validation status
            if not amount_match and extracted_amount:
                validation_status = f"⚠️ Amount Mismatch (Odoo: ${odoo_amount:.2f})"
                errors.append(f"{inv_num}: Amount mismatch - Scanned: ${extracted_amount:.2f}, Odoo: ${odoo_amount:.2f}")
            elif delivery_status != "✅ Delivered":
                validation_status = f"⚠️ {delivery_status}"
            else:
                validation_status = "✅ Valid"

            summaries[i]["odoo_so_id"] = so_id
            summaries[i]["odoo_amount"] = odoo_amount
            summaries[i]["amount_match"] = amount_match
            summaries[i]["delivery_status"] = delivery_status
            summaries[i]["validation_status"] = validation_status

        except Exception as e:
            summaries[i]["validation_status"] = f"❌ Error: {e}"
            errors.append(f"{inv_num}: Validation error - {e}")

    st.session_state["invoice_summaries"] = pd.DataFrame(summaries)
    st.session_state["invoice_validation_errors"] = errors


def _style_validation_row(row) -> List[str]:
    """Style DataFrame row based on validation status."""
    if "validation_status" not in row:
        return [""] * len(row)

    status = row["validation_status"]
    if "✅" in status:
        color = "background-color: #d4edda"
    elif "⚠️" in status:
        color = "background-color: #fff3cd"
    elif "❌" in status:
        color = "background-color: #f8d7da"
    else:
        color = ""

    return [color] * len(row)


def _render_attach_tab() -> None:
    """Tab 3: Attach invoices to Odoo SOs."""
    st.header(t("invoice.attach.header"))

    if not st.session_state["invoice_split_pdfs"]:
        st.warning(t("invoice.attach.no_data"))
        return

    client = st.session_state.get("odoo_client")
    if not client or not st.session_state.get("odoo_connected"):
        st.error(t("invoice.attach.not_connected"))
        return

    df = st.session_state["invoice_summaries"]

    if df.empty or "odoo_so_id" not in df.columns:
        st.warning(t("invoice.attach.not_validated"))
        return

    st.markdown(t("invoice.attach.instructions"))

    # Show summary
    valid_count = len(df[df["validation_status"].str.contains("✅", na=False)])
    st.metric(t("invoice.attach.valid_count"), f"{valid_count} / {len(df)}")

    # Attach button
    if st.button(t("invoice.attach.btn_attach"), type="primary", key="btn_attach_all"):
        with st.spinner(t("invoice.attach.attaching")):
            _attach_invoices_to_odoo(client)
            st.success(t("invoice.attach.success"))

    # Show attachment status
    if "attachment_status" in df.columns:
        st.dataframe(
            df[["invoice_number", "odoo_so_id", "validation_status", "attachment_status"]],
            use_container_width=True,
        )


def _attach_invoices_to_odoo(client) -> None:
    """Attach invoice PDFs to corresponding SOs in Odoo."""
    summaries = st.session_state["invoice_summaries"].to_dict("records")
    split_pdfs = st.session_state["invoice_split_pdfs"]

    for i, summary in enumerate(summaries):
        so_id = summary.get("odoo_so_id")
        if not so_id:
            summaries[i]["attachment_status"] = "❌ No SO"
            continue

        pdf_data, meta = split_pdfs[i]
        inv_num = meta["invoice_number"]

        try:
            # Create attachment
            attachment_data = {
                'name': f"Signed_Invoice_{inv_num}.pdf",
                'datas': base64.b64encode(pdf_data).decode(),
                'res_model': 'sale.order',
                'res_id': so_id,
                'type': 'binary',
            }

            client.models.execute_kw(
                client.db, client.uid, client.api_key,
                'ir.attachment', 'create',
                [attachment_data]
            )

            summaries[i]["attachment_status"] = "✅ Attached"

        except Exception as e:
            summaries[i]["attachment_status"] = f"❌ Error: {e}"

    st.session_state["invoice_summaries"] = pd.DataFrame(summaries)
