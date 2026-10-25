"""`devloop.py metrics`: the token cost of each stage of a feature and its numbers, from local Claude transcripts.

D2 read, D3 feature of a record, D4 stage of a record, D5 weights, D6 numbers, D7 row and notes file.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

WEIGHTS = {"input": 1.0, "cache_write_5m": 1.25, "cache_write_1h": 2.0, "cache_read": 0.1, "output": 5.0}
STAGES = ("1-2", "3", "4", "5", "6", "7")
GATE = "devloop.py gate --summary"
DENIAL = re.compile(r"PreToolUse:\S+ hook error: \[([^\]]*)\]")
HOOK = re.compile(r"[^\s/\[\]\"'`]+\.sh")
TESTS = (re.compile(r"^\s*def test_\w*\("), re.compile(r"^\s*(?:it|test)\("), re.compile(r"^func Test\w*\("))
HEADER = ("| feature | track | shipped | sessions | active h | user msgs | gate runs | gate s | unit tests "
          "| acceptance tests | denials | S1-2 | S3 | S4 | S5 | S6 | S7 | total | escaped |")
DATA_RULE = ("Data rule: no process cut based on cost (drop a reviewer, no `tdd` for a task, shorter spec stages) "
             "until 5 features have a row with stage cells that are not `?`.")
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


def boundaries(state_file: Path, slug: str, has_spec: bool) -> tuple[list[tuple[float, str]] | None, set[str]]:
    """Sorted (time, stage) of the keys 3 to 7 of the board `stage_times`, None when unattributed (D4); plus the
    keys that hold a time."""
    try:
        state = json.loads(state_file.read_text())
    except (OSError, ValueError):
        return None, set()
    features = state.get("features") if isinstance(state, dict) else None
    entry = features.get(slug) if isinstance(features, dict) else None
    times = entry.get("stage_times") if isinstance(entry, dict) else None
    if not isinstance(times, dict):
        return None, set()
    present = {str(k) for k, v in times.items() if parse_time(v) is not None}
    bounds = sorted((parse_time(v), str(k)) for k, v in times.items()
                    if str(k) in STAGES[1:] and parse_time(v) is not None)
    if (has_spec and entry.get("backfilled") is True) or not bounds:
        return None, present
    return bounds, present  # type: ignore[return-value]


def stage_of(when: float | None, bounds: list[tuple[float, str]]) -> str:
    stage = "1-2"
    for at, key in bounds:
        if when is not None and at <= when:
            stage = key
    return stage


def text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def typed(rec: dict) -> bool:
    """A user message the user typed (D6)."""
    message = rec.get("message")
    if rec.get("type") != "user" or rec.get("isMeta") is True or not isinstance(message, dict):
        return False
    content = message.get("content")
    if isinstance(content, str):
        return not content.startswith("<")
    if not isinstance(content, list):
        return False
    parts = [b for b in content if isinstance(b, dict)]
    if any(b.get("type") == "tool_result" for b in parts):
        return False
    first = next((b.get("text") for b in parts if b.get("type") == "text"), None)
    return not (isinstance(first, str) and first.startswith("<"))


def numbers(items: list[Counted], slug: str, bounds: list[tuple[float, str]] | None) -> dict:
    """The JSON numbers of the feature, from its records that are not copies (D5, D6)."""
    commands = {use["id"]: use for c in items for use in tool_uses(c.record) if isinstance(use.get("id"), str)}
    mine = [c for c in items if c.feature == slug and not c.copy]
    stages: dict[str, dict] = {}
    per_transcript: dict[int, float] = {}
    seen_messages: set[str] = set()
    times: list[float] = []
    gate = {"runs": 0, "seconds": 0.0}
    denials: dict[str, int] = {}
    total = 0.0
    for c in mine:
        rec = c.record
        when = parse_time(rec.get("timestamp"))
        if when is not None:
            times.append(when)
        stage = stages.setdefault(stage_of(when, bounds or []), {"weighted": 0.0, "models": {}})
        message = rec.get("message")
        if rec.get("type") == "assistant" and isinstance(message, dict) and isinstance(message.get("usage"), dict):
            mid = message.get("id")
            if not (isinstance(mid, str) and mid in seen_messages):
                if isinstance(mid, str):
                    seen_messages.add(mid)
                raw = raw_usage(message)
                weight = weigh(raw)
                total += weight
                stage["weighted"] += weight
                model = message.get("model") if isinstance(message.get("model"), str) else "unknown"
                kinds = stage["models"].setdefault(model, dict.fromkeys(WEIGHTS, 0))
                for key, value in raw.items():
                    kinds[key] += value
                per_transcript[id(c.transcript)] = per_transcript.get(id(c.transcript), 0.0) + weight
        for block in blocks(rec) if rec.get("type") == "user" else []:
            if block.get("type") != "tool_result":
                continue
            text = text_of(block.get("content"))
            use = commands.get(block.get("tool_use_id"), {})
            inp = use.get("input") if isinstance(use.get("input"), dict) else {}
            if use.get("name") == "Bash" and GATE in str(inp.get("command", "")):
                try:
                    payload = json.loads(text)
                except ValueError:
                    payload = None
                seconds = payload.get("seconds") if isinstance(payload, dict) else None
                if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
                    gate["runs"] += 1
                    gate["seconds"] += seconds
            if block.get("is_error") is True and (m := DENIAL.match(text)):
                names = HOOK.findall(m.group(1))
                key = names[-1] if names else "other"
                denials[key] = denials.get(key, 0) + 1

    times.sort()
    active = sum(b - a for a, b in zip(times, times[1:]) if b - a <= 300)
    agents = []
    for t in {id(c.transcript): c.transcript for c in mine if not c.transcript.main}.values():
        stamps = [x for c in mine if c.transcript is t if (x := parse_time(c.record.get("timestamp"))) is not None]
        meta = t.meta or {}
        agents.append({"type": str(meta.get("agentType", "")), "description": str(meta.get("description", "")),
                       "weighted": round(per_transcript.get(id(t), 0.0)),
                       "seconds": float(max(stamps) - min(stamps)) if stamps else 0.0})
    agents.sort(key=lambda a: -a["weighted"])
    return {
        "sessions": len({id(c.transcript) for c in mine if c.transcript.main}),
        "active_hours": round(active / 3600, 1),
        "user_messages": sum(1 for c in mine if c.transcript.main and typed(c.record)),
        "gate": gate,
        "denials": denials,
        "unattributed": bounds is None,
        "stages": {} if bounds is None else {
            k: {"weighted": round(stages[k]["weighted"]), "models": stages[k]["models"]} for k in STAGES if k in stages},
        "total": round(total),
        "agents": agents,
    }


def test_counts(worktree: Path, ref: str) -> dict[str, int]:
    """Test functions added minus removed on the branch, by tier (D6)."""
    counts = {"unit": 0, "acceptance": 0}
    git = ["git", "-C", str(worktree)]
    base = subprocess.run([*git, "merge-base", "HEAD", ref], capture_output=True, text=True).stdout.strip()
    if not base:
        return counts
    diff = subprocess.run([*git, "diff", "--no-color", "--no-ext-diff", "-U0", f"{base}...HEAD"],
                          capture_output=True, text=True, errors="replace").stdout
    path = ""
    for line in diff.splitlines():
        if line.startswith(("--- a/", "+++ b/")):
            path = line[6:]  # a removed file keeps the `--- a/` path: its `+++` side is /dev/null
        elif line.startswith(("--- ", "+++ ")):
            continue
        elif line[:1] in "+-" and line and any(p.match(line[1:]) for p in TESTS):
            tier = "acceptance" if path.startswith("tests/acceptance/") else "unit"
            counts[tier] += 1 if line[0] == "+" else -1
    return counts


def row(slug: str, track: str, shipped: str, payload: dict, present: set[str]) -> str:
    """The table row of the feature, `escaped` empty (D7)."""
    def stage(key: str) -> str:
        if payload["unattributed"]:
            return "?"
        if key != "1-2" and key not in present:
            return "-"
        return str(round(payload["stages"].get(key, {}).get("weighted", 0) / 1000))

    cells = [slug, track, shipped, payload["sessions"], payload["active_hours"], payload["user_messages"],
             payload["gate"]["runs"], round(payload["gate"]["seconds"]), payload["tests"]["unit"],
             payload["tests"]["acceptance"], sum(payload["denials"].values()), *map(stage, STAGES),
             round(payload["total"] / 1000), ""]
    return "| " + " | ".join(map(str, cells)) + " |"


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().removeprefix("|").removesuffix("|").split("|")]


def write_row(file: Path, slug: str, line: str) -> None:
    """Replace the feature's row (its `escaped` cell kept), else add it after the last table line. Raises OSError."""
    table = [HEADER, "| " + " | ".join(["---"] * len(cells(HEADER))) + " |"]
    if file.exists():
        lines = file.read_text().splitlines()
        rows = [i for i, x in enumerate(lines) if x.lstrip().startswith("|")]
        data = [i for i in rows if cells(lines[i]) != cells(HEADER) and not all(set(c) <= set("-:") for c in cells(lines[i]))]
        mine = next((i for i in data if cells(lines[i])[0] == slug), None)
        if mine is not None:
            escaped = cells(lines[mine])[-1]
            lines[mine] = line.rstrip().removesuffix("|").rsplit("|", 1)[0] + f"| {escaped} |"
        elif rows:
            lines.insert(rows[-1] + 1, line)
        else:
            lines += ["", *table, line]
    else:
        lines = ["---", "type: note", "tags: [dev-loop, feature-metrics]", "---", "", "# Feature Metrics", "",
                 DATA_RULE, "", *table, line]
    temp = file.with_name(f".{file.name}.{os.getpid()}.tmp")
    try:
        temp.write_text("\n".join(lines) + "\n")
        os.replace(temp, file)
    finally:
        temp.unlink(missing_ok=True)
