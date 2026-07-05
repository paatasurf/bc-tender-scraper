"""Preserve registry provenance across capability profile recomputation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

REGISTRY_PROVENANCE_KEY = "registry_provenance"


def extract_registry_provenance(capability_profile_json: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(capability_profile_json, dict):
        return {}
    value = capability_profile_json.get(REGISTRY_PROVENANCE_KEY)
    return deepcopy(value) if isinstance(value, dict) else {}


def merge_registry_provenance_into_profile(
    new_profile: dict[str, Any],
    *,
    existing_capability_profile_json: dict[str, Any] | None,
) -> dict[str, Any]:
    """Re-attach registry_provenance after CIP/CCP rebuild (whole-blob replace safe)."""
    preserved = extract_registry_provenance(existing_capability_profile_json)
    if not preserved:
        return new_profile
    merged = dict(new_profile)
    merged[REGISTRY_PROVENANCE_KEY] = preserved
    return merged
