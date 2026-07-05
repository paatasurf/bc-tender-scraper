"""Tests for CIP permit loading."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pipeline.cip_builder import _load_company_permits


def test_load_company_permits_prefers_company_id():
    session = MagicMock()
    permit = SimpleNamespace(id=1, applicant="Acme Ltd")
    session.scalars.return_value.all.return_value = [permit]

    results = _load_company_permits(session, "acme ltd", company_id=42, limit=10)

    assert results == [permit]
    session.scalars.assert_called_once()


def test_load_company_permits_falls_back_to_name_when_company_id_empty():
    session = MagicMock()
    empty_scalars = MagicMock()
    empty_scalars.all.return_value = []
    name_scalars = MagicMock()
    permit = SimpleNamespace(id=2, applicant="Acme Ltd")
    name_scalars.all.return_value = [permit]
    session.scalars.side_effect = [empty_scalars, name_scalars]

    with patch("pipeline.cip_builder.normalize_vendor_name", side_effect=lambda value: value.lower()):
        results = _load_company_permits(session, "acme ltd", company_id=42, limit=10)

    assert results == [permit]
    assert session.scalars.call_count == 2
