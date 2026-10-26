"""devloop.py review-merge and acceptance: the mechanical steps of the Stage 6 feature gate.

`review-merge` reads the reviewer tables (columns `file`, `line`, `finding`, `class`, `verdict`) and merges the
findings with the same file and line. `acceptance` runs the project acceptance command, reads its JUnit XML,
records the run and gives the progress guard verdict.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

import devloop

COLUMNS = ("file", "line", "finding", "class", "verdict")
CLASSES = {"correctness", "security", "spec", "standards"}
ACCEPTANCE_HINT = ("No JUnit XML read in {folder}. The acceptance command writes JUnit XML into $DEVLOOP_REPORT_DIR "
                   "(the dev-loop JUnit standard).")


class TableError(Exception):
    def __init__(self, message: str, row: int | None) -> None:
        super().__init__(message)
        self.message, self.row = message, row


def cells(line: str) -> list[str]:
    """The cells of one table line: `\\|` is a pipe, each cell stripped of spaces and of one pair of backticks."""
    line = line.strip()
    line = line[1:] if line.startswith("|") else line
    line = line[:-1] if line.endswith("|") and not line.endswith("\\|") else line
    out = []
    for cell in re.split(r"(?<!\\)\|", line):
        cell = cell.replace("\\|", "|").strip()
        if len(cell) >= 2 and cell.startswith("`") and cell.endswith("`"):
            cell = cell[1:-1]
        out.append(cell)
    return out


def norm_file(cell: str, top: str | None) -> str:
    cell = re.sub(r"^.*?/devloop-second-review-[^/]*/", "", cell)
    if top and cell.startswith(top + "/"):
        cell = cell[len(top) + 1:]
    return cell[2:] if cell.startswith("./") else cell


def norm_line(cell: str) -> int | None:
    if cell.lower() in ("", "-", "n/a"):
        return None
    match = re.fullmatch(r"[Ll]?(\d+)(\s*-\s*\d+)?", cell)
    if match is None:
        raise ValueError(cell)
    return int(match.group(1))


def parse_table(text: str, top: str | None) -> list[dict]:
    """The findings of the first table whose header holds the five columns. Raises TableError."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        header = [c.lower() for c in cells(line)]
        if set(COLUMNS) <= set(header):
            break
    else:
        raise TableError("no review table with the columns file, line, finding, class, verdict", None)
    index = {name: header.index(name) for name in COLUMNS}
    body = lines[i + 1:]
    if body and re.fullmatch(r"[\s|:-]+", body[0]):
        body = body[1:]
    rows = []
    for n, line in enumerate(body, 1):
        if not line.strip().startswith("|"):
            break
        row = cells(line)
        if len(row) != len(header):
            raise TableError(f"cell count {len(row)}, header has {len(header)}", n)
        values = {name: row[index[name]] for name in COLUMNS}
        if not values["file"]:
            raise TableError("empty file", n)
        if not values["finding"]:
            raise TableError("empty finding", n)
        try:
            line_no = norm_line(values["line"])
        except ValueError:
            raise TableError(f"bad line: {values['line']}", n) from None
        kind = values["class"].lower()
        if kind not in CLASSES:
            raise TableError(f"bad class: {values['class']}", n)
        rows.append({"file": norm_file(values["file"], top), "line": line_no, "finding": values["finding"],
                     "class": kind, "verdict": values["verdict"]})
    return rows


def merge(tables: list[tuple[str, list[dict]]]) -> dict:
    """One row per file and line, sorted by file then line (no line first), numbered from 1."""
    rows: dict[tuple[str, int | None], dict] = {}
    for reviewer, findings in tables:
        for f in findings:
            row = rows.setdefault((f["file"], f["line"]), {"file": f["file"], "line": f["line"], "reviewers": [],
                                                           "findings": []})
            if reviewer not in row["reviewers"]:
                row["reviewers"].append(reviewer)
            row["findings"].append({"reviewer": reviewer, "finding": f["finding"], "class": f["class"],
                                    "verdict": f["verdict"]})
    ordered = [rows[key] for key in sorted(rows, key=lambda k: (k[0], k[1] is not None, k[1] or 0))]
    ordered = [{"n": n, **row} for n, row in enumerate(ordered, 1)]
    return {"rows": ordered, "counts": {reviewer: len(findings) for reviewer, findings in tables},
            "security": [row["n"] for row in ordered if any(f["class"] == "security" for f in row["findings"])]}


def review_merge(pairs: list[str], top: str | None) -> tuple[dict, int]:
    texts: list[tuple[str, str, str]] = []
    for pair in pairs:
        reviewer, sep, path = pair.partition("=")
        if not sep or not reviewer or not path:
            return {"error": f"not <reviewer>=<file>: {pair}"}, 2
        if any(reviewer == seen for seen, _, _ in texts):
            return {"error": f"reviewer given twice: {reviewer}"}, 2
        try:
            texts.append((reviewer, path, Path(path).read_text(errors="replace")))
        except OSError as exc:
            return {"error": f"cannot read {path}: {type(exc).__name__}"}, 2
    tables = []
    for reviewer, path, text in texts:
        try:
            tables.append((reviewer, parse_table(text, top)))
        except TableError as exc:
            return {"error": exc.message, "file": path, "row": exc.row}, 1
    return merge(tables), 0


# acceptance


def junit_failures(folder: Path) -> tuple[int | None, list[str] | None]:
    """The failing test ids of every JUnit file in `folder`. No file, or one that does not parse: (None, None)."""
    files = sorted(folder.glob("*.xml"))
    if not files:
        return None, None
    ids: set[str] = set()
    for path in files:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            return None, None
        for case in root.iter("testcase"):
            if case.find("failure") is not None or case.find("error") is not None:
                name, classname = case.get("name", ""), case.get("classname")
                ids.add(f"{classname}::{name}" if classname else name)
    return len(ids), sorted(ids)


def known(run: dict) -> bool:
    return run["counted"] and run["failing"] is not None and not (run["exit"] != 0 and run["failing"] == 0)


def green(run: dict) -> bool:
    return run["counted"] and run["mode"] == "full" and run["exit"] == 0 and run["failing"] in (0, None)


def guard(runs: list[dict]) -> dict | None:
    """The progress guard verdict of the last run of `runs`. None when that run is not counted."""
    this = runs[-1]
    if not this["counted"]:
        return None
    if green(this):
        return {"verdict": "green", "reason": None, "ids": [], "counts": []}
    earlier = runs[:-1]
    start = max((i + 1 for i, r in enumerate(earlier) if green(r)), default=0)
    loop = [r for r in runs[start:] if known(r)]
    counts = [r["failing"] for r in loop]
    verdict = {"verdict": "continue", "reason": None, "ids": [], "counts": counts}
    if not known(this):
        return {**verdict, "verdict": "unknown"}
    if len(loop) >= 4:
        stuck = set.intersection(*(set(r["ids"]) for r in loop[-4:]))
        if stuck:
            return {**verdict, "verdict": "stop", "reason": "stuck", "ids": sorted(stuck)}
    if len(counts) >= 3 and counts[-2] >= counts[-3] and counts[-1] >= counts[-2]:
        return {**verdict, "verdict": "stop", "reason": "no-progress"}
    return verdict


def read_record(path: Path) -> list[dict] | None:
    """The runs of the record. No file: no run. None: not the record shape."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        return None
    return data["runs"] if all(isinstance(r, dict) for r in data["runs"]) else None


def write_record(path: Path, runs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".acceptance-")
    with os.fdopen(fd, "w") as out:
        json.dump({"runs": runs}, out, indent=2)
    os.replace(tmp, path)


def created_seconds(text: str) -> int:
    """An RFC 3339 time (up to 9 fraction digits, `Z` or an offset) in whole seconds since the epoch."""
    match = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d{1,9})?(Z|[+-]\d\d:\d\d)", text)
    if match is None:
        raise ValueError(f"bad time: {text}")
    zone = "+00:00" if match.group(3) == "Z" else match.group(3)
    return int(datetime.fromisoformat(match.group(1) + zone).timestamp())


def reference_commit(top: Path) -> tuple[str | None, int]:
    """The sha and committer time of the newest commit of HEAD that changes a code path. None: (None, 0)."""
    out = subprocess.run(["git", "-C", str(top), "log", "--format=%x00%H %ct", "--name-only", "--no-renames",
                          "--diff-merges=first-parent", "HEAD"], capture_output=True, text=True, check=True).stdout
    for block in out.split("\0")[1:]:
        head, *paths = block.strip().splitlines()
        sha, ct = head.split()
        if any(path and not devloop.is_test_or_doc(path) for path in paths):
            return sha, int(ct)
    return None, 0


def docker(top: Path, env: Mapping[str, str], *args: str) -> str:
    result = subprocess.run(["docker", *args], cwd=top, env=dict(env), capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:3])} failed: {result.stderr.strip()[-300:]}")
    return result.stdout


def stale_check(top: Path, env: Mapping[str, str]) -> dict:
    """Is a container of a compose build service older than the newest commit that changes code."""
    names = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
    if not any((top / name).exists() for name in names) and not env.get("COMPOSE_FILE"):
        return {"state": "skipped", "reason": "no-compose"}
    try:
        config = json.loads(docker(top, env, "compose", "--profile", "test", "config", "--format", "json"))
        build = sorted(name for name, service in config["services"].items() if "build" in service)
        if not build:
            return {"state": "skipped", "reason": "no-build-service"}
        ids = {svc: docker(top, env, "compose", "--profile", "test", "ps", "-a", "-q", svc).split() for svc in build}
        if not any(ids.values()):
            return {"state": "skipped", "reason": "no-container"}
        inspected = json.loads(docker(top, env, "inspect", *(i for found in ids.values() for i in found)))
        created = {svc: [created_seconds(c["Created"]) for c in inspected
                         if any(c["Id"].startswith(i) for i in found)] for svc, found in ids.items() if found}
        sha, reference = reference_commit(top)
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, AttributeError,
            subprocess.CalledProcessError) as exc:
        return {"state": "error", "message": str(exc) or type(exc).__name__}
    stale = [svc for svc, times in created.items() if any(t < reference for t in times)]
    return {"state": "stale" if stale else "fresh", "checked": sorted(created), "services": stale, "reference": sha}


def mapped(top: Path, key: str) -> str | None:
    try:
        command = json.loads((top / "dev-loop.json").read_text()).get("commands", {}).get(key)
    except (OSError, ValueError, AttributeError):
        return None
    return command if isinstance(command, str) and command else None


def acceptance(cwd: Path, failed: bool, env: Mapping[str, str]) -> tuple[dict, int]:
    top_out = devloop.git(cwd, "rev-parse", "--show-toplevel")
    git_dir = devloop.git(cwd, "rev-parse", "--absolute-git-dir")
    if top_out is None or git_dir is None:
        return {"error": "not a git repository"}, 1
    top = Path(top_out)
    key = "acceptance_failed" if failed else "acceptance"
    command = mapped(top, key)
    if command is None:
        return {"error": f"no commands.{key} in dev-loop.json"}, 1
    record = Path(git_dir) / "dev-loop" / "acceptance.json"
    runs = read_record(record)
    if runs is None:
        return {"error": f"bad record: {record}"}, 1
    log = devloop.log_path(top, "acceptance")
    if log is None:
        return {"error": "not a git repository"}, 1
    reports = log.parent / f"{log.stem}-reports"
    reports.mkdir()
    print(f"acceptance log: {log}", file=sys.stderr, flush=True)
    payload = devloop.run_logged(["sh", "-c", command], log, top, {**env, "DEVLOOP_REPORT_DIR": str(reports)})
    failing, ids = junit_failures(reports)
    stale = stale_check(top, env)
    counted = stale["state"] not in ("stale", "error")
    head = devloop.git(top, "rev-parse", "HEAD")
    runs.append({"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "head": head,
                 "mode": "failed" if failed else "full", "exit": payload["exit"], "failing": failing, "ids": ids,
                 "stale": stale["state"], "counted": counted, "log": str(log)})
    write_record(record, runs)
    payload.update(mode=runs[-1]["mode"], head=head, failing=failing, ids=ids, stale=stale, counted=counted,
                   guard=guard(runs), record=str(record))
    if failing is None:
        payload["hint"] = ACCEPTANCE_HINT.format(folder=reports)
    return payload, 0 if payload["exit"] == 0 and counted else 1
