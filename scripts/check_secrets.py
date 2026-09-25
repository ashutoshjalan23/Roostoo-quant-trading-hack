#!/usr/bin/env python3
"""Scan for credential-shaped strings before they reach a commit.

The repository is submitted publicly (README section 10.4, CLAUDE.md rule 9), so no
credential may ever be committed, logged or printed. This script therefore reports
*locations* only: a matched value is never written to stdout, only its length.

Modes:
    --staged   scan the staged content of added/copied/modified files (pre-commit hook)
    --all      scan every tracked and untracked non-ignored file in the working tree

Exit codes:
    0  clean
    1  findings (or .env is tracked by git)
    2  usage or environment error
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass

MAX_FILE_BYTES = 2_000_000

# A value matching this is structurally incapable of being a credential: it carries no
# alphanumeric content at all (e.g. "...", "****", "<>"). Nothing else is exempted --
# a word-based allowlist would be a hole in the scanner.
_NO_ALNUM = re.compile(r"^[\W_]*$")

PATTERNS: dict[str, re.Pattern[str]] = {
    "key-shaped assignment": re.compile(
        r"(?i)\b[a-z0-9_.\-]*"
        r"(?:api[_-]?key|secret|token|passwd|password|access[_-]?key|private[_-]?key)"
        r"[a-z0-9_.\-]*"
        r"\s*(?:=|:=|:)\s*"
        r"(?P<value>\"[^\"\n]{16,}\"|'[^'\n]{16,}'|[A-Za-z0-9+/=_\-]{16,})"
    ),
    "aws access key id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "github token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "telegram bot token": re.compile(r"\b[0-9]{8,10}:[A-Za-z0-9_-]{35}\b"),
    "discord bot token": re.compile(
        r"\b[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}\b"
    ),
    "json web token": re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    ),
    "pem private key": re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    "slack webhook url": re.compile(r"https://hooks\.slack\.com/services/\S+"),
    "discord webhook url": re.compile(
        r"https://(?:[A-Za-z0-9-]+\.)?discord(?:app)?\.com/api/webhooks/\S+"
    ),
    "telegram bot api url": re.compile(r"https://api\.telegram\.org/bot[A-Za-z0-9:_-]{10,}"),
    "generic webhook url": re.compile(
        r"https://[^\s\"']+/webhooks?/[A-Za-z0-9_\-/]{16,}"
    ),
}


@dataclass(frozen=True)
class Finding:
    path: str
    line_no: int
    rule: str
    redacted_line: str


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        sys.stderr.write(f"check_secrets: git {' '.join(args)} failed: {result.stderr.strip()}\n")
        raise SystemExit(2)
    return result.stdout


def _git_bytes(*args: str) -> bytes:
    result = subprocess.run(["git", *args], capture_output=True)
    if result.returncode != 0:
        sys.stderr.write(f"check_secrets: git {' '.join(args)} failed\n")
        raise SystemExit(2)
    return result.stdout


def _nul_list(output: str) -> list[str]:
    return [entry for entry in output.split("\0") if entry]


def _redact(line: str, start: int, end: int) -> str:
    stripped = line.rstrip("\r\n")
    head = stripped[:start].lstrip()
    tail = stripped[end:]
    return f"{head}[REDACTED {end - start} chars]{tail}".strip()


def scan_text(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in PATTERNS.items():
            for match in pattern.finditer(line):
                group = "value" if "value" in pattern.groupindex else 0
                value = match.group(group)
                if _NO_ALNUM.match(value.strip("\"'")):
                    continue
                start, end = match.start(group), match.end(group)
                findings.append(
                    Finding(path, line_no, rule, _redact(line, start, end))
                )
    return findings


def _decode(blob: bytes) -> str | None:
    if len(blob) > MAX_FILE_BYTES:
        return None
    if b"\0" in blob[:8192]:
        return None
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan_staged() -> tuple[list[Finding], list[str]]:
    paths = _nul_list(_git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACM"))
    findings: list[Finding] = []
    for path in sorted(paths):
        text = _decode(_git_bytes("show", f":{path}"))
        if text is None:
            continue
        findings.extend(scan_text(path, text))
    return findings, sorted(paths)


def scan_all() -> tuple[list[Finding], list[str]]:
    paths = _nul_list(_git("ls-files", "-z", "--cached", "--others", "--exclude-standard"))
    findings: list[Finding] = []
    scanned: list[str] = []
    for path in sorted(paths):
        try:
            with open(path, "rb") as handle:
                blob = handle.read()
        except OSError:
            continue
        text = _decode(blob)
        if text is None:
            continue
        scanned.append(path)
        findings.extend(scan_text(path, text))
    return findings, scanned


def env_is_tracked(staged_only: bool) -> bool:
    args = ["diff", "--cached", "--name-only", "-z"] if staged_only else ["ls-files", "-z"]
    return ".env" in _nul_list(_git(*args))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--staged", action="store_true", help="scan staged content")
    group.add_argument("--all", action="store_true", help="scan the whole working tree")
    args = parser.parse_args(argv)

    if not args.staged and not args.all:
        parser.error("choose --staged or --all")

    findings, scanned = (scan_staged() if args.staged else scan_all())
    tracked_env = env_is_tracked(staged_only=args.staged)

    if tracked_env:
        sys.stderr.write("check_secrets: .env is under version control. It must be git-ignored.\n")

    if findings:
        sys.stderr.write(f"check_secrets: {len(findings)} credential-shaped string(s) found.\n")
        sys.stderr.write("Values are redacted below by design; open the file to inspect.\n\n")
        for finding in findings:
            sys.stderr.write(f"  {finding.path}:{finding.line_no}: {finding.rule}\n")
            sys.stderr.write(f"    {finding.redacted_line}\n")
        sys.stderr.write("\nCredentials belong in .env only (CLAUDE.md rule 9).\n")

    if findings or tracked_env:
        return 1

    scope = "staged file" if args.staged else "file"
    print(f"check_secrets: clean ({len(scanned)} {scope}(s) scanned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
