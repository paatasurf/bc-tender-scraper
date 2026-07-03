"""Unit tests for company resolution (ingest faucet)."""

from __future__ import annotations

from pipeline.company_resolution import (
    CompanyResolver,
    compute_bc_confidence,
    resolve_company,
    RESOLUTION_STATUS_PERSON_SKIP,
    RESOLUTION_STATUS_REVIEW,
)


def test_dba_pattern_resolves_to_trade_name():
    conf, method = compute_bc_confidence("Python Group", has_dba=True)
    assert conf == 1.0
    assert method == "dba_explicit"


def test_incorporated_bc_confidence():
    conf, method = compute_bc_confidence("Acme Construction Ltd.", city="Vancouver", province="BC")
    assert conf == 1.0
    assert method == "incorporated_bc"


def test_non_incorporated_bc_confidence():
    conf, method = compute_bc_confidence("Acme Builders", city="Vancouver", province="BC")
    assert conf == 0.9
    assert method == "bc_other"


def test_probable_person_skips_company_creation():
    class _ScalarsResult:
        def all(self):
            return []

    class _SessionStub:
        def scalars(self, _q):
            return _ScalarsResult()

        def execute(self, _q):
            class _R:
                def all(self):
                    return []

            return _R()

        def flush(self):
            return None

        def add(self, _obj):
            return None

    resolver = CompanyResolver(_SessionStub())
    resolution = resolver.resolve("Michael Yee", source="permits:test", city="Vancouver")
    assert resolution.status == RESOLUTION_STATUS_PERSON_SKIP
    assert resolution.company_id is None
    assert resolution.method == "probable_person"


def test_conflict_review_does_not_create():
    from types import SimpleNamespace

    row1 = SimpleNamespace(
        id=1, entity_role="standalone", canonical_company_id=None,
        total_value=1.0, total_award_value=0.0, total_projects=1,
    )
    row2 = SimpleNamespace(
        id=2, entity_role="standalone", canonical_company_id=None,
        total_value=0.0, total_award_value=0.0, total_projects=0,
    )

    class _ScalarsResult:
        def __init__(self, items):
            self._items = items

        def all(self):
            return self._items

    class _SessionStub:
        def scalars(self, _q):
            return _ScalarsResult([row1, row2])

        def execute(self, _q):
            class _Empty:
                def scalar_one_or_none(self):
                    return None

            return _Empty()

        def get(self, _id):
            return None

        def flush(self):
            return None

        def add(self, _obj):
            raise AssertionError("should not create company on conflict review")

    resolver = CompanyResolver(_SessionStub())
    resolver._loaded = True
    resolver._id_to_row = {1: row1, 2: row2}
    resolver._key_to_ids["acmebuilders"] = {1, 2}
    resolution = resolver.resolve("Acme Builders", source="permits:vancouver", city="Vancouver")
    assert resolution.status == RESOLUTION_STATUS_REVIEW
    assert resolution.company_id in {1, 2}


def test_dba_short_trade_name_prefers_canonical_over_standalone():
    """New Person DBA: Ledcor must bind to canonical 8756, not standalone 3046."""
    from types import SimpleNamespace

    canonical = SimpleNamespace(
        id=8756,
        name="Ledcor Construction Limited",
        display_name="Ledcor Construction Limited",
        entity_role="canonical",
        canonical_company_id=None,
        total_value=1_000_000_000.0,
        total_award_value=0.0,
        total_projects=50,
    )
    standalone = SimpleNamespace(
        id=3046,
        name="Chris Burrows DBA: Ledcor",
        display_name="Ledcor",
        entity_role="standalone",
        canonical_company_id=None,
        total_value=2_360_000.0,
        total_award_value=0.0,
        total_projects=8,
    )

    class _ScalarsResult:
        def __init__(self, items):
            self._items = items

        def all(self):
            return self._items

    class _SessionStub:
        def scalars(self, _q):
            return _ScalarsResult([canonical, standalone])

        def get(self, company_id):
            return {8756: canonical, 3046: standalone}.get(int(company_id))

        def execute(self, _q):
            class _Empty:
                def scalar_one_or_none(self):
                    return None

            return _Empty()

        def flush(self):
            return None

        def add(self, _obj):
            raise AssertionError("should not create company when canonical exists in family")

    resolver = CompanyResolver(_SessionStub())
    resolver._loaded = True
    resolver._id_to_row = {8756: canonical, 3046: standalone}
    resolver._key_to_ids = {"ledcor": {3046}, "ledcorconstruction": {8756}}

    resolution = resolver.resolve(
        "New Person DBA: Ledcor",
        source="permits:verification_test",
        city="Vancouver",
        create_if_missing=True,
    )
    assert resolution.status == "resolved"
    assert resolution.company_id == 8756
    assert resolution.created is False
    assert resolution.display_name == "Ledcor"
