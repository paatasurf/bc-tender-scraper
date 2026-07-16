-- Migration 029: Classification Claims schema foundation (PR-B1).
-- Class D — standalone, run via scripts/run_classification_claims_migration.py only.
-- NOT wired into _run_migrations()/init_db() — this schema stays inert until an
-- operator explicitly applies it (Codex decision).
--
-- Six new, purely additive tables. No ALTER of any existing table. No data
-- migration, no seed rows, no backfill. Creation order below is FK-dependency
-- order; db/migrations/029_classification_claims_rollback.sql drops them in
-- the exact reverse order.
--
-- UUID primary keys are generated in Python (uuid.uuid4()) by the future
-- Claims Gateway (PR-B2) — no DEFAULT clause, no pgcrypto/uuid-ossp extension.
-- rule_set_versions.rule_set_version_id is a human-readable string key
-- (e.g. 'sector_classification_v1'), not a UUID.
--
-- Every CHECK constraint is explicitly named (CONSTRAINT ck_... CHECK (...))
-- rather than left for Postgres to auto-name — db/classification_claims_schema_contract.py
-- verifies the deployed schema against this exact set of names, and relying on
-- Postgres's internal auto-naming heuristic for that would be fragile.

-- 1. rule_set_versions — no dependency on the other five tables.
CREATE TABLE IF NOT EXISTS rule_set_versions (
    rule_set_version_id                VARCHAR(40) PRIMARY KEY,
    claim_type                         VARCHAR(40) NOT NULL,
    description                        TEXT NOT NULL DEFAULT '',
    precedence_definition_json         JSONB NOT NULL,
    source_reliability_defaults_json   JSONB NOT NULL,
    staleness_policy_json              JSONB NOT NULL,
    effective_from                     TIMESTAMPTZ NOT NULL,
    created_at                         TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_rule_set_versions_claim_type
        CHECK (claim_type IN ('sector_classification', 'licence_registration')),
    CONSTRAINT ck_rule_set_versions_precedence_not_empty
        CHECK (precedence_definition_json <> '{}'::jsonb),
    CONSTRAINT uq_rule_set_versions_claim_type_effective_from UNIQUE (claim_type, effective_from)
);

-- 2. classification_claims — FK -> companies(id) [existing table], FK -> rule_set_versions.
CREATE TABLE IF NOT EXISTS classification_claims (
    claim_id                       UUID PRIMARY KEY,
    company_id                     INTEGER NOT NULL REFERENCES companies(id),
    claim_type                     VARCHAR(40) NOT NULL,
    predicate                      VARCHAR(60) NOT NULL,
    value_json                     JSONB NOT NULL,
    source_type                    VARCHAR(30) NOT NULL,
    source_reliability             DOUBLE PRECISION NOT NULL,
    extraction_confidence          DOUBLE PRECISION NOT NULL,
    extraction_method               VARCHAR(120) NOT NULL,
    rule_set_version_id             VARCHAR(40) NOT NULL
        REFERENCES rule_set_versions (rule_set_version_id),
    primary_evidence_content_hash   VARCHAR(64) NOT NULL,
    observed_at                     TIMESTAMPTZ NOT NULL,
    effective_at                    TIMESTAMPTZ NOT NULL,
    extracted_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    idempotency_key                 VARCHAR(64) NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_classification_claims_claim_type
        CHECK (claim_type IN ('sector_classification', 'licence_registration')),
    CONSTRAINT ck_classification_claims_source_type
        CHECK (source_type IN ('government_registry', 'licence_authority', 'association_directory',
                                'official_website', 'google_business_profile', 'activity_derived',
                                'ai_inference')),
    CONSTRAINT ck_classification_claims_source_reliability_range
        CHECK (source_reliability >= 0 AND source_reliability <= 1),
    CONSTRAINT ck_classification_claims_extraction_confidence_range
        CHECK (extraction_confidence >= 0 AND extraction_confidence <= 1),
    CONSTRAINT ck_classification_claims_extraction_method_not_empty
        CHECK (extraction_method <> ''),
    CONSTRAINT ck_classification_claims_primary_evidence_hash_format
        CHECK (primary_evidence_content_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_classification_claims_idempotency_key_format
        CHECK (idempotency_key ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_claim_type_predicate CHECK (
        (claim_type = 'sector_classification' AND predicate IN ('dominant_sector', 'primary_trade'))
        OR (claim_type = 'licence_registration' AND predicate IN ('licence_identifier', 'business_number'))
    ),
    CONSTRAINT ck_effective_at_not_before_observed CHECK (effective_at >= observed_at),
    CONSTRAINT uq_classification_claims_idempotency UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_classification_claims_resolution_key
    ON classification_claims (company_id, claim_type, predicate, effective_at);
CREATE INDEX IF NOT EXISTS ix_classification_claims_rule_set
    ON classification_claims (rule_set_version_id);

-- 3. claim_evidence — FK -> classification_claims only. No evidence_excerpt in V1.
CREATE TABLE IF NOT EXISTS claim_evidence (
    claim_evidence_id      UUID PRIMARY KEY,
    claim_id                UUID NOT NULL REFERENCES classification_claims (claim_id),
    evidence_source          VARCHAR(30) NOT NULL,
    evidence_locator           JSONB NOT NULL,
    content_hash                VARCHAR(64) NOT NULL,
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_claim_evidence_source
        CHECK (evidence_source IN ('kg_observation', 'permit', 'contract_award', 'tender_outcome',
                                    'licence_authority_raw', 'government_registry_raw', 'external_url')),
    CONSTRAINT ck_claim_evidence_content_hash_format
        CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT uq_claim_evidence UNIQUE (claim_id, evidence_source, content_hash)
);

CREATE INDEX IF NOT EXISTS ix_claim_evidence_claim ON claim_evidence (claim_id);

-- 4. claim_events — FK -> classification_claims (claim_id + related_claim_id), FK -> rule_set_versions.
-- Every event_type in V1 is terminal (no 'reaffirmed') -> at most one event per
-- claim, period, hence a plain (non-partial) UNIQUE index on claim_id.
CREATE TABLE IF NOT EXISTS claim_events (
    event_id                UUID PRIMARY KEY,
    claim_id                 UUID NOT NULL REFERENCES classification_claims (claim_id),
    event_type                VARCHAR(20) NOT NULL,
    related_claim_id            UUID REFERENCES classification_claims (claim_id),
    actor_type                    VARCHAR(20) NOT NULL,
    actor_id                        VARCHAR(120) NOT NULL,
    rationale                         TEXT,
    rule_set_version_id                 VARCHAR(40) NOT NULL
        REFERENCES rule_set_versions (rule_set_version_id),
    event_at                              TIMESTAMPTZ NOT NULL,
    created_at                              TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_claim_events_event_type
        CHECK (event_type IN ('superseded', 'rejected', 'adjudicated')),
    CONSTRAINT ck_claim_events_actor_type
        CHECK (actor_type IN ('system', 'human')),
    CONSTRAINT ck_claim_events_actor_id_not_empty
        CHECK (actor_id <> ''),
    CONSTRAINT ck_adjudicated_requires_human
        CHECK (event_type <> 'adjudicated' OR actor_type = 'human'),
    CONSTRAINT ck_related_claim_required_for_superseded
        CHECK (event_type <> 'superseded' OR related_claim_id IS NOT NULL),
    CONSTRAINT ck_related_claim_only_for_superseded
        CHECK (event_type = 'superseded' OR related_claim_id IS NULL),
    CONSTRAINT ck_related_claim_differs
        CHECK (related_claim_id IS NULL OR related_claim_id <> claim_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_claim_events_one_per_claim ON claim_events (claim_id);
CREATE INDEX IF NOT EXISTS ix_claim_events_claim_ordered ON claim_events (claim_id, event_at, event_id);

-- 5. projector_runs — singular claim_type + singular rule_set_version_id FK
-- (Codex decision: one projector run resolves exactly one claim_type using
-- exactly one rule set; sector and licence resolution are two separate runs).
CREATE TABLE IF NOT EXISTS projector_runs (
    projector_run_id        UUID PRIMARY KEY,
    resolution_as_of          TIMESTAMPTZ NOT NULL,
    started_at                  TIMESTAMPTZ NOT NULL,
    finished_at                   TIMESTAMPTZ NOT NULL,
    claim_type                      VARCHAR(40) NOT NULL,
    rule_set_version_id                VARCHAR(40) NOT NULL
        REFERENCES rule_set_versions (rule_set_version_id),
    companies_processed                  INTEGER NOT NULL,
    beliefs_upserted                       INTEGER NOT NULL,
    beliefs_deleted                          INTEGER NOT NULL,
    dataset_hash                               VARCHAR(64) NOT NULL,

    CONSTRAINT ck_projector_runs_claim_type
        CHECK (claim_type IN ('sector_classification', 'licence_registration')),
    CONSTRAINT ck_projector_runs_companies_processed_nonneg
        CHECK (companies_processed >= 0),
    CONSTRAINT ck_projector_runs_beliefs_upserted_nonneg
        CHECK (beliefs_upserted >= 0),
    CONSTRAINT ck_projector_runs_beliefs_deleted_nonneg
        CHECK (beliefs_deleted >= 0),
    CONSTRAINT ck_projector_runs_dataset_hash_format
        CHECK (dataset_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_projector_runs_finished_after_started
        CHECK (finished_at >= started_at)
);

CREATE INDEX IF NOT EXISTS ix_projector_runs_claim_type ON projector_runs (claim_type, finished_at);

-- 6. resolved_company_beliefs — the one disposable/upsertable table (materialized
-- projection). FK -> classification_claims, FK -> projector_runs, FK -> rule_set_versions.
CREATE TABLE IF NOT EXISTS resolved_company_beliefs (
    company_id                INTEGER NOT NULL REFERENCES companies (id),
    claim_type                  VARCHAR(40) NOT NULL,
    predicate                     VARCHAR(60) NOT NULL,
    resolved_value_json              JSONB NOT NULL,
    winning_claim_id                   UUID NOT NULL REFERENCES classification_claims (claim_id),
    source_type                          VARCHAR(30) NOT NULL,
    source_reliability                     DOUBLE PRECISION NOT NULL,
    extraction_confidence                    DOUBLE PRECISION NOT NULL,
    resolution_confidence                      DOUBLE PRECISION NOT NULL,
    resolution_status                            VARCHAR(20) NOT NULL,
    competing_claim_count                          INTEGER NOT NULL DEFAULT 0,
    resolution_as_of                                 TIMESTAMPTZ NOT NULL,
    projector_run_id                                   UUID NOT NULL
        REFERENCES projector_runs (projector_run_id),
    rule_set_version_id                                  VARCHAR(40) NOT NULL
        REFERENCES rule_set_versions (rule_set_version_id),

    PRIMARY KEY (company_id, claim_type, predicate),
    CONSTRAINT ck_resolved_beliefs_claim_type
        CHECK (claim_type IN ('sector_classification', 'licence_registration')),
    CONSTRAINT ck_resolved_beliefs_source_reliability_range
        CHECK (source_reliability >= 0 AND source_reliability <= 1),
    CONSTRAINT ck_resolved_beliefs_extraction_confidence_range
        CHECK (extraction_confidence >= 0 AND extraction_confidence <= 1),
    CONSTRAINT ck_resolved_beliefs_resolution_confidence_range
        CHECK (resolution_confidence >= 0 AND resolution_confidence <= 1),
    CONSTRAINT ck_resolved_beliefs_resolution_status
        CHECK (resolution_status IN ('resolved', 'disputed', 'stale', 'adjudicated')),
    CONSTRAINT ck_resolved_beliefs_competing_claim_count_nonneg
        CHECK (competing_claim_count >= 0),
    CONSTRAINT ck_resolved_beliefs_claim_type_predicate CHECK (
        (claim_type = 'sector_classification' AND predicate IN ('dominant_sector', 'primary_trade'))
        OR (claim_type = 'licence_registration' AND predicate IN ('licence_identifier', 'business_number'))
    )
);

CREATE INDEX IF NOT EXISTS ix_resolved_company_beliefs_projector_run
    ON resolved_company_beliefs (projector_run_id);
