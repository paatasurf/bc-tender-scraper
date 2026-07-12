-- Migration 026: Registry Gateway — Engine decision audit (Phase 2 shadow/enforce)
-- Additive only. No changes to companies semantics until KG_GATEWAY_ENFORCE=1.

CREATE TABLE IF NOT EXISTS kg_engine_decision_records (
    id                  BIGSERIAL PRIMARY KEY,
    decision            VARCHAR(20) NOT NULL,
    source_path         VARCHAR(120) NOT NULL,
    trigger_source      VARCHAR(80) NOT NULL DEFAULT '',
    raw_identity        TEXT NOT NULL DEFAULT '',
    canonical_key       VARCHAR(300) NOT NULL DEFAULT '',
    company_id          INTEGER NULL,
    policy_version      VARCHAR(20) NOT NULL DEFAULT '1',
    gateway_mode        VARCHAR(20) NOT NULL DEFAULT 'legacy',
    legacy_proceeded    BOOLEAN NOT NULL DEFAULT FALSE,
    reject_reason       VARCHAR(80) NOT NULL DEFAULT '',
    metadata_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_kg_engine_decision
        CHECK (decision IN ('match', 'merge', 'create', 'reject', 'review'))
);

CREATE INDEX IF NOT EXISTS ix_kg_engine_decision_created_at
    ON kg_engine_decision_records (created_at DESC);

CREATE INDEX IF NOT EXISTS ix_kg_engine_decision_source_path
    ON kg_engine_decision_records (source_path, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_kg_engine_decision_decision
    ON kg_engine_decision_records (decision, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_kg_engine_decision_company_id
    ON kg_engine_decision_records (company_id)
    WHERE company_id IS NOT NULL;
