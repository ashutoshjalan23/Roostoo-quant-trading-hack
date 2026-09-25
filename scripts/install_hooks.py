#!/usr/bin/env python3
"""Install the pre-commit secret scanner into this repository's git hooks.

The repo is submitted publicly, so the scanner is the last line of defence against a
committed credential (README section 5.4, CLAUDE.md rule 9). Re-running is safe: an
existing qtrend hook is replaced, an unrecognised hook is left alone unless --force.
"""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
from pathlib import Path

MARKER = "# qtrend pre-commit hook -- installed by scripts/install_hooks.py"

HOOK_TEMPLATE = """#!/bin/sh
{marker}
# Regenerate with: python scripts/install_hooks.py
exec "{python}" "$(git rev-parse --show-toplevel)/scripts/check_secrets.py" --staged
"""


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(f"install_hooks: git {' '.join(args)} failed: {result.stderr.strip()}\n")
        raise SystemExit(2)
    return result.stdout.strip()


def hooks_dir() -> Path:
    configured = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"], capture_output=True, text=True
    )
    if configured.returncode == 0 and configured.stdout.strip():
        return Path(_git("rev-parse", "--show-toplevel")) / configured.stdout.strip()
    return Path(_git("rev-parse", "--absolute-git-dir")) / "hooks"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--force", action="store_true", help="replace an unknown hook")
    args = parser.parse_args(argv)

    target = hooks_dir() / "pre-commit"
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        existing = target.read_text(encoding="utf-8", errors="replace")
        if MARKER not in existing and not args.force:
            sys.stderr.write(
                f"install_hooks: {target} exists and was not installed by this script.\n"
                "Inspect it, then re-run with --force to replace it.\n"
            )
            return 1

    python = Path(sys.executable).as_posix()
    hook = HOOK_TEMPLATE.format(marker=MARKER, python=python)
    target.write_text(hook, encoding="utf-8", newline="\n")
    mode = target.stat().st_mode
    target.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print(f"install_hooks: pre-commit hook installed at {os.fspath(target)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
