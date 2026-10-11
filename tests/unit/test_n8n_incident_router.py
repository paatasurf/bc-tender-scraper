"""Regression tests for the exported n8n incident router workflow."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


WORKFLOW_PATH = Path(__file__).parents[2] / "n8n" / "workflows" / "incident-router.json"


def _code_node_js(node_name: str) -> str:
    workflow = json.loads(WORKFLOW_PATH.read_text())
    for node in workflow["nodes"]:
        if node["name"] == node_name:
            return node["parameters"]["jsCode"]
    raise AssertionError(f"Code node not found: {node_name}")


def _run_code_node(js_code: str, item: dict) -> dict:
    script = f"""
const inputItem = {json.dumps(item)};
global.$input = {{ first: () => ({{ json: inputItem }}) }};
const execute = () => {{
{js_code}
}};
const result = execute();
process.stdout.write(JSON.stringify(result[0].json));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_global_error_handler_placeholder_is_not_sent_to_cursor():
    item = {
        "incident_id": "25752ccb-a18b-4de5-a90c-275c0cd2019a",
        "incident_key": 'TenderScope - Global Error Handler:unknown-node:"unknown-error"',
        "severity": "high",
        "tier": None,
        "source": "n8n-error:TenderScope - Global Error Handler",
        "step": "unknown-node",
        "summary": "n8n workflow failed: TenderScope - Global Error Handler at node unknown-node",
        "error": '"unknown error"',
        "context": {
            "workflow_id": "ZP6fnTQE1hOzcU9L",
            "workflow_name": "TenderScope - Global Error Handler",
            "node_name": "unknown-node",
        },
        "auto_fix_allowed": True,
    }

    result = _run_code_node(_code_node_js("Classify Tier"), item)

    assert result["tier"] == 3
    assert result["auto_fix_allowed"] is False


def test_arcgis_scraper_incident_still_routes_to_cursor():
    item = {
        "incident_id": "incident-arcgis",
        "incident_key": "surrey-permits:arcgis-400",
        "severity": "high",
        "tier": None,
        "source": "pipeline-runs-monitor",
        "step": "scrape-surrey-permits",
        "summary": "Pipeline step failed: scrape-surrey-permits",
        "error": "502 detail: ArcGIS Invalid query parameters",
        "context": {"pipeline_run_id": 123, "run_id": "abc"},
        "auto_fix_allowed": True,
    }

    result = _run_code_node(_code_node_js("Classify Tier"), item)

    assert result["tier"] == 1
    assert result["auto_fix_allowed"] is True
