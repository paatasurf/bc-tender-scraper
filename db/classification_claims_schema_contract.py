"""Single source of truth for the Classification Claims schema contract
(migration 029) — the exact columns, primary key, unique constraints, CHECK
constraints (name AND expression), indexes (name, uniqueness, ordered
columns/expressions, partial predicate), and foreign keys (source column AND
exact referenced table/column) expected for all six tables.

Used by both ``db.classification_claims_migration`` (the "is this already
fully and correctly applied" gate before ``--apply`` reports "Already
applied") and ``scripts/run_classification_claims_schema_audit.py`` (the
Class A audit), so the two checks can never silently drift apart from each
other or from ``db/migrations/029_classification_claims.sql``.

A matching *name* is never treated as sufficient on its own for a CHECK
constraint, an index, or a foreign key — the actual definition (expression,
structure, or referenced column) is introspected via PostgreSQL's own
canonicalizing catalog functions (``pg_get_constraintdef``,
``pg_get_indexdef``, ``pg_get_expr``) and compared against the expected
value. PostgreSQL's deparser already normalizes formatting/casts
deterministically for a given parsed expression tree; ``_normalize_sql_text``
adds a light, deterministic whitespace/case normalization on top so the
comparison is robust to incidental differences without weakening it.

Read-only introspection only — no DDL, no writes.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text


def _normalize_sql_text(raw: str | None) -> str | None:
    """Deterministic normalization for comparing PostgreSQL-deparsed SQL
    fragments (CHECK expressions, index predicates): trim, collapse all
    whitespace runs to a single space, lowercase. PostgreSQL's own deparser
    (pg_get_constraintdef/pg_get_indexdef/pg_get_expr) already canonicalizes
    casts, parenthesization, and literal formatting for a given expression
    tree, so this normalization only needs to absorb incidental whitespace/
    case differences, not do any SQL-aware rewriting."""
    if raw is None:
        return None
    return " ".join(raw.strip().lower().split())


@dataclass(frozen=True)
class ColumnContract:
    name: str
    data_type: str  # exact information_schema.columns.data_type string
    is_nullable: bool


@dataclass(frozen=True)
class ForeignKeyContract:
    source_column: str
    referenced_table: str
    referenced_column: str


@dataclass(frozen=True)
class CheckConstraintContract:
    name: str
    expression: (
        str  # expected pg_get_constraintdef() output; compared via _normalize_sql_text
    )


@dataclass(frozen=True)
class IndexContract:
    name: str
    is_unique: bool
    columns: tuple[
        str, ...
    ]  # ordered column names/expressions, as pg_get_indexdef(oid, k, true) renders each
    where_predicate: str | None = (
        None  # expected partial-index predicate (pg_get_expr output); None if not partial
    )


@dataclass(frozen=True)
class TableContract:
    table_name: str
    columns: tuple[ColumnContract, ...]
    primary_key_columns: tuple[str, ...]
    unique_constraints: tuple[
        tuple[str, ...], ...
    ]  # each entry: the column-set of one UNIQUE table constraint
    check_constraints: tuple[CheckConstraintContract, ...]
    indexes: tuple[
        IndexContract, ...
    ]  # explicitly created indexes only (excludes PK/UNIQUE-constraint-backing indexes)
    foreign_keys: tuple[ForeignKeyContract, ...]


RULE_SET_VERSIONS = TableContract(
    table_name="rule_set_versions",
    columns=(
        ColumnContract("rule_set_version_id", "character varying", False),
        ColumnContract("claim_type", "character varying", False),
        ColumnContract("description", "text", False),
        ColumnContract("precedence_definition_json", "jsonb", False),
        ColumnContract("source_reliability_defaults_json", "jsonb", False),
        ColumnContract("staleness_policy_json", "jsonb", False),
        ColumnContract("effective_from", "timestamp with time zone", False),
        ColumnContract("created_at", "timestamp with time zone", False),
    ),
    primary_key_columns=("rule_set_version_id",),
    unique_constraints=(("claim_type", "effective_from"),),
    check_constraints=(
        CheckConstraintContract(
            "ck_rule_set_versions_claim_type",
            "CHECK (((claim_type)::text = ANY ((ARRAY['sector_classification'::character varying, "
            "'licence_registration'::character varying])::text[])))",
        ),
        CheckConstraintContract(
            "ck_rule_set_versions_precedence_not_empty",
            "CHECK ((precedence_definition_json <> '{}'::jsonb))",
        ),
    ),
    indexes=(),
    foreign_keys=(),
)

CLASSIFICATION_CLAIMS = TableContract(
    table_name="classification_claims",
    columns=(
        ColumnContract("claim_id", "uuid", False),
        ColumnContract("company_id", "integer", False),
        ColumnContract("claim_type", "character varying", False),
        ColumnContract("predicate", "character varying", False),
        ColumnContract("value_json", "jsonb", False),
        ColumnContract("source_type", "character varying", False),
        ColumnContract("source_reliability", "double precision", False),
        ColumnContract("extraction_confidence", "double precision", False),
        ColumnContract("extraction_method", "character varying", False),
        ColumnContract("rule_set_version_id", "character varying", False),
        ColumnContract("primary_evidence_content_hash", "character varying", False),
        ColumnContract("observed_at", "timestamp with time zone", False),
        ColumnContract("effective_at", "timestamp with time zone", False),
        ColumnContract("extracted_at", "timestamp with time zone", False),
        ColumnContract("idempotency_key", "character varying", False),
        ColumnContract("created_at", "timestamp with time zone", False),
    ),
    primary_key_columns=("claim_id",),
    unique_constraints=(("idempotency_key",),),
    check_constraints=(
        CheckConstraintContract(
            "ck_classification_claims_claim_type",
            "CHECK (((claim_type)::text = ANY ((ARRAY['sector_classification'::character varying, "
            "'licence_registration'::character varying])::text[])))",
        ),
        CheckConstraintContract(
            "ck_classification_claims_source_type",
            "CHECK (((source_type)::text = ANY ((ARRAY['government_registry'::character varying, "
            "'licence_authority'::character varying, 'association_directory'::character varying, "
            "'official_website'::character varying, 'google_business_profile'::character varying, "
            "'activity_derived'::character varying, 'ai_inference'::character varying])::text[])))",
        ),
        CheckConstraintContract(
            "ck_classification_claims_source_reliability_range",
            "CHECK (((source_reliability >= (0)::double precision) AND "
            "(source_reliability <= (1)::double precision)))",
        ),
        CheckConstraintContract(
            "ck_classification_claims_extraction_confidence_range",
            "CHECK (((extraction_confidence >= (0)::double precision) AND "
            "(extraction_confidence <= (1)::double precision)))",
        ),
        CheckConstraintContract(
            "ck_classification_claims_extraction_method_not_empty",
            "CHECK (((extraction_method)::text <> ''::text))",
        ),
        CheckConstraintContract(
            "ck_classification_claims_primary_evidence_hash_format",
            "CHECK (((primary_evidence_content_hash)::text ~ '^[0-9a-f]{64}$'::text))",
        ),
        CheckConstraintContract(
            "ck_classification_claims_idempotency_key_format",
            "CHECK (((idempotency_key)::text ~ '^[0-9a-f]{64}$'::text))",
        ),
        CheckConstraintContract(
            "ck_claim_type_predicate",
            "CHECK (((((claim_type)::text = 'sector_classification'::text) AND "
            "((predicate)::text = ANY ((ARRAY['dominant_sector'::character varying, "
            "'primary_trade'::character varying])::text[]))) OR (((claim_type)::text = "
            "'licence_registration'::text) AND ((predicate)::text = ANY "
            "((ARRAY['licence_identifier'::character varying, "
            "'business_number'::character varying])::text[])))))",
        ),
        CheckConstraintContract(
            "ck_effective_at_not_before_observed",
            "CHECK ((effective_at >= observed_at))",
        ),
    ),
    indexes=(
        IndexContract(
            "ix_classification_claims_resolution_key",
            is_unique=False,
            columns=("company_id", "claim_type", "predicate", "effective_at"),
        ),
        IndexContract(
            "ix_classification_claims_rule_set",
            is_unique=False,
            columns=("rule_set_version_id",),
        ),
    ),
    foreign_keys=(
        ForeignKeyContract("company_id", "companies", "id"),
        ForeignKeyContract(
            "rule_set_version_id", "rule_set_versions", "rule_set_version_id"
        ),
    ),
)

CLAIM_EVIDENCE = TableContract(
    table_name="claim_evidence",
    columns=(
        ColumnContract("claim_evidence_id", "uuid", False),
        ColumnContract("claim_id", "uuid", False),
        ColumnContract("evidence_source", "character varying", False),
        ColumnContract("evidence_locator", "jsonb", False),
        ColumnContract("content_hash", "character varying", False),
        ColumnContract("created_at", "timestamp with time zone", False),
    ),
    primary_key_columns=("claim_evidence_id",),
    unique_constraints=(("claim_id", "evidence_source", "content_hash"),),
    check_constraints=(
        CheckConstraintContract(
            "ck_claim_evidence_source",
            "CHECK (((evidence_source)::text = ANY ((ARRAY['kg_observation'::character varying, "
            "'permit'::character varying, 'contract_award'::character varying, "
            "'tender_outcome'::character varying, 'licence_authority_raw'::character varying, "
            "'government_registry_raw'::character varying, "
            "'external_url'::character varying])::text[])))",
        ),
        CheckConstraintContract(
            "ck_claim_evidence_content_hash_format",
            "CHECK (((content_hash)::text ~ '^[0-9a-f]{64}$'::text))",
        ),
    ),
    indexes=(
        IndexContract(
            "ix_claim_evidence_claim", is_unique=False, columns=("claim_id",)
        ),
    ),
    foreign_keys=(ForeignKeyContract("claim_id", "classification_claims", "claim_id"),),
)

CLAIM_EVENTS = TableContract(
    table_name="claim_events",
    columns=(
        ColumnContract("event_id", "uuid", False),
        ColumnContract("claim_id", "uuid", False),
        ColumnContract("event_type", "character varying", False),
        ColumnContract("related_claim_id", "uuid", True),
        ColumnContract("actor_type", "character varying", False),
        ColumnContract("actor_id", "character varying", False),
        ColumnContract("rationale", "text", True),
        ColumnContract("rule_set_version_id", "character varying", False),
        ColumnContract("event_at", "timestamp with time zone", False),
        ColumnContract("created_at", "timestamp with time zone", False),
    ),
    primary_key_columns=("event_id",),
    unique_constraints=(),
    check_constraints=(
        CheckConstraintContract(
            "ck_claim_events_event_type",
            "CHECK (((event_type)::text = ANY ((ARRAY['superseded'::character varying, "
            "'rejected'::character varying, 'adjudicated'::character varying])::text[])))",
        ),
        CheckConstraintContract(
            "ck_claim_events_actor_type",
            "CHECK (((actor_type)::text = ANY ((ARRAY['system'::character varying, "
            "'human'::character varying])::text[])))",
        ),
        CheckConstraintContract(
            "ck_claim_events_actor_id_not_empty",
            "CHECK (((actor_id)::text <> ''::text))",
        ),
        CheckConstraintContract(
            "ck_adjudicated_requires_human",
            "CHECK ((((event_type)::text <> 'adjudicated'::text) OR "
            "((actor_type)::text = 'human'::text)))",
        ),
        CheckConstraintContract(
            "ck_related_claim_required_for_superseded",
            "CHECK ((((event_type)::text <> 'superseded'::text) OR "
            "(related_claim_id IS NOT NULL)))",
        ),
        CheckConstraintContract(
            "ck_related_claim_only_for_superseded",
            "CHECK ((((event_type)::text = 'superseded'::text) OR (related_claim_id IS NULL)))",
        ),
        CheckConstraintContract(
            "ck_related_claim_differs",
            "CHECK (((related_claim_id IS NULL) OR (related_claim_id <> claim_id)))",
        ),
    ),
    indexes=(
        IndexContract(
            "uq_claim_events_one_per_claim", is_unique=True, columns=("claim_id",)
        ),
        IndexContract(
            "ix_claim_events_claim_ordered",
            is_unique=False,
            columns=("claim_id", "event_at", "event_id"),
        ),
    ),
    foreign_keys=(
        ForeignKeyContract("claim_id", "classification_claims", "claim_id"),
        ForeignKeyContract("related_claim_id", "classification_claims", "claim_id"),
        ForeignKeyContract(
            "rule_set_version_id", "rule_set_versions", "rule_set_version_id"
        ),
    ),
)

PROJECTOR_RUNS = TableContract(
    table_name="projector_runs",
    columns=(
        ColumnContract("projector_run_id", "uuid", False),
        ColumnContract("resolution_as_of", "timestamp with time zone", False),
        ColumnContract("started_at", "timestamp with time zone", False),
        ColumnContract("finished_at", "timestamp with time zone", False),
        ColumnContract("claim_type", "character varying", False),
        ColumnContract("rule_set_version_id", "character varying", False),
        ColumnContract("companies_processed", "integer", False),
        ColumnContract("beliefs_upserted", "integer", False),
        ColumnContract("beliefs_deleted", "integer", False),
        ColumnContract("dataset_hash", "character varying", False),
    ),
    primary_key_columns=("projector_run_id",),
    unique_constraints=(),
    check_constraints=(
        CheckConstraintContract(
            "ck_projector_runs_claim_type",
            "CHECK (((claim_type)::text = ANY ((ARRAY['sector_classification'::character varying, "
            "'licence_registration'::character varying])::text[])))",
        ),
        CheckConstraintContract(
            "ck_projector_runs_companies_processed_nonneg",
            "CHECK ((companies_processed >= 0))",
        ),
        CheckConstraintContract(
            "ck_projector_runs_beliefs_upserted_nonneg",
            "CHECK ((beliefs_upserted >= 0))",
        ),
        CheckConstraintContract(
            "ck_projector_runs_beliefs_deleted_nonneg",
            "CHECK ((beliefs_deleted >= 0))",
        ),
        CheckConstraintContract(
            "ck_projector_runs_dataset_hash_format",
            "CHECK (((dataset_hash)::text ~ '^[0-9a-f]{64}$'::text))",
        ),
        CheckConstraintContract(
            "ck_projector_runs_finished_after_started",
            "CHECK ((finished_at >= started_at))",
        ),
    ),
    indexes=(
        IndexContract(
            "ix_projector_runs_claim_type",
            is_unique=False,
            columns=("claim_type", "finished_at"),
        ),
    ),
    foreign_keys=(
        ForeignKeyContract(
            "rule_set_version_id", "rule_set_versions", "rule_set_version_id"
        ),
    ),
)

RESOLVED_COMPANY_BELIEFS = TableContract(
    table_name="resolved_company_beliefs",
    columns=(
        ColumnContract("company_id", "integer", False),
        ColumnContract("claim_type", "character varying", False),
        ColumnContract("predicate", "character varying", False),
        ColumnContract("resolved_value_json", "jsonb", False),
        ColumnContract("winning_claim_id", "uuid", False),
        ColumnContract("source_type", "character varying", False),
        ColumnContract("source_reliability", "double precision", False),
        ColumnContract("extraction_confidence", "double precision", False),
        ColumnContract("resolution_confidence", "double precision", False),
        ColumnContract("resolution_status", "character varying", False),
        ColumnContract("competing_claim_count", "integer", False),
        ColumnContract("resolution_as_of", "timestamp with time zone", False),
        ColumnContract("projector_run_id", "uuid", False),
        ColumnContract("rule_set_version_id", "character varying", False),
    ),
    primary_key_columns=("company_id", "claim_type", "predicate"),
    unique_constraints=(),
    check_constraints=(
        CheckConstraintContract(
            "ck_resolved_beliefs_claim_type",
            "CHECK (((claim_type)::text = ANY ((ARRAY['sector_classification'::character varying, "
            "'licence_registration'::character varying])::text[])))",
        ),
        CheckConstraintContract(
            "ck_resolved_beliefs_source_reliability_range",
            "CHECK (((source_reliability >= (0)::double precision) AND "
            "(source_reliability <= (1)::double precision)))",
        ),
        CheckConstraintContract(
            "ck_resolved_beliefs_extraction_confidence_range",
            "CHECK (((extraction_confidence >= (0)::double precision) AND "
            "(extraction_confidence <= (1)::double precision)))",
        ),
        CheckConstraintContract(
            "ck_resolved_beliefs_resolution_confidence_range",
            "CHECK (((resolution_confidence >= (0)::double precision) AND "
            "(resolution_confidence <= (1)::double precision)))",
        ),
        CheckConstraintContract(
            "ck_resolved_beliefs_resolution_status",
            "CHECK (((resolution_status)::text = ANY ((ARRAY['resolved'::character varying, "
            "'disputed'::character varying, 'stale'::character varying, "
            "'adjudicated'::character varying])::text[])))",
        ),
        CheckConstraintContract(
            "ck_resolved_beliefs_competing_claim_count_nonneg",
            "CHECK ((competing_claim_count >= 0))",
        ),
        CheckConstraintContract(
            "ck_resolved_beliefs_claim_type_predicate",
            "CHECK (((((claim_type)::text = 'sector_classification'::text) AND "
            "((predicate)::text = ANY ((ARRAY['dominant_sector'::character varying, "
            "'primary_trade'::character varying])::text[]))) OR (((claim_type)::text = "
            "'licence_registration'::text) AND ((predicate)::text = ANY "
            "((ARRAY['licence_identifier'::character varying, "
            "'business_number'::character varying])::text[])))))",
        ),
    ),
    indexes=(
        IndexContract(
            "ix_resolved_company_beliefs_projector_run",
            is_unique=False,
            columns=("projector_run_id",),
        ),
    ),
    foreign_keys=(
        ForeignKeyContract("company_id", "companies", "id"),
        ForeignKeyContract("winning_claim_id", "classification_claims", "claim_id"),
        ForeignKeyContract("projector_run_id", "projector_runs", "projector_run_id"),
        ForeignKeyContract(
            "rule_set_version_id", "rule_set_versions", "rule_set_version_id"
        ),
    ),
)

# Creation-dependency order — mirrors 029_classification_claims.sql exactly.
ALL_TABLE_CONTRACTS: tuple[TableContract, ...] = (
    RULE_SET_VERSIONS,
    CLASSIFICATION_CLAIMS,
    CLAIM_EVIDENCE,
    CLAIM_EVENTS,
    PROJECTOR_RUNS,
    RESOLVED_COMPANY_BELIEFS,
)


@dataclass(frozen=True)
class TableConformance:
    exists: bool
    missing_columns: tuple[str, ...] = ()
    wrong_type_columns: tuple[str, ...] = ()
    wrong_nullability_columns: tuple[str, ...] = ()
    missing_primary_key: bool = False
    missing_unique_constraints: tuple[tuple[str, ...], ...] = ()
    missing_check_constraints: tuple[str, ...] = ()
    wrong_check_constraints: tuple[
        str, ...
    ] = ()  # name exists, expression does not match
    missing_indexes: tuple[str, ...] = ()
    wrong_indexes: tuple[
        str, ...
    ] = ()  # name exists, uniqueness/columns/predicate does not match
    missing_foreign_keys: tuple[ForeignKeyContract, ...] = ()

    @property
    def conforms(self) -> bool:
        return (
            self.exists
            and not self.missing_columns
            and not self.wrong_type_columns
            and not self.wrong_nullability_columns
            and not self.missing_primary_key
            and not self.missing_unique_constraints
            and not self.missing_check_constraints
            and not self.wrong_check_constraints
            and not self.missing_indexes
            and not self.wrong_indexes
            and not self.missing_foreign_keys
        )


@dataclass(frozen=True)
class SchemaConformanceResult:
    tables: dict[str, TableConformance]

    @property
    def any_table_exists(self) -> bool:
        return any(t.exists for t in self.tables.values())

    @property
    def all_tables_exist(self) -> bool:
        return all(t.exists for t in self.tables.values())

    @property
    def fully_conforms(self) -> bool:
        return all(t.conforms for t in self.tables.values())

    def describe_violations(self) -> list[str]:
        lines: list[str] = []
        for name, tc in self.tables.items():
            if not tc.exists:
                lines.append(f"{name}: table does not exist")
                continue
            if tc.missing_columns:
                lines.append(f"{name}: missing columns {list(tc.missing_columns)}")
            if tc.wrong_type_columns:
                lines.append(
                    f"{name}: columns with wrong data type {list(tc.wrong_type_columns)}"
                )
            if tc.wrong_nullability_columns:
                lines.append(
                    f"{name}: columns with wrong nullability {list(tc.wrong_nullability_columns)}"
                )
            if tc.missing_primary_key:
                lines.append(f"{name}: missing or incorrect primary key")
            if tc.missing_unique_constraints:
                lines.append(
                    f"{name}: missing unique constraints on {list(tc.missing_unique_constraints)}"
                )
            if tc.missing_check_constraints:
                lines.append(
                    f"{name}: missing CHECK constraints {list(tc.missing_check_constraints)}"
                )
            if tc.wrong_check_constraints:
                lines.append(
                    f"{name}: CHECK constraints present but with a different expression than "
                    f"expected {list(tc.wrong_check_constraints)}"
                )
            if tc.missing_indexes:
                lines.append(f"{name}: missing indexes {list(tc.missing_indexes)}")
            if tc.wrong_indexes:
                lines.append(
                    f"{name}: indexes present but with a different definition (uniqueness, "
                    f"columns, or partial predicate) than expected {list(tc.wrong_indexes)}"
                )
            if tc.missing_foreign_keys:
                lines.append(
                    f"{name}: missing foreign keys {list(tc.missing_foreign_keys)}"
                )
        return lines


def _verify_table(conn, table: TableContract) -> TableConformance:
    exists = (
        conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": table.table_name},
        ).first()
        is not None
    )
    if not exists:
        return TableConformance(
            exists=False, missing_columns=tuple(c.name for c in table.columns)
        )

    col_rows = conn.execute(
        text("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :t
            """),
        {"t": table.table_name},
    ).all()
    actual_cols = {r[0]: (r[1], r[2] == "YES") for r in col_rows}
    missing_columns = tuple(c.name for c in table.columns if c.name not in actual_cols)
    wrong_type = tuple(
        c.name
        for c in table.columns
        if c.name in actual_cols and actual_cols[c.name][0] != c.data_type
    )
    wrong_null = tuple(
        c.name
        for c in table.columns
        if c.name in actual_cols and actual_cols[c.name][1] != c.is_nullable
    )

    pk_rows = conn.execute(
        text("""
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = 'public' AND tc.table_name = :t AND tc.constraint_type = 'PRIMARY KEY'
            ORDER BY kcu.ordinal_position
            """),
        {"t": table.table_name},
    ).all()
    actual_pk = tuple(r[0] for r in pk_rows)
    missing_primary_key = actual_pk != table.primary_key_columns

    uq_rows = conn.execute(
        text("""
            SELECT tc.constraint_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = 'public' AND tc.table_name = :t AND tc.constraint_type = 'UNIQUE'
            ORDER BY tc.constraint_name, kcu.ordinal_position
            """),
        {"t": table.table_name},
    ).all()
    uq_groups: dict[str, list[str]] = {}
    for cname, colname in uq_rows:
        uq_groups.setdefault(cname, []).append(colname)
    actual_unique_sets = {tuple(cols) for cols in uq_groups.values()}
    missing_unique = tuple(
        cols for cols in table.unique_constraints if cols not in actual_unique_sets
    )

    # CHECK constraints: name AND expression. pg_get_constraintdef() is
    # PostgreSQL's own canonicalizing deparser -- it already normalizes
    # casts/parens/literal formatting for a given expression tree, so a
    # same-name-different-logic CHECK is caught even though the constraint
    # name matches.
    check_rows = conn.execute(
        text("""
            SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
            WHERE conrelid = (:t)::regclass AND contype = 'c'
            """),
        {"t": table.table_name},
    ).all()
    actual_checks = {r[0]: r[1] for r in check_rows}
    missing_checks = tuple(
        c.name for c in table.check_constraints if c.name not in actual_checks
    )
    wrong_checks = tuple(
        c.name
        for c in table.check_constraints
        if c.name in actual_checks
        and _normalize_sql_text(actual_checks[c.name])
        != _normalize_sql_text(c.expression)
    )

    # Indexes: name, uniqueness, ordered columns/expressions, and partial
    # predicate. Only indexes explicitly named in table.indexes are checked
    # here -- PK-backing and UNIQUE-constraint-backing indexes are already
    # covered above via primary_key_columns / unique_constraints.
    idx_rows = conn.execute(
        text("""
            SELECT
                ic.relname AS index_name,
                ix.indisunique AS is_unique,
                pg_get_expr(ix.indpred, ix.indrelid) AS where_predicate,
                (
                    SELECT array_agg(pg_get_indexdef(ix.indexrelid, gs.k, true) ORDER BY gs.k)
                    FROM generate_series(1, ix.indnkeyatts) AS gs(k)
                ) AS columns
            FROM pg_index ix
            JOIN pg_class ic ON ic.oid = ix.indexrelid
            WHERE ix.indrelid = (:t)::regclass
            """),
        {"t": table.table_name},
    ).all()
    actual_indexes = {
        r[0]: (
            bool(r[1]),
            _normalize_sql_text(r[2]),
            tuple(_normalize_sql_text(c) for c in (r[3] or ())),
        )
        for r in idx_rows
    }
    missing_indexes = tuple(
        idx.name for idx in table.indexes if idx.name not in actual_indexes
    )
    wrong_indexes = tuple(
        idx.name
        for idx in table.indexes
        if idx.name in actual_indexes
        and actual_indexes[idx.name]
        != (
            idx.is_unique,
            _normalize_sql_text(idx.where_predicate),
            tuple(_normalize_sql_text(c) for c in idx.columns),
        )
    )

    fk_rows = conn.execute(
        text("""
            SELECT kcu.column_name AS source_column,
                   ccu.table_name AS referenced_table,
                   ccu.column_name AS referenced_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
                ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
            WHERE tc.table_schema = 'public' AND tc.table_name = :t AND tc.constraint_type = 'FOREIGN KEY'
            """),
        {"t": table.table_name},
    ).all()
    actual_fks = {(r[0], r[1], r[2]) for r in fk_rows}
    missing_fks = tuple(
        fk
        for fk in table.foreign_keys
        if (fk.source_column, fk.referenced_table, fk.referenced_column)
        not in actual_fks
    )

    return TableConformance(
        exists=True,
        missing_columns=missing_columns,
        wrong_type_columns=wrong_type,
        wrong_nullability_columns=wrong_null,
        missing_primary_key=missing_primary_key,
        missing_unique_constraints=missing_unique,
        missing_check_constraints=missing_checks,
        wrong_check_constraints=wrong_checks,
        missing_indexes=missing_indexes,
        wrong_indexes=wrong_indexes,
        missing_foreign_keys=missing_fks,
    )


def verify_schema_contract(conn) -> SchemaConformanceResult:
    """Read-only. Accepts a SQLAlchemy Connection or Session — anything with
    an ``.execute()`` compatible with ``text()`` statements."""
    return SchemaConformanceResult(
        tables={
            table.table_name: _verify_table(conn, table)
            for table in ALL_TABLE_CONTRACTS
        }
    )
