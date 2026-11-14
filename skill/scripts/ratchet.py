"""Time ratchet of `gate --summary` and `acceptance`: test times against the local record of an ancestor tree."""

from __future__ import annotations

import json
import math
import re
import statistics
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from autonomy import write_atomic
from devloop import git
from gate import case_id
from merge_check import read_config

DEFAULTS = {
    "gate": {"unmarked": 2, "marked": 10, "max": 30, "growth": 0.03},
    "acceptance": {"unmarked": 10, "marked": 30, "max": 60},
}
KEEP = 50
LOOKBACK = 200
MIN_SHARED = 10
SHARED_FLOOR = 0.1
SLOW = re.compile(r"(?<![a-z])slow(?![a-z])", re.IGNORECASE)


def skipped(reason: str, ok: bool = True) -> dict:
    return {"ok": ok, "state": "skipped" if ok else "error", "reason": reason, "baseline": None, "factor": None,
            "red": [], "attention": [], "waived": [], "growth": None, "record": None}


def limits(top: Path, main_ref: str, kind: str) -> dict | None:
    value = (read_config(top, main_ref) or {}).get("test_time", {})
    if value is False:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"test_time in dev-loop.json at {main_ref} must be an object or false")
    for part, numbers in value.items():
        if part not in DEFAULTS:
            raise ValueError(f"test_time.{part} is not a known key")
        if not isinstance(numbers, dict):
            raise ValueError(f"test_time.{part} must be an object")
        for name, number in numbers.items():
            if name not in DEFAULTS[part]:
                raise ValueError(f"test_time.{part}.{name} is not a known key")
            if (isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number)
                    or number <= 0):
                raise ValueError(f"test_time.{part}.{name} must be a positive number")
    return {**DEFAULTS[kind], **value.get(kind, {})}


def read_times(top: Path, report_dir: Path) -> dict[str, float]:
    times: dict[str, float] = {}
    for path in sorted(report_dir.glob("*.xml")):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for case in root.iter("testcase"):
            if case.find("skipped") is not None:
                continue
            try:
                seconds = float(case.get("time") or 0)
            except ValueError:
                seconds = 0.0
            seconds = seconds if math.isfinite(seconds) and seconds > 0 else 0.0
            cid = case_id(case, top)
            times[cid] = max(seconds, times.get(cid, 0.0))
    return times


def store(top: Path, kind: str) -> Path:
    common = git(top, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common or top / ".git") / "dev-loop" / "test-times" / kind


def load(path: Path) -> dict | None:
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not all(isinstance(record.get(k), str) for k in ("tree", "head", "at")):
        return None
    times = record.get("times")
    if not isinstance(times, dict) or not all(
            isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t) for t in times.values()):
        return None
    return record


def lookup(top: Path, kind: str, main_ref: str) -> dict | None:
    base = git(top, "merge-base", "HEAD", main_ref)
    trees = git(top, "log", "--first-parent", f"-n{LOOKBACK}", "--format=%T", base) if base else None
    folder = store(top, kind)
    for tree in (trees or "").split():
        record = load(folder / f"{tree}.json")
        if record:
            return record
    return None


def waivers(repo: Path, main_ref: str, head: str) -> list[dict]:
    out = git(repo, "log", "--reverse", "--format=%H%x00%(trailers:key=Test-Time,valueonly)%x1e", f"{main_ref}..{head}")
    found = []
    for entry in (out or "").split("\x1e"):
        sha, _, values = entry.strip().partition("\0")
        reason = next((line.strip() for line in values.splitlines() if line.strip()), "")
        if sha and reason:
            found.append({"commit": sha, "reason": reason})
    return found


def load_factor(times: dict[str, float], baseline: dict[str, float]) -> float:
    ratios = [times[i] / baseline[i] for i in times if baseline.get(i, 0) >= SHARED_FLOOR]
    return max(1.0, statistics.median(ratios)) if len(ratios) >= MIN_SHARED else 1.0


def new_rule(cid: str, seconds: float, limit: dict) -> tuple[str, float] | None:
    if seconds > limit["max"]:
        return "max", limit["max"]
    rule = "marked" if SLOW.search(cid) else "unmarked"
    return (rule, limit[rule]) if seconds > limit[rule] else None


def grown(seconds: float, baseline: float) -> bool:
    return seconds >= 1.5 * baseline and seconds >= baseline + 5


def check(kind: str, top: Path, report_dir: Path, main_ref: str | None) -> dict:
    """The `ratchet` payload of a `kind` run (`gate` or `acceptance`) whose JUnit files are in `report_dir`."""
    if main_ref is None:
        return skipped("no-main-ref")
    try:
        limit = limits(top, main_ref, kind)
    except ValueError as exc:
        return skipped(str(exc), ok=False)
    if limit is None:
        return skipped("off")
    times = read_times(top, report_dir)
    if not times:
        return skipped("no-junit")
    payload = {**skipped(""), "reason": None}
    record = lookup(top, kind, main_ref)
    if record is None:
        payload["state"] = "no-baseline"
        payload["attention"] = [{"id": i, "seconds": round(t, 1), "rule": "max", "limit": limit["max"]}
                                for i, t in sorted(times.items()) if t > limit["max"]]
        return payload
    base = {i: float(t) for i, t in record["times"].items()}
    factor = load_factor(times, base)
    head = git(top, "rev-parse", "HEAD") or ""
    found = waivers(top, main_ref, head)
    waiver = found[-1]["reason"] if found else None
    norm = {i: t / factor for i, t in sorted(times.items())}
    payload.update(state="checked", baseline={k: record[k] for k in ("tree", "head", "at")}, factor=round(factor, 3))
    for cid, t in norm.items():
        old = base.get(cid)
        if kind == "gate":
            hit = new_rule(cid, t, limit) if old is None else (
                ("max", limit["max"]) if grown(t, old) and t > limit["max"] else None)
            if hit:
                payload["red"].append({"id": cid, "seconds": round(t, 1),
                                       "baseline": None if old is None else round(old, 1), "rule": hit[0],
                                       "limit": hit[1]})
            continue
        if old is not None and grown(t, old):
            if waiver:
                payload["waived"].append({"id": cid, "seconds": round(t, 1), "baseline": round(old, 1), "reason": waiver})
            else:
                payload["red"].append({"id": cid, "seconds": round(t, 1), "baseline": round(old, 1), "rule": "grown",
                                       "limit": round(1.5 * old, 1)})
        hit = ("max", limit["max"]) if t > limit["max"] else new_rule(cid, t, limit) if old is None else None
        if hit:
            payload["attention"].append({"id": cid, "seconds": round(t, 1), "rule": hit[0], "limit": hit[1]})
    total = sum(base.values())
    share = (sum(norm.values()) - total) / total if total else 0.0
    if kind == "gate":
        ok = share <= limit["growth"]
        payload["growth"] = {"share": round(share, 3), "limit": limit["growth"], "ok": ok,
                             "waived": waiver if not ok else None}
        payload["ok"] = not payload["red"] and (ok or waiver is not None)
    else:
        payload["growth"] = {"share": round(share, 3), "limit": None, "ok": True, "waived": None}
        payload["ok"] = not payload["red"]
    return payload


def clean(top: Path) -> bool:
    """No change, staged or not, and no untracked file outside `.gitignore`."""
    return git(top, "status", "--porcelain") == ""


def write_record(kind: str, top: Path, report_dir: Path) -> str | None:
    """Write the times of this run as the record of the HEAD tree, keep the newest 50 of `kind`; its path, or None."""
    times = read_times(top, report_dir)
    tree, head = git(top, "rev-parse", "HEAD^{tree}"), git(top, "rev-parse", "HEAD")
    if not times or not tree or not head:
        return None
    folder = store(top, kind)
    path = folder / f"{tree}.json"
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_atomic(path, json.dumps({"tree": tree, "head": head, "at": at, "times": times}, sort_keys=True) + "\n")
    old = sorted(folder.glob("*.json"), key=lambda p: (p.stat().st_mtime_ns, p.name))
    for stale in old[:-KEEP]:
        stale.unlink(missing_ok=True)
    return str(path)
