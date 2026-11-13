#!/usr/bin/env python3
"""`stack-detect`, the presets.json reader and the `stacks` lines of check_setup.py."""

from __future__ import annotations

import argparse
import json
import os
from fnmatch import fnmatchcase
from pathlib import Path

import source_dirs
from capabilities import COMPOSE_FILES
from devloop import emit
from seal import ACCEPTANCE_DIR

PRESETS = Path(__file__).resolve().parent.parent / "presets.json"
HELD = ACCEPTANCE_DIR + "/"


def load_presets(path: Path = PRESETS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def by_name(data: dict) -> dict[str, dict]:
    return {preset["name"]: preset for preset in data["presets"]}


def unit_name(path: str) -> str:
    return "root" if path == "." else path.rstrip("/").replace("/", "-")


def _skipped(rel: str) -> bool:
    name = rel.rstrip("/").rsplit("/", 1)[-1]
    return name.startswith(".") or name == "node_modules" or rel == HELD


def _folders(repo: Path, rel: str) -> list[str]:
    try:
        entries = sorted(os.scandir(repo / rel), key=lambda e: e.name)
    except OSError:
        return []
    found = []
    for entry in entries:
        child = f"{rel}{entry.name}/"
        if entry.is_dir(follow_symlinks=False) and not _skipped(child):
            found.append(child)
    return found


def _candidates(repo: Path) -> list[str]:
    top = _folders(repo, "")
    children = _folders(repo, "backend/") if "backend/" in top else []
    return ["."] + top + children


def folder_matches(folder: Path, preset: dict) -> bool:
    try:
        names = [e.name for e in os.scandir(folder) if e.is_file()]
    except OSError:
        return False
    return any(all(any(fnmatchcase(name, glob) for name in names) for glob in alternative) for alternative in preset["detect"])


def _holds(ancestor: str, path: str) -> bool:
    return ancestor == "." or (path != "." and path.startswith(ancestor))


def _ui(folder: Path, frameworks: list[str]) -> bool:
    try:
        data = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    for key in ("dependencies", "devDependencies"):
        deps = data.get(key)
        if isinstance(deps, dict) and any(name in deps for name in frameworks):
            return True
    return False


def _holds_a_file(repo: Path, folder: str) -> bool:
    for root, dirs, files in os.walk(repo / folder):
        if files:
            return True
        rel = Path(root).relative_to(repo).as_posix() + "/"
        dirs[:] = [d for d in dirs if not _skipped(f"{rel}{d}/")]
    return False


def _covered(folder: str, paths: list[str]) -> bool:
    return any(folder.startswith(p) or p.startswith(folder) for p in paths)


def detect(repo: Path, data: dict) -> dict:
    presets = data["presets"]
    matched: list[tuple[str, dict]] = []
    for path in _candidates(repo):
        folder = repo if path == "." else repo / path
        preset = next((p for p in presets if folder_matches(folder, p)), None)
        if preset:
            matched.append((path, preset))
    units = [(path, preset) for path, preset in matched
             if not any(q != path and q_preset["family"] == preset["family"] and _holds(q, path) for q, q_preset in matched)]
    units.sort(key=lambda item: (item[0] != ".", item[0]))
    stacks = [{"path": path, "preset": preset["name"], "unit": unit_name(path),
               "commands": {"lint": preset["lint"], "typecheck": preset["typecheck"], "test": preset["test"],
                            "junit": {"command": preset["junit"]["command"], "package": preset["junit"]["package"]}}}
              for path, preset in units]
    capabilities = []
    if any(_ui(repo if path == "." else repo / path, data["ui_frameworks"]) for path, _ in matched):
        capabilities.append("ui")
    if any((repo / name).is_file() for name in COMPOSE_FILES):
        capabilities.append("backend")
    dirs = {path for path, _ in matched if path != "."}
    for path, preset in units:
        if path == ".":
            dirs.update(preset["source_dirs"])
            if preset["test_dir"]:
                dirs.add(preset["test_dir"])
    source_dirs = sorted(d for d in dirs if not (d == HELD or d.startswith(HELD)))
    claimed = [path for path, _ in matched if path != "."] + source_dirs
    unknown = [folder for folder in _folders(repo, "")
               if not _covered(folder, claimed) and _holds_a_file(repo, folder)]
    return {"stacks": stacks, "capabilities": capabilities, "source_dirs": source_dirs, "unknown": unknown}


def validate(value: object) -> str | None:
    if not isinstance(value, list):
        return 'must be a list of {"path", "preset"} objects'
    seen: list[str] = []
    for entry in value:
        shown = json.dumps(entry, ensure_ascii=False)
        if not isinstance(entry, dict) or set(entry) != {"path", "preset"}:
            return f"{shown} must hold exactly the keys path and preset"
        path, preset = entry["path"], entry["preset"]
        path_shown = json.dumps(path, ensure_ascii=False)
        if path != ".":
            rule = source_dirs.validate([path])
            if rule:
                return rule
        if preset is not None and not isinstance(preset, str):
            return f"preset of {path_shown} must be a preset name or null"
        folded = source_dirs.fold(path)
        if folded in seen:
            return f"{path_shown} repeats another entry"
        seen.append(folded)
    return None


def setup(repo: Path) -> list[str]:
    """The check_setup.py failure lines of the `stacks` key, read from this checkout."""
    try:
        config = json.loads((repo / "dev-loop.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(config, dict) or "stacks" not in config:
        return []
    rule = validate(config["stacks"])
    if rule:
        return [f"stacks in dev-loop.json is invalid: {rule}"]
    try:
        presets = by_name(load_presets())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"stacks: presets.json cannot be read: {exc}"]
    entries = config.get("source_dirs")
    covered = entries if "source_dirs" in config and source_dirs.validate(entries) is None else None
    lines = []
    for entry in config["stacks"]:
        path, name = entry["path"], entry["preset"]
        preset = presets.get(name) if isinstance(name, str) else None
        if isinstance(name, str) and preset is None:
            lines.append(f"stacks in dev-loop.json names an unknown preset: {name}")
        if path != "." and not (repo / path).is_dir():
            lines.append(f"stacks path {path} is not a folder in this checkout")
            continue
        if covered is None:
            continue
        if path != ".":
            if not source_dirs.matches(covered, path):
                lines.append(f'stacks path {path} is not in source_dirs: add "{path}"')
        elif preset:
            for folder in preset["source_dirs"]:
                if (repo / folder).is_dir() and not source_dirs.matches(covered, folder):
                    lines.append(f'stacks path . preset {name}: {folder} is not in source_dirs: add "{folder}"')
    return lines


def cmd_stack_detect(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    if not repo.is_dir():
        return emit({"error": f"stack-detect: {args.repo} is not a folder"}, 2)
    try:
        data = load_presets()
    except (OSError, ValueError) as exc:
        return emit({"error": f"presets.json cannot be read: {exc}"}, 1)
    return emit(detect(repo, data), 0)
