"""`devloop.py metrics`: the token cost of each stage of a feature and its numbers, from local Claude transcripts.

D2 read, D3 feature of a record, D4 stage of a record, D5 weights, D6 numbers, D7 row and notes file.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

WEIGHTS = {"input": 1.0, "cache_write_5m": 1.25, "cache_write_1h": 2.0, "cache_read": 0.1, "output": 5.0}
CLAIM = re.compile(r"devloop\.py board\b.*--claim[ =]([a-z0-9]+(?:-[a-z0-9]+)*)")


@dataclass
class Transcript:
    path: Path
    records: list[dict]
    meta: dict | None  # the subagent's `.meta.json` object, None for a main transcript

    @property
    def main(self) -> bool:
        return self.meta is None


@dataclass
class Counted:
    record: dict
    transcript: Transcript
    feature: str
    copy: bool  # its `uuid` was read before: it drives attribution, it is not counted


def parse_time(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def project_folders(root: Path, worktrees: list[Path]) -> list[Path]:
    names = [re.sub(r"[^A-Za-z0-9]", "-", str(w)) for w in worktrees]
    return [root / n for n in dict.fromkeys(names) if (root / n).is_dir()]


def read_records(path: Path) -> list[dict]:
    records = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def read_meta(path: Path) -> dict:
    try:
        value = json.loads(path.with_name(path.name.removesuffix(".jsonl") + ".meta.json").read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def load(folders: list[Path]) -> list[Transcript]:
    found = []
    for folder in folders:
        found += [Transcript(p, read_records(p), None) for p in sorted(folder.glob("*.jsonl"))]
        found += [Transcript(p, read_records(p), read_meta(p)) for p in sorted(folder.glob("*/subagents/agent-*.jsonl"))]

    def first_time(t: Transcript) -> float:
        times = (parse_time(r.get("timestamp")) for r in t.records)
        return next((x for x in times if x is not None), float("inf"))

    return sorted(found, key=lambda t: (first_time(t), str(t.path)))


def blocks(rec: dict) -> list[dict]:
    message = rec.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def tool_uses(rec: dict) -> list[dict]:
    return [b for b in blocks(rec) if b.get("type") == "tool_use"] if rec.get("type") == "assistant" else []


def mention(rec: dict, patterns: dict[str, re.Pattern]) -> str | None:
    """The feature the record's last tool_use names, or None (D3)."""
    found = None
    for use in tool_uses(rec):
        inp = use.get("input")
        command = inp.get("command") if isinstance(inp, dict) else None
        if use.get("name") == "Bash" and isinstance(command, str) and (m := CLAIM.search(command)):
            found = m.group(1)
            continue
        text = json.dumps(inp, ensure_ascii=False)
        for slug, pattern in patterns.items():
            if pattern.search(text):
                found = slug
    return found


def attribute(transcripts: list[Transcript], worktrees: dict[str, Path]) -> list[Counted]:
    """Each record that belongs to a feature, in read order (D2, D3)."""
    patterns = {slug: re.compile(re.escape(str(path)) + r'(?=[/" ]|$)') for slug, path in worktrees.items()}
    seen: set[str] = set()
    copies: dict[int, list[bool]] = {}
    for t in transcripts:
        flags = []
        for rec in t.records:
            uid = rec.get("uuid")
            flags.append(isinstance(uid, str) and uid in seen)
            if isinstance(uid, str):
                seen.add(uid)
        copies[id(t)] = flags

    features: dict[int, list[str | None]] = {}
    for t in transcripts:
        if not t.main:
            continue
        current = None
        marks = []
        for rec in t.records:
            current = mention(rec, patterns) or current
            marks.append(current)
        first = next((m for m in marks if m), None)
        features[id(t)] = [m or first for m in marks]

    by_tool: dict[str, str] = {}

    def index(t: Transcript) -> None:
        for rec, feature in zip(t.records, features[id(t)]):
            for use in tool_uses(rec):
                if feature and isinstance(use.get("id"), str):
                    by_tool[use["id"]] = feature

    for t in transcripts:
        if t.main:
            index(t)
    pending = [t for t in transcripts if not t.main]
    while resolved := [t for t in pending if (t.meta or {}).get("toolUseId") in by_tool]:
        for t in resolved:
            features[id(t)] = [by_tool[(t.meta or {})["toolUseId"]]] * len(t.records)
            index(t)
            pending.remove(t)

    return [Counted(rec, t, feature, copy)
            for t in transcripts
            for rec, feature, copy in zip(t.records, features.get(id(t), []), copies[id(t)])
            if feature]


def number(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def raw_usage(message: dict) -> dict[str, int]:
    usage = message.get("usage") if isinstance(message, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    creation = usage.get("cache_creation")
    if isinstance(creation, dict):
        write_5m = number(creation.get("ephemeral_5m_input_tokens"))
        write_1h = number(creation.get("ephemeral_1h_input_tokens"))
    else:
        write_5m, write_1h = number(usage.get("cache_creation_input_tokens")), 0
    return {"input": number(usage.get("input_tokens")), "cache_write_5m": write_5m, "cache_write_1h": write_1h,
            "cache_read": number(usage.get("cache_read_input_tokens")), "output": number(usage.get("output_tokens"))}


def weigh(raw: dict[str, int]) -> float:
    return sum(raw[k] * w for k, w in WEIGHTS.items())
