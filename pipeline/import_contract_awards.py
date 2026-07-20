from __future__ import annotations

import csv
import io
import re
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import requests

from config.env import get_env
from scraper.config import USER_AGENT

HEADERS = {"User-Agent": USER_AGENT}

FEDERAL_CSV_URL = (
    "https://canadabuys.canada.ca/opendata/pub/{year}-awardNotice-avisAttribution.csv"
)
BC_CATALOGUE_API = (
    "https://catalogue.data.gov.bc.ca/api/3/action/package_show"
    "?id=ministry-contract-awards-province-of-british-columbia"
)
VANCOUVER_API = "https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/awarded-contracts/records"

FEDERAL_HISTORY_YEARS = 5


def _default_federal_years(*, as_of: date | None = None) -> tuple[str, ...]:
    """Return the current and recent CanadaBuys fiscal-year resources.

    CanadaBuys publishes award CSVs by the Canadian government fiscal year
    (April 1 through March 31). Deriving the list avoids silently dropping a
    new fiscal year when a hard-coded tuple becomes stale.
    """
    resolved_as_of = as_of or datetime.now(timezone.utc).date()
    fiscal_start = (
        resolved_as_of.year if resolved_as_of.month >= 4 else resolved_as_of.year - 1
    )
    return tuple(
        f"{start_year}-{start_year + 1}"
        for start_year in range(
            fiscal_start,
            fiscal_start - FEDERAL_HISTORY_YEARS,
            -1,
        )
    )


def _federal_years(*, as_of: date | None = None) -> tuple[str, ...]:
    defaults = _default_federal_years(as_of=as_of)
    raw = get_env("CONTRACT_AWARDS_FEDERAL_YEARS", ",".join(defaults))
    years = tuple(part.strip() for part in raw.split(",") if part.strip())
    return years or defaults


def _parse_money(raw: str | None) -> float | None:
    if not raw:
        return None
    digits = re.sub(r"[^\d.]", "", str(raw))
    if not digits:
        return None
    try:
        return float(digits)
    except ValueError:
        return None


def _normalize_date(raw: str | None) -> str:
    if not raw:
        return ""
    text = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return text[:20]


def _is_bc_region(region: str) -> bool:
    return "BRITISH COLUMBIA" in (region or "").upper()


def _normalize_category(raw: str) -> str:
    return (raw or "").replace("*", "").replace("\n", " ").strip()


def _fetch_text(url: str, *, timeout: int = 180) -> str:
    response = requests.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.text


def _iter_federal_rows(year: str) -> Iterator[dict[str, Any]]:
    url = FEDERAL_CSV_URL.format(year=year)
    print(f"[ContractAwards] Fetching federal CSV {year}...")
    text = _fetch_text(url)
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        region = row.get("regionsOfDelivery-regionsLivraison-eng", "") or ""
        if not _is_bc_region(region):
            continue

        contract_number = (row.get("contractNumber-numeroContrat") or "").strip()
        if not contract_number:
            continue

        winner = (row.get("supplierLegalName-nomLegalFournisseur-eng") or "").strip()
        if not winner:
            continue

        title = (row.get("title-titre-eng") or "").strip()
        value = _parse_money(row.get("totalContractValue-valeurTotaleContrat"))
        if value is None:
            value = _parse_money(row.get("contractAmount-montantContrat"))

        reference = (row.get("referenceNumber-numeroReference") or "").strip()
        url_hint = ""
        if reference:
            url_hint = f"https://canadabuys.canada.ca/en/tender-opportunities/award-notice/{reference}"

        yield {
            "source": "federal_award_notice",
            "external_id": contract_number,
            "url": url_hint,
            "title": title or f"Federal contract {contract_number}",
            "description": (
                row.get("awardDescription-descriptionAttribution-eng") or ""
            ).strip(),
            "procurement_category": _normalize_category(
                row.get("procurementCategory-categorieApprovisionnement", "")
            ),
            "procurement_method": (row.get("noticeType-avisType-eng") or "").strip(),
            "winner_company": winner,
            "winner_address": (
                row.get("supplierAddressLine-ligneAdresseFournisseur-eng") or ""
            ).strip(),
            "winner_city": (
                row.get("supplierAddressCity-fournisseurAdresseVille-eng") or ""
            ).strip(),
            "winner_province": (
                row.get("supplierAddressProvince-fournisseurAdresseProvince-eng") or ""
            ).strip(),
            "buyer_organization": (
                row.get("contractingEntityName-nomEntitContractante-eng") or ""
            ).strip(),
            "buyer_level": "federal",
            "award_value": value,
            "currency": (row.get("contractCurrency-contratMonnaie") or "CAD").strip()
            or "CAD",
            "award_date": _normalize_date(
                row.get("contractAwardDate-dateAttributionContrat")
            ),
            "contract_start_date": _normalize_date(
                row.get("contractStartDate-contratDateDebut")
            ),
            "contract_end_date": _normalize_date(
                row.get("contractEndDate-dateFinContrat")
            ),
            "delivery_region": region.replace("*", "").replace("\n", ", ").strip(),
        }


def _is_valid_bc_row(row: dict[str, str]) -> bool:
    opportunity_id = (row.get("Opportunity ID") or "").strip()
    vendor = (row.get("Successful Vendor") or "").strip()
    if not vendor:
        return False
    return opportunity_id.isdigit() and 1 <= len(opportunity_id) <= 8


def _iter_bc_provincial_rows() -> Iterator[dict[str, Any]]:
    payload = requests.get(BC_CATALOGUE_API, headers=HEADERS, timeout=60).json()
    resources = payload.get("result", {}).get("resources", [])
    print(f"[ContractAwards] Fetching {len(resources)} BC provincial CSV resources...")
    for resource in resources:
        name = resource.get("name", "unknown")
        url = resource.get("url")
        if not url:
            continue
        print(f"[ContractAwards] BC provincial file: {name}")
        text = _fetch_text(url)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".csv", delete=False, newline=""
        ) as handle:
            handle.write(text)
            temp_path = Path(handle.name)
        count = 0
        try:
            with temp_path.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    if not _is_valid_bc_row(row):
                        continue
                    count += 1
                    opportunity_id = (row.get("Opportunity ID") or "").strip()
                    award_date = _normalize_date(row.get("Date Awarded"))
                    external_id = (
                        f"{opportunity_id}:{award_date}"
                        if opportunity_id
                        else f"{name}:{count}"
                    )
                    address = (row.get("Successful Vendor Address") or "").strip()
                    city = ""
                    province = "BC"
                    if address:
                        parts = address.replace("  ", " ").split()
                        if "British" in address and "Columbia" in address:
                            province = "BC"
                        if len(parts) >= 3:
                            city = parts[-3] if parts[-2] == "British" else ""

                    yield {
                        "source": "bc_provincial",
                        "external_id": external_id,
                        "url": "",
                        "title": (row.get("Title") or "").strip()
                        or f"BC opportunity {opportunity_id}",
                        "description": "",
                        "procurement_category": "",
                        "procurement_method": (
                            row.get("Procurement Method") or ""
                        ).strip(),
                        "winner_company": (row.get("Successful Vendor") or "").strip(),
                        "winner_address": address,
                        "winner_city": city,
                        "winner_province": province,
                        "buyer_organization": (
                            row.get("Issued by Organization")
                            or row.get("Issued for Organization")
                            or ""
                        ).strip(),
                        "buyer_level": "provincial",
                        "award_value": _parse_money(row.get("Award Total")),
                        "currency": "CAD",
                        "award_date": award_date,
                        "contract_start_date": "",
                        "contract_end_date": "",
                        "delivery_region": "British Columbia",
                    }
        finally:
            temp_path.unlink(missing_ok=True)
        print(f"[ContractAwards] BC provincial file {name}: {count} valid rows")


def _iter_vancouver_rows() -> Iterator[dict[str, Any]]:
    offset = 0
    page_size = 100
    total = None
    while True:
        response = requests.get(
            VANCOUVER_API,
            headers=HEADERS,
            params={"limit": page_size, "offset": offset},
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        if total is None:
            total = payload.get("total_count") or 0
            print(f"[ContractAwards] Fetching Vancouver open data ({total} records)...")
        rows = payload.get("results") or []
        if not rows:
            break
        for row in rows:
            bid_number = (row.get("bid_number") or "").strip()
            vendor = (row.get("vendor_name") or "").strip()
            if not bid_number or not vendor:
                continue
            yield {
                "source": "vancouver_open_data",
                "external_id": bid_number,
                "url": "",
                "title": (row.get("bid_description") or "").strip()
                or f"Vancouver bid {bid_number}",
                "description": "",
                "procurement_category": (row.get("bid_type") or "").strip(),
                "procurement_method": "",
                "winner_company": vendor,
                "winner_address": "",
                "winner_city": "Vancouver",
                "winner_province": "BC",
                "buyer_organization": "City of Vancouver",
                "buyer_level": "municipal",
                "award_value": _parse_money(row.get("bid_amount")),
                "currency": "CAD",
                "award_date": _normalize_date(row.get("award_date")),
                "contract_start_date": "",
                "contract_end_date": "",
                "delivery_region": "Vancouver, BC",
            }
        offset += len(rows)
        if offset >= (total or 0):
            break


def _clamp_text(value: str | None, max_len: int) -> str:
    return (value or "")[:max_len]


def _finalize_record(record: dict[str, Any]) -> dict[str, Any]:
    record["external_id"] = _clamp_text(record.get("external_id"), 100)
    record["url"] = _clamp_text(record.get("url"), 500)
    record["description"] = (record.get("description") or "")[:8000]
    record["procurement_category"] = _clamp_text(record.get("procurement_category"), 40)
    record["procurement_method"] = _clamp_text(record.get("procurement_method"), 80)
    record["winner_company"] = _clamp_text(record.get("winner_company"), 300)
    record["winner_address"] = _clamp_text(record.get("winner_address"), 500)
    record["winner_city"] = _clamp_text(record.get("winner_city"), 100)
    record["winner_province"] = _clamp_text(record.get("winner_province"), 50)
    record["buyer_organization"] = _clamp_text(record.get("buyer_organization"), 300)
    record["buyer_level"] = _clamp_text(record.get("buyer_level"), 20)
    record["currency"] = _clamp_text(record.get("currency"), 10) or "CAD"
    record["award_date"] = _clamp_text(record.get("award_date"), 20)
    record["contract_start_date"] = _clamp_text(record.get("contract_start_date"), 20)
    record["contract_end_date"] = _clamp_text(record.get("contract_end_date"), 20)
    record["delivery_region"] = _clamp_text(record.get("delivery_region"), 200)
    record.setdefault("match_method", "")
    return record


def fetch_all_contract_awards() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for year in _federal_years():
        try:
            batch = list(_iter_federal_rows(year))
            print(f"[ContractAwards] Federal {year}: {len(batch)} BC rows")
            records.extend(batch)
        except Exception as exc:
            print(f"[ContractAwards] Federal {year} failed: {exc}")

    try:
        batch = list(_iter_bc_provincial_rows())
        print(f"[ContractAwards] BC provincial total: {len(batch)} rows")
        records.extend(batch)
    except Exception as exc:
        print(f"[ContractAwards] BC provincial failed: {exc}")

    try:
        batch = list(_iter_vancouver_rows())
        print(f"[ContractAwards] Vancouver total: {len(batch)} rows")
        records.extend(batch)
    except Exception as exc:
        print(f"[ContractAwards] Vancouver failed: {exc}")

    deduped: list[dict[str, Any]] = []
    for record in records:
        key = (record["source"], record["external_id"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(_finalize_record(record))
    print(f"[ContractAwards] Total unique records fetched: {len(deduped)}")
    return deduped
