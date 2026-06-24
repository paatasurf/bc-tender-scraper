"""Unit tests for exported n8n error workflow incident normalization."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


WORKFLOW_PATH = Path("n8n/workflows/error-workflow.json")


def _prepare_incident(raw: dict) -> dict:
    workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
    code = next(
        node["parameters"]["jsCode"]
        for node in workflow["nodes"]
        if node["name"] == "Prepare Incident Payload"
    )
    script = """
const fs = require('fs');
const { code, raw } = JSON.parse(fs.readFileSync(0, 'utf8'));
const fn = new Function('$input', code);
const result = fn({ first: () => ({ json: raw }) });
process.stdout.write(JSON.stringify(result[0].json));
"""
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps({"code": code, "raw": raw}),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_prepare_incident_extracts_alternate_n8n_error_shape():
    incident = _prepare_incident(
        {
            "workflowName": "TenderScope - Building Permits",
            "execution": {
                "id": "123",
                "error": {
                    "description": "HTTP 502: Vancouver permits request failed",
                    "nodeName": "Fetch Building Permits",
                },
            },
        }
    )

    assert incident["source"] == "n8n-error:TenderScope - Building Permits"
    assert incident["step"] == "Fetch Building Permits"
    assert incident["error"] == "HTTP 502: Vancouver permits request failed"
    assert incident["context"]["execution_id"] == "123"
    assert incident["context"]["node_name"] == "Fetch Building Permits"
    assert "raw_payload_sample" not in incident["context"]


def test_prepare_incident_keeps_bounded_payload_sample_for_unknowns():
    incident = _prepare_incident(
        {
            "workflow": {"id": "9LFwN8lBr9zGabgu", "name": "TenderScope - Building Permits"},
            "execution": {"id": "456"},
        }
    )

    assert incident["step"] == "unknown-node"
    assert incident["error"] == "unknown error"
    assert "TenderScope - Building Permits" in incident["context"]["raw_payload_sample"]
