-- T&T PO Processor: Initial Schema
-- Auto-runs on first PostgreSQL container creation via docker-entrypoint-initdb.d

CREATE TABLE IF NOT EXISTS staged_pos (
    id                  SERIAL PRIMARY KEY,

    -- Source tracking
    source              VARCHAR(20) NOT NULL DEFAULT 'manual',
    gmail_message_id    TEXT,
    gmail_subject       TEXT,
    gmail_from          TEXT,
    gmail_received_at   TIMESTAMPTZ,
    original_filename   TEXT NOT NULL,

    -- PO identity (extracted from PDF)
    po_number           TEXT,
    store_id            INTEGER,
    store_name          TEXT,
    order_date          TEXT,
    delivery_date       TEXT,

    -- Region: 'east' (CE) or 'west' (CW) — determined from first PO's store ID
    region              VARCHAR(10),

    -- Full extracted payload as JSON array of row dicts
    extracted_data      JSONB NOT NULL DEFAULT '[]',

    -- Status state machine: unprocessed → processing → processed | error
    status              VARCHAR(20) NOT NULL DEFAULT 'unprocessed',
    error_message       TEXT,

    -- Audit timestamps
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at        TIMESTAMPTZ
);

-- Prevent duplicate Gmail messages
CREATE UNIQUE INDEX IF NOT EXISTS idx_staged_pos_gmail_msg_id
    ON staged_pos(gmail_message_id)
    WHERE gmail_message_id IS NOT NULL;

-- Fast queue page queries
CREATE INDEX IF NOT EXISTS idx_staged_pos_status
    ON staged_pos(status, created_at DESC);

-- Filter by region
CREATE INDEX IF NOT EXISTS idx_staged_pos_region
    ON staged_pos(region);

-- App settings (persisted across restarts)
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed default settings
INSERT INTO app_settings (key, value) VALUES
    ('gmail_poll_interval_seconds', '300')
ON CONFLICT (key) DO NOTHING;
