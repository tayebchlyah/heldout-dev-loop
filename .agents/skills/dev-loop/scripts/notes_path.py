#!/usr/bin/env python3
"""Resolve the notes folder for a repo from env or the main worktree files."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping


class NotesPathError(Exception):
    pass


class TrackedLocalFile(NotesPathError):
    """The main worktree `dev-loop.local.json` is tracked by git: a branch could change it."""


def git_path(repo: Path, *args: str) -> Path | None:
    try:
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    except OSError:
        return None
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


def worktrees(repo: Path) -> tuple[Path, Path]:
    """The worktree of repo and the main worktree."""
    worktree = git_path(repo, "rev-parse", "--show-toplevel") or repo
    common = git_path(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return worktree, common.parent if common else worktree


def tracked(main: Path) -> bool:
    try:
        return subprocess.run(["git", "-C", str(main), "ls-files", "--error-unmatch", "dev-loop.local.json"],
                              capture_output=True).returncode == 0
    except OSError:
        return False


def resolve(repo: Path, env: Mapping[str, str], home: Path) -> Path:
    """`DEV_LOOP_NOTES` (relative to the repo worktree), else `dev-loop.local.json`, then `notes` in
    `dev-loop.json`, both of the main worktree. A feature worktree's own files are never read."""
    worktree, main = worktrees(repo)
    found: tuple[str, Path, str] | None = None
    if env.get("DEV_LOOP_NOTES"):
        found = (env["DEV_LOOP_NOTES"], worktree, "DEV_LOOP_NOTES")
    elif tracked(main):
        raise TrackedLocalFile(f"{main / 'dev-loop.local.json'} is tracked by git: run git rm --cached dev-loop.local.json")
    for name, strict in (("dev-loop.local.json", True), ("dev-loop.json", False)):
        if found:
            break
        value = read_notes(main / name, strict)
        if value:
            found = (value, main, str(main / name))
    if not found:
        raise NotesPathError(f'no notes path for {repo}. Write {{"notes": "<path>"}} to {main / "dev-loop.local.json"}')
    path, source = expand(found[0], found[1], home), found[2]
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
