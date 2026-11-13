"""Built-in mutago adapter of `devloop.py mutation`, protocol 1.

The kit runs it in the entry folder (the folder of go.mod), the request JSON on stdin:
  python3 mutago.py < request.json
Tool: mutago v2.10.42, installed on first use into ${XDG_CACHE_HOME:-~/.cache}/dev-loop/mutago/v2.10.42/.
Unit: `<file>:<Func>` or `<file>:<Recv>.<Method>` of the enclosing top-level func.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import adapter

# allow-comment: dependency, mutago v2.10.42 finds no changed line under other values of these (its issue 353).
GIT_SETTINGS = (("color.diff", "never"), ("diff.noprefix", "false"), ("diff.mnemonicPrefix", "false"),
                ("diff.relative", "false"), ("diff.dstPrefix", "b/"))
REPORTS = ("report.json", "mutago-summary.json", "mutago-agentic.json")
VERSION = "v2.10.42"
PACKAGE = "github.com/quality-gates/mutago/v2/cmd/mutago"
SETUP = "Stage 0 owns it: see references/mutation.md, Setup."
LISTS = {"killed": ("killed", None), "escaped": ("survived", None), "notCovered": ("no-tests", None),
         "errored": ("inconclusive", "errored"), "skipped": ("inconclusive", "skipped")}
FUNC = re.compile(r"func\s*(?:\(\s*(?:\w+\s+)?\*?\s*(\w+)(?:\[[^\]]*\])?\s*\)\s*)?(\w+)")


def go_units(source: str) -> list[tuple[str, int, int]]:
    rows = source.split("\n")
    units = []
    for start, row in enumerate(rows, 1):
        if not row.startswith("func ") or not (match := FUNC.match(row)):
            continue
        end = start if row.rstrip().endswith("}") else next(
            (i for i, body in enumerate(rows[start:], start + 1) if body.rstrip() == "}"), start)
        units.append((f"{match[1]}.{match[2]}" if match[1] else match[2], start, end))
    return units


def spans(lines: set[int]) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    for line in sorted(lines):
        if found and line == found[-1][1] + 1:
            found[-1] = (found[-1][0], line)
        else:
            found.append((line, line))
    return found


def select(entry: Path, files: dict[str, list[list[int]]], only: list[str] | None) -> tuple[set[str], list[dict]]:
    chosen: set[str] = set()
    gaps: list[dict] = []
    for file, ranges in files.items():
        if not file.endswith(".go") or file.endswith("_test.go"):
            reason = "not a Go source file" if not file.endswith(".go") else "Go test file, mutago skips it"
            gaps += [{"file": file, "start": a, "end": b, "reason": reason} for a, b in ranges]
            continue
        units = go_units((entry / file).read_text())
        missed: dict[str, set[int]] = {}
        for start, end in ranges:
            for line in range(start, end + 1):
                if hit := next((u for u in units if u[1] <= line <= u[2]), None):
                    unit = f"{file}:{hit[0]}"
                    chosen.add(unit)
                    if only is None or unit in only:
                        continue
                    reason = "unit not in only"
                else:
                    reason = "outside any function"
                missed.setdefault(reason, set()).add(line)
        gaps += [{"file": file, "start": a, "end": b, "reason": reason}
                 for reason, lines in sorted(missed.items()) for a, b in spans(lines)]
    return chosen, gaps


def binary() -> Path:
    folder = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "dev-loop" / "mutago" / VERSION
    tool = folder / "mutago"
    if tool.is_file():
        return tool
    env = {k: v for k, v in os.environ.items() if k not in ("GONOSUMDB", "GONOSUMCHECK", "GOPRIVATE", "GOINSECURE")}
    code, _ = adapter.stream(["go", "install", f"{PACKAGE}@{VERSION}"], Path.cwd(),
                             {**env, "GOBIN": str(folder), "GOSUMDB": "sum.golang.org"})
    if code != 0 or not tool.is_file():
        adapter.setup_error(f"mutation setup broken: go install of mutago {VERSION} exited {code}. {SETUP}")
    return tool


def mutants_from(report: dict, files: dict[str, list[list[int]]], entry: Path) -> list[dict]:
    units = {file: go_units((entry / file).read_text()) for file in files if file.endswith(".go")}
    found = []
    for key, (status, detail) in LISTS.items():
        for mutant in report.get(key) or []:
            where = mutant["mutator"]["location"]["physical"]
            file, line = Path(os.path.relpath(entry / where["filename"], entry)).as_posix(), where["line"]
            if not any(a <= line <= b for a, b in files.get(file, [])):
                continue
            hit = next((u for u in units[file] if u[1] <= line <= u[2]), None)
            if hit is None:
                continue
            row = {"unit": f"{file}:{hit[0]}", "file": file, "line": line, "status": status,
                   "diff": adapter.diff_rows(mutant.get("diff") or "")}
            if detail:
                row["detail"] = detail
            found.append(row)
    return found


def tool_env() -> dict[str, str]:
    env = dict(os.environ)
    count = int(env.get("GIT_CONFIG_COUNT") or 0)
    for i, (key, value) in enumerate(GIT_SETTINGS, count):
        env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = key, value
    env["GIT_CONFIG_COUNT"] = str(count + len(GIT_SETTINGS))
    return env


def check_setup(entry: Path) -> None:
    external = subprocess.run(["git", "config", "diff.external"], cwd=entry, capture_output=True, text=True)
    if external.stdout.strip():
        adapter.setup_error("mutation setup broken: git config diff.external is set, and mutago then finds no "
                            f"changed line. {SETUP}")
    if found := [name for name in REPORTS if os.path.lexists(entry / name)]:
        adapter.setup_error(f"mutation setup broken: {', '.join(found)} already in the entry folder, and mutago "
                            f"writes over it; move it away. {SETUP}")


def run(entry: Path, request: dict, names: list[str] | None) -> list[dict]:
    tool = binary()
    scratch = Path(tempfile.mkdtemp())
    try:
        config = scratch / "mutago.yml"
        config.write_text("json_output: true\nskip_without_test: false\n")
        command = [str(tool), "--git-diff-lines", "--git-diff-base", request["base"], "--coverage",
                   "--config", str(config)]
        if names is not None:
            command += ["--match", f"^({'|'.join(names)})$"]
        try:
            code, _ = adapter.stream([*command, "./..."], entry, tool_env())
        finally:
            for name in REPORTS:
                if os.path.lexists(entry / name):
                    shutil.move(entry / name, scratch / name)
        # allow-comment: dependency, mutago v2.10.42 exits 3 on a tool error or a failing baseline test.
        if code == 3:
            adapter.setup_error(f"mutation setup broken: mutago exited 3, see its output above. {SETUP}")
        if code != 0:
            adapter.tool_failed(f"mutago exited {code}")
        try:
            report = json.loads((scratch / "report.json").read_text())
        except (OSError, ValueError) as error:
            adapter.tool_failed(f"mutago wrote no readable report.json ({error})")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return mutants_from(report, request["files"], entry)


def main() -> None:
    request = adapter.read_request()
    entry = Path.cwd()
    only = request["only"]
    chosen, gaps = select(entry, request["files"], only)
    units = sorted(chosen)
    if not request["run"]:
        adapter.finish(request, units, [], [])
    check_setup(entry)
    picked = [unit for unit in units if only is None or unit in only]
    names = None if only is None else sorted({unit.rpartition(":")[2].rpartition(".")[2] for unit in picked})
    mutants = [m for m in run(entry, request, names) if m["unit"] in picked] if picked else []
    adapter.finish(request, units, mutants, gaps)


if __name__ == "__main__":
    main()
