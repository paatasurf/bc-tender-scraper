-- Migration 008: win/loss outcome tracking per company and tender

CREATE TABLE IF NOT EXISTS tender_outcomes (
    id SERIAL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    tender_id VARCHAR(255) NOT NULL,
    tender_title VARCHAR(500),
    outcome VARCHAR(20) CHECK (outcome IN ('won', 'lost', 'withdrew', 'pending')),
    bid_amount NUMERIC,
    award_amount NUMERIC,
    notes TEXT,
    recorded_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(company_id, tender_id)
);

CREATE INDEX IF NOT EXISTS ix_tender_outcomes_company_id ON tender_outcomes (company_id);
CREATE INDEX IF NOT EXISTS ix_tender_outcomes_recorded_at ON tender_outcomes (recorded_at DESC);
