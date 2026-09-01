"""Unit tests for pipeline/company_enrichment/website_contact_provider.py
(Phase 3A: direct-site contact enrichment for already-known domains).

Mock-based throughout, matching test_company_enrichment_provider.py's own
convention (MagicMock session, unittest.mock.patch for module-level
dependencies) -- no real network call and no real DB connection anywhere in
this file. Extraction-logic tests call the pure `_extract_contacts()`/
`_compute_confidence()` helpers directly with hand-crafted HTML/values;
SSRF tests call `_ssrf_and_dns_check()` directly against literal IPs (no
real DNS lookup needed for a literal IP); orchestration tests
(`lookup()` end-to-end) patch the network-touching module-level functions
(`_check_robots`, `_redirect_walk`, `_fetch_rendered_page`) so `lookup()`'s
own control flow is exercised without touching a real site.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pipeline.company_enrichment.provider import EnrichmentRequest
from pipeline.company_enrichment.website_contact_provider import (
    DnsResolutionError,
    SsrfBlockedError,
    WebsiteContactProvider,
    _compute_confidence,
    _extract_contacts,
    _normalize_website_candidate,
    _redirect_walk,
    _ssrf_and_dns_check,
)

MODULE = "pipeline.company_enrichment.website_contact_provider"


def _company(
    website: str = "example.com",
    google_website: str = "",
    name: str = "Acme Construction Ltd",
) -> MagicMock:
    company = MagicMock()
    company.website = website
    company.google_website = google_website
    company.name = name
    company.display_name = name
    return company


def _request(company_id: int = 1, website: str | None = None) -> EnrichmentRequest:
    return EnrichmentRequest(
        company_id=company_id, company_name="Acme Construction Ltd", website=website
    )


def _resolved_walk(final_url: str) -> dict:
    return {
        "outcome": "resolved",
        "final_url": final_url,
        "final_status": 200,
        "body_snippet": "",
        "hops": [],
    }


# ---------------------------------------------------------------------------
# 1. JSON-LD phone/email extraction
# ---------------------------------------------------------------------------


def test_extracts_phone_and_email_from_json_ld_organization_block():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "Organization",
     "name": "Acme Construction Ltd", "telephone": "(604) 555-1234",
     "email": "info@example.com"}
    </script>
    </head><body>Acme Construction</body></html>
    """
    jsonld_found, org_name, phones, emails = _extract_contacts(
        html, "https://example.com/"
    )

    assert jsonld_found is True
    assert org_name == "Acme Construction Ltd"
    assert "6045551234" in phones
    assert phones["6045551234"].method == "json_ld"
    assert phones["6045551234"].raw == "(604) 555-1234"
    assert "info@example.com" in emails
    assert emails["info@example.com"].method == "json_ld"


# ---------------------------------------------------------------------------
# 2. tel:/mailto: fallback (no JSON-LD present)
# ---------------------------------------------------------------------------


def test_extracts_phone_and_email_from_tel_and_mailto_links_when_no_structured_data():
    html = """
    <html><body>
      <a href="tel:6045551234">Call us</a>
      <a href="mailto:contact@example.com">Email us</a>
    </body></html>
    """
    jsonld_found, org_name, phones, emails = _extract_contacts(
        html, "https://example.com/"
    )

    assert jsonld_found is False
    assert org_name is None
    assert phones["6045551234"].method == "tel_link"
    assert emails["contact@example.com"].method == "mailto_link"


def test_trafilatura_text_fallback_used_only_when_no_stronger_signal_present():
    html = "<html><body><p>Reach us at 604-555-1234 or hello@example.com any time.</p></body></html>"
    jsonld_found, org_name, phones, emails = _extract_contacts(
        html, "https://example.com/"
    )

    assert phones["6045551234"].method == "trafilatura_text"
    assert emails["hello@example.com"].method == "trafilatura_text"


def test_higher_priority_method_wins_when_the_same_value_is_found_twice():
    html = """
    <html><body>
      <script type="application/ld+json">
      {"@context": "https://schema.org", "@type": "Organization", "telephone": "604-555-1234"}
      </script>
      <p>Call 604-555-1234 today</p>
    </body></html>
    """
    _, _, phones, _ = _extract_contacts(html, "https://example.com/")
    assert (
        phones["6045551234"].method == "json_ld"
    )  # not overwritten by the weaker trafilatura hit


# ---------------------------------------------------------------------------
# 2b. Microdata extraction (schema.org itemscope/itemprop, no JSON-LD)
# ---------------------------------------------------------------------------


_MICRODATA_HTML = """
<html><body>
<div itemscope itemtype="https://schema.org/LocalBusiness">
  <span itemprop="name">Acme Construction Ltd</span>
  <span itemprop="telephone">604-555-9876</span>
  <span itemprop="email">contact@example.com</span>
</div>
</body></html>
"""


def test_extracts_phone_and_email_from_microdata_when_no_json_ld_present():
    structured_data_found, org_name, phones, emails = _extract_contacts(
        _MICRODATA_HTML, "https://example.com/"
    )

    assert structured_data_found is True
    assert phones["6045559876"].method == "microdata"
    assert phones["6045559876"].raw == "604-555-9876"
    assert emails["contact@example.com"].method == "microdata"


def test_microdata_organization_name_is_used_for_name_corroboration():
    """Name corroboration (decision doc S6.2 / design doc S4) must not be a
    JSON-LD-only privilege -- a microdata LocalBusiness `name` property is
    equally valid evidence of which entity a page's contact info belongs
    to. Regression coverage for the earlier revision of this function,
    where org-name detection only ever looked at data["json-ld"], never
    data["microdata"], so a microdata-only page could never corroborate a
    name no matter what it actually said."""
    _, org_name, _, _ = _extract_contacts(_MICRODATA_HTML, "https://example.com/")
    assert org_name == "Acme Construction Ltd"


def test_microdata_extraction_survives_a_block_with_no_matching_org_type():
    html = """
    <html><body>
    <div itemscope itemtype="https://schema.org/BreadcrumbList">
      <span itemprop="name">Home</span>
    </div>
    <p>Call 604-555-1234</p>
    </body></html>
    """
    structured_data_found, org_name, phones, _ = _extract_contacts(
        html, "https://example.com/"
    )
    assert structured_data_found is True  # a microdata item WAS found...
    assert org_name is None  # ...but it isn't an Organization-shaped one
    assert phones["6045551234"].method == "trafilatura_text"


def test_json_ld_org_name_wins_over_microdata_when_both_are_present():
    html = """
    <html><body>
      <script type="application/ld+json">
      {"@context": "https://schema.org", "@type": "Organization", "name": "JSON-LD Name Ltd"}
      </script>
      <div itemscope itemtype="https://schema.org/LocalBusiness">
        <span itemprop="name">Microdata Name Ltd</span>
      </div>
    </body></html>
    """
    _, org_name, _, _ = _extract_contacts(html, "https://example.com/")
    assert (
        org_name == "JSON-LD Name Ltd"
    )  # json-ld branch runs first in _extract_contacts


def test_lookup_end_to_end_returns_a_microdata_sourced_fact_with_its_own_confidence_ceiling():
    provider = WebsiteContactProvider()
    session = MagicMock()
    session.get.return_value = _company()

    with (
        patch(f"{MODULE}._check_robots", return_value={"allowed": True}),
        patch(
            f"{MODULE}._redirect_walk",
            return_value=_resolved_walk("https://example.com/"),
        ),
        patch(f"{MODULE}._ssrf_and_dns_check"),
        patch(
            f"{MODULE}._fetch_rendered_page",
            new_callable=AsyncMock,
            return_value=(_MICRODATA_HTML, 200, "https://example.com/"),
        ),
    ):
        result = provider.lookup(session, _request())

    assert result.matched is True
    phone_fact = next(f for f in result.facts if f.field_name == "phone")
    assert phone_fact.extraction_method == "microdata"
    # microdata ceiling 0.75, exact domain, canonical "/" path, and the
    # LocalBusiness name ("Acme Construction Ltd") corroborates the
    # requested company name exactly -- no reductions apply.
    assert phone_fact.confidence == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# 3. robots.txt deny
# ---------------------------------------------------------------------------


def test_lookup_returns_no_match_and_never_fetches_when_robots_disallows():
    provider = WebsiteContactProvider()
    session = MagicMock()
    session.get.return_value = _company()

    with patch(
        f"{MODULE}._check_robots",
        return_value={
            "allowed": False,
            "error": "Disallow: /",
            "robots_url": "https://example.com/robots.txt",
        },
    ) as robots_mock, patch(f"{MODULE}._redirect_walk") as walk_mock, patch(
        f"{MODULE}._fetch_rendered_page", new_callable=AsyncMock
    ) as fetch_mock:
        result = provider.lookup(session, _request())

    robots_mock.assert_called_once()
    walk_mock.assert_not_called()
    fetch_mock.assert_not_called()
    assert result.matched is False
    assert result.error is not None and result.error.startswith("robots_disallowed:")
    assert result.facts == ()


# ---------------------------------------------------------------------------
# 4. SSRF -- localhost/private/link-local/metadata, DNS-vs-SSRF distinction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://10.1.2.3/",
        "http://172.16.5.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/",  # cloud metadata
        "http://localhost/",
        "http://localhost.localdomain/",
        "file:///etc/passwd",
    ],
)
def test_ssrf_guard_blocks_every_private_loopback_link_local_metadata_and_bad_scheme_target(
    url,
):
    with pytest.raises(SsrfBlockedError):
        _ssrf_and_dns_check(url)


def test_ssrf_guard_allows_a_normal_public_host():
    # example.com resolves to a real public IP; this must NOT raise.
    _ssrf_and_dns_check("https://example.com/")


def test_dns_resolution_failure_raises_a_distinct_exception_from_ssrf_block():
    with pytest.raises(DnsResolutionError):
        _ssrf_and_dns_check("https://this-domain-should-not-resolve-ever.invalid/")


# ---------------------------------------------------------------------------
# 5. Redirect to a private IP -- each hop independently re-validated
# ---------------------------------------------------------------------------


def test_redirect_walk_blocks_a_redirect_target_pointing_at_a_private_ip_and_never_probes_it():
    from pipeline.company_enrichment.website_contact_provider import (
        _resolve_ips as real_resolve_ips,
    )

    probe_calls: list[str] = []

    def fake_probe(url: str) -> dict:
        probe_calls.append(url)
        if url == "https://fake-public-host.example/":
            return {
                "status": 302,
                "headers": {"Location": "http://192.168.1.50/admin"},
                "body": b"",
                "error": None,
                "connection_level": False,
            }
        return {
            "status": 200,
            "headers": {},
            "body": b"UNSAFE: should never be fetched",
            "error": None,
            "connection_level": False,
        }

    def fake_resolve(hostname: str) -> list[str]:
        # Only the fake hop-0 hostname is stubbed (not real DNS); the
        # private-IP redirect target below is an IP literal and goes
        # through the UNMODIFIED real resolver, so the actual
        # SSRF/range-check logic is exercised for real, not mocked away.
        if hostname == "fake-public-host.example":
            return ["93.184.216.34"]  # a real public IP, just to pass DNS
        return real_resolve_ips(hostname)

    with patch(f"{MODULE}._resolve_ips", side_effect=fake_resolve), patch(
        f"{MODULE}._manual_probe", side_effect=fake_probe
    ):
        walk = _redirect_walk("fake-public-host.example")

    assert walk["outcome"] == "redirect_blocked"
    assert walk["hop_index"] == 1
    assert "http://192.168.1.50/admin" not in probe_calls


# ---------------------------------------------------------------------------
# 6. Timeout
# ---------------------------------------------------------------------------


def test_lookup_returns_no_match_when_the_fetch_times_out():
    provider = WebsiteContactProvider()
    session = MagicMock()
    session.get.return_value = _company()

    with patch(f"{MODULE}._check_robots", return_value={"allowed": True}), patch(
        f"{MODULE}._redirect_walk", return_value=_resolved_walk("https://example.com/")
    ), patch(f"{MODULE}._ssrf_and_dns_check"), patch(
        f"{MODULE}._fetch_rendered_page",
        new_callable=AsyncMock,
        side_effect=TimeoutError("simulated timeout"),
    ):
        result = provider.lookup(session, _request())

    assert result.matched is False
    assert result.error is not None and result.error.startswith("timeout:")
    assert result.facts == ()


# ---------------------------------------------------------------------------
# 7. Gmail / free-mail domain -- candidate only, confidence capped
# ---------------------------------------------------------------------------


def test_free_mail_domain_confidence_is_hard_capped_even_at_the_highest_ceiling():
    capped = _compute_confidence(
        extraction_method="json_ld",  # highest possible ceiling (0.85)
        domain_exact_match=True,
        page_is_canonical=True,
        org_name="Acme Construction Ltd",
        company_name_normalized="acmeconstruction",
        is_free_mail=True,
    )
    assert capped <= 0.50


def test_lookup_still_returns_a_gmail_candidate_but_never_marks_anything_verified():
    provider = WebsiteContactProvider()
    session = MagicMock()
    session.get.return_value = _company()
    html = "<html><body><p>Email us at info@gmail.com</p></body></html>"

    with patch(f"{MODULE}._check_robots", return_value={"allowed": True}), patch(
        f"{MODULE}._redirect_walk", return_value=_resolved_walk("https://example.com/")
    ), patch(f"{MODULE}._ssrf_and_dns_check"), patch(
        f"{MODULE}._fetch_rendered_page",
        new_callable=AsyncMock,
        return_value=(html, 200, "https://example.com/"),
    ):
        result = provider.lookup(session, _request())

    assert result.matched is True
    email_facts = [f for f in result.facts if f.field_name == "email"]
    assert len(email_facts) == 1
    assert email_facts[0].value == "info@gmail.com"
    assert email_facts[0].confidence <= 0.50
    # ProviderFact has no `verified` concept at all -- this provider is
    # structurally incapable of writing one; only write_enrichment_facts()
    # (orchestrator.py, untouched by this change) can ever set it, and it
    # always defaults new rows to verified=False.
    assert not hasattr(email_facts[0], "verified")


# ---------------------------------------------------------------------------
# 8. No-match
# ---------------------------------------------------------------------------


def test_lookup_returns_clean_no_match_when_page_has_no_contact_info():
    provider = WebsiteContactProvider()
    session = MagicMock()
    session.get.return_value = _company()
    html = "<html><body><h1>Welcome to Acme</h1><p>We build things.</p></body></html>"

    with patch(f"{MODULE}._check_robots", return_value={"allowed": True}), patch(
        f"{MODULE}._redirect_walk", return_value=_resolved_walk("https://example.com/")
    ), patch(f"{MODULE}._ssrf_and_dns_check"), patch(
        f"{MODULE}._fetch_rendered_page",
        new_callable=AsyncMock,
        return_value=(html, 200, "https://example.com/"),
    ):
        result = provider.lookup(session, _request())

    assert result.matched is False
    assert (
        result.error is None
    )  # a clean no-match is not an error (provider.py's own contract)
    assert result.facts == ()


def test_lookup_returns_clean_no_match_without_any_network_call_when_no_domain_is_known():
    provider = WebsiteContactProvider()
    session = MagicMock()
    session.get.return_value = _company(website="", google_website="")

    with patch(f"{MODULE}._check_robots") as robots_mock, patch(
        f"{MODULE}._redirect_walk"
    ) as walk_mock:
        result = provider.lookup(session, _request(website=None))

    robots_mock.assert_not_called()
    walk_mock.assert_not_called()
    assert result.matched is False
    assert result.error is None
    assert result.facts == ()


# ---------------------------------------------------------------------------
# 8b. Strict website-value normalization -- sentinel/malformed values are
# no_match, never a network call.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "N/A",
        "n/a",
        "  n/a  ",
        "NA",
        "unknown",
        "Unknown",
        "-",
        "--",
        "none",
        "None",
        "null",
        "NULL",
        "nil",
        "TBD",
        "tbd",
        "pending",
        "Not Available",
        "not applicable",
        "no website",
        "No Website",
        "no site",
        "",
        "   ",
        None,
        "acme",  # single-label token, not a real hostname
        "some text with spaces.com",  # contains whitespace
        "192.168.1.1",  # bare IP literal, not a hostname
        "http://192.168.1.1",
        "localhost",  # single-label, also separately SSRF-blocked downstream
    ],
)
def test_normalize_website_candidate_rejects_every_sentinel_and_malformed_value(raw):
    assert _normalize_website_candidate(raw) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("example.com", "example.com"),
        ("https://example.com/path", "example.com"),
        ("www.example.com", "www.example.com"),
        ("example.com:8080", "example.com"),
        ("biz-name.co.uk", "biz-name.co.uk"),
    ],
)
def test_normalize_website_candidate_accepts_real_looking_hostnames(raw, expected):
    assert _normalize_website_candidate(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["N/A", "unknown", "-", "", None, "192.168.1.1", "acme"],
)
def test_lookup_returns_clean_no_match_with_zero_network_calls_for_a_sentinel_website_value(
    raw,
):
    """The exact requirement: a malformed/sentinel website value must
    never trigger _check_robots()/_redirect_walk() -- not just eventually
    fail somewhere downstream, but never be attempted at all."""
    provider = WebsiteContactProvider()
    session = MagicMock()
    session.get.return_value = _company(website=raw, google_website="")

    with patch(f"{MODULE}._check_robots") as robots_mock, patch(
        f"{MODULE}._redirect_walk"
    ) as walk_mock, patch(f"{MODULE}._fetch_rendered_page") as fetch_mock:
        result = provider.lookup(session, _request(website=None))

    robots_mock.assert_not_called()
    walk_mock.assert_not_called()
    fetch_mock.assert_not_called()
    assert result.matched is False
    assert result.error is None
    assert result.facts == ()


def test_lookup_falls_through_a_sentinel_request_website_to_a_valid_company_website():
    """A sentinel EnrichmentRequest.website must not poison the whole
    lookup -- _resolve_domain() falls through to Company.website exactly
    as it already does for an empty EnrichmentRequest.website."""
    provider = WebsiteContactProvider()
    session = MagicMock()
    session.get.return_value = _company(website="example.com")

    with patch(f"{MODULE}._check_robots", return_value={"allowed": True}), patch(
        f"{MODULE}._redirect_walk", return_value=_resolved_walk("https://example.com/")
    ), patch(f"{MODULE}._ssrf_and_dns_check"), patch(
        f"{MODULE}._fetch_rendered_page",
        new_callable=AsyncMock,
        return_value=(
            "<html><body>no contact info</body></html>",
            200,
            "https://example.com/",
        ),
    ) as fetch_mock:
        result = provider.lookup(session, _request(website="N/A"))

    fetch_mock.assert_called_once()
    assert result.matched is False
    assert result.error is None


# ---------------------------------------------------------------------------
# 9. Malformed HTML -- extraction must never raise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "html",
    [
        "<html><body><p>Unclosed tag <div>nested <span>oops",
        '<script type="application/ld+json">{not valid json at all</script>',
        "",
        "\x00\x01\x02 binary garbage not html at all",
        "<html><body>" + ("x" * 5) + "<script>" + ("y" * 5) + "</html>",
    ],
)
def test_extraction_never_raises_on_malformed_or_garbage_html(html):
    jsonld_found, org_name, phones, emails = _extract_contacts(
        html, "https://example.com/"
    )
    # No assertion on content -- the only requirement is that this returns
    # cleanly rather than raising.
    assert isinstance(phones, dict)
    assert isinstance(emails, dict)


def test_lookup_survives_malformed_html_as_a_clean_no_match_not_a_crash():
    provider = WebsiteContactProvider()
    session = MagicMock()
    session.get.return_value = _company()
    malformed = (
        '<script type="application/ld+json">{broken</script><body>no valid markup'
    )

    with patch(f"{MODULE}._check_robots", return_value={"allowed": True}), patch(
        f"{MODULE}._redirect_walk", return_value=_resolved_walk("https://example.com/")
    ), patch(f"{MODULE}._ssrf_and_dns_check"), patch(
        f"{MODULE}._fetch_rendered_page",
        new_callable=AsyncMock,
        return_value=(malformed, 200, "https://example.com/"),
    ):
        result = provider.lookup(session, _request())

    assert result.matched is False
    assert result.facts == ()


# ---------------------------------------------------------------------------
# 10. Domain mismatch -- lower confidence, not silently equal to a real match
# ---------------------------------------------------------------------------


def test_domain_mismatch_reduces_confidence_relative_to_an_exact_match():
    matched = _compute_confidence(
        extraction_method="trafilatura_text",
        domain_exact_match=True,
        page_is_canonical=True,
        org_name=None,
        company_name_normalized="acmeconstruction",
        is_free_mail=False,
    )
    mismatched = _compute_confidence(
        extraction_method="trafilatura_text",
        domain_exact_match=False,
        page_is_canonical=True,
        org_name=None,
        company_name_normalized="acmeconstruction",
        is_free_mail=False,
    )
    assert mismatched < matched
    assert round(matched - mismatched, 2) == 0.15


def test_lookup_reflects_domain_mismatch_in_the_returned_facts_confidence():
    provider = WebsiteContactProvider()
    session = MagicMock()
    session.get.return_value = _company(website="example.com")
    html = "<html><body><p>Call 604-555-1234</p></body></html>"

    # Rendered final URL lands on an entirely different host than the
    # requested domain (e.g. an unexpected but SSRF-safe redirect target).
    with patch(f"{MODULE}._check_robots", return_value={"allowed": True}), patch(
        f"{MODULE}._redirect_walk", return_value=_resolved_walk("https://example.com/")
    ), patch(f"{MODULE}._ssrf_and_dns_check"), patch(
        f"{MODULE}._fetch_rendered_page",
        new_callable=AsyncMock,
        return_value=(html, 200, "https://totally-different-domain.example/"),
    ):
        result = provider.lookup(session, _request())

    assert result.matched is True
    phone_fact = next(f for f in result.facts if f.field_name == "phone")
    # trafilatura_text ceiling 0.45, minus 0.15 (domain mismatch), minus 0.10
    # (not a canonical "/" or /contact*/about* path -- it is "/" here, so
    # only the domain-mismatch reduction applies) = 0.30
    assert phone_fact.confidence == pytest.approx(0.30)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def test_provider_metadata():
    assert WebsiteContactProvider.name == "website_contact"
    assert WebsiteContactProvider.is_fact_source is True
