"""Unit tests for ODB import filters and production apply guard."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from db.market_registry_constants import (
    ODBUS_FILTER_ALL,
    ODBUS_FILTER_OR_NAICS23,
    ODBUS_FILTER_PRIMARY_NAICS23,
)
from pipeline.registry_verification.odbus_import import (
    assert_production_odbus_apply_allowed,
    plan_odbus_import,
    production_apply_authorized,
    row_passes_odbus_filter,
)


@pytest.mark.parametrize(
    "row,filter_mode,expected",
    [
        (
            {"prov_terr": "BC", "source_NAICS_primary": "236220", "derived_NAICS": "23"},
            ODBUS_FILTER_PRIMARY_NAICS23,
            True,
        ),
        (
            {"prov_terr": "BC", "source_NAICS_primary": "", "derived_NAICS": "23"},
            ODBUS_FILTER_PRIMARY_NAICS23,
            False,
        ),
        (
            {"prov_terr": "BC", "source_NAICS_primary": "", "derived_NAICS": "23"},
            ODBUS_FILTER_OR_NAICS23,
            True,
        ),
        (
            {"prov_terr": "ON", "source_NAICS_primary": "236220", "derived_NAICS": "23"},
            ODBUS_FILTER_OR_NAICS23,
            False,
        ),
        (
            {"prov_terr": "ON", "source_NAICS_primary": "541330", "derived_NAICS": "54"},
            ODBUS_FILTER_ALL,
            True,
        ),
    ],
)
def test_row_passes_odbus_filter(row, filter_mode, expected) -> None:
    assert row_passes_odbus_filter(row, filter_mode) is expected


def test_production_apply_authorized_only_primary() -> None:
    assert production_apply_authorized(ODBUS_FILTER_PRIMARY_NAICS23) is True
    assert production_apply_authorized(ODBUS_FILTER_OR_NAICS23) is False
    assert production_apply_authorized(ODBUS_FILTER_ALL) is False


def test_production_guard_allows_primary_on_railway_url() -> None:
    url = "postgresql://u:p@acela.proxy.rlwy.net:47306/railway"
    assert_production_odbus_apply_allowed(
        filter_mode=ODBUS_FILTER_PRIMARY_NAICS23,
        allow_production=True,
        database_url=url,
    )


@pytest.mark.parametrize("filter_mode", [ODBUS_FILTER_OR_NAICS23, ODBUS_FILTER_ALL])
def test_production_guard_refuses_noisy_filters_on_railway_url(filter_mode: str, capsys) -> None:
    url = "postgresql://u:p@acela.proxy.rlwy.net:47306/railway"
    with pytest.raises(SystemExit) as exc:
        assert_production_odbus_apply_allowed(
            filter_mode=filter_mode,
            allow_production=True,
            database_url=url,
        )
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Refusing production apply" in err


def test_production_guard_allows_noisy_filters_on_local_url() -> None:
    url = "postgresql://u:p@localhost:5432/bc_tenders"
    assert_production_odbus_apply_allowed(
        filter_mode=ODBUS_FILTER_ALL,
        allow_production=False,
        database_url=url,
    )


def test_plan_odbus_import_primary_counts(tmp_path) -> None:
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(
        "idx,business_name,alt_business_name,city,prov_terr,status,derived_NAICS,source_NAICS_primary,licence_number,business_id_no,provider,latitude,longitude\n"
        "a1,Alpha Ltd,,Vancouver,BC,Active,23,236220,,,City of Vancouver,,\n"
        "a2,Beta Ltd,,Vancouver,BC,Active,23,,,,City of Vancouver,,\n"
        "a3,Gamma Ltd,,Toronto,ON,Active,23,236220,,,Toronto,,\n",
        encoding="utf-8",
    )
    plan = plan_odbus_import(csv_path, filter_mode=ODBUS_FILTER_PRIMARY_NAICS23)
    assert plan["rows_upserted"] == 1
    assert plan["destructive_delete"] is False
    assert plan["production_apply_authorized"] is True


def test_run_odbus_import_cli_refuses_production_apply_or_filter(capsys) -> None:
    from scripts import run_odbus_import as cli

    csv_path = "exports/odbus_cache/ODBus_v1.csv"
    prod = "postgresql://u:p@acela.proxy.rlwy.net:47306/railway"
    with patch.object(
        cli.sys,
        "argv",
        ["run_odbus_import.py", csv_path, "--filter", ODBUS_FILTER_OR_NAICS23, "--apply", "--allow-production"],
    ):
        with patch("scripts.run_odbus_import.guard_destructive_db_from_args", return_value=prod):
            with patch("scripts.run_odbus_import.resolve_script_database_url", return_value=prod):
                with pytest.raises(SystemExit) as exc:
                    cli.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Refusing production apply" in err
