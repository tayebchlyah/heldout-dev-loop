"""Fixtures of the mutation tests: a two-commit git repo, adapter scripts, the `devloop.py mutation` runner."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

from support import ROOT, git_in, write

DEVLOOP = ROOT / "skill" / "scripts" / "devloop.py"


def _put(repo: Path, files: dict[str, str | None]) -> None:
    for name, text in files.items():
        path = repo / name
        if text is None:
            path.unlink()
            continue
        write(path, text)
        if name.endswith("adapt"):
            path.chmod(0o755)


def make_repo(root: Path, files: dict[str, str]) -> Path:
    repo = root / "repo"
    repo.mkdir(parents=True)
    git_in(repo, "init", "-q", "-b", "main")
    _put(repo, dict(files))
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-q", "-m", "base")
    git_in(repo, "checkout", "-q", "-b", "feat/x")
    return repo


def commit(repo: Path, files: dict[str, str | None]) -> str:
    before = git_in(repo, "rev-parse", "HEAD").strip()
    _put(repo, files)
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-q", "-m", "change")
    return before


def run_mutation(repo: Path, *args: str, timeout: float | None = None) -> tuple[int, dict, str]:
    done = subprocess.run([sys.executable, str(DEVLOOP), "mutation", "--repo", str(repo), *args],
                          capture_output=True, text=True, timeout=timeout)
    return done.returncode, json.loads(done.stdout), done.stderr


def adapter_script(record: Path, output: str, exit_code: int = 0, stderr: str = "") -> str:
    return (f"#!/bin/sh\ncat > {shlex.quote(str(record))}\nprintf '%s\\n' {shlex.quote(stderr)} >&2\n"
            f"printf '%s\\n' {shlex.quote(output)}\nexit {exit_code}\n")
