"""Built-in StrykerJS adapter of `devloop.py mutation`, protocol 1.

The kit runs it in the entry folder, the request JSON on stdin:
  python3 stryker.py < request.json
Config: the entry stryker.config.json, any test runner but `command`. Unit: the file.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json
from pathlib import Path

import adapter

INCONCLUSIVE = {"Timeout", "RuntimeError", "CompileError"}
STATUSES = {"Killed": "killed", "Survived": "survived", "NoCoverage": "no-tests"}
REPORT = Path("reports") / "mutation" / "mutation.json"
SETUP = "Stage 0 owns it: see references/mutation.md, Setup."


def check_setup(folder: Path) -> None:
    if not (folder / "node_modules" / "@stryker-mutator" / "core").is_dir():
        adapter.setup_error(f"mutation setup missing: no node_modules/@stryker-mutator/core, run npm ci first. {SETUP}")
    file = folder / "stryker.config.json"
    if not file.is_file():
        adapter.setup_error(f"mutation setup missing: no stryker.config.json in the entry folder. {SETUP}")
    try:
        config = json.loads(file.read_text())
    except ValueError as error:
        adapter.setup_error(f"mutation setup broken: stryker.config.json is not valid JSON ({error}). {SETUP}")
    if not isinstance(config, dict):
        adapter.setup_error(f"mutation setup broken: stryker.config.json is not a JSON object. {SETUP}")
    if config.get("incremental") is True:
        adapter.setup_error('mutation setup broken: stryker.config.json sets "incremental": true, and a run never '
                            f"reuses an earlier one. {SETUP}")
    if config.get("testRunner") in (None, "command"):
        adapter.setup_error("mutation setup broken: stryker.config.json testRunner is command or absent; name the "
                            f"project test runner. {SETUP}")


def mutate_arg(files: dict[str, list[list[int]]]) -> str:
    # shortcut: a path with a comma or a glob character breaks the --mutate list, escape it when a project has one.
    return ",".join(f"{file}:{start}-{end}" for file in sorted(files) for start, end in sorted(files[file]))


def diff_of(source: str, location: dict, replacement: str) -> str:
    start, end = location["start"], location["end"]
    old = [row.removesuffix("\r") for row in source.split("\n")[start["line"] - 1:end["line"]]]
    new = old[0][:start["column"] - 1] + replacement + old[-1][end["column"] - 1:]
    return "\n".join([*("-" + row for row in old), *("+" + row for row in new.split("\n"))])


def status_of(mutant: dict) -> tuple[str, str | None]:
    status = mutant["status"]
    if mutant.get("testsCompleted") == 0:
        return "inconclusive", "no test completed"
    if status in INCONCLUSIVE:
        return "inconclusive", status
    if status == "NoCoverage" or (status == "Survived" and not mutant.get("coveredBy")):
        return "no-tests", None
    if status in STATUSES:
        return STATUSES[status], None
    return "inconclusive", status


def mutants_from(report: dict, files: dict) -> list[dict]:
    found = []
    for file, data in report["files"].items():
        if file not in files:
            continue
        for mutant in data["mutants"]:
            status, detail = status_of(mutant)
            row = {"unit": file, "file": file, "line": mutant["location"]["start"]["line"], "status": status,
                   "diff": diff_of(data["source"], mutant["location"], mutant["replacement"])}
            if detail:
                row["detail"] = detail
            found.append(row)
    return found


def run(folder: Path, files: dict[str, list[list[int]]]) -> list[dict]:
    (folder / REPORT).unlink(missing_ok=True)
    code, _ = adapter.stream(["npx", "stryker", "run", "--mutate", mutate_arg(files), "--reporters", "json"], folder)
    if code != 0:
        adapter.tool_failed(f"stryker run exited {code}")
    try:
        report = json.loads((folder / REPORT).read_text())
    except (OSError, ValueError) as error:
        adapter.tool_failed(f"stryker run wrote no readable {REPORT.as_posix()} ({error})")
    return mutants_from(report, files)


def main() -> None:
    request = adapter.read_request()
    folder = Path.cwd()
    check_setup(folder)
    files, only = request["files"], request["only"]
    units = sorted(files)
    if not request["run"]:
        adapter.finish(request, units, [], [])
    picked = {file: ranges for file, ranges in files.items() if only is None or file in only}
    gaps = [{"file": file, "start": start, "end": end, "reason": "unit not in only"}
            for file, ranges in files.items() if file not in picked for start, end in ranges]
    adapter.finish(request, units, run(folder, picked) if picked else [], gaps)


if __name__ == "__main__":
    main()
