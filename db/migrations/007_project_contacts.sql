-- Migration 007: project_contacts for Project Intelligence

CREATE TABLE IF NOT EXISTS project_contacts (
    id            SERIAL PRIMARY KEY,
    project_id    INTEGER NOT NULL,
    project_type  VARCHAR(20) NOT NULL,
    role          VARCHAR(20) NOT NULL,
    company_name  VARCHAR(300) NOT NULL DEFAULT '',
    contact_name  VARCHAR(300) NOT NULL DEFAULT '',
    phone         VARCHAR(50) NOT NULL DEFAULT '',
    email         VARCHAR(320) NOT NULL DEFAULT '',
    source        VARCHAR(100) NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_project_contacts_project
    ON project_contacts (project_id, project_type);

CREATE INDEX IF NOT EXISTS ix_project_contacts_role
    ON project_contacts (role);

CREATE INDEX IF NOT EXISTS ix_project_contacts_company_name
    ON project_contacts (company_name)
    WHERE company_name <> '';

CREATE UNIQUE INDEX IF NOT EXISTS ix_project_contacts_project_role
    ON project_contacts (project_id, project_type, role);
