"""Filters for company analytics queries."""

from __future__ import annotations

from sqlalchemy import ColumnElement

from db.company_canonical_constants import COMPANY_ANALYTICS_EXCLUDED_ENTITY_ROLES
from db.models import Company


def company_analytics_entity_filter() -> ColumnElement[bool]:
    """Exclude alias shards and probable person records from company analytics."""
    return Company.entity_role.notin_(tuple(COMPANY_ANALYTICS_EXCLUDED_ENTITY_ROLES))
