"""Shared helpers for permit list queries."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from db.models import Permit

PermitCity = Literal["Vancouver", "Surrey", "Burnaby"]
PERMIT_CITIES: tuple[PermitCity, ...] = ("Vancouver", "Surrey", "Burnaby")

_CITY_ALIASES: dict[str, PermitCity] = {
    "vancouver": "Vancouver",
    "surrey": "Surrey",
    "burnaby": "Burnaby",
}


def normalize_permit_city(raw: str | None) -> PermitCity | None:
    """Normalize a city filter to Vancouver, Surrey, or Burnaby."""
    if raw is None or not str(raw).strip():
        return None
    normalized = _CITY_ALIASES.get(str(raw).strip().lower())
    if normalized is None:
        supported = ", ".join(PERMIT_CITIES)
        raise ValueError(f"Unsupported city '{raw}'. Use one of: {supported}.")
    return normalized


def permits_base_query(*, city: PermitCity | None = None) -> Select[tuple[Permit]]:
    query = select(Permit)
    if city is not None:
        query = query.where(Permit.city == city)
    return query


def count_permits(session: Session, *, city: PermitCity | None = None) -> int:
    query = select(func.count()).select_from(Permit)
    if city is not None:
        query = query.where(Permit.city == city)
    return session.scalar(query) or 0


def list_permits_page(
    session: Session,
    *,
    city: PermitCity | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Permit]:
    query = (
        permits_base_query(city=city)
        .order_by(Permit.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(session.scalars(query).all())
