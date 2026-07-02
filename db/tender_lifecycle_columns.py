"""Shared ORM columns for tender lifecycle schema (P2-01)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column


class TenderLifecycleColumnsMixin:
    """Lifecycle columns added to tenders, commercial_tenders, and arch_tenders."""

    lifecycle_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        server_default=text("'active'"),
    )
    is_open: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    lifecycle_status_override: Mapped[str | None] = mapped_column(String(30), nullable=True)
    lifecycle_override_reason: Mapped[str] = mapped_column(Text, default="")
    lifecycle_override_by: Mapped[str] = mapped_column(String(100), default="")
    closing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    awarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    missing_from_source_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    source_status_raw: Mapped[str] = mapped_column(Text, default="")
    source_status_normalized: Mapped[str] = mapped_column(String(50), default="")
    award_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    award_match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    addenda_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    last_addendum_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
