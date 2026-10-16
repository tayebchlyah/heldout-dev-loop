"""`<git-common-dir>/dev-loop/board-state.json`: the board state of one repo (Board Robustness D1, D2, D8)."""

from __future__ import annotations

import json
from pathlib import Path

from . import store

NAME = "board-state.json"


def load(folder: Path) -> dict:
    """The saved state over the default. A missing, unreadable or non-object file gives the default."""
    data: dict = {"features": {}, "sessions": {}, "last_run": None}
    try:
        saved = json.loads((folder / NAME).read_text())
    except (OSError, ValueError):
        return data
    if not isinstance(saved, dict):
        return data
    data.update(saved)
    if not isinstance(data["features"], dict):
        data["features"] = {}
    return data


def save(folder: Path, data: dict) -> None:
    store.write_file(folder, NAME, json.dumps(data, indent=1) + "\n")


def entry(data: dict, slug: str) -> dict:
    """The state entry of `slug`, created empty."""
    if not isinstance(data["features"].get(slug), dict):
        data["features"][slug] = {}
    return data["features"][slug]


def seed(entry: dict, note: dict) -> None:
    """D2: copy the values the state keeps from an own note that parses. D8: backfilled when its lowest stage is 5+."""
    times = note.get("stage_times")
    if isinstance(times, dict):
        entry["stage_times"] = dict(times)
    tasks = note.get("tasks")
    if isinstance(tasks, list):
        entry["tasks"] = {str(t["n"]): {k: t[k] for k in ("started", "done") if t.get(k)}
                          for t in tasks if isinstance(t, dict) and isinstance(t.get("n"), int)}
    if isinstance(note.get("assumptions_baseline"), list):
        entry["assumptions_baseline"] = list(note["assumptions_baseline"])
    for key in ("shipped_at", "head", "waiting", "updated"):
        if isinstance(note.get(key), str):
            entry[key] = note[key]
    stages = [int(k) for k in entry.get("stage_times", {}) if str(k).isdigit()]
    entry["backfilled"] = bool(stages) and min(stages) >= 5
