#!/usr/bin/env python3
"""The `source_dirs` key of dev-loop.json: validator, matcher, main ref read, top-level folder list."""

from __future__ import annotations

import json
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from notes_path import main_ref

DEFAULT: tuple[str, ...] = ("src/", "app/", "backend/", "frontend/")
RESERVED = frozenset("\\*?[]{}!$'\"`():=&;<>|")
HELD_OUT = "tests/" + "acceptance/"


def fold(text: str) -> str:
    """The NFC, case-folded form of a path or entry, the form every match compares."""
    return unicodedata.normalize("NFC", text).casefold()


def _is_folder(entry: object) -> bool:
    if not isinstance(entry, str) or not entry.endswith("/") or entry.startswith("/"):
        return False
    segments = entry[:-1].split("/")
    return all(part and part not in (".", "..") for part in segments)


def _reserved(entry: str) -> bool:
    return any(ch.isspace() or unicodedata.category(ch) == "Cc" or ch in RESERVED for ch in entry)


def validate(value: object) -> str | None:
    if not isinstance(value, list):
        return "must be a list of folder paths"
    seen: list[str] = []
    for entry in value:
        shown = json.dumps(entry, ensure_ascii=False)
        if not _is_folder(entry):
            return f"{shown} must be a folder path from the repo root ending in /"
        if _reserved(entry):
            return f"{shown} holds a reserved character"
        folded = fold(entry)
        if folded == HELD_OUT or folded.startswith(HELD_OUT):
            return f"{shown} is the held-out suite or under it"
        if folded in seen:
            return f"{shown} repeats another entry"
        seen.append(folded)
    return None


def matches(entries: Sequence[str], rel: str) -> str | None:
    """The first entry that a repo-relative path starts with or equals without its final `/`, else None."""
    folded = fold(rel)
    for entry in entries:
        target = fold(entry)
        if folded.startswith(target) or folded == target[:-1]:
            return entry
    return None


def in_dot_folder(path: str) -> bool:
    return path.startswith(".") and "/" in path


@dataclass(frozen=True)
class Main:
    ref: str | None
    state: str
    entries: tuple[str, ...] = ()
    rule: str | None = None

    def error(self) -> str | None:
        if self.state == "not-object":
            return f"dev-loop.json at {self.ref} is not a JSON object: source_dirs cannot be read"
        if self.state == "invalid":
            return f"source_dirs at {self.ref} is invalid: {self.rule}"
        return None


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True)


def read_main(repo: Path) -> Main:
    """The `source_dirs` key of `dev-loop.json` at the main ref, never the checkout file."""
    full = main_ref(repo)
    if not full:
        return Main(None, "no-ref")
    ref = full.removeprefix("refs/remotes/").removeprefix("refs/heads/")
    blob = f"{full}:dev-loop.json"
    if _git(repo, "cat-file", "-e", blob).returncode != 0:
        return Main(ref, "no-key")
    shown = _git(repo, "cat-file", "-p", blob)
    try:
        data = json.loads(shown.stdout)
    except ValueError:
        return Main(ref, "not-object")
    if not isinstance(data, dict):
        return Main(ref, "not-object")
    if "source_dirs" not in data:
        return Main(ref, "no-key")
    rule = validate(data["source_dirs"])
    if rule:
        return Main(ref, "invalid", rule=rule)
    return Main(ref, "configured", tuple(data["source_dirs"]))


def top_folders(repo: Path) -> list[str]:
    try:
        listed = _git(repo, "ls-files", "-z")
    except OSError:
        return []
    if listed.returncode != 0:
        return []
    paths = listed.stdout.decode("utf-8", "surrogateescape").split("\0")
    return sorted({p.split("/", 1)[0] + "/" for p in paths if "/" in p and not p.startswith(".")})


def _shown(entries: Sequence[str]) -> str:
    return " ".join(entries) or "no folder"


def setup(repo: Path) -> tuple[list[str], list[str], list[str]]:
    """The check_setup.py lines of the key: failures, user actions that fail the verdict, notices."""
    try:
        config = json.loads((repo / "dev-loop.json").read_text())
    except (OSError, ValueError):
        config = None
    has = isinstance(config, dict) and "source_dirs" in config
    rule = validate(config["source_dirs"]) if has else None
    failures = [f"source_dirs in dev-loop.json is invalid: {rule}"] if rule else []
    main = read_main(repo)
    ref = main.ref
    if main.state == "not-object":
        return failures + [f"dev-loop.json at {ref} is not a JSON object. Every spec-tester call is denied until a valid file reaches {ref}"], [], []
    if main.state == "invalid":
        return failures + [f"source_dirs at {ref} is invalid: {main.rule}. Every spec-tester call is denied until a valid value reaches {ref}"], [], []
    if not has:
        if main.state == "configured":
            return failures, [], [f"no source_dirs in this checkout: the spec-tester wall at {ref} uses {_shown(main.entries)}. Rebase onto {ref}"]
        folders = top_folders(repo)
        if not folders:
            return failures, [], []
        return failures, [f"source_dirs missing in dev-loop.json: the spec-tester wall uses {_shown(DEFAULT)}. Top-level folders: {' '.join(folders)}. "
                          'List in "source_dirs" each folder that holds implementation. An entry matches from the repo root only'], []
    if rule:
        return failures, [], []
    if main.state == "no-ref":
        return failures, [], [f"no main ref: the spec-tester wall uses {_shown(DEFAULT)}"]
    if main.state == "configured" and list(main.entries) == config["source_dirs"]:
        return failures, [], []
    used = main.entries if main.state == "configured" else DEFAULT
    return failures, [], [f"the spec-tester wall at {ref} uses {_shown(used)} until this dev-loop.json change reaches {ref}"]
