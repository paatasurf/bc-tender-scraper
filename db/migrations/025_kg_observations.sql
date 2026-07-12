-- Migration 025: Self-Improving KG Phase 1 — Observation spine (additive only)
-- Append-only observation store + transactional outbox skeleton.
-- No consumers in P1. No changes to companies / permits / registry semantics.

CREATE TABLE IF NOT EXISTS kg_observations (
    id                  BIGSERIAL PRIMARY KEY,
    source              VARCHAR(80) NOT NULL,
    external_id         VARCHAR(200) NOT NULL,
    content_hash        CHAR(64) NOT NULL,
    observed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status              VARCHAR(20) NOT NULL DEFAULT 'active',
    raw_payload         JSONB NOT NULL,
    schema_version      VARCHAR(20) NOT NULL DEFAULT '1',
    adapter_version     VARCHAR(20) NOT NULL DEFAULT '1',
    entity_type         VARCHAR(40) NOT NULL DEFAULT '',
    superseded_by_id    BIGINT NULL REFERENCES kg_observations(id),
    CONSTRAINT ck_kg_observations_status
        CHECK (status IN ('active', 'superseded', 'quarantined', 'needs_normalize'))
);

-- Idempotent re-ingest of identical payload
CREATE UNIQUE INDEX IF NOT EXISTS uq_kg_observations_idempotency
    ON kg_observations (source, external_id, content_hash);

-- At most one active observation per source external key
CREATE UNIQUE INDEX IF NOT EXISTS uq_kg_observations_active_external
    ON kg_observations (source, external_id)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS ix_kg_observations_source_status
    ON kg_observations (source, status);

CREATE INDEX IF NOT EXISTS ix_kg_observations_entity_type
    ON kg_observations (entity_type);

CREATE INDEX IF NOT EXISTS ix_kg_observations_ingested_at
    ON kg_observations (ingested_at DESC);

-- Outbox skeleton (no consumers in P1)
CREATE TABLE IF NOT EXISTS kg_outbox_events (
    id                  BIGSERIAL PRIMARY KEY,
    event_type          VARCHAR(80) NOT NULL,
    aggregate_type      VARCHAR(40) NOT NULL DEFAULT 'observation',
    aggregate_id        BIGINT NOT NULL,
    payload             JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at        TIMESTAMPTZ NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'pending',
    CONSTRAINT ck_kg_outbox_events_status
        CHECK (status IN ('pending', 'processed', 'dead_letter'))
);

CREATE INDEX IF NOT EXISTS ix_kg_outbox_events_pending
    ON kg_outbox_events (status, created_at)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS ix_kg_outbox_events_aggregate
    ON kg_outbox_events (aggregate_type, aggregate_id);
