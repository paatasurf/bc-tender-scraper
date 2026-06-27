-- Migration 005: early_signal_events for rezoning / development permit signals

CREATE TABLE IF NOT EXISTS early_signal_events (
    id               SERIAL PRIMARY KEY,
    external_id      VARCHAR(100) NOT NULL DEFAULT '',
    source           VARCHAR(100) NOT NULL DEFAULT '',
    transaction_date VARCHAR(20) NOT NULL DEFAULT '',
    municipality     VARCHAR(100) NOT NULL DEFAULT '',
    region           VARCHAR(100) NOT NULL DEFAULT '',
    property_type    VARCHAR(300) NOT NULL DEFAULT '',
    signal_type      VARCHAR(50) NOT NULL DEFAULT '',
    scraped_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_early_signal_events_signal_type
    ON early_signal_events (signal_type);

CREATE INDEX IF NOT EXISTS ix_early_signal_events_source
    ON early_signal_events (source);

CREATE INDEX IF NOT EXISTS ix_early_signal_events_transaction_date
    ON early_signal_events (transaction_date)
    WHERE transaction_date <> '';

CREATE UNIQUE INDEX IF NOT EXISTS ix_early_signal_events_source_external_id
    ON early_signal_events (source, external_id)
    WHERE external_id <> '';
