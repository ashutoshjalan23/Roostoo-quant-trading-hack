"""Regression tests for staged and working-tree secret scanning."""

from __future__ import annotations

from scripts import check_secrets


def test_staged_scan_includes_renamed_files(monkeypatch):
    commands = []
    monkeypatch.setattr(
        check_secrets,
        "_git",
        lambda *args: commands.append(args) or "renamed.py\0",
    )
    content = f"{('api' + '_key')} = {'x' * 20}\n".encode()
    monkeypatch.setattr(check_secrets, "_git_bytes", lambda *_args: content)

    findings, paths = check_secrets.scan_staged()

    assert paths == ["renamed.py"]
    assert any(finding.rule == "key-shaped assignment" for finding in findings)
    assert "--diff-filter=ACMR" in commands[0]


def test_staged_scan_fails_closed_on_oversized_files(monkeypatch):
    monkeypatch.setattr(check_secrets, "MAX_FILE_BYTES", 4)
    monkeypatch.setattr(check_secrets, "_git", lambda *_args: "large.txt\0")
    monkeypatch.setattr(check_secrets, "_git_bytes", lambda *_args: b"12345")

    findings, paths = check_secrets.scan_staged()

    assert paths == ["large.txt"]
    assert len(findings) == 1
    assert findings[0].rule == "unscanned file"
    assert "scan limit" in findings[0].redacted_line
