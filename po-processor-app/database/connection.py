import os
import logging
from contextlib import contextmanager
from typing import Generator

import psycopg2
import psycopg2.pool

logger = logging.getLogger(__name__)

# Thread-safe connection pool — shared across Streamlit sessions and the Gmail monitor thread
_pool: psycopg2.pool.ThreadedConnectionPool = None
_schema_initialized = False

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS staged_pos (
    id                  SERIAL PRIMARY KEY,
    source              VARCHAR(20) NOT NULL DEFAULT 'manual',
    gmail_message_id    TEXT,
    gmail_subject       TEXT,
    gmail_from          TEXT,
    gmail_received_at   TIMESTAMPTZ,
    original_filename   TEXT NOT NULL,
    po_number           TEXT,
    store_id            INTEGER,
    store_name          TEXT,
    order_date          TEXT,
    delivery_date       TEXT,
    region              VARCHAR(10),
    extracted_data      JSONB NOT NULL DEFAULT '[]',
    pdf_content         BYTEA,
    pdf_page_count      INTEGER,
    last_downloaded_at  TIMESTAMPTZ,
    status              VARCHAR(20) NOT NULL DEFAULT 'unprocessed',
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at        TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_staged_pos_gmail_msg_id
    ON staged_pos(gmail_message_id) WHERE gmail_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_staged_pos_status
    ON staged_pos(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_staged_pos_region
    ON staged_pos(region);
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
INSERT INTO app_settings (key, value) VALUES
    ('gmail_poll_interval_seconds', '300')
ON CONFLICT (key) DO NOTHING;
"""

# Idempotent migration for existing databases that lack new columns
_MIGRATE_SQL = """
ALTER TABLE staged_pos ADD COLUMN IF NOT EXISTS gmail_from TEXT;
ALTER TABLE staged_pos ADD COLUMN IF NOT EXISTS region VARCHAR(10);
ALTER TABLE staged_pos ADD COLUMN IF NOT EXISTS pdf_content BYTEA;
ALTER TABLE staged_pos ADD COLUMN IF NOT EXISTS pdf_page_count INTEGER;
ALTER TABLE staged_pos ADD COLUMN IF NOT EXISTS last_downloaded_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_staged_pos_region ON staged_pos(region);
"""


def _ensure_schema(conn):
    """Create tables if they don't exist yet, and run migrations for new columns.

    Two-phase approach handles both fresh installs and upgrades:
      1. Run migrations first (adds columns to existing tables).
         This may fail on a fresh DB where the table doesn't exist — that's fine.
      2. Run init SQL (CREATE TABLE IF NOT EXISTS + indexes).
         On fresh DB this creates everything; on existing DB it's a no-op for the
         table but creates any missing indexes (safe now because columns exist).
    """
    global _schema_initialized
    if _schema_initialized:
        return

    # Phase 1: migrate existing tables (may fail on fresh install — OK)
    try:
        with conn.cursor() as cur:
            cur.execute(_MIGRATE_SQL)
        conn.commit()
    except Exception:
        conn.rollback()

    # Phase 2: create tables + indexes (columns exist now if table was migrated)
    try:
        with conn.cursor() as cur:
            cur.execute(_INIT_SQL)
        conn.commit()
        _schema_initialized = True
        logger.info("Database schema initialized")
    except Exception as e:
        conn.rollback()
        logger.error(f"Schema initialization failed: {e}")
        raise


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "DATABASE_URL environment variable is not set. "
                "Add it to your .env file: "
                "DATABASE_URL=postgresql://po_user:password@localhost:5432/po_processor"
            )
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=database_url,
        )
        logger.info("PostgreSQL connection pool created")
        # Auto-initialize schema on first pool creation
        conn = _pool.getconn()
        try:
            _ensure_schema(conn)
        finally:
            _pool.putconn(conn)
    return _pool


@contextmanager
def get_db_connection() -> Generator[psycopg2.extensions.connection, None, None]:
    """
    Context manager that yields a psycopg2 connection from the pool.

    Usage:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ...")
            conn.commit()
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def test_connection() -> bool:
    """Returns True if the database is reachable, False otherwise."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception as e:
        logger.error(f"Database connection test failed: {e}")
        return False
