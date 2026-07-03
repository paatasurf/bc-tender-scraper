"""Unit tests for deterministic company canonical merge."""

from __future__ import annotations

from pipeline.company_canonical_merge import (
    MergeGroup,
    MergeGroupMember,
    _apply_forced_canonical,
    _choose_primary,
    _pick_display_name,
    build_merge_plan,
    classify_merge_group,
    is_probable_person_name,
    partition_merge_groups,
    resolve_company_name,
)
from pipeline.company_canonical_merge import MergePlan


def test_resolve_company_name_dba_extracts_trade_name():
    resolved = resolve_company_name("John Smith DBA Python Group")
    assert resolved is not None
    assert resolved.display_name == "Python Group"
    assert resolved.signatory == "John Smith"
    assert resolved.has_dba is True
    assert resolved.confidence == 1.0
    assert resolved.method == "dba_name"


def test_resolve_company_name_legal_only():
    resolved = resolve_company_name("Python Group Ltd.")
    assert resolved is not None
    assert resolved.display_name == "Python Group Ltd."
    assert resolved.has_dba is False
    assert resolved.confidence == 1.0


def test_same_dba_produces_same_canonical_key():
    a = resolve_company_name("John Smith DBA Python Group")
    b = resolve_company_name("Michael Brown DBA Python Group")
    assert a is not None and b is not None
    assert a.canonical_key == b.canonical_key
    assert a.display_name == b.display_name == "Python Group"


def test_pick_display_name_prefers_dba_spelling():
    members = [
        MergeGroupMember(
            company_id=1,
            name="John Smith DBA Python Group",
            signatory="John Smith",
            has_dba=True,
            total_projects=10,
            total_value=100.0,
            total_award_value=0.0,
        ),
        MergeGroupMember(
            company_id=2,
            name="Michael Brown DBA Python Group",
            signatory="Michael Brown",
            has_dba=True,
            total_projects=5,
            total_value=50.0,
            total_award_value=0.0,
        ),
    ]
    assert _pick_display_name(members) == "Python Group"


def test_choose_primary_creates_row_when_names_are_all_dba():
    members = [
        MergeGroupMember(
            company_id=10,
            name="John Smith DBA Python Group",
            signatory="John Smith",
            has_dba=True,
            total_projects=100,
            total_value=1_000_000.0,
            total_award_value=0.0,
        ),
        MergeGroupMember(
            company_id=11,
            name="Michael Brown DBA Python Group",
            signatory="Michael Brown",
            has_dba=True,
            total_projects=1,
            total_value=1.0,
            total_award_value=0.0,
        ),
    ]
    primary_id, create_row, insert_name = _choose_primary(members, "Python Group")
    assert primary_id == 10
    assert create_row is True
    assert insert_name == "Python Group"


def test_choose_primary_uses_exact_name_match():
    members = [
        MergeGroupMember(
            company_id=20,
            name="Python Group",
            signatory="",
            has_dba=False,
            total_projects=1,
            total_value=1.0,
            total_award_value=0.0,
        ),
        MergeGroupMember(
            company_id=21,
            name="John Smith DBA Python Group",
            signatory="John Smith",
            has_dba=True,
            total_projects=99,
            total_value=9_000_000.0,
            total_award_value=0.0,
        ),
    ]
    primary_id, create_row, _ = _choose_primary(members, "Python Group")
    assert primary_id == 20
    assert create_row is False


def test_merge_plan_summary_counts(monkeypatch):
    from pipeline import company_canonical_merge as merge_mod

    companies = [
        merge_mod.CompanyRecord(1, "John Smith DBA Python Group", 10, 100.0, 0.0, 0),
        merge_mod.CompanyRecord(2, "Michael Brown DBA Python Group", 5, 50.0, 0.0, 0),
        merge_mod.CompanyRecord(3, "Standalone Builder Inc", 1, 1.0, 0.0, 0),
    ]

    class _PermitRows:
        def yield_per(self, _n):
            return [
                (101, "John Smith DBA Python Group", ""),
                (102, "Michael Brown DBA Python Group", ""),
                (103, "Standalone Builder Inc", ""),
            ]

    class _SessionStub:
        def execute(self, query):
            sql = str(query)
            if "information_schema" in sql.lower():
                class _Info:
                    def first(self):
                        return None
                return _Info()
            if "count(" in sql.lower():
                class _Scalar:
                    def scalar_one(self):
                        return 0
                return _Scalar()
            if "permits.id" in sql.lower() or "from permits" in sql.lower():
                return _PermitRows()
            return companies

        def scalar(self):
            return 0

    monkeypatch.setattr(merge_mod, "_load_companies", lambda _session: companies)
    plan = build_merge_plan(_SessionStub())
    assert plan.summary["merge_groups"] == 1
    assert plan.summary["companies_to_mark_alias"] == 1
    assert plan.summary["permits_to_assign"] == 3
    assert plan.summary["safe_auto_merge"]["merge_groups"] == 1
    assert plan.summary["safe_auto_merge"]["permits_to_assign"] == 2
    assert plan.summary["excluded_from_apply"]["non_dba_groups_total"] == 0
    assert isinstance(plan, MergePlan)


def test_is_probable_person_name():
    assert is_probable_person_name("Michael Yee") is True
    assert is_probable_person_name("Ledcor Construction Limited") is False
    assert is_probable_person_name("Python Group Ltd.") is False


def test_classify_non_dba_person_group():
    group = MergeGroup(
        canonical_key="michaelyee",
        display_name="Michael Yee",
        confidence=1.0,
        method="normalized_key",
        members=[
            MergeGroupMember(
                company_id=1,
                name="Michael Yee",
                signatory="",
                has_dba=False,
                total_projects=1,
                total_value=1.0,
                total_award_value=0.0,
            ),
            MergeGroupMember(
                company_id=2,
                name="MICHAEL YEE",
                signatory="",
                has_dba=False,
                total_projects=1,
                total_value=1.0,
                total_award_value=0.0,
            ),
        ],
    )
    assert classify_merge_group(group) == "excluded_probable_person"


def test_forced_canonical_preserves_pontem_id():
    group = MergeGroup(
        canonical_key="pontem",
        display_name="Pontem Group",
        confidence=1.0,
        method="dba_name",
        members=[
            MergeGroupMember(
                company_id=8638,
                name="Jack Hui DBA: Pontem Group",
                signatory="Jack Hui",
                has_dba=True,
                total_projects=78,
                total_value=1.0,
                total_award_value=0.0,
            ),
        ],
        primary_company_id=8638,
        create_canonical_row=True,
        canonical_name_for_insert="Pontem Group",
    )
    _apply_forced_canonical(group)
    assert group.primary_company_id == 8638
    assert group.create_canonical_row is False
