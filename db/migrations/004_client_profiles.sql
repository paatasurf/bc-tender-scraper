-- Migration 004: client_profiles table for email alert digests

CREATE TABLE IF NOT EXISTS client_profiles (
    id                  SERIAL PRIMARY KEY,
    clerk_user_id       VARCHAR(100) NOT NULL DEFAULT '',
    company_id          INTEGER NOT NULL,
    company_name        VARCHAR(300) NOT NULL DEFAULT '',
    email               VARCHAR(320) NOT NULL,
    regions             VARCHAR[] DEFAULT '{}',
    specializations     VARCHAR[] DEFAULT '{}',
    min_project_value   FLOAT,
    max_project_value   FLOAT,
    alerts_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_client_profiles_clerk_user_id ON client_profiles (clerk_user_id);
CREATE INDEX IF NOT EXISTS ix_client_profiles_company_id ON client_profiles (company_id);
CREATE INDEX IF NOT EXISTS ix_client_profiles_email ON client_profiles (email);
CREATE INDEX IF NOT EXISTS ix_client_profiles_alerts_enabled ON client_profiles (alerts_enabled);

-- Test client profile (company_id=1)
INSERT INTO client_profiles (
    clerk_user_id, company_id, company_name, email, regions, specializations, alerts_enabled
)
SELECT
    '',
    1,
    COALESCE(c.name, 'Test Company'),
    'test@tenderscope.ca',
    ARRAY['Vancouver', 'Burnaby', 'Surrey']::varchar[],
    ARRAY[]::varchar[],
    TRUE
FROM companies c
WHERE c.id = 1
  AND NOT EXISTS (
      SELECT 1 FROM client_profiles WHERE email = 'test@tenderscope.ca'
  );
