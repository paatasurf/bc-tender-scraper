"""Unit tests for pipeline.company_intelligence (PR-E4.1).

Fully mocked, deterministic -- never calls a real Anthropic API, never
reads a real environment secret, never touches a database. `anthropic`
is monkeypatched at the pipeline.company_intelligence module level (it
binds `import anthropic` at import time, so patching the module's own
`anthropic` attribute affects every `anthropic.Anthropic(...)` call
site inside it). Real public dataclasses/models (db.models.Company,
db.models.Tender) are used as fixtures where practical, per the
established convention in this test suite (PR-E3A/E3B).

This file documents CURRENT behavior, including known gaps (e.g.
match_company_to_tender has no caching today -- see
test_match_company_to_tender_two_identical_calls_hit_api_twice). It does
not pin a non-deterministic AI score as correct business logic anywhere;
all score/summary values used in assertions come from mocked Claude
responses this file controls, never from a live model.

Read-only against pipeline/company_intelligence.py -- production logic
is never modified.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import pipeline.company_intelligence as ci
from db.models import Company, Tender

# ===================================================================
# Fixture builders
# ===================================================================


def _make_company(**overrides) -> Company:
    defaults = dict(
        name="Acme Construction Ltd",
        total_projects=12,
        total_value=5_000_000.0,
        avg_project_value=416_666.0,
        project_types=["Commercial", "Residential"],
        neighborhoods=["Mount Pleasant", "Kitsilano"],
        first_project_date="2020-01-01",
        last_project_date="2026-06-01",
        google_rating=4.5,
        google_reviews_count=42,
        ai_reliability_score=None,
        ai_summary="",
    )
    defaults.update(overrides)
    return Company(**defaults)


def _make_tender(**overrides) -> Tender:
    defaults = dict(
        title="New Community Centre",
        organization="City of Vancouver",
        category="Construction",
        estimated_value="$1,000,000",
        closing_date="2026-12-31",
        url=f"https://example.test/tender/{uuid.uuid4().hex}",
    )
    defaults.update(overrides)
    return Tender(**defaults)


def _fake_response(text: str | None, *, blocks: list | None = None):
    """A stand-in for anthropic's Message response object -- only the
    `.content` list of blocks with `.type`/`.text` is read by production
    code."""
    if blocks is not None:
        return SimpleNamespace(content=blocks)
    if text is None:
        return SimpleNamespace(content=[])
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def _mock_anthropic_client(*, text=None, blocks=None, side_effect=None) -> MagicMock:
    client = MagicMock()
    if side_effect is not None:
        client.messages.create.side_effect = side_effect
    else:
        client.messages.create.return_value = _fake_response(text, blocks=blocks)
    return client


# ===================================================================
# 1. _extract_json()
# ===================================================================


def test_extract_json_clean_json():
    result = ci._extract_json('{"reliability_score": 80, "summary": "Solid firm."}')
    assert result == {"reliability_score": 80, "summary": "Solid firm."}


def test_extract_json_embedded_in_text():
    text = 'Sure, here is the result:\n{"reliability_score": 65, "summary": "Ok."}\nHope that helps!'
    result = ci._extract_json(text)
    assert result == {"reliability_score": 65, "summary": "Ok."}


def test_extract_json_missing_raises_value_error():
    with pytest.raises(ValueError, match="did not contain JSON"):
        ci._extract_json("There is no JSON in this response at all.")


def test_extract_json_malformed_braces_raises_json_decode_error():
    """Braces are present so the regex fallback fires, but the content
    inside is not valid JSON -- json.loads raises JSONDecodeError,
    uncaught by _extract_json itself (documents current behavior: this
    is not wrapped into a friendlier ValueError)."""
    with pytest.raises(json.JSONDecodeError):
        ci._extract_json("Result: {this is not valid json}")


def test_extract_json_wrong_payload_shape_is_not_validated():
    """_extract_json has no shape validation -- valid JSON that is not a
    dict (e.g. a list) is returned as-is. Documents current behavior;
    callers are the ones that assume dict-shaped payloads."""
    result = ci._extract_json("[1, 2, 3]")
    assert result == [1, 2, 3]


# ===================================================================
# 2. _build_analysis_prompt()
# ===================================================================


def test_build_analysis_prompt_contains_required_company_signals():
    company = _make_company()
    prompt = ci._build_analysis_prompt(company)
    assert "Acme Construction Ltd" in prompt
    assert "12" in prompt  # total_projects
    assert "5,000,000" in prompt  # total_value formatted
    assert "416,666" in prompt  # avg_project_value formatted
    assert "Commercial" in prompt and "Residential" in prompt
    assert "Mount Pleasant" in prompt
    assert "2020-01-01" in prompt and "2026-06-01" in prompt
    assert "4.5" in prompt and "42 reviews" in prompt


def test_build_analysis_prompt_handles_empty_nullable_fields():
    company = _make_company(
        project_types=[],
        neighborhoods=[],
        first_project_date="",
        last_project_date="",
        google_rating=None,
        google_reviews_count=None,
    )
    prompt = ci._build_analysis_prompt(company)
    assert "Unknown" in prompt  # project types / areas of activity fallback
    assert "No Google rating found" in prompt
    assert "Active from ? to ?" in prompt


def test_build_analysis_prompt_stable_for_identical_input():
    company = _make_company()
    p1 = ci._build_analysis_prompt(company)
    p2 = ci._build_analysis_prompt(company)
    assert p1 == p2


# ===================================================================
# 3. _analyze_company()
# ===================================================================


def test_analyze_company_parses_score_and_summary():
    client = _mock_anthropic_client(
        text='{"reliability_score": 72, "summary": "A well-established contractor."}'
    )
    company = _make_company()
    score, summary = ci._analyze_company(client, company)
    assert score == 72
    assert summary == "A well-established contractor."


def test_analyze_company_clamps_score_below_zero():
    client = _mock_anthropic_client(
        text='{"reliability_score": -50, "summary": "Weak signals."}'
    )
    score, _ = ci._analyze_company(client, _make_company())
    assert score == 0


def test_analyze_company_clamps_score_above_hundred():
    client = _mock_anthropic_client(
        text='{"reliability_score": 999, "summary": "Very strong."}'
    )
    score, _ = ci._analyze_company(client, _make_company())
    assert score == 100


def test_analyze_company_empty_summary_raises_value_error():
    client = _mock_anthropic_client(text='{"reliability_score": 80, "summary": ""}')
    with pytest.raises(ValueError, match="missing summary"):
        ci._analyze_company(client, _make_company())


def test_analyze_company_no_text_blocks_raises_value_error():
    client = _mock_anthropic_client(blocks=[])
    with pytest.raises(ValueError, match="no text content"):
        ci._analyze_company(client, _make_company())


def test_analyze_company_non_text_block_type_raises_value_error():
    client = _mock_anthropic_client(
        blocks=[SimpleNamespace(type="tool_use", text="ignored")]
    )
    with pytest.raises(ValueError, match="no text content"):
        ci._analyze_company(client, _make_company())


def test_analyze_company_malformed_json_response_propagates():
    client = _mock_anthropic_client(text="I cannot help with that.")
    with pytest.raises(ValueError, match="did not contain JSON"):
        ci._analyze_company(client, _make_company())


def test_analyze_company_calls_with_current_model_and_max_tokens_contract():
    client = _mock_anthropic_client(text='{"reliability_score": 50, "summary": "Ok."}')
    ci._analyze_company(client, _make_company())
    _, kwargs = client.messages.create.call_args
    assert kwargs["model"] == ci.CLAUDE_MODEL
    assert kwargs["model"] == "claude-sonnet-4-5"
    assert kwargs["max_tokens"] == 400
    assert len(kwargs["messages"]) == 1
    assert kwargs["messages"][0]["role"] == "user"


# ===================================================================
# 4. compute_enrichment_status()
# ===================================================================


def test_compute_enrichment_status_pending_when_neither_present():
    from pipeline.company_classification import compute_enrichment_status

    company = _make_company(google_reviews_count=None, ai_summary="")
    assert compute_enrichment_status(company) == "pending"


def test_compute_enrichment_status_partial_google_only():
    from pipeline.company_classification import compute_enrichment_status

    company = _make_company(google_reviews_count=0, ai_summary="")
    assert compute_enrichment_status(company) == "partial"


def test_compute_enrichment_status_partial_ai_only():
    from pipeline.company_classification import compute_enrichment_status

    company = _make_company(google_reviews_count=None, ai_summary="A profile.")
    assert compute_enrichment_status(company) == "partial"


def test_compute_enrichment_status_complete_when_both_present():
    from pipeline.company_classification import compute_enrichment_status

    company = _make_company(google_reviews_count=10, ai_summary="A profile.")
    assert compute_enrichment_status(company) == "complete"


# ===================================================================
# 5. analyze_companies_ai()
# ===================================================================


def test_analyze_companies_ai_skips_entirely_without_api_key(monkeypatch):
    monkeypatch.setattr(ci, "get_anthropic_api_key", lambda: "")
    session = MagicMock()

    result = ci.analyze_companies_ai(session)

    assert result == 0
    session.scalars.assert_not_called()


def test_analyze_companies_ai_applies_limit_and_null_score_filter(monkeypatch):
    monkeypatch.setattr(ci, "get_anthropic_api_key", lambda: "test-key")
    monkeypatch.setattr(ci, "_batch_limit", lambda name, default: 7)
    monkeypatch.setattr(ci.anthropic, "Anthropic", MagicMock())
    monkeypatch.setattr(ci.time, "sleep", lambda *_: None)

    session = MagicMock()
    session.scalars.return_value.all.return_value = []

    ci.analyze_companies_ai(session)

    stmt = session.scalars.call_args[0][0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "ai_reliability_score IS NULL" in compiled
    assert "LIMIT 7" in compiled


def test_analyze_companies_ai_baseline_success_reaches_commit(monkeypatch):
    """Baseline: with the real (module-level, actually imported)
    compute_enrichment_status reference -- no shim, no raising=False --
    a successful _analyze_company call reaches session.commit() and
    result == 1. Narrow proof of the commit-on-success/rollback contract
    (score/summary/status field values, explicit non-rollback assertion)
    lives in PR-E4.0's dedicated regression file
    (test_company_intelligence_enrichment_status_fix.py); this test only
    pins that the orchestration loop in analyze_companies_ai reaches a
    successful outcome end-to-end using the real import path."""
    monkeypatch.setattr(ci, "get_anthropic_api_key", lambda: "test-key")
    monkeypatch.setattr(ci, "_batch_limit", lambda name, default: 50)
    monkeypatch.setattr(ci.anthropic, "Anthropic", MagicMock())
    monkeypatch.setattr(ci.time, "sleep", lambda *_: None)
    monkeypatch.setattr(ci, "_analyze_company", lambda client, company: (77, "Solid."))

    company = _make_company()
    session = MagicMock()
    session.scalars.return_value.all.return_value = [company]

    result = ci.analyze_companies_ai(session)

    assert result == 1
    session.commit.assert_called_once()


def test_analyze_companies_ai_rolls_back_and_continues_after_failure(monkeypatch):
    """Orchestration behavior: one company's _analyze_company raising
    does not abort the batch -- it is rolled back individually and the
    next company still succeeds. Uses the real (actually imported)
    compute_enrichment_status -- no shim needed."""
    monkeypatch.setattr(ci, "get_anthropic_api_key", lambda: "test-key")
    monkeypatch.setattr(ci, "_batch_limit", lambda name, default: 50)
    monkeypatch.setattr(ci.anthropic, "Anthropic", MagicMock())
    monkeypatch.setattr(ci.time, "sleep", lambda *_: None)

    def _analyze_side_effect(client, company):
        if company.name == "Fails Ltd":
            raise ValueError("boom")
        return 60, "Fine."

    monkeypatch.setattr(ci, "_analyze_company", _analyze_side_effect)

    failing = _make_company(name="Fails Ltd")
    ok = _make_company(name="Succeeds Ltd")
    session = MagicMock()
    session.scalars.return_value.all.return_value = [failing, ok]

    result = ci.analyze_companies_ai(session)

    assert result == 1
    assert session.rollback.call_count == 1
    assert session.commit.call_count == 1
    # the failing company's fields were never set by the failed attempt
    assert failing.ai_reliability_score is None


def test_analyze_companies_ai_never_sleeps_or_touches_real_time(monkeypatch):
    """Explicit no-real-sleep guard: time.sleep is monkeypatched to a spy
    and this test runs fast regardless of how many companies are
    processed."""
    monkeypatch.setattr(ci, "get_anthropic_api_key", lambda: "test-key")
    monkeypatch.setattr(ci, "_batch_limit", lambda name, default: 50)
    monkeypatch.setattr(ci.anthropic, "Anthropic", MagicMock())
    monkeypatch.setattr(ci, "_analyze_company", lambda client, company: (50, "Ok."))
    sleep_calls = []
    monkeypatch.setattr(ci.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    companies = [_make_company(name=f"Co {i}") for i in range(3)]
    session = MagicMock()
    session.scalars.return_value.all.return_value = companies

    ci.analyze_companies_ai(session)

    assert len(sleep_calls) == 3  # one sleep per company, never a real one


# ===================================================================
# 6. match_company_to_tender()
# ===================================================================


def test_match_company_to_tender_parses_result(monkeypatch):
    monkeypatch.setattr(ci, "get_anthropic_api_key", lambda: "test-key")
    monkeypatch.setattr(
        ci.anthropic,
        "Anthropic",
        MagicMock(
            return_value=_mock_anthropic_client(
                text=json.dumps(
                    {
                        "match_score": 85,
                        "win_probability": 60,
                        "recommendations": ["Tip 1", "Tip 2", "Tip 3"],
                        "analysis": "Strong fit.",
                    }
                )
            )
        ),
    )
    result = ci.match_company_to_tender(_make_company(), _make_tender())
    assert result == {
        "match_score": 85,
        "win_probability": 60,
        "recommendations": ["Tip 1", "Tip 2", "Tip 3"],
        "analysis": "Strong fit.",
    }


def test_match_company_to_tender_win_probability_defaults_to_match_score(monkeypatch):
    monkeypatch.setattr(ci, "get_anthropic_api_key", lambda: "test-key")
    monkeypatch.setattr(
        ci.anthropic,
        "Anthropic",
        MagicMock(
            return_value=_mock_anthropic_client(
                text=json.dumps({"match_score": 70, "recommendations": ["A", "B", "C"]})
            )
        ),
    )
    result = ci.match_company_to_tender(_make_company(), _make_tender())
    assert result["win_probability"] == 70


def test_match_company_to_tender_pads_recommendations_to_three(monkeypatch):
    monkeypatch.setattr(ci, "get_anthropic_api_key", lambda: "test-key")
    monkeypatch.setattr(
        ci.anthropic,
        "Anthropic",
        MagicMock(
            return_value=_mock_anthropic_client(
                text=json.dumps({"match_score": 50, "recommendations": ["Only one"]})
            )
        ),
    )
    result = ci.match_company_to_tender(_make_company(), _make_tender())
    assert len(result["recommendations"]) == 3
    assert result["recommendations"][0] == "Only one"
    assert all(
        r == "Emphasize relevant BC project experience in your bid response."
        for r in result["recommendations"][1:]
    )


def test_match_company_to_tender_truncates_recommendations_to_three(monkeypatch):
    monkeypatch.setattr(ci, "get_anthropic_api_key", lambda: "test-key")
    monkeypatch.setattr(
        ci.anthropic,
        "Anthropic",
        MagicMock(
            return_value=_mock_anthropic_client(
                text=json.dumps(
                    {"match_score": 50, "recommendations": ["A", "B", "C", "D", "E"]}
                )
            )
        ),
    )
    result = ci.match_company_to_tender(_make_company(), _make_tender())
    assert result["recommendations"] == ["A", "B", "C"]


def test_match_company_to_tender_filters_blank_recommendations(monkeypatch):
    monkeypatch.setattr(ci, "get_anthropic_api_key", lambda: "test-key")
    monkeypatch.setattr(
        ci.anthropic,
        "Anthropic",
        MagicMock(
            return_value=_mock_anthropic_client(
                text=json.dumps(
                    {"match_score": 50, "recommendations": ["Real tip", "   ", ""]}
                )
            )
        ),
    )
    result = ci.match_company_to_tender(_make_company(), _make_tender())
    assert result["recommendations"][0] == "Real tip"
    assert len(result["recommendations"]) == 3
    assert "   " not in result["recommendations"]


def test_match_company_to_tender_non_list_recommendations_becomes_padded_defaults(
    monkeypatch,
):
    monkeypatch.setattr(ci, "get_anthropic_api_key", lambda: "test-key")
    monkeypatch.setattr(
        ci.anthropic,
        "Anthropic",
        MagicMock(
            return_value=_mock_anthropic_client(
                text=json.dumps({"match_score": 50, "recommendations": "not a list"})
            )
        ),
    )
    result = ci.match_company_to_tender(_make_company(), _make_tender())
    assert len(result["recommendations"]) == 3
    assert all(
        r == "Emphasize relevant BC project experience in your bid response."
        for r in result["recommendations"]
    )


def test_match_company_to_tender_missing_analysis_defaults_to_empty_string(monkeypatch):
    monkeypatch.setattr(ci, "get_anthropic_api_key", lambda: "test-key")
    monkeypatch.setattr(
        ci.anthropic,
        "Anthropic",
        MagicMock(
            return_value=_mock_anthropic_client(
                text=json.dumps({"match_score": 50, "recommendations": ["A", "B", "C"]})
            )
        ),
    )
    result = ci.match_company_to_tender(_make_company(), _make_tender())
    assert result["analysis"] == ""


def test_match_company_to_tender_no_text_blocks_raises_value_error(monkeypatch):
    monkeypatch.setattr(ci, "get_anthropic_api_key", lambda: "test-key")
    monkeypatch.setattr(
        ci.anthropic,
        "Anthropic",
        MagicMock(return_value=_mock_anthropic_client(blocks=[])),
    )
    with pytest.raises(ValueError, match="no text content"):
        ci.match_company_to_tender(_make_company(), _make_tender())


def test_match_company_to_tender_malformed_response_raises(monkeypatch):
    monkeypatch.setattr(ci, "get_anthropic_api_key", lambda: "test-key")
    monkeypatch.setattr(
        ci.anthropic,
        "Anthropic",
        MagicMock(return_value=_mock_anthropic_client(text="not json at all")),
    )
    with pytest.raises(ValueError, match="did not contain JSON"):
        ci.match_company_to_tender(_make_company(), _make_tender())


def test_match_company_to_tender_missing_api_key_raises_runtime_error(monkeypatch):
    monkeypatch.setattr(ci, "get_anthropic_api_key", lambda: "")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        ci.match_company_to_tender(_make_company(), _make_tender())


def test_match_company_to_tender_two_identical_calls_hit_api_twice(monkeypatch):
    """Documents CURRENT behavior only -- match_company_to_tender has no
    caching or idempotency anywhere in the stack today (confirmed in the
    PR-E4 discovery report). Two calls with byte-identical company/tender
    inputs make two separate mocked API calls and are NOT guaranteed to
    return the same result. This test pins today's absence of caching so
    a future PR that adds caching must consciously update it -- it is
    not a statement that this is the desired permanent contract."""
    monkeypatch.setattr(ci, "get_anthropic_api_key", lambda: "test-key")
    client = _mock_anthropic_client(
        text=json.dumps({"match_score": 50, "recommendations": ["A", "B", "C"]})
    )
    monkeypatch.setattr(ci.anthropic, "Anthropic", MagicMock(return_value=client))

    company = _make_company()
    tender = _make_tender()

    ci.match_company_to_tender(company, tender)
    ci.match_company_to_tender(company, tender)

    assert client.messages.create.call_count == 2
