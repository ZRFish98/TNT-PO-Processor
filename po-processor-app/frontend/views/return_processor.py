"""Return Processor — Upload, Review & Verify, Export & Import to Odoo."""

import io
import os
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from frontend.i18n import t

# Internal tab keys (language-independent)
_TAB_KEYS = ["upload", "review", "export"]


# ── Store name resolution ─────────────────────────────────────────────────────

import re

# Regex to strip the "T&T Supermarket Inc." prefix (any casing) and store number suffix
_PREFIX_RE = re.compile(r"^t\s*&\s*t\s+supermarket\s+inc\.?,?\s*", re.IGNORECASE)
_SUFFIX_RE = re.compile(r"\s*-\s*\d{2,3}\s*$")


def _normalize_store_keyword(name: str) -> str:
    """Extract the distinctive keyword from a store name for fuzzy matching.

    Examples:
        "T&T SUPERMARKET INC. COLLEGE STORE"  -> "college store"
        "T&T Supermarket Inc., College Store - 032" -> "college store"
    """
    s = _PREFIX_RE.sub("", name.strip())
    s = _SUFFIX_RE.sub("", s)
    return s.strip().lower()


def _resolve_store_name(extracted_name: str, settings: Dict) -> str:
    """Map a Gemini-extracted store name to the Odoo display_name from settings.

    Gemini extracts names like "T&T SUPERMARKET INC. COLLEGE STORE".
    Odoo expects "T&T Supermarket Inc., College Store - 032".

    Falls back to the original extracted_name if no match is found.
    """
    if not extracted_name:
        return extracted_name

    target = _normalize_store_keyword(extracted_name)
    if not target:
        return extracted_name

    store_names: Dict = settings.get("tt_store_names", {})
    for _store_id, odoo_name in store_names.items():
        if _normalize_store_keyword(odoo_name) == target:
            return odoo_name

    # Fallback: partial match (extracted keyword is contained in Odoo name)
    for _store_id, odoo_name in store_names.items():
        odoo_kw = _normalize_store_keyword(odoo_name)
        if target in odoo_kw or odoo_kw in target:
            return odoo_name

    return extracted_name


# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_summaries(extracted: List[dict]) -> pd.DataFrame:
    """Build a summary DataFrame from extracted page data."""
    rows = []
    for page in extracted:
        items = page.get("line_items", [])
        total = sum(it.get("amount", 0) or 0 for it in items)
        matched_count = sum(1 for it in items if it.get("matched"))
        total_items = len(items)
        if total_items == 0:
            status = t("returns.review.status_unmatched")
        elif matched_count == total_items:
            status = t("returns.review.status_matched")
        elif matched_count > 0:
            status = t("returns.review.status_partial")
        else:
            status = t("returns.review.status_unmatched")

        rows.append({
            t("returns.review.col.page"): page.get("page_number", ""),
            t("returns.review.col.store"): page.get("store_name", ""),
            t("returns.review.col.doc_no"): page.get("doc_no", ""),
            t("returns.review.col.print_date"): page.get("print_date", ""),
            t("returns.review.col.items"): total_items,
            t("returns.review.col.amount"): round(total, 2),
            t("returns.review.col.status"): status,
        })
    return pd.DataFrame(rows)


def _build_details(extracted: List[dict]) -> pd.DataFrame:
    """Build a detail DataFrame from extracted page data with editable actual_qty."""
    from backend.return_extractor import _tnt_upc_to_ean13

    rows = []
    for page in extracted:
        for item in page.get("line_items", []):
            qty = item.get("quantity", 0) or 0
            upc = item.get("upc", "")
            # Prefer Odoo barcode (per-variant) after matching; fall back to UPC→EAN-13
            odoo_bc = item.get("odoo_barcode", "")
            ean13 = odoo_bc or (_tnt_upc_to_ean13(upc) if upc else "")
            rows.append({
                "_page": page.get("page_number", ""),
                "_doc_no": page.get("doc_no", ""),
                t("returns.review.col.page"): page.get("page_number", ""),
                t("returns.review.col.doc_no"): page.get("doc_no", ""),
                t("returns.review.col.item_id"): item.get("item_id", ""),
                t("returns.review.col.upc"): upc,
                t("returns.review.col.barcode"): ean13 or "",
                t("returns.review.col.desc_en"): item.get("description_en", ""),
                t("returns.review.col.desc_cn"): item.get("description_cn", ""),
                t("returns.review.col.size_pack"): item.get("size_pack", ""),
                t("returns.review.col.class"): item.get("item_class", ""),
                t("returns.review.col.qty_pdf"): qty,
                t("returns.review.col.actual_qty"): item.get("actual_received", qty),
                t("returns.review.col.cost"): item.get("cost", 0) or 0,
                t("returns.review.col.amount_line"): item.get("amount", 0) or 0,
                t("returns.review.col.reason"): item.get("reason", ""),
                t("returns.review.col.matched"): item.get("matched", False),
                t("returns.review.col.odoo_product"): item.get("odoo_product_name", ""),
                "_odoo_product_id": item.get("odoo_product_id"),
                "_odoo_barcode": item.get("odoo_barcode", ""),
            })
    return pd.DataFrame(rows)


def _details_to_extracted(details_df: pd.DataFrame, extracted: List[dict]) -> List[dict]:
    """Sync editable detail rows back into the extracted data structure."""
    actual_col = t("returns.review.col.actual_qty")
    item_id_col = t("returns.review.col.item_id")
    for page in extracted:
        pn = page.get("page_number")
        page_rows = details_df[details_df["_page"] == pn]
        for idx, (_, row) in enumerate(page_rows.iterrows()):
            if idx < len(page.get("line_items", [])):
                page["line_items"][idx]["actual_received"] = row.get(actual_col, 0)
                page["line_items"][idx]["item_id"] = row.get(item_id_col, "")
    return extracted


def _generate_excel(extracted: List[dict]) -> bytes:
    """Generate a two-sheet Excel workbook: Credit Notes + Credit Note Lines."""
    summary_rows = []
    detail_rows = []

    for page in extracted:
        items = page.get("line_items", [])
        total = sum(it.get("amount", 0) or 0 for it in items)
        summary_rows.append({
            "Doc No.": page.get("doc_no", ""),
            "Store": page.get("store_name", ""),
            "Print Date": page.get("print_date", ""),
            "Items": len(items),
            "Total Amount": round(total, 2),
        })
        for item in items:
            detail_rows.append({
                "Doc No.": page.get("doc_no", ""),
                "Store": page.get("store_name", ""),
                "Item ID": item.get("item_id", ""),
                "UPC": item.get("upc", ""),
                "Description": item.get("description_en", ""),
                "Chinese Desc.": item.get("description_cn", ""),
                "Size/Pack": item.get("size_pack", ""),
                "Class": item.get("item_class", ""),
                "Qty (PDF)": item.get("quantity", 0),
                "Actual Received": item.get("actual_received", item.get("quantity", 0)),
                "Cost": item.get("cost", 0),
                "Amount": item.get("amount", 0),
                "Reason": item.get("reason", ""),
                "Odoo Product": item.get("odoo_product_name", ""),
                "Odoo Product ID": item.get("odoo_product_id", ""),
                "Matched": item.get("matched", False),
            })

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name="Credit Notes", index=False)
        pd.DataFrame(detail_rows).to_excel(writer, sheet_name="Credit Note Lines", index=False)
    return buf.getvalue()


# ── Tab renderers ─────────────────────────────────────────────────────────────


def _render_upload() -> None:
    """Tab 1: Upload & Extract."""
    st.caption(t("returns.upload.caption"))

    uploaded = st.file_uploader(
        t("returns.upload.label"),
        type=["pdf"],
        key="return_pdf_uploader",
    )

    if uploaded:
        # Only split on new upload (avoid re-splitting on every Streamlit rerun)
        if st.session_state.get("return_uploaded_pdf") is None:
            from backend.return_extractor import ReturnExtractor

            pdf_bytes = uploaded.read()
            st.session_state["return_uploaded_pdf"] = pdf_bytes

            page_pdfs = ReturnExtractor.split_pdf(pdf_bytes)
            # Compress scanned pages (PNG→JPEG) — ~96% size reduction
            page_pdfs = [ReturnExtractor.compress_page_pdf(p) for p in page_pdfs]
            st.session_state["return_page_pdfs"] = page_pdfs

    # Show page count (thumbnails skipped — use PDF viewer in Review tab)
    page_pdfs = st.session_state.get("return_page_pdfs", [])
    if page_pdfs:
        st.info(t("returns.upload.thumbnails", count=len(page_pdfs)))

    # Extract button
    if page_pdfs:
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        if not gemini_key:
            st.warning(t("returns.upload.no_gemini_key"))
            return

        if st.button(t("returns.upload.btn_extract"), type="primary", key="btn_extract_returns"):
            from backend.return_extractor import ReturnExtractor

            extractor = ReturnExtractor(api_key=gemini_key)
            total = len(page_pdfs)

            progress_bar = st.progress(0, text=t("returns.upload.spinner", current=1, total=total))

            def _progress(current: int, total_pages: int) -> None:
                pct = max(0.0, min(1.0, (current - 1) / total_pages)) if total_pages > 0 else 1.0
                progress_bar.progress(
                    pct,
                    text=t("returns.upload.spinner", current=current, total=total_pages),
                )

            # Use already-split pages — send PDF bytes directly to Gemini (no PNG conversion)
            extracted: list[dict] = []
            errors: list[dict] = []
            for i, page_pdf in enumerate(page_pdfs):
                _progress(i + 1, total)
                try:
                    data = extractor.extract_page(page_pdf, page_number=i + 1)
                    extracted.append(data)
                except Exception as e:
                    errors.append({"page": i + 1, "error": str(e)})
            progress_bar.progress(1.0)

            st.session_state["return_extracted"] = extracted
            st.session_state["return_extraction_errors"] = errors
            st.session_state["return_summaries"] = _build_summaries(extracted)
            st.session_state["return_details"] = _build_details(extracted)

            progress_bar.empty()
            st.success(t("returns.upload.done", ok=len(extracted), fail=len(errors)))

            if errors:
                with st.expander(t("returns.upload.errors")):
                    for err in errors:
                        st.error(f"Page {err['page']}: {err['error']}")

            st.rerun()

    # Show existing extraction results + green "Next" button
    if st.session_state.get("return_extracted"):
        st.success(t("returns.upload.done",
                      ok=len(st.session_state["return_extracted"]),
                      fail=len(st.session_state.get("return_extraction_errors", []))))

        # Green "Complete → Next" button — uses st.button with on_click to avoid page refresh
        st.markdown(
            '<style>'
            'div[data-testid="stButton"] button[kind="secondary"]#btn_next_review {'
            'background-color:#22c55e !important;border-color:#16a34a !important;'
            'color:white !important;font-weight:600 !important;}'
            '</style>',
            unsafe_allow_html=True,
        )

        def _go_to_review() -> None:
            st.session_state["return_active_tab"] = "review"
            st.session_state["_return_tab_radio"] = t("returns.tab.review")

        st.button(
            t("returns.upload.btn_next"),
            key="btn_next_review",
            on_click=_go_to_review,
        )

    # Clear button
    if st.session_state.get("return_uploaded_pdf"):
        if st.button(t("returns.upload.btn_clear"), key="btn_clear_returns"):
            for k in ("return_uploaded_pdf", "return_page_pdfs", "return_page_images",
                       "return_extracted", "return_summaries", "return_details",
                       "return_extraction_errors", "return_unmatched"):
                st.session_state[k] = [] if isinstance(st.session_state.get(k), list) else (
                    pd.DataFrame() if isinstance(st.session_state.get(k), pd.DataFrame) else None
                )
            st.rerun()


def _render_review() -> None:
    """Tab 2: Review & Verify."""
    extracted = st.session_state.get("return_extracted", [])
    if not extracted:
        st.info(t("returns.review.no_data"))
        return

    # Rebuild DataFrames fresh from extracted data so column names match current language
    summaries = _build_summaries(extracted)
    details = _build_details(extracted)

    # ── Summary table ─────────────────────────────────────────────────────
    st.subheader(t("returns.review.summary_title"))
    if not summaries.empty:
        st.dataframe(summaries, use_container_width=True, hide_index=True)

    # ── Detail table ────────────────────────────────────────────────────
    if not details.empty:
        st.subheader(t("returns.review.detail_title"))
        actual_col = t("returns.review.col.actual_qty")

        # Priority columns first, then remaining
        _priority = [
            t("returns.review.col.page"),
            t("returns.review.col.doc_no"),
            t("returns.review.col.item_id"),
            t("returns.review.col.barcode"),
            t("returns.review.col.desc_cn"),
            t("returns.review.col.qty_pdf"),
            t("returns.review.col.actual_qty"),
            t("returns.review.col.cost"),
        ]
        all_display = [c for c in details.columns if not c.startswith("_")]
        remaining = [c for c in all_display if c not in _priority]
        display_cols = [c for c in _priority if c in all_display] + remaining

        edited = st.data_editor(
            details[display_cols],
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            column_config={
                t("returns.review.col.item_id"): st.column_config.TextColumn(
                    t("returns.review.col.item_id"),
                ),
                actual_col: st.column_config.NumberColumn(
                    actual_col,
                    min_value=0,
                    step=1,
                ),
                t("returns.review.col.barcode"): st.column_config.TextColumn(
                    t("returns.review.col.barcode"),
                    disabled=True,
                ),
                t("returns.review.col.matched"): st.column_config.CheckboxColumn(
                    t("returns.review.col.matched"),
                    disabled=True,
                ),
            },
            key="return_detail_editor",
        )

        # ── Action buttons ────────────────────────────────────────────────
        btn_col1, btn_col2 = st.columns(2)

        with btn_col1:
            if st.button(t("returns.review.btn_match"), type="primary", key="btn_match_odoo"):
                if not st.session_state.get("odoo_connected"):
                    st.warning(t("returns.export.no_odoo"))
                else:
                    with st.spinner(t("returns.review.match_spinner")):
                        from backend.return_extractor import ReturnExtractor

                        client = st.session_state["odoo_client"]
                        matched_pages, unmatched = ReturnExtractor.match_products_to_odoo(
                            extracted, client,
                        )
                        st.session_state["return_extracted"] = matched_pages
                        st.session_state["return_unmatched"] = unmatched

                        total_items = sum(len(p.get("line_items", [])) for p in matched_pages)
                        matched_count = total_items - len(unmatched)
                        st.success(t("returns.review.match_done",
                                     matched=matched_count,
                                     total=total_items,
                                     unmatched=len(unmatched)))
                    st.rerun()

        with btn_col2:
            if st.button(t("returns.review.btn_save"), key="btn_save_returns"):
                # Sync edited actual_qty back into return_extracted
                full_edited = details.copy()
                for col in display_cols:
                    if col in edited.columns:
                        full_edited[col] = edited[col].values
                _details_to_extracted(full_edited, extracted)
                st.success(t("returns.review.saved"))

        # Show unmatched items
        unmatched = st.session_state.get("return_unmatched", [])
        if unmatched:
            with st.expander(t("returns.review.unmatched_title")):
                st.dataframe(
                    pd.DataFrame(unmatched),
                    use_container_width=True,
                    hide_index=True,
                )

    # ── Next button ──────────────────────────────────────────────────────
    def _go_to_export() -> None:
        st.session_state["return_active_tab"] = "export"
        st.session_state["_return_tab_radio"] = t("returns.tab.export")

    st.button(
        t("returns.review.btn_next_export"),
        key="btn_next_export",
        on_click=_go_to_export,
        type="primary",
    )

    # ── PDF Viewer (below details) ────────────────────────────────────────
    st.divider()
    st.subheader(t("returns.review.pdf_title"))
    page_pdfs = st.session_state.get("return_page_pdfs", [])
    if page_pdfs:
        import base64
        import streamlit.components.v1 as components

        page_idx = st.selectbox(
            t("returns.review.page_select"),
            range(len(page_pdfs)),
            format_func=lambda i: f"Page {i + 1}",
            key="return_pdf_page_select",
        )

        # Render PDF page to image at 150 DPI for zoomable viewer
        if "return_page_images" not in st.session_state:
            st.session_state["return_page_images"] = {}
        cache = st.session_state["return_page_images"]
        if page_idx not in cache:
            from pdf2image import convert_from_bytes

            pil_imgs = convert_from_bytes(page_pdfs[page_idx], dpi=150, fmt="png")
            buf = io.BytesIO()
            pil_imgs[0].save(buf, format="JPEG", quality=80)
            cache[page_idx] = buf.getvalue()

        img_b64 = base64.b64encode(cache[page_idx]).decode()
        components.html(
            f"""
            <style>
              #viewer {{
                width:100%;height:680px;overflow:auto;
                background:#1a1a1a;border-radius:6px;
              }}
              #pdf-img {{ width:100%;transform-origin:top left; }}
              .zoom-bar {{
                display:flex;align-items:center;gap:8px;
                margin-top:6px;font-family:'Poppins',sans-serif;
              }}
              .zoom-bar button {{
                background:#333;color:#fff;border:1px solid #444;
                border-radius:4px;padding:4px 12px;cursor:pointer;
                font-size:14px;font-weight:600;
              }}
              .zoom-bar button:hover {{ background:#444; }}
              .zoom-bar span {{
                color:#999;font-size:12px;min-width:40px;text-align:center;
              }}
            </style>
            <div class="zoom-bar">
              <button onclick="zoom(0.8)">\u2212</button>
              <span id="zoom-label">100%</span>
              <button onclick="zoom(1.25)">+</button>
              <button onclick="resetZoom()">Fit</button>
            </div>
            <div id="viewer">
              <img id="pdf-img" src="data:image/jpeg;base64,{img_b64}">
            </div>
            <script>
              let scale = 1;
              function zoom(factor) {{
                scale *= factor;
                document.getElementById('pdf-img').style.width = (scale * 100) + '%';
                document.getElementById('zoom-label').textContent = Math.round(scale * 100) + '%';
              }}
              function resetZoom() {{
                scale = 1;
                document.getElementById('pdf-img').style.width = '100%';
                document.getElementById('zoom-label').textContent = '100%';
              }}
            </script>
            """,
            height=730,
        )
    else:
        st.caption(t("returns.upload.no_pdf"))


def _render_export(settings: Dict) -> None:
    """Tab 3: Export & Import."""
    extracted = st.session_state.get("return_extracted", [])
    if not extracted:
        st.info(t("returns.export.no_data"))
        return

    # ── Metrics ───────────────────────────────────────────────────────────────
    total_returns = len(extracted)
    total_lines = sum(len(p.get("line_items", [])) for p in extracted)
    total_value = sum(
        sum(it.get("amount", 0) or 0 for it in p.get("line_items", []))
        for p in extracted
    )

    m1, m2, m3 = st.columns(3)
    m1.metric(t("returns.export.metric.returns"), total_returns)
    m2.metric(t("returns.export.metric.lines"), total_lines)
    m3.metric(t("returns.export.metric.value"), f"${total_value:,.2f}")

    # ── Summary table with import checkbox ────────────────────────────────────
    st.subheader(t("returns.export.summary_title"))

    summary_rows = []
    for page in extracted:
        items = page.get("line_items", [])
        total = sum(it.get("amount", 0) or 0 for it in items)
        matched_count = sum(1 for it in items if it.get("matched"))
        resolved = _resolve_store_name(page.get("store_name", ""), settings)
        summary_rows.append({
            t("returns.export.col.import"): True,
            t("returns.export.col.doc_no"): page.get("doc_no", ""),
            t("returns.export.col.store"): resolved,
            t("returns.export.col.date"): page.get("print_date", ""),
            t("returns.export.col.lines"): len(items),
            t("returns.export.col.amount"): round(total, 2),
            t("returns.export.col.matched"): f"{matched_count}/{len(items)}",
        })

    export_df = pd.DataFrame(summary_rows)
    import_col = t("returns.export.col.import")

    edited_export = st.data_editor(
        export_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            import_col: st.column_config.CheckboxColumn(import_col, default=True),
        },
        key="return_export_editor",
    )

    selected_indices = [
        i for i, val in enumerate(edited_export[import_col]) if val
    ]

    # ── Excel Download ────────────────────────────────────────────────────────
    excel_bytes = _generate_excel(extracted)
    st.download_button(
        t("returns.export.btn_download"),
        data=excel_bytes,
        file_name="credit_notes_export.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="btn_download_returns_excel",
    )

    # ── Import to Odoo ────────────────────────────────────────────────────────
    st.divider()

    if not st.session_state.get("odoo_connected"):
        st.warning(t("returns.export.no_odoo"))
        return

    # ── Duplicate check ──────────────────────────────────────────────────────
    client = st.session_state["odoo_client"]
    selected_doc_nos = [
        extracted[i].get("doc_no", "") for i in selected_indices if extracted[i].get("doc_no")
    ]

    dup_refs: set[str] = set()
    if selected_doc_nos:
        with st.spinner(t("returns.export.dup_check_spinner")):
            existing = client.get_credit_notes_by_ref(selected_doc_nos)
        if existing:
            st.warning(t("returns.export.dup_warning"))
            for rec in existing:
                partner = rec.get("partner_id")
                partner_name = partner[1] if isinstance(partner, (list, tuple)) else str(partner)
                st.markdown(t("returns.export.dup_row",
                              ref=rec.get("ref", ""),
                              name=rec.get("name", ""),
                              partner=partner_name,
                              date=rec.get("invoice_date") or "—",
                              state=rec.get("state", "")))
                dup_refs.add(rec.get("ref", ""))

    # Filter out duplicates from selected indices
    if dup_refs:
        filtered_indices = [
            i for i in selected_indices if extracted[i].get("doc_no", "") not in dup_refs
        ]
        skipped = len(selected_indices) - len(filtered_indices)
        st.info(t("returns.export.dup_skip_note", count=skipped))
    else:
        filtered_indices = selected_indices

    import_count = len(filtered_indices)
    if st.button(
        t("returns.export.btn_import", count=import_count),
        type="primary",
        disabled=import_count == 0,
        key="btn_import_returns_odoo",
    ):
        page_pdfs = st.session_state.get("return_page_pdfs", [])
        ok_count = 0
        fail_count = 0

        for idx in filtered_indices:
            page = extracted[idx]
            doc_no = page.get("doc_no", f"Page {idx + 1}")
            store_name = _resolve_store_name(page.get("store_name", ""), settings)

            with st.spinner(t("returns.export.spinner", doc_no=doc_no)):
                # Find partner
                partner_id = client.get_partner_id_by_name(store_name) if store_name else None
                if not partner_id:
                    st.warning(t("returns.export.no_partner", store=store_name))
                    fail_count += 1
                    continue

                # Build credit note lines (only matched items with qty > 0)
                cn_lines = []
                for item in page.get("line_items", []):
                    if item.get("matched") and item.get("odoo_product_id"):
                        qty = item.get("actual_received", item.get("quantity", 0))
                        if not qty or qty <= 0:
                            continue
                        cn_lines.append({
                            "product_id": item["odoo_product_id"],
                            "quantity": qty,
                            "price_unit": item.get("cost", 0),
                            "name": item.get("description_en", "") or item.get("description_cn", ""),
                        })

                if not cn_lines:
                    st.warning(t("returns.export.no_matched_lines", doc_no=doc_no))
                    fail_count += 1
                    continue

                # Normalize date to YYYY-MM-DD for Odoo
                raw_date = page.get("print_date")
                normalized_date = None
                if raw_date:
                    from dateutil import parser as dateparser

                    try:
                        normalized_date = dateparser.parse(raw_date).strftime("%Y-%m-%d")
                    except (ValueError, TypeError):
                        pass  # Will be sent as None; Odoo defaults to today

                # Create credit note
                move_id, move_name = client.create_credit_note(
                    partner_id=partner_id,
                    date=normalized_date,
                    ref=doc_no,
                    lines=cn_lines,
                )

                if not move_id:
                    st.error(t("returns.export.fail", doc_no=doc_no, error="create failed"))
                    fail_count += 1
                    continue

                st.success(t("returns.export.created", name=move_name, doc_no=doc_no))

                # Attach split PDF
                if idx < len(page_pdfs):
                    pdf_data = page_pdfs[idx]
                    att_id = client.attach_pdf_to_record(
                        res_model="account.move",
                        res_id=move_id,
                        filename=f"{doc_no}.pdf",
                        pdf_bytes=pdf_data,
                    )
                    if att_id:
                        st.caption(t("returns.export.attach_ok", name=move_name))
                        client.post_chatter_message(
                            res_model="account.move",
                            res_id=move_id,
                            body=f"Return form {doc_no} from {store_name}",
                            attachment_ids=[att_id],
                        )
                    else:
                        st.caption(t("returns.export.attach_fail", name=move_name))

                # Step 2: Confirm credit note (name is assigned after posting)
                with st.spinner(t("returns.export.confirming", name=doc_no)):
                    confirmed = client.confirm_credit_note(move_id)
                if confirmed:
                    # Re-read name — Odoo assigns the sequence (e.g. CRAT/...) on confirm
                    move_name = client.get_credit_note_name(move_id) or move_name
                    st.caption(t("returns.export.confirmed", name=move_name))
                else:
                    st.warning(t("returns.export.confirm_fail", name=move_name))
                    fail_count += 1
                    continue

                # Step 3: Create return transfer (server action 1443)
                with st.spinner(t("returns.export.creating_transfer", name=move_name)):
                    transfer_ok = client.create_return_transfer(move_id)
                if transfer_ok:
                    st.caption(t("returns.export.transfer_created", name=move_name))
                else:
                    st.warning(t("returns.export.transfer_fail", name=move_name, error="server action failed"))
                    fail_count += 1
                    continue

                # Step 4: Validate return transfer
                with st.spinner(t("returns.export.validating_transfer", name=move_name)):
                    val_ok, val_detail = client.validate_return_transfer(move_id)
                if val_ok:
                    st.caption(t("returns.export.transfer_validated", name=move_name, detail=val_detail))
                else:
                    st.warning(t("returns.export.transfer_validate_fail", name=move_name, error=val_detail))
                    fail_count += 1
                    continue

                ok_count += 1

        st.info(t("returns.export.done", ok=ok_count, fail=fail_count))


# ── Main render ───────────────────────────────────────────────────────────────


def render(settings: Dict) -> None:
    st.title(t("returns.title"))

    if "return_active_tab" not in st.session_state:
        st.session_state["return_active_tab"] = _TAB_KEYS[0]

    _tab_labels = [
        t("returns.tab.upload"),
        t("returns.tab.review"),
        t("returns.tab.export"),
    ]
    _key_to_label = dict(zip(_TAB_KEYS, _tab_labels))
    _label_to_key = dict(zip(_tab_labels, _TAB_KEYS))

    # Initialize radio widget state if not yet set (avoids conflict between index= and session state)
    if "_return_tab_radio" not in st.session_state:
        current_key = st.session_state["return_active_tab"]
        st.session_state["_return_tab_radio"] = _key_to_label.get(current_key, _tab_labels[0])

    active_label = st.radio(
        t("returns.workflow_step"),
        _tab_labels,
        horizontal=True,
        key="_return_tab_radio",
        label_visibility="collapsed",
    )
    st.session_state["return_active_tab"] = _label_to_key.get(active_label, _TAB_KEYS[0])

    st.divider()

    active_key = st.session_state["return_active_tab"]
    if active_key == "upload":
        _render_upload()
    elif active_key == "review":
        _render_review()
    elif active_key == "export":
        _render_export(settings)
