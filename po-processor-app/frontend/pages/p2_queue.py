"""
Dashboard / Queue page — shows staged POs from PostgreSQL with multi-select,
manual PDF upload, and "Process Selected" button.
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
               order_date, delivery_date, status, error_message,
               created_at, processed_at
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


def _insert_manual_po(filename: str, extracted_data: list, errors: list):
    """Insert a manually uploaded PO into staged_pos."""
    po_number = store_id = store_name = order_date = delivery_date = None
    if extracted_data:
        first = extracted_data[0]
        po_number = str(first.get("PO No.", "")) or None
        store_id = first.get("Store ID")
        store_name = first.get("Store Name", "") or None
        order_date = first.get("Order Date", "") or None
        delivery_date = first.get("Delivery Date", "") or None

    status = "unprocessed" if extracted_data else "error"
    error_msg = "; ".join(errors) if errors and not extracted_data else None

    sql = """
        INSERT INTO staged_pos (
            source, original_filename, po_number, store_id, store_name,
            order_date, delivery_date, extracted_data, status, error_message
        ) VALUES ('manual', %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
        RETURNING id
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (
                filename, po_number, store_id, store_name,
                order_date, delivery_date,
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


def render():
    st.title("📋 PO Queue")

    # Check DB connectivity
    if not test_connection():
        st.error(
            "⚠️ Cannot connect to PostgreSQL. "
            "Ensure the database service is running and DATABASE_URL is set."
        )
        return

    # Auto-refresh toggle
    refresh_col, info_col = st.columns([1, 3])
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

    if queue_df.empty:
        st.info("📭 No POs in queue yet. Upload a PDF below or wait for Gmail monitor to pick one up.")
    else:
        st.subheader(f"Queue ({len(queue_df)} items)")

        # Add a checkbox column for selection
        queue_df.insert(0, "Select", False)

        # Status badge column
        queue_df["Status"] = queue_df["status"].apply(
            lambda s: f"🔵 {s}" if s == "unprocessed"
            else (f"🟡 {s}" if s == "processing"
            else (f"✅ {s}" if s == "processed"
            else f"🔴 {s}"))
        )

        display_cols = [
            "Select", "id", "source", "original_filename",
            "store_id", "store_name", "po_number",
            "order_date", "delivery_date", "Status", "created_at",
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
                "source": st.column_config.TextColumn("Source", width="small"),
                "original_filename": st.column_config.TextColumn("Filename"),
                "store_id": st.column_config.NumberColumn("Store", width="small"),
                "store_name": st.column_config.TextColumn("Store Name"),
                "po_number": st.column_config.TextColumn("PO #"),
                "Status": st.column_config.TextColumn("Status"),
            },
            disabled=[c for c in display_df.columns if c != "Select"],
            hide_index=True,
            use_container_width=True,
        )

        selected_rows = edited[edited["Select"] == True]
        selected_ids = queue_df.loc[selected_rows.index, "id"].tolist()

        # Filter to only unprocessed / error rows
        valid_ids = queue_df.loc[
            (queue_df["id"].isin(selected_ids)) &
            (queue_df["status"].isin(["unprocessed", "error"])),
            "id"
        ].tolist()

        if selected_ids:
            invalid_count = len(selected_ids) - len(valid_ids)
            if invalid_count:
                st.warning(f"{invalid_count} selected item(s) are already processed or processing — they will be skipped.")

        btn_col, stat_col = st.columns([1, 3])
        with btn_col:
            process_disabled = len(valid_ids) == 0
            if st.button(
                f"⚡ Process Selected ({len(valid_ids)})",
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
                    st.session_state["config_latest_so"] = None
                    st.session_state["current_page"] = "Transform & Review"
                    st.rerun()

        with stat_col:
            counts = queue_df["status"].value_counts()
            summary = " | ".join(
                f"**{s}**: {counts.get(s, 0)}"
                for s in ["unprocessed", "processing", "processed", "error"]
            )
            st.caption(summary)

        # Clear processed button
        if st.button("🗑️ Clear Processed (older than 7 days)"):
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM staged_pos WHERE status='processed' "
                        "AND processed_at < NOW() - INTERVAL '7 days'"
                    )
                conn.commit()
            st.success("Cleared old processed records.")
            st.rerun()

    st.divider()

    # ── Manual PDF Upload ──────────────────────────────────────────────────────
    st.subheader("📂 Manual PDF Upload")
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
                    new_id = _insert_manual_po(f.name, data, errors)
                    new_ids.append(new_id)
                    all_errors.extend(errors)

            st.success(f"✅ Added {len(new_ids)} PDF(s) to queue.")

            if all_errors:
                with st.expander("Extraction warnings"):
                    for e in all_errors:
                        st.write(e)

            st.rerun()
