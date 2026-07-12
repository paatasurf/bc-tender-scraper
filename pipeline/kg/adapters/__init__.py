"""Observation adapters — dual-write producers for existing ETL paths."""

from __future__ import annotations

from pipeline.kg.adapters.base import ObservationAdapter
from pipeline.kg.adapters.permit import PermitObservationAdapter

__all__ = ["ObservationAdapter", "PermitObservationAdapter"]
