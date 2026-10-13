#!/usr/bin/env python3
"""Resolve the notes folder for a repo from env, local files, or the legacy layout."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Mapping


class NotesPathError(Exception):
    pass


def git_path(repo: Path, *args: str) -> Path | None:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    out = result.stdout.strip()
    return Path(out) if result.returncode == 0 and out else None


def expand(value: str, base: Path, home: Path) -> Path:
    if value == "~" or value.startswith("~/"):
        return home / value[2:]
    path = Path(value)
    return path if path.is_absolute() else base / path


def read_notes(file: Path, strict: bool = True) -> str | None:
    if not file.is_file():
        return None
    try:
        value = json.loads(file.read_text()).get("notes")
    except (OSError, ValueError, AttributeError) as exc:
        raise NotesPathError(f"invalid {file}: {exc}") from exc
    if not isinstance(value, str) or not value:
        if not strict:
            return None
        raise NotesPathError(f"invalid {file}: no notes key")
    return value


def legacy_notes(worktree: Path, home: Path) -> Path | None:
    # Legacy 1.4 layout. Remove in 1.6.
    try:
        config = json.loads((home / ".agents" / "dev-loop.config.json").read_text())
        match = re.search(r"^vault_notes:\s*(.+)$", (worktree / "AGENTS.md").read_text(), re.MULTILINE)
    except (OSError, ValueError):
        return None
    vault = config.get("vault") if isinstance(config, dict) else None
    if not vault or not match:
        return None
    return expand(str(vault), worktree, home) / match.group(1).strip()


def resolve(repo: Path, env: Mapping[str, str], home: Path) -> Path:
    worktree = git_path(repo, "rev-parse", "--show-toplevel") or repo
    common = git_path(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    main = common.parent if common else worktree
    found: tuple[str, Path, str] | None = None
    if env.get("DEV_LOOP_NOTES"):
        found = (env["DEV_LOOP_NOTES"], worktree, "DEV_LOOP_NOTES")
    for root, name, strict in (
        (worktree, "dev-loop.local.json", True),
        (main, "dev-loop.local.json", True),
        (worktree, "dev-loop.json", False),
    ):
        if found:
            break
        value = read_notes(root / name, strict)
        if value:
            found = (value, root, str(root / name))
    if found:
        path, source = expand(found[0], found[1], home), found[2]
    else:
        path = legacy_notes(worktree, home)
        source = "dev-loop.config.json and AGENTS.md"
        if path is None:
            raise NotesPathError(
                f'no notes path for {repo}. Write {{"notes": "<path>"}} to {main / "dev-loop.local.json"}'
            )
    if not path.is_dir():
        change = "DEV_LOOP_NOTES" if source == "DEV_LOOP_NOTES" else f"the path in {source}"
        raise NotesPathError(f"notes folder does not exist: {path} (from {source}). Create it or change {change}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    args = parser.parse_args()
    try:
        print(resolve(Path(args.repo).resolve(), os.environ, Path.home()))
    except NotesPathError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
