"""Built-in mutmut adapter of `devloop.py mutation`, protocol 1.

The kit runs it in the entry folder, the request JSON on stdin:
  python3 mutmut.py < request.json
Config: `[tool.mutmut]` of the entry pyproject.toml. Unit: the mutmut function name, such as `pkg.mod.x_f`.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import ast
import fnmatch
import os
import re
import shutil
import subprocess
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

import adapter

HUNK = re.compile(r"^@@ -(\d+)")
# allow-comment: dependency, mutmut 3.8 never trampolines these (mutation/file_mutation.py).
NEVER_MUTATE = {"__getattribute__", "__setattr__", "__new__"}
PLAIN_DECORATORS = {"staticmethod", "classmethod"}
STATUSES = {"killed": "killed", "caught by type check": "killed", "survived": "survived", "no tests": "no-tests"}
SETUP = "Stage 0 owns it: see references/mutation.md, Setup."

Unit = tuple[str, int, int]
Span = tuple[int, int, str]


def units_of(source: str, relative: str) -> tuple[list[Unit], list[Span]]:
    module = relative.removesuffix(".py").replace("/", ".").removeprefix("src.")
    units: list[Unit] = []
    skipped: list[Span] = []
    classes: list[Span] = []

    def add(node: ast.FunctionDef | ast.AsyncFunctionDef, class_name: str | None) -> None:
        key = f"xǁ{class_name}ǁ{node.name}" if class_name else f"x_{node.name}"
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        end = node.end_lineno or node.lineno
        plain = len(node.decorator_list) == 1 and isinstance(node.decorator_list[0], ast.Name) and (
            node.decorator_list[0].id in PLAIN_DECORATORS)
        if node.name in NEVER_MUTATE:
            skipped.append((start, end, "function mutmut never mutates"))
        elif node.decorator_list and not plain:
            skipped.append((start, end, "decorated function, mutmut skips it"))
        else:
            units.append((f"{module}.{key}".replace(".__init__.", "."), start, end))

    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node, None)
        elif isinstance(node, ast.ClassDef):
            classes.append((node.lineno, node.end_lineno or node.lineno, "class body outside any method"))
            if node.body and node.body[0].lineno == node.lineno:
                continue
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(child, node.name)
    return units, skipped + classes


def runner(folder: Path) -> list[str]:
    for place in (folder, adapter.repo_root(folder)):
        if (place / "uv.lock").is_file():
            return ["uv", "run", "mutmut"]
        if (place / "poetry.lock").is_file():
            return ["poetry", "run", "mutmut"]
    return ["mutmut"]


def parse_results(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for row in text.splitlines():
        name, _, status = row.strip().rpartition(": ")
        if "__mutmut_" in name:
            found[name] = status
    return found


def mutant_line(show_text: str, unit_start: int) -> int:
    rows = show_text.split("\n")
    for i, row in enumerate(rows):
        if match := HUNK.match(row):
            for offset, body in enumerate(rows[i + 1:]):
                if body.startswith(("-", "+")):
                    return unit_start + int(match[1]) - 1 + offset
    return unit_start


def tool_env() -> dict[str, str]:
    env = dict(os.environ)
    if sys.platform == "darwin":
        # allow-comment: dependency, macOS aborts a forked child that touches Objective-C state; mutmut forks each mutant.
        env.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
    return env


def read_config(folder: Path) -> tuple[list[str], list[str], list[str]]:
    file = folder / "pyproject.toml"
    try:
        config = tomllib.loads(file.read_text()).get("tool", {}).get("mutmut") if file.is_file() else None
    except tomllib.TOMLDecodeError as error:
        adapter.setup_error(f"mutation setup broken: pyproject.toml is not valid TOML ({error}). {SETUP}")
    if not isinstance(config, dict):
        adapter.setup_error(f"mutation setup missing: no pyproject.toml holds [tool.mutmut]. {SETUP}")
    sources = config.get("source_paths") or config.get("paths_to_mutate")
    if not isinstance(sources, list) or not sources:
        adapter.setup_error(f"mutation setup missing: [tool.mutmut] has no source_paths. {SETUP}")

    def strings(value: object) -> list[str]:
        return [str(item) for item in value] if isinstance(value, list) else []

    paths = [str(PurePosixPath(str(p))).rstrip("/") for p in sources]
    return paths, strings(config.get("only_mutate")), strings(config.get("do_not_mutate"))


def file_gap(file: str, sources: list[str], only: list[str], never: list[str]) -> str | None:
    if not file.endswith(".py"):
        return "not a Python file"
    if not any(s == "." or file == s or file.startswith(s + "/") for s in sources):
        return "outside source_paths"
    # allow-comment: dependency, mutmut 3.8 matches only_mutate and do_not_mutate with fnmatch on this path.
    if only and not any(fnmatch.fnmatch(file, g) for g in only):
        return "outside only_mutate"
    if any(fnmatch.fnmatch(file, g) for g in never):
        return "in do_not_mutate"
    return None


def spans(lines: set[int]) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    for line in sorted(lines):
        if found and line == found[-1][1] + 1:
            found[-1] = (found[-1][0], line)
        else:
            found.append((line, line))
    return found


def select(folder: Path, files: dict[str, list[list[int]]], picked: list[str] | None) -> tuple[
        dict[str, tuple[str, int]], list[dict]]:
    sources, only, never = read_config(folder)
    chosen: dict[str, tuple[str, int]] = {}
    gaps: list[dict] = []
    for file, ranges in files.items():
        if reason := file_gap(file, sources, only, never):
            gaps += [{"file": file, "start": a, "end": b, "reason": reason} for a, b in ranges]
            continue
        units, skipped = units_of((folder / file).read_text(), file)
        missed: dict[str, set[int]] = {}
        for start, end in ranges:
            for line in range(start, end + 1):
                if hit := next((u for u in units if u[1] <= line <= u[2]), None):
                    chosen[hit[0]] = (file, hit[1])
                    if picked is None or hit[0] in picked:
                        continue
                    reason = "unit not in only"
                else:
                    reason = next((s[2] for s in skipped if s[0] <= line <= s[1]), "outside any function")
                missed.setdefault(reason, set()).add(line)
        gaps += [{"file": file, "start": a, "end": b, "reason": reason}
                 for reason, lines in sorted(missed.items()) for a, b in spans(lines)]
    return chosen, gaps


def run(folder: Path, names: list[str], chosen: dict[str, tuple[str, int]]) -> list[dict]:
    command, env = runner(folder), tool_env()
    shutil.rmtree(folder / "mutants", ignore_errors=True)
    code, output = adapter.stream([*command, "run", *(f"{n}__mutmut_*" for n in names)], folder, env)
    if code != 0 and "nothing matches" in output:
        return []
    if code != 0 and ("failed to collect stats" in output or "Failed to run clean test" in output):
        failed = [row for row in output.splitlines() if row.startswith("FAILED ")][:5]
        adapter.setup_error("mutation setup broken: unit tests fail inside mutmut's copy at mutants/: "
                            + ("; ".join(failed) or "see the output above") + f". {SETUP}")
    if code != 0:
        adapter.tool_failed(f"mutmut run exited {code}")
    results = parse_results(capture([*command, "results", "--all", "True"], folder, env))
    statuses = {name: status for name, status in results.items() if name.partition("__mutmut_")[0] in names}
    total = len(statuses)
    crashed = sum(status == "segfault" for status in statuses.values())
    if crashed * 2 > total:
        adapter.setup_error(f"mutation setup broken: {crashed} of {total} mutants crashed the forked worker "
                            '(status segfault), so no test judged them. Set process_isolation = "forkserver" '
                            f"in [tool.mutmut]. {SETUP}")
    untested = sum(status == "no tests" for status in statuses.values())
    if untested * 2 > total:
        adapter.setup_error(f"mutation setup broken: {untested} of {total} mutants reach no unit test, so unit "
                            f"tests are skipped or not collected inside mutmut's copy. {SETUP}")
    shown = [name for name, status in statuses.items() if status in ("survived", "no tests")]
    with ThreadPoolExecutor(max_workers=8) as pool:
        texts = dict(zip(shown, pool.map(lambda name: capture([*command, "show", name], folder, env), shown)))
    mutants = []
    for name, status in statuses.items():
        unit = name.partition("__mutmut_")[0]
        file, start = chosen[unit]
        text = texts.get(name)
        mutant = {"unit": unit, "file": file, "line": mutant_line(text, start) if text else start,
                  "status": STATUSES.get(status, "inconclusive"), "diff": adapter.diff_rows(text) if text else None}
        if status not in STATUSES:
            mutant["detail"] = status
        mutants.append(mutant)
    return mutants


def capture(command: list[str], folder: Path, env: dict[str, str]) -> str:
    done = subprocess.run(command, cwd=folder, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          errors="replace")
    sys.stderr.write(done.stderr)
    if done.returncode != 0:
        adapter.tool_failed(f"{' '.join(command)} exited {done.returncode}")
    return done.stdout


def main() -> None:
    request = adapter.read_request()
    folder = Path.cwd()
    only = request["only"]
    chosen, gaps = select(folder, request["files"], only)
    units = sorted(chosen)
    if not request["run"]:
        adapter.finish(request, units, [], [])
    names = [unit for unit in units if only is None or unit in only]
    adapter.finish(request, units, run(folder, names, chosen) if names else [], gaps)


if __name__ == "__main__":
    main()
