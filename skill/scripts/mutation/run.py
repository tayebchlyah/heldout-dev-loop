#!/usr/bin/env python3
"""`devloop.py mutation`: the changed critical lines of each `mutation` entry of dev-loop.json."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
import re
import subprocess
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path

import source_dirs
import stacks
from devloop import emit

ADAPTERS: tuple[str, ...] = ("mutmut", "stryker", "mutago")
ENTRY_KEYS = frozenset({"path", "critical", "adapter", "command"})
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
ESCAPES = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34}
DIFF = ("diff", "--no-ext-diff", "--no-color", "-U0", "-M", "--diff-filter=AMR", "--src-prefix=a/",
        "--dst-prefix=b/", "--ignore-submodules")


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def validate(value: object) -> str | None:
    if value is False:
        return None
    if not isinstance(value, list):
        return "must be false or a list of entries"
    seen: list[str] = []
    for entry in value:
        shown = json.dumps(entry, ensure_ascii=False)
        if not isinstance(entry, dict) or not {"path", "critical"} <= set(entry) <= ENTRY_KEYS:
            return f"{shown} must hold the keys path, critical and one of adapter, command"
        if ("adapter" in entry) == ("command" in entry):
            return f"{shown} must hold exactly one of adapter and command"
        for key in ("adapter", "command"):
            if key in entry and not _non_empty(entry[key]):
                return f"{key} of {shown} must be a non-empty string"
        path = entry["path"]
        if path != "." and (rule := source_dirs.validate([path])):
            return rule
        critical = entry["critical"]
        if not isinstance(critical, list) or not critical or not all(_non_empty(g) for g in critical):
            return f"critical of {shown} must be a non-empty list of non-empty strings"
        if all(g.startswith("!") for g in critical):
            return f"critical of {shown} needs one glob without !"
        folded = source_dirs.fold(path)
        if folded in seen:
            return f"{json.dumps(path, ensure_ascii=False)} repeats another entry"
        seen.append(folded)
    return None


def glob_match(path: str, pattern: str) -> bool:
    parts, globs = tuple(path.split("/")), tuple(pattern.split("/"))

    @lru_cache(maxsize=None)
    def walk(i: int, j: int) -> bool:
        if j == len(globs):
            return i == len(parts)
        if globs[j] == "**":
            return walk(i, j + 1) or (i < len(parts) and walk(i + 1, j))
        return i < len(parts) and fnmatchcase(parts[i], globs[j]) and walk(i + 1, j + 1)

    return walk(0, 0)


def in_critical(path: str, globs: list[str]) -> bool:
    hit = any(glob_match(path, g) for g in globs if not g.startswith("!"))
    return hit and not any(glob_match(path, g[1:]) for g in globs if g.startswith("!"))


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True)


def _unquote(text: str) -> str:
    raw, i = bytearray(), 1
    while i < len(text) - 1:
        ch = text[i]
        if ch != "\\":
            raw += ch.encode()
            i += 1
        elif text[i + 1] in "01234567":
            raw.append(int(text[i + 1:i + 4], 8))
            i += 4
        else:
            raw.append(ESCAPES.get(text[i + 1], ord(text[i + 1])))
            i += 2
    return raw.decode("utf-8", errors="replace")


def _target(rest: str) -> str:
    if rest.startswith('"'):
        return _unquote(rest).removeprefix("b/")
    return rest.removesuffix("\t").removeprefix("b/")


def _merge(ranges: list[list[int]]) -> list[list[int]]:
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def changed_lines(repo: Path, base: str, head: str) -> dict[str, list[list[int]]]:
    done = _git(repo, "-c", "core.quotePath=false", *DIFF, f"{base}..{head}")
    found: dict[str, list[list[int]]] = {}
    path: str | None = None
    in_hunk = False
    for line in done.stdout.decode("utf-8", errors="replace").split("\n"):
        if line.startswith("diff --git "):
            path, in_hunk = None, False
        elif in_hunk and not line.startswith("@@"):
            continue
        elif not in_hunk and line.startswith("+++ "):
            path = _target(line[4:])
        elif match := HUNK.match(line):
            in_hunk = True
            start, count = int(match[1]), int(match[2] if match[2] is not None else 1)
            span = [max(start, 1), max(start, 1)] if count == 0 else [start, start + count - 1]
            if path is not None:
                found.setdefault(path, []).append(span)
    return {name: _merge(ranges) for name, ranges in found.items()}


def _resolve(repo: Path, rev: str) -> str | None:
    done = _git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    return done.stdout.decode().strip() if done.returncode == 0 else None


def _read_config(repo: Path, head: str) -> dict | str:
    shown = _git(repo, "show", f"{head}:dev-loop.json")
    if shown.returncode != 0:
        return {}
    try:
        data = json.loads(shown.stdout)
    except ValueError:
        data = None
    return data if isinstance(data, dict) else f"dev-loop.json at {head} is not a JSON object"


def _setup(cause: str) -> int:
    return emit({"status": "setup", "cause": cause}, 1)


def _check_entries(repo: Path, head: str, value: list) -> str | None:
    for entry in value:
        name = entry.get("adapter")
        if name is not None and name not in ADAPTERS:
            return f"mutation entry {entry['path']}: unknown adapter {name}"
    for entry in value:
        if entry["path"] != "." and _git(repo, "cat-file", "-t", f"{head}:{entry['path']}").stdout.strip() != b"tree":
            return f"mutation in dev-loop.json is invalid: {entry['path']} is not a folder at {head}"
    return None


def _holds(entry_path: str, path: str) -> bool:
    return entry_path == "." or path.startswith(entry_path)


def _select(entries: list[dict], lines: dict[str, list[list[int]]]) -> tuple[list[dict], list[str]]:
    selected = [{"path": e["path"], **{k: e[k] for k in ("adapter", "command") if k in e}, "files": {}}
                for e in entries]
    outside: list[str] = []
    for path, ranges in sorted(lines.items()):
        owners = [i for i, e in enumerate(entries) if _holds(e["path"], path)]
        if not owners:
            continue
        owner = max(owners, key=lambda i: 0 if entries[i]["path"] == "." else len(entries[i]["path"]))
        folder = entries[owner]["path"]
        rel = path if folder == "." else path[len(folder):]
        if in_critical(rel, entries[owner]["critical"]):
            selected[owner]["files"][rel] = ranges
        else:
            outside.append(path)
    return selected, outside


def _uncovered(config: dict, entries: list[dict]) -> list[str]:
    listed = config.get("stacks")
    if not isinstance(listed, list) or stacks.validate(listed):
        return []
    return [s["path"] for s in listed
            if not any(_holds(source_dirs.fold(e["path"]), source_dirs.fold(s["path"])) for e in entries)]


def cmd_mutation(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    head = _resolve(repo, args.head)
    if head is None:
        return emit({"error": f"--head: {args.head} is not a commit"}, 2)
    if args.base:
        base = _resolve(repo, args.base)
    else:
        merged = _git(repo, "merge-base", args.main, head)
        base = merged.stdout.decode().strip() if merged.returncode == 0 else None
    if base is None:
        return emit({"error": "--base: no base commit (give --base or a --main branch that exists)"}, 2)
    if args.run_adapters:
        return emit({"error": "--run: not built"}, 2)
    config = _read_config(repo, head)
    if isinstance(config, str):
        return _setup(config)
    value = config.get("mutation", False)
    rule = validate(value)
    if rule:
        return _setup(f"mutation in dev-loop.json is invalid: {rule}")
    entries = value or []
    if cause := _check_entries(repo, head, entries):
        return _setup(cause)
    selected, outside = _select(entries, changed_lines(repo, base, head) if entries else {})
    return emit({"base": base, "head": head, "entries": selected, "outside": outside,
                 "none": _uncovered(config, entries) if entries else []}, 0)
