"""`<git-common-dir>/dev-loop/board-state.json`: the board state of one repo (Board Robustness D1, D2, D8)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from . import store

NAME = "board-state.json"
SESSIONS = 50  # D12: the most sessions the map keeps.
AGENT_IDLE_MINUTES = 30  # A subagent with no transcript activity for this long no longer runs: a killed one fires no SubagentStop.
# The note fields the entry holds itself; `digest` covers the others.
HELD = ("stage_times", "tasks", "assumptions_baseline", "shipped_at", "head", "backfilled", "waiting", "updated")


def digest(fields: dict) -> str:
    """D1: sha256 hex of the JSON (sorted keys) of the note fields the entry does not hold."""
    rest = {k: v for k, v in fields.items() if k not in HELD}
    tasks = fields.get("tasks")
    if isinstance(tasks, list):  # The entry holds the task times; the titles and states go into the digest.
        rest["tasks"] = [{k: v for k, v in t.items() if k not in ("started", "done")} if isinstance(t, dict) else t
                         for t in tasks]
    return hashlib.sha256(json.dumps(rest, sort_keys=True).encode()).hexdigest()


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
    if not isinstance(data["sessions"], dict):
        data["sessions"] = {}
    return data


def save(folder: Path, data: dict) -> None:
    store.write_file(folder, NAME, json.dumps(data, indent=1) + "\n")


def save_last_run(folder: Path, at: str, skipped: list[dict]) -> None:
    """D5: save `last_run` only, every other key kept. The caller holds the lock or needs none."""
    folder.mkdir(parents=True, exist_ok=True)
    data = load(folder)
    data["last_run"] = {"at": at, "skipped": skipped}
    save(folder, data)


def map_session(data: dict, session: object, slug: str, now: str) -> None:
    """D12: map `session` to `slug`, seen at `now`. A non-string or empty session is ignored. The 50 sessions with the
    highest `last_seen` stay."""
    if not isinstance(session, str) or not session:
        return
    sessions = data["sessions"]
    old = sessions.get(session)
    agents = old.get("agents") if isinstance(old, dict) else None
    sessions[session] = {"feature": slug, "last_seen": now, **({"agents": agents} if agents else {})}
    trim(sessions)


def trim(sessions: dict) -> None:
    while len(sessions) > SESSIONS:
        del sessions[min(sessions, key=lambda s: str(sessions[s].get("last_seen")) if isinstance(sessions[s], dict) else "")]


def agent_started(data: dict, session: object, agent: object, now: str, stamp: float,
                  transcript_path: object = None) -> None:
    """Record subagent `agent` of `session` as running since `stamp` (epoch), with its transcript, which Claude writes
    at `<dirname(transcript_path)>/<session>/subagents/agent-<agent>.jsonl`. A non-string or empty id is ignored."""
    if not isinstance(session, str) or not session or not isinstance(agent, str) or not agent:
        return
    item = data["sessions"].get(session)
    item = item if isinstance(item, dict) else {}
    agents = item.get("agents") if isinstance(item.get("agents"), dict) else {}
    log = None
    if isinstance(transcript_path, str) and transcript_path:
        log = str(Path(transcript_path).parent / session / "subagents" / f"agent-{agent}.jsonl")
    data["sessions"][session] = {**item, "last_seen": now, "agents": {**agents, agent: {"at": stamp, "transcript": log}}}
    trim(data["sessions"])


def agent_stopped(data: dict, session: object, agent: object) -> None:
    """Drop subagent `agent` of `session`. A None `agent` changes nothing."""
    item = data["sessions"].get(session) if isinstance(session, str) else None
    if not isinstance(item, dict) or not isinstance(item.get("agents"), dict) or not isinstance(agent, str):
        return
    item["agents"].pop(agent, None)
    if not item["agents"]:
        del item["agents"]


def running_agents(data: dict, session: object, now: float) -> list[str]:
    """The subagents of `session` whose last activity (the later of the start and the transcript mtime, epoch) is
    less than AGENT_IDLE_MINUTES before `now`."""
    item = data["sessions"].get(session) if isinstance(session, str) else None
    agents = item.get("agents") if isinstance(item, dict) else None
    if not isinstance(agents, dict):
        return []
    running = []
    for agent, info in agents.items():
        if not isinstance(info, dict) or not isinstance(info.get("at"), (int, float)):
            continue
        last = info["at"]
        try:
            if isinstance(info.get("transcript"), str):
                last = max(last, os.stat(info["transcript"]).st_mtime)
        except OSError:
            pass
        if now - last < AGENT_IDLE_MINUTES * 60:
            running.append(agent)
    return running


def feature_of(data: dict, session: object) -> str | None:
    """D12: the feature `session` is mapped to, else None."""
    item = data["sessions"].get(session) if isinstance(session, str) else None
    feature = item.get("feature") if isinstance(item, dict) else None
    return feature if isinstance(feature, str) and feature else None


def session_of(data: dict, slug: str) -> str | None:
    """D9 (a): the session mapped to `slug` with the highest `last_seen`, else None."""
    mapped = [(str(v.get("last_seen")), s) for s, v in data["sessions"].items()
              if isinstance(v, dict) and v.get("feature") == slug]
    return max(mapped)[1] if mapped else None


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
                          for t in tasks if isinstance(t, dict) and isinstance(t.get("n"), int)
                          and (t.get("started") or t.get("done"))}
    if isinstance(note.get("assumptions_baseline"), list):
        entry["assumptions_baseline"] = list(note["assumptions_baseline"])
    for key in ("shipped_at", "head", "waiting", "updated"):
        if isinstance(note.get(key), str):
            entry[key] = note[key]
    entry["fields_digest"] = digest(note)
    stages = [int(k) for k in entry.get("stage_times", {}) if str(k).isdigit()]
    entry["backfilled"] = bool(stages) and min(stages) >= 5
