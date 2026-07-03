-- Migration 015: probable_person entity_role + analytics exclusion support

ALTER TABLE companies DROP CONSTRAINT IF EXISTS ck_companies_entity_role;
ALTER TABLE companies ADD CONSTRAINT ck_companies_entity_role
    CHECK (entity_role IN (
        'canonical', 'applicant_alias', 'standalone', 'probable_person'
    ));
