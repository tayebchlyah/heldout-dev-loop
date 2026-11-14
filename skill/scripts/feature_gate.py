"""devloop.py review-merge, review-save and acceptance: the mechanical steps of the Stage 6 feature gate.

`review-merge` reads the reviewer tables (columns `file`, `line`, `finding`, `class`, `verdict`) and merges the
findings with the same file and line. `review-save` writes one checked reviewer table, or the Track S marker, to
the worktree git dir. `acceptance` runs the project acceptance command, reads its JUnit XML, records the run and
gives the progress guard verdict.
"""

from __future__ import annotations

import argparse
import os
import json
import re
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

import autonomy
import devloop
from devloop import emit, git
import gate
import merge_check
from seal import ACCEPTANCE_DIR

COLUMNS = ("file", "line", "finding", "class", "verdict")
CLASSES = {"correctness", "security", "spec", "standards"}
COMPOSE_LABEL = "com.docker.compose."
ACCEPTANCE_HINT = ("No JUnit XML read in {folder}. The acceptance command writes JUnit XML into $DEVLOOP_REPORT_DIR "
                   "(the dev-loop JUnit standard).")
SHORT_HINT = ("ran {tests} of {floor} cases (full run at {head}). A full run that executes fewer cases than an "
              "earlier full run since the newest seal is never green: the user rules.")
CASES_HINT = ("No executed test case in the JUnit XML in {folder}: no testcase, or every testcase skipped. A run with "
              "no executed case is never green.")


class TableError(Exception):
    def __init__(self, message: str, row: int | None) -> None:
        super().__init__(message)
        self.message, self.row = message, row


def cells(line: str) -> list[str]:
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


def review_save(reviewer: str | None, text: str, cwd: Path) -> tuple[dict, int]:
    git_dir = git(cwd, "rev-parse", "--absolute-git-dir")
    if git_dir is None:
        return {"error": "not in a git worktree"}, 2
    folder = Path(git_dir) / "dev-loop"
    if reviewer is None:
        sha = git(cwd, "rev-parse", "HEAD")
        if sha is None:
            return {"error": "no HEAD commit"}, 2
        target, body, extra = folder / "review-done", sha + "\n", {"sha": sha}
    else:
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.:-]*", reviewer) or ".." in reviewer:
            return {"error": f"not a plain file name: {reviewer}"}, 2
        try:
            findings = parse_table(text, git(cwd, "rev-parse", "--show-toplevel"))
        except TableError as exc:
            return {"error": exc.message, "row": exc.row}, 1
        target, body, extra = folder / "review" / f"{reviewer}.md", text, {"findings": len(findings)}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    return {"file": str(target), **extra}, 0


def known(run: dict) -> bool:
    return (run["counted"] and run["failing"] is not None and not (run["exit"] != 0 and run["failing"] == 0)
            and (run.get("cases") or 0) > 0)


def green(run: dict) -> bool:
    return (run["counted"] and run["mode"] == "full" and run["exit"] == 0 and run["failing"] == 0
            and (run.get("cases") or 0) > 0 and not run.get("short"))


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
    if this.get("short"):
        return {**verdict, "verdict": "stop", "reason": "short", "floor": this["floor"]}
    if len(loop) >= 4:
        stuck = set.intersection(*(set(r["ids"]) for r in loop[-4:]))
        if stuck:
            return {**verdict, "verdict": "stop", "reason": "stuck", "ids": sorted(stuck)}
    if len(counts) >= 3 and counts[-2] >= counts[-3] and counts[-1] >= counts[-2]:
        return {**verdict, "verdict": "stop", "reason": "no-progress"}
    return verdict


def case_floor(runs: list[dict], suite: str | None) -> tuple[int | None, str | None]:
    floor, floor_head = None, None
    for run in runs:
        tests = run.get("tests")
        if run["mode"] != "full" or type(tests) is not int or "suite" not in run or run["suite"] != suite:
            continue
        if floor is None or tests >= floor:
            floor, floor_head = tests, run.get("head")
    return floor, floor_head


def read_record(path: Path) -> list[dict] | None:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        return None
    keys = {"at", "head", "mode", "exit", "failing", "ids", "stale", "counted", "log"}
    return data["runs"] if all(isinstance(r, dict) and keys <= r.keys() for r in data["runs"]) else None


def write_record(path: Path, runs: list[dict]) -> None:
    autonomy.write_atomic(path, json.dumps({"runs": runs}, indent=2))


def created_seconds(text: str) -> int:
    """An RFC 3339 time (up to 9 fraction digits, `Z` or an offset) in whole seconds since the epoch."""
    match = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d{1,9})?(Z|[+-]\d\d:\d\d)", text)
    if match is None:
        raise ValueError(f"bad time: {text}")
    zone = "+00:00" if match.group(3) == "Z" else match.group(3)
    return int(datetime.fromisoformat(match.group(1) + zone).timestamp())


def reference_commit(top: Path) -> tuple[str | None, int]:
    out = subprocess.run(["git", "-C", str(top), "log", "--format=%x01%H %ct", "--name-only", "-z", "--no-renames",
                          "--diff-merges=first-parent", "HEAD"], capture_output=True, text=True, check=True).stdout
    for block in out.split("\x01")[1:]:
        head, _, rest = block.partition("\0")
        sha, ct = head.split()
        if any(path and not merge_check.is_test_or_doc(path) for path in rest.removeprefix("\n").split("\0")):
            return sha, int(ct)
    return None, 0


def docker(top: Path, env: Mapping[str, str], *args: str) -> str:
    result = subprocess.run(["docker", *args], cwd=top, env=dict(env), capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:3])} failed: {result.stderr.strip()[-300:]}")
    return result.stdout


def stale_verdict(top: Path, created: dict[str, list[int]]) -> dict:
    sha, reference = reference_commit(top)
    stale = [svc for svc, times in created.items() if any(t < reference for t in times)]
    return {"state": "stale" if stale else "fresh", "checked": sorted(created), "services": stale, "reference": sha}


def service_check(top: Path, env: Mapping[str, str]) -> dict:
    names = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
    if not any((top / name).exists() for name in names) and not env.get("COMPOSE_FILE"):
        return {"state": "skipped", "reason": "no-compose"}
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
    return stale_verdict(top, created)


def project_check(top: Path, env: Mapping[str, str], command: str) -> tuple[str, dict]:
    run = subprocess.run(["sh", "-c", command], cwd=top, env=dict(env), stdin=subprocess.DEVNULL,
                         capture_output=True, text=True)
    if run.returncode != 0:
        raise RuntimeError(f"commands.compose_project failed with exit {run.returncode}: {run.stderr.strip()[-300:]}")
    name = run.stdout.strip()
    if not name or "\n" in name:
        raise RuntimeError("commands.compose_project must print the project name on one line")
    ids = docker(top, env, "ps", "-a", "-q", "--no-trunc", "--filter", f"label={COMPOSE_LABEL}project={name}").split()
    boxes = [(c, (c.get("Config") or {}).get("Labels") or {}) for c in
             (json.loads(docker(top, env, "inspect", *ids)) if ids else [])]
    boxes = [(c, labels) for c, labels in boxes if labels.get(f"{COMPOSE_LABEL}oneoff") != "True"]
    if not boxes:
        return name, {"state": "skipped", "reason": "no-container"}
    dirs = {labels.get(f"{COMPOSE_LABEL}project.working_dir") for _, labels in boxes}
    files = {labels.get(f"{COMPOSE_LABEL}project.config_files") for _, labels in boxes}
    if len(dirs - {None}) > 1:
        raise RuntimeError(f"the containers of compose project {name} come from {', '.join(sorted(dirs - {None}))}: "
                           "commands.compose_project must print one name for each worktree")
    if None in dirs or None in files or len(files) != 1:
        raise RuntimeError(f"the containers of compose project {name} do not share one compose file set: "
                           "reset the stack")
    (folder,), (config_files,) = dirs, files
    try:
        config = json.loads(docker(top, env, "compose", "-p", name, "--project-directory", folder,
                                   *(word for file in config_files.split(",") for word in ("-f", file)),
                                   "--profile", "test", "config", "--format", "json"))
    except RuntimeError as exc:
        raise RuntimeError(f"docker compose config of project {name} failed ({exc}): reset the stack") from exc
    build = {svc for svc, service in config["services"].items() if "build" in service}
    if not build:
        return name, {"state": "skipped", "reason": "no-build-service"}
    created: dict[str, list[int]] = {}
    for c, labels in boxes:
        if labels.get(f"{COMPOSE_LABEL}service") in build:
            created.setdefault(labels[f"{COMPOSE_LABEL}service"], []).append(created_seconds(c["Created"]))
    if not created:
        return name, {"state": "skipped", "reason": "no-container"}
    return name, stale_verdict(top, created)


def stale_check(top: Path, env: Mapping[str, str], compose_project: str | None) -> dict:
    """Is a container of a compose build service older than the newest commit that changes code."""
    named = env.get("COMPOSE_PROJECT_NAME") or None
    try:
        if named is None and compose_project is not None:
            name, result = project_check(top, env, compose_project)
        else:
            name, result = named, service_check(top, env)
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, AttributeError,
            subprocess.CalledProcessError) as exc:
        return {"state": "error", "message": str(exc) or type(exc).__name__, "project": None}
    return {**result, "project": name}


def acceptance(cwd: Path, failed: bool, env: Mapping[str, str]) -> tuple[dict, int]:
    top_out = devloop.git(cwd, "rev-parse", "--show-toplevel")
    git_dir = devloop.git(cwd, "rev-parse", "--absolute-git-dir")
    if top_out is None or git_dir is None:
        return {"error": "not a git repository"}, 1
    top = Path(top_out)
    key = "acceptance_failed" if failed else "acceptance"
    ref = merge_check.main_ref(top)
    if ref is None:
        return {"error": "no refs/remotes/origin/main and no refs/heads/main"}, 1
    trusted = (merge_check.read_config(top, ref) or {}).get("commands")
    command = trusted.get(key) if isinstance(trusted, dict) else None
    if not isinstance(command, str) or not command:
        return {"error": f"no commands.{key} in dev-loop.json at {ref}"}, 1
    if gate.mapped_command(top, key) != command:
        return {"error": f"commands.{key} in dev-loop.json differs from {ref}"}, 1
    compose = trusted.get("compose_project")
    if "compose_project" in trusted and (not isinstance(compose, str) or not compose):
        return {"error": f"commands.compose_project in dev-loop.json at {ref} must be a non-empty string"}, 1
    if gate.mapped_command(top, "compose_project") != compose:
        return {"error": f"commands.compose_project in dev-loop.json differs from {ref}"}, 1
    record = Path(git_dir) / "dev-loop" / "acceptance.json"
    runs = read_record(record)
    if runs is None:
        return {"error": f"bad record: {record}"}, 1
    log = gate.log_path(top, "acceptance")
    if log is None:
        return {"error": "not a git repository"}, 1
    reports = log.parent / f"{log.stem}-reports"
    reports.mkdir()
    print(f"acceptance log: {log}", file=sys.stderr, flush=True)
    payload = gate.run_logged(["sh", "-c", command], log, top, {**env, "DEVLOOP_REPORT_DIR": str(reports)})
    failing, ids, cases, tests = gate.junit_failures(reports, top)
    stale = stale_check(top, env, compose)
    counted = stale["state"] not in ("stale", "error")
    head = devloop.git(top, "rev-parse", "HEAD")
    suite = devloop.git(top, "rev-parse", f"HEAD:{ACCEPTANCE_DIR}")
    floor, floor_head = (None, None) if failed else case_floor(runs, suite)
    this = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "head": head,
            "mode": "failed" if failed else "full", "exit": payload["exit"], "failing": failing, "ids": ids,
            "stale": stale["state"], "counted": counted, "log": str(log), "cases": cases, "tests": tests,
            "floor": floor, "suite": suite}
    this["short"] = green(this) and floor is not None and tests < floor
    runs.append(this)
    write_record(record, runs)
    payload.update(mode=this["mode"], head=head, failing=failing, ids=ids, cases=cases, tests=tests, floor=floor,
                   short=this["short"], stale=stale, counted=counted, guard=guard(runs), record=str(record))
    if this["short"]:
        payload["hint"] = SHORT_HINT.format(tests=tests, floor=floor, head=floor_head[:12])
    elif failing is None:
        payload["hint"] = ACCEPTANCE_HINT.format(folder=reports)
    elif cases == 0:
        payload["hint"] = CASES_HINT.format(folder=reports)
    return payload, 0 if payload["exit"] == 0 and counted and cases and not this["short"] else 1


def cmd_review_merge(args: argparse.Namespace) -> int:
    return emit(*review_merge(args.pairs, git(Path.cwd(), "rev-parse", "--show-toplevel")))


def cmd_review_save(args: argparse.Namespace) -> int:
    return emit(*review_save(None if args.done else args.reviewer, "" if args.done else sys.stdin.read(), Path.cwd()))


def cmd_acceptance(args: argparse.Namespace) -> int:
    return emit(*acceptance(Path.cwd(), args.failed, os.environ))
