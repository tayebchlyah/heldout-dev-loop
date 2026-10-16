"""The files that hold the real stage and task times and the doing tasks (Board Robustness D6, D7, D9, D10)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from types import ModuleType


def birth(path: Path) -> float | None:
    """The birth time of `path` (macOS `st_birthtime`), else its modification time; None when it is missing."""
    try:
        st = path.stat()
    except OSError:
        return None
    return getattr(st, "st_birthtime", st.st_mtime)


def mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def commit_time(dl: ModuleType, worktree: Path, line: str) -> float | None:
    """The committer date of `<b>` in `commits <a>..<b>` of a ledger line, when `<b>` is a commit hash."""
    found = re.search(r"\bcommits\s+\S+?\.\.([^\s,;)]+)", line)
    if not found or not re.fullmatch(r"[0-9a-f]{7,40}", found.group(1)):
        return None
    out = dl.git(worktree, "show", "-s", "--format=%cI", found.group(1))
    try:
        return datetime.fromisoformat(out).timestamp() if out else None
    except ValueError:
        return None


def native_doing(config_dir: Path, session: str | None) -> set[int]:
    """D9 (a), D10: the task numbers of the `in_progress` items `Task N: ...` in the Claude task list of `session`.
    A missing or bad file gives nothing."""
    if not session or Path(session).name != session or session in (".", ".."):
        return set()
    doing: set[int] = set()
    for path in sorted((config_dir / "tasks" / session).glob("*.json")):
        try:
            item = json.loads(path.read_text())
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if not isinstance(item, dict) or item.get("status") != "in_progress" or not isinstance(item.get("subject"), str):
            continue
        number = re.match(r"Task (\d+):", item["subject"])
        if number:
            doing.add(int(number.group(1)))
    return doing


def briefs(sdd: Path) -> set[int]:
    """D9 (b): the numbers of the `task-N-brief.md` files in the SDD workspace."""
    return {int(m.group(1)) for p in sdd.glob("task-*-brief.md") if (m := re.fullmatch(r"task-(\d+)-brief\.md", p.name))}
