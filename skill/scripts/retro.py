"""`devloop.py status` `review_due` and `devloop.py sessions`: the weekly review and the sessions retro reads.

D3 log rows, D4 due rule, D8 to D11 the session list.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import metrics

LOG = "Gardener Log.md"
DUE_DAYS = 7
CELL = re.compile(r"(?<!\\)\|")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
FIELDS = ("harness", "session", "path", "worktree", "feature", "start", "end", "bytes", "subagents", "stage",
          "first_request")
INJECTED = ("<", "# AGENTS.md instructions")


def cells(line: str) -> list[str]:
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [c.strip() for c in CELL.split(body)]


def run_dates(text: str) -> list[date]:
    found: list[date] = []
    columns: list[str] | None = None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            columns = None
            continue
        row = cells(line)
        if columns is None:
            if "Date" in row and "Verdict" in row and i + 1 < len(lines) and lines[i + 1].lstrip().startswith("|"):
                columns = row
            continue
        values = dict(zip(columns, row))
        when = values.get("Date", "")
        if values.get("Verdict") == "run" and ISO_DATE.fullmatch(when):
            try:
                found.append(date.fromisoformat(when))
            except ValueError:
                continue
    return found


def review_due(repo: Path, notes: Path, today: date) -> dict | None:
    """`{"last", "days", "log"}` when the weekly review is due, else None (D3, D4)."""
    from devloop import frontmatter, specs

    log = notes / LOG
    if not log.exists():
        if (notes / f".{LOG}.icloud").exists():
            return None
        if not any(frontmatter(path).get("shipped") for _, path in specs(notes)):
            return None
        dates = []
    else:
        try:
            dates = run_dates(log.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            dates = []
    if not dates:
        return {"last": None, "days": None, "log": str(log)}
    last = max(dates)
    days = (today - last).days
    return {"last": last.isoformat(), "days": days, "log": str(log)} if days >= DUE_DAYS else None


def utc(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def recent(paths: list[Path], start: float) -> list[Path]:
    return sorted(p for p in paths if p.stat().st_mtime >= start)


def item(harness: str, session: str, path: Path, records: list[dict], worktree: Path, feature: str | None,
         first: str | None) -> dict | None:
    times = [t for t in (metrics.parse_time(r.get("timestamp")) for r in records) if t is not None]
    if not times:
        return None
    return {"harness": harness, "session": session, "path": str(path), "worktree": str(worktree), "feature": feature,
            "start": min(times), "end": max(times), "bytes": path.stat().st_size, "subagents": 0, "stage": None,
            "first_request": first.strip()[:200] if first and first.strip() else None}


def claude_text(rec: dict) -> str | None:
    content = rec["message"].get("content")
    if isinstance(content, str):
        return content
    return next((b["text"] for b in content
                 if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)), None)


def claude_sessions(root: Path, worktrees: dict[Path, str | None], start: float) -> list[dict]:
    found = []
    scanned: set[Path] = set()
    for worktree, slug in worktrees.items():
        folders = [f for f in metrics.project_folders(root, [worktree]) if f not in scanned]
        if not folders:
            continue
        folder = folders[0]
        scanned.add(folder)
        for path in recent(list(folder.glob("*.jsonl")), start):
            records = metrics.read_records(path)
            first = next((claude_text(r) for r in records if metrics.typed(r)), None)
            listed = item("claude", path.stem, path, records, worktree, slug, first)
            if listed is None:
                continue
            for sub in sorted((folder / path.stem / "subagents").glob("agent-*.jsonl")):
                listed["bytes"] += sub.stat().st_size
                listed["subagents"] += 1
            found.append(listed)
    return found


def codex_text(rec: dict) -> str | None:
    payload = rec.get("payload")
    if rec.get("type") != "response_item" or not isinstance(payload, dict) or payload.get("type") != "message" \
            or payload.get("role") != "user" or not isinstance(payload.get("content"), list):
        return None
    text = "\n".join(b["text"] for b in payload["content"]
                     if isinstance(b, dict) and b.get("type") == "input_text" and isinstance(b.get("text"), str)).strip()
    return text if text and not text.startswith(INJECTED) else None


def owner(cwd: str, worktrees: dict[Path, str | None]) -> Path | None:
    real = os.path.realpath(cwd)
    matches = [w for w in worktrees if real == str(w) or real.startswith(str(w).rstrip("/") + "/")]
    return max(matches, key=lambda w: len(str(w)), default=None)


def codex_sessions(root: Path, worktrees: dict[Path, str | None], start: float) -> list[dict]:
    mains: dict[str, dict] = {}
    children: list[tuple[str, Path]] = []
    for path in recent(list(root.rglob("rollout-*.jsonl")), start):
        records = metrics.read_records(path)
        payload = records[0].get("payload") if records and records[0].get("type") == "session_meta" else None
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
            continue
        source = payload.get("source")
        if isinstance(source, dict):
            spawn = source.get("subagent", {}).get("thread_spawn") if isinstance(source.get("subagent"), dict) else None
            parent = spawn.get("parent_thread_id") if isinstance(spawn, dict) else None
            if isinstance(parent, str):
                children.append((parent, path))
            continue
        worktree = owner(payload["cwd"], worktrees) if isinstance(payload.get("cwd"), str) else None
        if not isinstance(source, str) or worktree is None:
            continue
        first = next((t for t in map(codex_text, records) if t), None)
        listed = item("codex", payload["id"], path, records, worktree, worktrees[worktree], first)
        if listed is not None:
            mains[payload["id"]] = listed
    for parent, path in children:
        if parent in mains:
            mains[parent]["bytes"] += path.stat().st_size
            mains[parent]["subagents"] += 1
    return list(mains.values())


def stage_times(repo: Path) -> dict:
    from devloop import git

    common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    try:
        features = json.loads((Path(common or ".") / "dev-loop" / "board-state.json").read_text()).get("features")
    except (OSError, ValueError, AttributeError):
        return {}
    return features if isinstance(features, dict) else {}


def stage_at(entry: object, when: float) -> str | None:
    times = entry.get("stage_times") if isinstance(entry, dict) else None
    if not isinstance(times, dict):
        return None
    keyed = [(t, str(k)) for k, v in times.items() if (t := metrics.parse_time(v)) is not None and t <= when]
    return max(keyed)[1] if keyed else None


def sessions(repo: Path, since_days: int, env: Mapping[str, str], home: Path, now: datetime) -> list[dict]:
    """The Claude Code and Codex main sessions of every worktree of `repo` active since `now` minus the days (D8 to D11)."""
    import devloop
    from board import writer

    start = (now - timedelta(days=since_days)).timestamp()
    worktrees = writer.worktrees(devloop, repo)
    claude_root = Path(env["CLAUDE_CONFIG_DIR"]) / "projects" if env.get("CLAUDE_CONFIG_DIR") else home / ".claude" / "projects"
    codex_root = Path(env["CODEX_HOME"]) if env.get("CODEX_HOME") else home / ".codex"
    found = claude_sessions(claude_root, worktrees, start) + codex_sessions(codex_root / "sessions", worktrees, start)
    features = stage_times(repo)
    listed = []
    for s in found:
        if s["end"] < start:
            continue
        s["stage"] = stage_at(features.get(s["feature"]), s["start"]) if s["feature"] else None
        listed.append(s)
    listed.sort(key=lambda s: (-s["bytes"], s["start"], s["path"]))
    return [{**s, "start": utc(s["start"]), "end": utc(s["end"])} for s in listed]
