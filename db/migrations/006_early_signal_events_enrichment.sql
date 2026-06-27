-- Migration 006: enrichment fields for early_signal_events

ALTER TABLE early_signal_events ADD COLUMN IF NOT EXISTS url_link VARCHAR(500) NOT NULL DEFAULT '';
ALTER TABLE early_signal_events ADD COLUMN IF NOT EXISTS address VARCHAR(300) NOT NULL DEFAULT '';
ALTER TABLE early_signal_events ADD COLUMN IF NOT EXISTS applicant VARCHAR(300) NOT NULL DEFAULT '';
ALTER TABLE early_signal_events ADD COLUMN IF NOT EXISTS project_value VARCHAR(50) NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS ix_early_signal_events_url_link
    ON early_signal_events (url_link)
    WHERE url_link <> '';
