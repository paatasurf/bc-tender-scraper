from __future__ import annotations

from scraper.config import COMMERCIAL_CATEGORY_LABEL, COMMERCIAL_TENDERS_CSV
from scraper.models import CommercialTender
from scraper.utils import save_csv_rows


def save_commercial_tenders(
    tenders: list[CommercialTender],
    csv_path: str = COMMERCIAL_TENDERS_CSV,
) -> None:
    fieldnames = [
        "title",
        "company",
        "value",
        "deadline",
        "status",
        "category",
        "url",
        "tender_id",
        "source",
    ]
    save_csv_rows((tender.to_dict() for tender in tenders), csv_path, fieldnames)


def make_commercial_tender(
    *,
    title: str,
    company: str,
    url: str,
    source: str,
    deadline: str = "",
    status: str = "Open",
    value: str = "",
    tender_id: str = "",
) -> CommercialTender:
    return CommercialTender(
        title=title,
        company=company,
        value=value,
        deadline=deadline,
        status=status,
        category=COMMERCIAL_CATEGORY_LABEL,
        url=url,
        tender_id=tender_id,
        source=source,
    )
