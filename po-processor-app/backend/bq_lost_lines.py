"""Non-blocking BigQuery writer for deleted/lost PO order lines.

Captures lines removed during the Transform & Review phase and
inserts them into ``odoo_data.lost_order_lines`` for analytics.
"""

import base64
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

import pandas as pd

logger = logging.getLogger(__name__)

_BQ_PROJECT = "odoo-471420"
_BQ_DATASET = "odoo_data"
_BQ_TABLE = "lost_order_lines"
_BQ_LOCATION = "northamerica-northeast2"

# Lazy-loaded client: None = not attempted, False = unavailable
_bq_client: Optional[object] = None
_bq_lock = threading.Lock()


def _get_bq_credentials() -> Optional[dict]:
    """Load BQ service account from env var (base64) or file path."""
    raw = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if raw:
        try:
            return json.loads(base64.b64decode(raw))
        except (json.JSONDecodeError, Exception) as e:
            logger.error("Failed to decode GOOGLE_APPLICATION_CREDENTIALS_JSON: %s", e)
    path = os.getenv("BQ_SERVICE_ACCOUNT_PATH")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def _get_client() -> Optional[object]:
    """Lazy-init BigQuery client.  Returns *None* if unavailable."""
    global _bq_client
    if _bq_client is False:
        return None
    if _bq_client is not None:
        return _bq_client
    with _bq_lock:
        # Double-checked locking after acquiring lock
        if _bq_client is False:
            return None
        if _bq_client is not None:
            return _bq_client
        try:
            from google.cloud import bigquery
            from google.oauth2 import service_account

            creds_info = _get_bq_credentials()
            if not creds_info:
                logger.info("No BQ credentials configured — lost line tracking disabled.")
                _bq_client = False
                return None

            credentials = service_account.Credentials.from_service_account_info(creds_info)
            _bq_client = bigquery.Client(
                credentials=credentials,
                project=_BQ_PROJECT,
                location=_BQ_LOCATION,
            )
            logger.info("BigQuery client initialized for lost line tracking.")
            return _bq_client
        except ImportError:
            logger.info("google-cloud-bigquery not installed — lost line tracking disabled.")
            _bq_client = False
            return None
        except Exception as e:
            logger.warning("Failed to init BigQuery client: %s", e)
            _bq_client = False
            return None


def _safe_float(val: object) -> Optional[float]:
    """Convert to float, returning None for NaN/None/unparseable values."""
    try:
        if val is None or (not isinstance(val, str) and pd.isna(val)):
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_int(val: object) -> Optional[int]:
    """Convert to int, returning None for NaN/None/unparseable values."""
    try:
        if val is None or (not isinstance(val, str) and pd.isna(val)):
            return None
        return int(val)
    except (TypeError, ValueError):
        return None


def _safe_str(val: object) -> Optional[str]:
    """Convert to str, returning None for NaN/None/empty values."""
    try:
        if val is None or (not isinstance(val, str) and pd.isna(val)):
            return None
    except (TypeError, ValueError):
        pass
    s = str(val)
    return s if s else None


def _safe_bool(val: object) -> bool:
    """Convert to bool, treating NaN/None as False."""
    if val is None:
        return False
    try:
        if not isinstance(val, bool) and pd.isna(val):
            return False
    except (TypeError, ValueError):
        pass
    return bool(val)


def record_lost_lines(
    deleted_rows: pd.DataFrame,
    deletion_type: Literal["delete_flagged", "save_removed"],
    warehouse_code: str,
) -> bool:
    """Write deleted order lines to BigQuery.

    Non-blocking — returns ``False`` on any failure but never raises.
    """
    if deleted_rows.empty:
        return True

    client = _get_client()
    if client is None:
        return False

    try:
        now = datetime.now(timezone.utc).isoformat()
        table_ref = f"{_BQ_PROJECT}.{_BQ_DATASET}.{_BQ_TABLE}"

        rows = []
        for _, row in deleted_rows.iterrows():
            rows.append({
                "id": str(uuid.uuid4()),
                "deleted_at": now,
                "deletion_type": deletion_type,
                "warehouse": warehouse_code,
                "store_id": _safe_int(row.get("store_id")),
                "store_name": _safe_str(row.get("store_name")),
                "order_date": _safe_str(row.get("order_date")),
                "delivery_date": _safe_str(row.get("delivery_date")),
                "po_number": _safe_str(row.get("po_number")),
                "internal_reference": _safe_str(row.get("internal_reference")),
                "product_id": _safe_int(row.get("product_id")),
                "product_name": _safe_str(row.get("product_name")),
                "barcode": _safe_str(row.get("barcode")),
                "units_per_order": _safe_float(row.get("units_per_order")),
                "original_qty": _safe_float(row.get("original_qty")),
                "po_unit_price": _safe_float(row.get("po_unit_price")),
                "product_uom_qty": _safe_float(row.get("product_uom_qty")),
                "price_unit": _safe_float(row.get("price_unit")),
                "total_price": _safe_float(row.get("total_price")),
                "odoo_on_hand": _safe_float(row.get("odoo_on_hand")),
                "odoo_available": _safe_float(row.get("odoo_available")),
                "flagged": _safe_bool(row.get("flagged")),
                "flag_reason": _safe_str(row.get("flag_reason")),
                "shortage_details": _safe_str(row.get("shortage_details")),
            })

        errors = client.insert_rows_json(
            table_ref,
            rows,
            row_ids=[r["id"] for r in rows],
        )
        if errors:
            logger.warning("BQ insert errors for lost lines: %s", errors)
            return False

        logger.info(
            "Recorded %d lost order line(s) to BQ (%s, %s)",
            len(rows), deletion_type, warehouse_code,
        )
        return True

    except Exception as e:
        logger.warning("Failed to record lost lines to BQ: %s", e)
        return False
