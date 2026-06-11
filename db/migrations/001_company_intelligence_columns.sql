-- Company intelligence columns (applied via db/connection.py init_db)
ALTER TABLE companies ADD COLUMN IF NOT EXISTS company_type VARCHAR(50) DEFAULT '';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS confidence_score FLOAT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS company_lifecycle VARCHAR(20) DEFAULT '';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS company_tier VARCHAR(20) DEFAULT '';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS enrichment_status VARCHAR(20) DEFAULT 'pending';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS last_enriched_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS ix_companies_company_type ON companies (company_type);
CREATE INDEX IF NOT EXISTS ix_companies_company_lifecycle ON companies (company_lifecycle);
CREATE INDEX IF NOT EXISTS ix_companies_enrichment_status ON companies (enrichment_status);
