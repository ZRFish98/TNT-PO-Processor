"""
Dashboard / Queue page — shows staged POs from PostgreSQL with multi-select,
manual PDF upload, and "Process Selected" button.

POs are split by region (East/West) so the user processes one region at a time.
"""
import json

import pandas as pd
import streamlit as st

from database.connection import get_db_connection, test_connection
from backend.pdf_extractor import PDFExtractor


def _load_queue() -> pd.DataFrame:
    """Fetch all staged_pos records ordered by created_at desc."""
    sql = """
        SELECT id, source, original_filename, po_number, store_id, store_name,
               order_date, delivery_date, region, status, error_message,
               gmail_from, gmail_received_at, created_at, processed_at
        FROM staged_pos
        ORDER BY created_at DESC
        LIMIT 200
    """
    try:
        with get_db_connection() as conn:
            return pd.read_sql(sql, conn)
    except Exception as e:
        st.error(f"Database error: {e}")
        return pd.DataFrame()


def _mark_processing(ids: list):
    sql = "UPDATE staged_pos SET status='processing', updated_at=NOW() WHERE id = ANY(%s)"
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (ids,))
        conn.commit()


def _fetch_extracted_data(ids: list) -> pd.DataFrame:
    """Retrieve and concatenate extracted_data JSONB for selected staged_pos IDs."""
    sql = "SELECT extracted_data FROM staged_pos WHERE id = ANY(%s) AND status != 'error'"
    rows = []
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (ids,))
            for (data,) in cur.fetchall():
                if data:
                    parsed = data if isinstance(data, list) else json.loads(data)
                    rows.extend(parsed)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if not df.empty:
        df["Store ID"] = pd.to_numeric(df.get("Store ID"), errors="coerce")
        df["PO No."] = pd.to_numeric(df.get("PO No."), errors="coerce")
        df = df.sort_values(by=["Store ID", "PO No."])
    return df


def _detect_region(extracted_data: list, cw_stores: set) -> str | None:
    """Determine 'east' or 'west' from the first PO's store ID."""
    if not extracted_data:
        return None
    first_store = extracted_data[0].get("Store ID")
    if first_store is None:
        return None
    try:
        store_id = int(first_store)
    except (ValueError, TypeError):
        return None
    return "west" if store_id in cw_stores else "east"


def _insert_manual_po(
    filename: str, extracted_data: list, errors: list, cw_stores: set,
) -> int:
    """Insert a manually uploaded PO into staged_pos with auto-detected region."""
    po_number = store_id = store_name = order_date = delivery_date = None
    if extracted_data:
        first = extracted_data[0]
        po_number = str(first.get("PO No.", "")) or None
        store_id = first.get("Store ID")
        store_name = first.get("Store Name", "") or None
        order_date = first.get("Order Date", "") or None
        delivery_date = first.get("Delivery Date", "") or None

    region = _detect_region(extracted_data, cw_stores)
    status = "unprocessed" if extracted_data else "error"
    error_msg = "; ".join(errors) if errors and not extracted_data else None

    sql = """
        INSERT INTO staged_pos (
            source, original_filename, po_number, store_id, store_name,
            order_date, delivery_date, region, extracted_data, status, error_message
        ) VALUES ('manual', %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
        RETURNING id
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (
                filename, po_number, store_id, store_name,
                order_date, delivery_date, region,
                json.dumps(extracted_data), status, error_msg,
            ))
            new_id = cur.fetchone()[0]
        conn.commit()
    return new_id


# ── Status badge helper ────────────────────────────────────────────────────────
_STATUS_COLORS = {
    "unprocessed": "#00D1FF",
    "processing":  "#FFD700",
    "processed":   "#666666",
    "error":       "#FF4444",
}


def render(settings: dict):
    st.title("PO Queue")

    cw_stores = set(settings.get("warehouse_mapping", {}).get("cw_stores", []))

    # Check DB connectivity
    if not test_connection():
        st.error(
            "Cannot connect to PostgreSQL. "
            "Ensure the database service is running and DATABASE_URL is set."
        )
        return

    # ── Region filter ─────────────────────────────────────────────────────────
    region_col, refresh_col, info_col = st.columns([2, 1, 3])
    with region_col:
        region_filter = st.radio(
            "Region",
            ["All", "East (CE)", "West (CW)"],
            horizontal=True,
            key="region_filter",
        )
    with refresh_col:
        auto_refresh = st.toggle("Auto-refresh (30s)", value=False, key="queue_auto_refresh")
    with info_col:
        st.caption("Queue shows the latest 200 POs. Gmail monitor adds new ones automatically.")

    if auto_refresh:
        try:
            from streamlit_autorefresh import st_autorefresh
            st_autorefresh(interval=30_000, key="queue_refresh_counter")
        except ImportError:
            st.warning("`streamlit-autorefresh` not installed. Add it to requirements.txt.")

    # ── Queue table ────────────────────────────────────────────────────────────
    queue_df = _load_queue()

    # Apply region filter
    if region_filter == "East (CE)" and "region" in queue_df.columns:
        queue_df = queue_df[queue_df["region"] == "east"]
    elif region_filter == "West (CW)" and "region" in queue_df.columns:
        queue_df = queue_df[queue_df["region"] == "west"]

    if queue_df.empty:
        st.info("No POs in queue for this region. Upload a PDF below or wait for Gmail monitor.")
    else:
        header_col, select_col = st.columns([3, 1])
        with header_col:
            st.subheader(f"Queue ({len(queue_df)} items)")
        with select_col:
            select_all = st.checkbox("Select All", key="queue_select_all")

        # Add a checkbox column for selection
        queue_df = queue_df.copy()
        queue_df.insert(0, "Select", select_all)

        # Status badge column
        queue_df["Status"] = queue_df["status"].apply(
            lambda s: f"🔵 {s}" if s == "unprocessed"
            else (f"🟡 {s}" if s == "processing"
            else (f"✅ {s}" if s == "processed"
            else f"🔴 {s}"))
        )

        # Region display column
        if "region" in queue_df.columns:
            queue_df["Region"] = queue_df["region"].map(
                {"east": "CE (East)", "west": "CW (West)"}
            ).fillna("—")
        else:
            queue_df["Region"] = "—"

        # Format received time: gmail_received_at for emails, created_at as fallback
        received_ts = pd.to_datetime(
            queue_df.get("gmail_received_at"), errors="coerce"
        )
        fallback_ts = pd.to_datetime(
            queue_df.get("created_at"), errors="coerce"
        )
        queue_df["Received"] = received_ts.fillna(fallback_ts).dt.strftime("%Y-%m-%d %H:%M").fillna("—")

        display_cols = [
            "Select", "id", "Received", "source", "original_filename",
            "store_id", "Region",
            "order_date", "delivery_date", "Status",
        ]
        display_df = queue_df[[c for c in display_cols if c in queue_df.columns]].copy()

        edited = st.data_editor(
            display_df,
            key="queue_editor",
            num_rows="fixed",
            height=500,
            column_config={
                "Select": st.column_config.CheckboxColumn("Select", width="small"),
                "id": st.column_config.NumberColumn("ID", width="small"),
                "Received": st.column_config.TextColumn("Received", width="medium"),
                "source": st.column_config.TextColumn("Source", width="small"),
                "original_filename": st.column_config.TextColumn("Filename"),
                "store_id": st.column_config.NumberColumn("Store", width="small"),
                "Region": st.column_config.TextColumn("Region", width="small"),
                "Status": st.column_config.TextColumn("Status"),
            },
            disabled=[c for c in display_df.columns if c != "Select"],
            hide_index=True,
            use_container_width=True,
        )

        selected_rows = edited[edited["Select"] == True]
        selected_ids = queue_df.loc[selected_rows.index, "id"].tolist()

        # Filter to processable rows (unprocessed, processing, error)
        valid_ids = queue_df.loc[
            (queue_df["id"].isin(selected_ids)) &
            (queue_df["status"].isin(["unprocessed", "processing", "error"])),
            "id"
        ].tolist()

        if selected_ids:
            invalid_count = len(selected_ids) - len(valid_ids)
            if invalid_count:
                st.warning(f"{invalid_count} selected item(s) are already processed — they will be skipped.")

        btn_col, stat_col = st.columns([1, 3])
        with btn_col:
            process_disabled = len(valid_ids) == 0
            if st.button(
                f"Process Selected ({len(valid_ids)})",
                type="primary",
                disabled=process_disabled,
            ):
                with st.spinner("Loading PO data from queue..."):
                    extracted_df = _fetch_extracted_data(valid_ids)

                if extracted_df.empty:
                    st.error("No extractable data found in selected records.")
                else:
                    _mark_processing(valid_ids)
                    st.session_state["extracted_po_data"] = extracted_df
                    st.session_state["po_errors"] = []
                    st.session_state["source_po_ids"] = valid_ids
                    # Clear downstream state
                    st.session_state["order_summaries"] = __import__("pandas").DataFrame()
                    st.session_state["line_details"] = __import__("pandas").DataFrame()
                    st.session_state["odoo_products_cache"] = None
                    # Clear export state (managed locally in p5_export)
                    for _k in ("export_latest_so", "export_so_fetched", "export_open_orders"):
                        st.session_state.pop(_k, None)
                    st.session_state["current_page"] = "Transform & Review"
                    st.rerun()

        with stat_col:
            counts = queue_df["status"].value_counts()
            summary = " | ".join(
                f"**{s}**: {counts.get(s, 0)}"
                for s in ["unprocessed", "processing", "processed", "error"]
            )
            st.caption(summary)

        # Delete / Clear buttons
        del_col1, del_col2 = st.columns([1, 1])
        with del_col1:
            if st.button(
                f"Delete Selected ({len(selected_ids)})",
                disabled=len(selected_ids) == 0,
            ):
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "DELETE FROM staged_pos WHERE id = ANY(%s)",
                            (selected_ids,),
                        )
                    conn.commit()
                st.success(f"Deleted {len(selected_ids)} record(s).")
                st.rerun()
        with del_col2:
            if st.button("Clear All Processed"):
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "DELETE FROM staged_pos WHERE status='processed'"
                        )
                    conn.commit()
                st.success("Cleared all processed records.")
                st.rerun()

    st.divider()

    # ── Manual PDF Upload ──────────────────────────────────────────────────────
    st.subheader("Manual PDF Upload")
    st.caption("Upload PDFs directly — they will appear in the queue above and be auto-selected for processing.")

    uploaded_files = st.file_uploader(
        "Upload T&T Purchase Orders (PDF)",
        type=["pdf"],
        accept_multiple_files=True,
        key="manual_pdf_uploader",
    )

    if uploaded_files:
        if st.button("Extract & Add to Queue"):
            new_ids = []
            all_errors = []
            with st.spinner("Extracting PDF data..."):
                for f in uploaded_files:
                    data, errors = PDFExtractor.extract_from_file(f, f.name)
                    new_id = _insert_manual_po(f.name, data, errors, cw_stores)
                    new_ids.append(new_id)
                    all_errors.extend(errors)

            st.success(f"Added {len(new_ids)} PDF(s) to queue.")

            if all_errors:
                with st.expander("Extraction warnings"):
                    for e in all_errors:
                        st.write(e)

            st.rerun()
