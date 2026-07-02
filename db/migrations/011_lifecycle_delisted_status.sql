-- Migration 011: add 'delisted' to lifecycle_status vocabulary (P2-02)

ALTER TABLE tenders DROP CONSTRAINT IF EXISTS ck_tenders_lifecycle_status;
ALTER TABLE tenders ADD CONSTRAINT ck_tenders_lifecycle_status
    CHECK (lifecycle_status IN (
        'new', 'active', 'closing_soon', 'closed', 'awarded',
        'cancelled', 'outcome_unknown', 'archived', 'delisted'
    ));

ALTER TABLE commercial_tenders DROP CONSTRAINT IF EXISTS ck_commercial_tenders_lifecycle_status;
ALTER TABLE commercial_tenders ADD CONSTRAINT ck_commercial_tenders_lifecycle_status
    CHECK (lifecycle_status IN (
        'new', 'active', 'closing_soon', 'closed', 'awarded',
        'cancelled', 'outcome_unknown', 'archived', 'delisted'
    ));

ALTER TABLE arch_tenders DROP CONSTRAINT IF EXISTS ck_arch_tenders_lifecycle_status;
ALTER TABLE arch_tenders ADD CONSTRAINT ck_arch_tenders_lifecycle_status
    CHECK (lifecycle_status IN (
        'new', 'active', 'closing_soon', 'closed', 'awarded',
        'cancelled', 'outcome_unknown', 'archived', 'delisted'
    ));
