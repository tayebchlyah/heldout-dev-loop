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


def notes_value(text: str | bytes, label: str, strict: bool = True) -> str | None:
    try:
        value = json.loads(text).get("notes")
    except (ValueError, AttributeError) as exc:
        raise NotesPathError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, str) or not value:
        if not strict:
            return None
        raise NotesPathError(f"invalid {label}: no notes key")
    return value


def read_notes(file: Path) -> str | None:
    if not file.is_file():
        return None
    try:
        text = file.read_text()
    except OSError as exc:
        raise NotesPathError(f"invalid {file}: {exc}") from exc
    return notes_value(text, str(file))


def main_ref(repo: Path) -> str | None:
    for ref in ("refs/remotes/origin/main", "refs/heads/main"):
        try:
            probe = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", f"{ref}^{{commit}}"],
                                   capture_output=True)
        except OSError:
            return None
        if probe.returncode == 0:
            return ref
    return None


def read_main_ref_notes(repo: Path) -> tuple[str, str] | None:
    ref = main_ref(repo)
    if not ref:
        return None
    label = f"{ref}:dev-loop.json"
    try:
        shown = subprocess.run(["git", "-C", str(repo), "show", label], capture_output=True)
    except OSError:
        return None
    value = notes_value(shown.stdout, label, strict=False) if shown.returncode == 0 else None
    return (value, label) if value else None


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
    """`DEV_LOOP_NOTES` (relative to the repo worktree), else `dev-loop.local.json` read from disk in the main
    worktree, then `notes` in `dev-loop.json` read from the main ref, never from disk: a branch checked out in
    the main worktree cannot change it. No main ref or no file on it: no notes key. A feature worktree's own
    files are never read."""
    worktree, main = worktrees(repo)
    found: tuple[str, Path, str] | None = None
    if env.get("DEV_LOOP_NOTES"):
        found = (env["DEV_LOOP_NOTES"], worktree, "DEV_LOOP_NOTES")
    elif tracked(main):
        raise TrackedLocalFile(f"{main / 'dev-loop.local.json'} is tracked by git: run git rm --cached dev-loop.local.json")
    if not found:
        value = read_notes(main / "dev-loop.local.json")
        if value:
            found = (value, main, str(main / "dev-loop.local.json"))
    if not found:
        shared = read_main_ref_notes(repo)
        if shared:
            found = (shared[0], main, shared[1])
    if not found:
        raise NotesPathError(f'no notes path for {repo}. Write {{"notes": "<path>"}} to {main / "dev-loop.local.json"}')
    path, source = expand(found[0], found[1], home), found[2]
    if not path.is_dir():
        if source.startswith("refs/"):
            fix = f"Change notes in dev-loop.json on main, or write {main / 'dev-loop.local.json'}"
        else:
            fix = f"Create it or change {'DEV_LOOP_NOTES' if source == 'DEV_LOOP_NOTES' else f'the path in {source}'}"
        raise NotesPathError(f"notes folder does not exist: {path} (from {source}). {fix}")
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
