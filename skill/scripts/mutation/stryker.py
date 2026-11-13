"""Built-in StrykerJS adapter of `devloop.py mutation`, protocol 1.

The kit runs it in the entry folder, the request JSON on stdin:
  python3 stryker.py < request.json
Config: the entry stryker.config.json, any test runner but `command`. Unit: the file.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json
import re
from pathlib import Path

import adapter

# allow-comment: dependency, Stryker 10.1.1 counts lines at each JavaScript line terminator, columns in UTF-16 units.
LINE_BREAK = re.compile(r"\r\n|[\n\r\u2028\u2029]")
# allow-comment: dependency, Stryker 10.1.1 refuses a --mutate range on a glob path ([[] escapes [), a comma splits it.
NOT_EXPRESSIBLE = re.compile(r"[{}*?,!]|[@+]\(")
BRACKET = re.compile(r"[][]")
INCONCLUSIVE = {"Timeout", "RuntimeError", "CompileError"}
STATUSES = {"Killed": "killed", "Survived": "survived", "NoCoverage": "no-tests"}
REPORT = Path("reports") / "mutation" / "mutation.json"
BIN = Path("node_modules") / ".bin" / "stryker"
SETUP = "Stage 0 owns it: see references/mutation.md, Setup."


def tool(folder: Path) -> Path | None:
    root = adapter.repo_root(folder)
    for parent in [folder.resolve(), *folder.resolve().parents]:
        if (parent / BIN).is_file():
            return parent / BIN
        if parent == root or root not in parent.parents:
            return None
    return None


def check_setup(folder: Path) -> Path:
    binary = tool(folder)
    if binary is None:
        adapter.setup_error("mutation setup missing: no node_modules/.bin/stryker in the entry folder or a parent up "
                            f"to the repo root, run npm ci first. {SETUP}")
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
    if config.get("coverageAnalysis") == "off":
        adapter.setup_error("mutation setup broken: stryker.config.json sets coverageAnalysis off, so a report has no "
                            f"coveredBy and every survivor reads as no tests; use perTest. {SETUP}")
    return binary


def mutate_arg(files: dict[str, list[list[int]]]) -> str:
    return ",".join(BRACKET.sub(r"[\g<0>]", file) + f":{start}-{end}" for file in sorted(files)
                    for start, end in sorted(files[file]))


def split_at(row: str, column: int) -> tuple[str, str]:
    units = row.encode("utf-16-le")
    return units[:2 * (column - 1)].decode("utf-16-le"), units[2 * (column - 1):].decode("utf-16-le")


def diff_of(source: str, location: dict, replacement: str) -> str:
    start, end = location["start"], location["end"]
    old = LINE_BREAK.split(source)[start["line"] - 1:end["line"]]
    new = split_at(old[0], start["column"])[0] + replacement + split_at(old[-1], end["column"])[1]
    return "\n".join([*("-" + row for row in old), *("+" + row for row in LINE_BREAK.split(new))])


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


def run(folder: Path, binary: Path, files: dict[str, list[list[int]]]) -> list[dict]:
    (folder / REPORT).unlink(missing_ok=True)
    # allow-comment: dependency, Stryker 10.1.1 loads stryker.conf.* before stryker.config.json unless one is named.
    command = [str(binary), "run", "stryker.config.json", "--mutate", mutate_arg(files), "--reporters", "json"]
    code, _ = adapter.stream(command, folder)
    # allow-comment: dependency, under thresholds.break Stryker writes the report, then exits 1.
    if code != 0 and not (folder / REPORT).is_file():
        adapter.tool_failed(f"stryker run exited {code}")
    try:
        report = json.loads((folder / REPORT).read_text())
    except (OSError, ValueError) as error:
        adapter.tool_failed(f"stryker run wrote no readable {REPORT.as_posix()} ({error})")
    return mutants_from(report, files)


def main() -> None:
    request = adapter.read_request()
    folder = Path.cwd()
    binary = check_setup(folder)
    files, only = request["files"], request["only"]
    units = sorted(files)
    if not request["run"]:
        adapter.finish(request, units, [], [])
    picked, gaps = {}, []
    for file, ranges in files.items():
        if only is not None and file not in only:
            reason = "unit not in only"
        elif NOT_EXPRESSIBLE.search(file):
            reason = "path not expressible in --mutate"
        else:
            picked[file] = ranges
            continue
        gaps += [{"file": file, "start": start, "end": end, "reason": reason} for start, end in ranges]
    adapter.finish(request, units, run(folder, binary, picked) if picked else [], gaps)


if __name__ == "__main__":
    main()
