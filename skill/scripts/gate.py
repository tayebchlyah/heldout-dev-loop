"""`logged` and `gate`: run a command with its full output in a log file; run the project gate and summarize it."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from devloop import emit, git


JUNIT_HINT = (
    "No JUnit XML in $DEVLOOP_REPORT_DIR. Make the gate write it there: "
    'pytest --junitxml="$DEVLOOP_REPORT_DIR/pytest.xml", '
    'vitest run --reporter=junit --outputFile="$DEVLOOP_REPORT_DIR/vitest.xml", '
    'gotestsum --junitfile "$DEVLOOP_REPORT_DIR/go.xml"'
)


def log_path(cwd: Path, name: str) -> Path | None:
    git_dir = git(cwd, "rev-parse", "--absolute-git-dir")
    if git_dir is None:
        return None
    logs = Path(git_dir) / "dev-loop" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = f"{name}-{datetime.now():%Y%m%dT%H%M%S}"
    log, n = logs / f"{stamp}.log", 1
    while True:
        try:
            log.open("x").close()
            return log
        except FileExistsError:
            n += 1
            log = logs / f"{stamp}-{n}.log"


def run_logged(cmd: list[str], log: Path, cwd: Path, env: dict[str, str] | None = None) -> dict:
    start = time.monotonic()
    with log.open("w") as out:
        try:
            code = subprocess.run(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT).returncode
        except FileNotFoundError as exc:
            out.write(f"{exc}\n")
            code = 127
        except PermissionError as exc:
            out.write(f"{exc}\n")
            code = 126
    return {"exit": code, "log": str(log), "seconds": round(time.monotonic() - start, 1)}


def parse_failures(text: str) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        if line.startswith(("FAILED ", "ERROR ", "--- FAIL")) or " FAIL " in line:
            line = line.strip()
            if line not in found:
                found.append(line)
    return found


def slowest(report_dir: Path, top: Path | None = None, n: int = 10) -> list[dict]:
    cases = []
    for path in sorted(report_dir.glob("*.xml")):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for case in root.iter("testcase"):
            try:
                seconds = float(case.get("time") or 0)
            except ValueError:
                continue
            cases.append({"name": case_id(case, top), "seconds": seconds})
    return sorted(cases, key=lambda c: c["seconds"], reverse=True)[:n]


def junit_cases(path: Path) -> list[ET.Element] | None:
    try:
        return list(ET.parse(path).getroot().iter("testcase"))
    except ET.ParseError:
        return None


def case_id(case: ET.Element, top: Path | None = None, name: str | None = None) -> str:
    name = case.get("name", "") if name is None else name
    classname, file = case.get("classname"), case.get("file")
    test = f"{classname}::{name}" if classname else name
    if not file:
        return test
    try:
        file = str(Path(file).relative_to(top)) if top else file
    except ValueError:
        pass
    return f"{file}::{test}"


def case_failed(case: ET.Element) -> bool:
    return case.find("failure") is not None or case.find("error") is not None


def junit_failures(folder: Path, top: Path | None = None) -> tuple[int | None, list[str] | None, int | None, int | None]:
    files = sorted(folder.glob("*.xml"))
    if not files:
        return None, None, None, None
    ids: set[str] = set()
    executed: set[str] = set()
    cases = 0
    for path in files:
        found = junit_cases(path)
        if found is None:
            return None, None, None, None
        for case in found:
            if case.find("skipped") is None:
                cases += 1
                executed.add(case_id(case, top, case.get("name", "").split(" (", 1)[0]))
            if case_failed(case):
                ids.add(case_id(case, top))
    return len(ids), sorted(ids), cases, len(executed)


def cmd_logged(args: argparse.Namespace) -> int:
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
    if not cmd:
        return emit({"error": "no command after --"}, 2)
    cwd = Path.cwd()
    log = log_path(cwd, args.name or Path(cmd[0]).name)
    if log is None:
        return emit({"error": "not a git repository"}, 2)
    payload = run_logged(cmd, log, cwd)
    payload["tail"] = log.read_text(errors="replace").splitlines()[-20:]
    return emit(payload, 0 if payload["exit"] == 0 else 1)


def mapped_command(top: Path, key: str) -> str | None:
    try:
        command = json.loads((top / "dev-loop.json").read_text()).get("commands", {}).get(key)
    except (OSError, ValueError, AttributeError):
        return None
    return command if isinstance(command, str) and command else None


def previous_reports(current: Path) -> set[str]:
    for folder in sorted(current.parent.glob("gate-*-reports"), reverse=True):
        names = {p.name for p in folder.glob("*.xml")}
        if folder != current and names:
            return names
    return set()


def cmd_gate(args: argparse.Namespace) -> int:
    top_out = git(Path.cwd(), "rev-parse", "--show-toplevel")
    log = log_path(Path.cwd(), "gate")
    if top_out is None or log is None:
        return emit({"error": "not a git repository"}, 2)
    top = Path(top_out)
    command = mapped_command(top, "gate")
    cmd = ["sh", "-c", command] if command else [str(top / "scripts" / "gate.sh")]
    reports = log.parent / f"{log.stem}-reports"
    reports.mkdir()
    payload = run_logged(cmd, log, top, {**os.environ, "DEVLOOP_REPORT_DIR": str(reports)})
    parsed = [(p, junit_cases(p)) for p in sorted(reports.glob("*.xml"))]
    ids = sorted({case_id(c, top) for _, found in parsed for c in found or [] if case_failed(c)})
    broken = [p.name for p, found in parsed if found is None]
    missing = sorted(previous_reports(reports) - {p.name for p, _ in parsed})
    failures = [f"FAILED {i}" for i in ids]
    failures += [f"FAILED {n}: report does not parse" for n in broken]
    failures += [f"FAILED {n}: report missing" for n in missing]
    if not ids or broken or missing:
        failures += [f for f in parse_failures(log.read_text(errors="replace")) if f not in failures]
    payload["failures"] = failures
    payload["slowest"] = slowest(reports, top)
    if not parsed:
        payload["hint"] = JUNIT_HINT
    import size
    from seal.check import check
    from seal.weakened import weakened

    payload["seal"] = check(top, True)
    payload["weakened"] = weakened(top)
    payload["size"] = size.check(top, "uncommitted")
    ok = payload["exit"] == 0 and payload["seal"]["ok"] and payload["size"]["ok"]
    return emit(payload, 0 if ok else 1)
