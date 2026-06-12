-- Contract award statistics on companies (Phase A — applied via db/connection.py init_db)
ALTER TABLE companies ADD COLUMN IF NOT EXISTS award_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS total_award_value FLOAT NOT NULL DEFAULT 0.0;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS avg_award_value FLOAT NOT NULL DEFAULT 0.0;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS award_categories VARCHAR[] DEFAULT '{}';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS award_clients VARCHAR[] DEFAULT '{}';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS buyer_levels VARCHAR[] DEFAULT '{}';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS award_sources VARCHAR[] DEFAULT '{}';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS first_award_date VARCHAR(20) DEFAULT '';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS last_award_date VARCHAR(20) DEFAULT '';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS primary_address VARCHAR(500) DEFAULT '';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS primary_city VARCHAR(100) DEFAULT '';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS primary_province VARCHAR(50) DEFAULT '';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS data_sources VARCHAR[] DEFAULT '{}';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS canonical_vendor_name VARCHAR(300) DEFAULT '';

CREATE INDEX IF NOT EXISTS ix_companies_award_count ON companies (award_count);
CREATE INDEX IF NOT EXISTS ix_companies_total_award_value ON companies (total_award_value);
CREATE INDEX IF NOT EXISTS ix_companies_last_award_date ON companies (last_award_date);
CREATE INDEX IF NOT EXISTS ix_companies_data_sources ON companies USING GIN (data_sources);
