"""Held-out seal: the protected acceptance folder and the git helpers its commands share."""

from __future__ import annotations

import subprocess
from pathlib import Path

ACCEPTANCE_DIR = "tests/acceptance"
SEAL_TRAILER = "Dev-Loop-Seal"
PATCH_TRAILER = "Dev-Loop-Seal-Patch"


def run_git(repo: Path, *args: str, stdin: str | None = None,
            env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """`git` with paths unquoted (spaces, non-ASCII), output as text (bytes that are not UTF-8 replaced)."""
    return subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=off", *args], input=stdin,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


def git_dir(repo: Path) -> Path:
    return Path(run_git(repo, "rev-parse", "--path-format=absolute", "--git-dir").stdout.strip())


def merge_base(repo: Path) -> str | None:
    for upstream in ("origin/main", "main"):
        result = run_git(repo, "merge-base", "HEAD", upstream)
        if result.returncode == 0:
            return result.stdout.strip()
    return None
