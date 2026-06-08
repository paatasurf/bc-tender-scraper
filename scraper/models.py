from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class ArchTender:
    title: str
    company: str
    value: str
    deadline: str
    status: str
    category: str
    url: str
    tender_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Tender:
    title: str
    organization: str
    category: str
    posted_date: str
    closing_date: str
    estimated_value: str
    location: str
    tender_id: str
    url: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
