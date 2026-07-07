"""Unit tests for db/merge_dry_run_provenance.py error visibility."""

from __future__ import annotations

import subprocess

from db import merge_dry_run_provenance as provenance


def test_get_git_commit_sha_warns_and_returns_unknown_on_failure(monkeypatch, capsys) -> None:
    def _boom(*args, **kwargs):
        raise subprocess.SubprocessError("git missing")

    monkeypatch.setattr(provenance.subprocess, "run", _boom)

    assert provenance.get_git_commit_sha() == "unknown"
    err = capsys.readouterr().err
    assert "[provenance] Could not resolve git commit SHA" in err
    assert "git missing" in err
