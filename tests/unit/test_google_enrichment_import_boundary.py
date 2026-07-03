"""Ensure google_enrichment stays isolated from product pipelines."""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Product ranking, scoring, recommendation, and intelligence modules must not
# import the enrichment infrastructure layer.
FORBIDDEN_IMPORTER_PATHS: tuple[str, ...] = (
    "pipeline/opportunity_discovery.py",
    "pipeline/bd_recommendations.py",
    "pipeline/unified_opportunities.py",
    "pipeline/company_intelligence.py",
    "pipeline/arch_company_intelligence.py",
)

COMPETITIVE_INTEL_GLOB = "pipeline/competitive_intel/*.py"


def _collect_forbidden_paths() -> list[Path]:
    paths: list[Path] = []
    for relative in FORBIDDEN_IMPORTER_PATHS:
        path = PROJECT_ROOT / relative
        if path.is_file():
            paths.append(path)
    paths.extend(sorted(PROJECT_ROOT.glob(COMPETITIVE_INTEL_GLOB)))
    return paths


def _imports_google_enrichment(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and (
                node.module == "pipeline.google_enrichment"
                or node.module.startswith("pipeline.google_enrichment.")
            ):
                return True
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pipeline.google_enrichment" or alias.name.startswith(
                    "pipeline.google_enrichment."
                ):
                    return True
    return False


def test_product_pipelines_do_not_import_google_enrichment():
    violations = []
    for path in _collect_forbidden_paths():
        if _imports_google_enrichment(path):
            violations.append(str(path.relative_to(PROJECT_ROOT)))
    assert not violations, f"Forbidden google_enrichment imports: {violations}"


def test_forbidden_importer_paths_exist():
    """Guardrail: keep the boundary list aligned with the repo layout."""
    paths = _collect_forbidden_paths()
    assert paths, "Expected at least one forbidden importer path"
    relative = {p.relative_to(PROJECT_ROOT).as_posix() for p in paths}
    assert "pipeline/unified_opportunities.py" in relative
    assert "pipeline/company_intelligence.py" in relative
    assert any(p.startswith("pipeline/competitive_intel/") for p in relative)
