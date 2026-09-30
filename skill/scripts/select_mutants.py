#!/usr/bin/env python3
"""Select the mutants a feature diff changed, and optionally run only those.

Python (mutmut 3): every top-level function and every method of a top-level
class that the diff touches, inside the critical modules `only_mutate` lists.
Output: mutmut name patterns such as `doctor.api.app.x_create_app__mutmut_*`.

UI (Stryker): every changed line range inside the critical files the `mutate`
list of stryker.config.json names. Output: Stryker mutation ranges such as
`src/logic/score.ts:10-24`.

After a run, each surviving mutant is classed: a decision mutant changes what
the code decides or returns, a message mutant changes only a string or a
logging call. The report lists every function that needs one unit test.

Every input is read from git at --head, so the same diff always gives the
same selection, whatever the working tree holds.

Run from the repository root:
  python3 select_mutants.py --head GATE_SHA           # print the selection
  python3 select_mutants.py --head GATE_SHA --run     # run the tools on it
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, cast

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
UI_SOURCE = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
UI_TEST = re.compile(r"(^|/)__tests__/|\.(test|spec)\.[cm]?[jt]sx?$|\.d\.ts$")
STRYKER_CONFIGS = {
    f"stryker.{kind}.{ext}" for kind in ("config", "conf") for ext in ("mjs", "js", "cjs", "json")
}
STRYKER_CONFIG = "stryker.config.json"
STRING_LITERAL = re.compile(r"""[rRbBuUfF]{0,2}('''|\"\"\"|'|")(?:\\.|(?!\1).)*\1""", re.S)
LOG_CALL = re.compile(r"\b(?:log(?:ger)?|logging|print|warnings?|console)\b[\w.]*\s*\(")
# A string here is read by a person, not by code.
MESSAGE_CONTEXT = re.compile(
    r"^\s*raise\b|^\s*assert\b.*,|\b(?:detail|message|msg|description|help|reason|summary)\s*=(?!=)"
)
# mutmut 3.8 never trampolines these (mutation/file_mutation.py).
NEVER_MUTATE = {"__getattribute__", "__setattr__", "__new__"}
PLAIN_DECORATORS = {"staticmethod", "classmethod"}
STACKS = re.compile(r"^stacks:\s*(.+)$", re.MULTILINE)
# Inconclusive: reported, never counted as killed, never a test.
OTHER_STATUSES = ("timeout", "suspicious", "segfault", "not checked")


class SetupError(Exception):
    """The repository lacks the mutation setup that Stage 0 owns."""


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def changed_lines(repo: Path, base: str, head: str) -> dict[str, set[int]]:
    """New-side line numbers each file's diff touches. A pure deletion marks the line above it."""
    diff = git(repo, "diff", "-U0", "--no-color", "--no-ext-diff", "-M", "--diff-filter=AMR", base, head)
    lines: dict[str, set[int]] = {}
    current: set[int] | None = None
    for row in diff.splitlines():
        if row.startswith("+++ "):
            target = row[4:]
            current = None if target == "/dev/null" else lines.setdefault(target.removeprefix("b/"), set())
        elif current is not None and (match := HUNK.match(row)):
            start = int(match.group(1))
            count = 1 if match.group(2) is None else int(match.group(2))
            if count == 0:
                current.add(max(start, 1))
            else:
                current.update(range(start, start + count))
    return {path: numbers for path, numbers in lines.items() if numbers}


def tracked_files(repo: Path, head: str) -> list[str]:
    return git(repo, "ls-tree", "-r", "--name-only", head).splitlines()


def show(repo: Path, head: str, path: str) -> str:
    return git(repo, "show", f"{head}:{path}")


def parent_dir(path: str) -> str:
    parent = str(PurePosixPath(path).parent)
    return "" if parent == "." else parent


def relative_to(path: str, root: str) -> str | None:
    if not root:
        return path
    prefix = root + "/"
    return path[len(prefix):] if path.startswith(prefix) else None


def ranges(numbers: set[int]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for number in sorted(numbers):
        if spans and number == spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], number)
        else:
            spans.append((number, number))
    return spans


@dataclass(frozen=True)
class Unit:
    key: str
    start: int
    end: int
    skip: str | None


@dataclass
class Gap:
    path: str
    start: int
    end: int
    reason: str

    def render(self) -> str:
        where = f"{self.start}" if self.start == self.end else f"{self.start}-{self.end}"
        return f"{self.path}:{where} {self.reason}"


@dataclass
class Selection:
    mutmut_root: str | None = None
    patterns: list[str] = field(default_factory=list[str])
    stryker_root: str | None = None
    stryker_ranges: list[str] = field(default_factory=list[str])
    gaps: list[Gap] = field(default_factory=list[Gap])
    skipped: list[str] = field(default_factory=list[str])


@dataclass
class ToolResult:
    mutants: int
    #: function or file -> decision survivors and untested mutants: one unit test each
    functions: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    #: survivors that change only a string or a logging call: Deferred
    messages: list[str] = field(default_factory=list[str])
    #: timeout, suspicious, segfault, not checked
    inconclusive: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    note: str | None = None


def classify(diff: str) -> str:
    """`message` when the mutant changes only text a person reads, else `decision`.

    Text a person reads: a string inside a raise, an assert message, a logging
    call, or a message-named argument or variable. Every other string can be a
    key, a compared value or a returned value, so it counts as a decision.
    """
    removed = [row[1:] for row in diff.splitlines() if row.startswith("-") and not row.startswith("---")]
    added = [row[1:] for row in diff.splitlines() if row.startswith("+") and not row.startswith("+++")]
    if not removed and not added:
        return "decision"
    def blank(rows: list[str]) -> list[str]:
        return [STRING_LITERAL.sub('""', row).strip() for row in rows]
    rows = removed + added
    if all(LOG_CALL.search(row) for row in rows):
        return "message"
    if blank(removed) == blank(added) and all(MESSAGE_CONTEXT.search(row) for row in rows):
        return "message"
    return "decision"


def glob_match(path: str, pattern: str) -> bool:
    """Stryker-style glob with `**`, on Python 3.13 and later; plain fnmatch before."""
    full_match = cast(Callable[[str], bool] | None, getattr(PurePosixPath(path), "full_match", None))
    if full_match is not None:
        return full_match(pattern)
    return fnmatch.fnmatch(path, pattern.replace("**/", "*").replace("**", "*"))


def in_globs(path: str, globs: list[str]) -> bool:
    included = any(glob_match(path, g) for g in globs if not g.startswith("!"))
    excluded = any(glob_match(path, g[1:]) for g in globs if g.startswith("!"))
    return included and not excluded


def str_list(value: object) -> list[str]:
    return [str(item) for item in cast(list[object], value)] if isinstance(value, list) else []


def mutmut_units(source: str) -> tuple[list[Unit], list[tuple[int, int]]]:
    """The functions mutmut 3 trampolines, plus the spans of top-level classes."""
    tree = ast.parse(source)
    units: list[Unit] = []
    classes: list[tuple[int, int]] = []

    def unit(node: ast.FunctionDef | ast.AsyncFunctionDef, class_name: str | None) -> Unit:
        key = f"xǁ{class_name}ǁ{node.name}" if class_name else f"x_{node.name}"
        skip = None
        if node.name in NEVER_MUTATE:
            skip = "function mutmut never mutates"
        elif node.decorator_list and not (
            len(node.decorator_list) == 1
            and isinstance(node.decorator_list[0], ast.Name)
            and node.decorator_list[0].id in PLAIN_DECORATORS
        ):
            skip = "decorated function, mutmut skips it"
        return Unit(key, node.lineno, node.end_lineno or node.lineno, skip)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            units.append(unit(node, None))
        elif isinstance(node, ast.ClassDef):
            classes.append((node.lineno, node.end_lineno or node.lineno))
            if node.body and node.body[0].lineno == node.lineno:
                continue
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    units.append(unit(child, node.name))
    return units, classes


def mutmut_name(relative_path: str, key: str) -> str:
    """mutmut 3 naming (utils/format_utils.py get_mutant_name), as a glob over one function."""
    module = relative_path.removesuffix(".py").replace("/", ".").removeprefix("src.")
    return f"{module}.{key}__mutmut_*".replace(".__init__.", ".")


def find_mutmut_config(repo: Path, head: str, files: list[str]) -> tuple[str, dict[str, Any]] | None:
    found: list[tuple[str, dict[str, Any]]] = []
    for path in files:
        if PurePosixPath(path).name != "pyproject.toml":
            continue
        tool = cast(dict[str, Any], tomllib.loads(show(repo, head, path)).get("tool", {}))
        config = tool.get("mutmut")
        if isinstance(config, dict):
            found.append((parent_dir(path), cast(dict[str, Any], config)))
    if len(found) > 1:
        raise SetupError("more than one pyproject.toml holds [tool.mutmut]: " + ", ".join(r or "." for r, _ in found))
    return found[0] if found else None


def select_python(repo: Path, head: str, files: list[str], changed: dict[str, set[int]], out: Selection) -> None:
    python_changes = {path: lines for path, lines in changed.items() if path.endswith(".py")}
    if not python_changes:
        return
    located = find_mutmut_config(repo, head, files)
    if located is None:
        raise SetupError("no pyproject.toml holds [tool.mutmut]")
    root, config = located
    source_paths: object = config.get("source_paths") or config.get("paths_to_mutate")
    if not isinstance(source_paths, list) or not source_paths:
        raise SetupError("[tool.mutmut] has no source_paths")
    out.mutmut_root = root or "."
    sources = [str(PurePosixPath(str(p))).rstrip("/") for p in cast(list[object], source_paths)]
    only = str_list(config.get("only_mutate"))
    never = str_list(config.get("do_not_mutate"))
    patterns: set[str] = set()
    for path in sorted(python_changes):
        relative = relative_to(path, root)
        if relative is None or not any(relative == s or relative.startswith(s + "/") for s in sources):
            continue
        # mutmut 3.8 matches only_mutate and do_not_mutate with fnmatch on this relative path.
        if only and not any(fnmatch.fnmatch(relative, g) for g in only):
            out.skipped.append(f"{path} outside only_mutate")
            continue
        if any(fnmatch.fnmatch(relative, g) for g in never):
            out.skipped.append(f"{path} in do_not_mutate")
            continue
        units, classes = mutmut_units(show(repo, head, path))
        uncovered: dict[str, set[int]] = {}
        for line in python_changes[path]:
            hit = next((u for u in units if u.start <= line <= u.end), None)
            if hit is None:
                in_class = any(start <= line <= end for start, end in classes)
                reason = "class body outside any method" if in_class else "outside any function"
                uncovered.setdefault(reason, set()).add(line)
            elif hit.skip:
                uncovered.setdefault(hit.skip, set()).add(line)
            else:
                patterns.add(mutmut_name(relative, hit.key))
        for reason, lines in sorted(uncovered.items()):
            out.gaps.extend(Gap(path, a, b, reason) for a, b in ranges(lines))
    out.patterns = sorted(patterns)


def select_ui(repo: Path, head: str, files: list[str], changed: dict[str, set[int]], out: Selection) -> None:
    ui_changes = {p: lines for p, lines in changed.items() if p.endswith(UI_SOURCE) and not UI_TEST.search(p)}
    if not ui_changes:
        return
    configs = sorted(p for p in files if PurePosixPath(p).name in STRYKER_CONFIGS)
    if len(configs) != 1 or PurePosixPath(configs[0]).name != STRYKER_CONFIG:
        found = ", ".join(configs) or "none"
        raise SetupError(f"exactly one {STRYKER_CONFIG} expected, found {found}")
    root = parent_dir(configs[0])
    globs = str_list(cast(dict[str, Any], json.loads(show(repo, head, configs[0]))).get("mutate"))
    if not globs:
        raise SetupError(f"{configs[0]} has no mutate list of critical files")
    out.stryker_root = root or "."
    selected: list[str] = []
    for path in sorted(ui_changes):
        relative = relative_to(path, root)
        if relative is None:
            continue
        if not in_globs(relative, globs):
            out.skipped.append(f"{path} outside the Stryker mutate list")
            continue
        selected.extend(f"{relative}:{a}-{b}" for a, b in ranges(ui_changes[path]))
    out.stryker_ranges = selected


def declared_stacks(repo: Path, head: str, files: list[str]) -> set[str]:
    """The `stacks:` line of the root AGENTS.md at head, as Stage 0 wrote it."""
    if "AGENTS.md" not in files:
        return set()
    match = STACKS.search(show(repo, head, "AGENTS.md"))
    return {s.strip() for s in match.group(1).split(",")} if match else set()


def select(repo: Path, base: str, head: str) -> Selection:
    out = Selection()
    changed = changed_lines(repo, base, head)
    files = tracked_files(repo, head)
    stacks = declared_stacks(repo, head, files)
    if "python" in stacks:
        select_python(repo, head, files, changed, out)
    if "ui" in stacks:
        select_ui(repo, head, files, changed, out)
    out.gaps.sort(key=lambda g: (g.path, g.start))
    return out


def tool_env() -> dict[str, str]:
    """macOS aborts a forked child that touches Objective-C state; mutmut forks every mutant."""
    env = dict(os.environ)
    if sys.platform == "darwin":
        env.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
    return env


def stream(command: list[str], cwd: Path) -> tuple[int, str]:
    """Run a tool, echo its output to stderr, keep the output for inspection."""
    process = subprocess.Popen(
        command, cwd=cwd, env=tool_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    kept: list[str] = []
    assert process.stdout is not None
    for chunk in process.stdout:
        sys.stderr.write(chunk)
        kept.append(chunk)
    return process.wait(), "".join(kept)


def run_mutmut(repo: Path, sel: Selection) -> ToolResult:
    root = repo / (sel.mutmut_root or ".")
    code, output = stream(["uv", "run", "mutmut", "run", *sel.patterns], root)
    if code != 0 and "nothing matches" in output:
        return ToolResult(0, note="the changed functions hold no mutable code")
    if code != 0 and ("failed to collect stats" in output or "Failed to run clean test" in output):
        failed = [row for row in output.splitlines() if row.startswith("FAILED ")][:5]
        raise SetupError(
            f"unit tests fail inside mutmut's copy at {sel.mutmut_root}/mutants/: "
            + ("; ".join(failed) or "see the output above")
        )
    if code != 0:
        raise RuntimeError(f"mutmut run exited {code}")
    results = subprocess.run(
        ["uv", "run", "mutmut", "results", "--all", "True"],
        cwd=root,
        env=tool_env(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    total = 0
    survived: list[str] = []
    untested: list[str] = []
    result = ToolResult(0)
    for row in results.splitlines():
        name, _, status = row.strip().rpartition(": ")
        if not name or not any(fnmatch.fnmatchcase(name, p) for p in sel.patterns):
            continue
        total += 1
        if status == "survived":
            survived.append(name)
        elif status == "no tests":
            untested.append(name)
        elif status in OTHER_STATUSES:
            result.inconclusive.setdefault(status, []).append(name)
    result.mutants = total
    crashed = len(result.inconclusive.get("segfault", []))
    if total and crashed * 2 > total:
        raise SetupError(
            f"{crashed} of {total} mutants crashed the forked worker (status segfault), so no test judged them. "
            'Set process_isolation = "forkserver" in [tool.mutmut]'
        )
    if total and len(untested) * 2 > total:
        raise SetupError(
            f"{len(untested)} of {total} mutants reach no unit test, so unit tests are skipped or not collected "
            "inside mutmut's copy"
        )

    def diff_of(name: str) -> str:
        return subprocess.run(
            ["uv", "run", "mutmut", "show", name], cwd=root, env=tool_env(), capture_output=True, text=True
        ).stdout

    def kind_of(name: str) -> str:
        return classify(diff_of(name))

    with ThreadPoolExecutor(max_workers=8) as pool:
        kinds = list(pool.map(kind_of, survived))
    for name, kind in zip(survived, kinds, strict=True):
        if kind == "message":
            result.messages.append(name)
        else:
            result.functions.setdefault(name.partition("__mutmut_")[0], []).append(f"{name} survived")
    for name in untested:
        result.functions.setdefault(name.partition("__mutmut_")[0], []).append(f"{name} no tests")
    return result


def run_stryker(repo: Path, sel: Selection) -> ToolResult:
    root = repo / (sel.stryker_root or ".")
    if not (root / "node_modules").is_dir():
        subprocess.run(["npm", "ci"], cwd=root, check=True)
    command = ["npx", "stryker", "run", "--mutate", ",".join(sel.stryker_ranges), "--reporters", "clear-text,json"]
    code, _ = stream(command, root)
    if code != 0:
        raise RuntimeError(f"stryker run exited {code}")
    report = cast(dict[str, Any], json.loads((root / "reports" / "mutation" / "mutation.json").read_text()))
    files = cast(dict[str, dict[str, Any]], report.get("files", {}))
    result = ToolResult(0)
    for path in sorted(files):
        for mutant in cast(list[dict[str, Any]], files[path].get("mutants", [])):
            result.mutants += 1
            status = str(mutant.get("status", ""))
            line = cast(dict[str, Any], mutant.get("location", {})).get("start", {}).get("line")
            label = f"{path}:{line} {mutant.get('mutatorName')} {status}"
            if status in ("Timeout", "RuntimeError"):
                result.inconclusive.setdefault(status, []).append(label)
            elif status == "Survived" and mutant.get("mutatorName") == "StringLiteral":
                result.messages.append(label)
            elif status in ("Survived", "NoCoverage"):
                result.functions.setdefault(path, []).append(label)
    return result


def report(sel: Selection, results: dict[str, ToolResult] | None) -> str:
    lines: list[str] = []
    if sel.mutmut_root:
        lines.append(f"mutmut root: {sel.mutmut_root}")
        lines.append(f"mutmut functions selected: {len(sel.patterns)}")
        lines.extend(f"  {p}" for p in sel.patterns)
    if sel.stryker_root:
        lines.append(f"stryker root: {sel.stryker_root}")
        lines.append(f"stryker ranges selected: {len(sel.stryker_ranges)}")
        lines.extend(f"  {r}" for r in sel.stryker_ranges)
    if not sel.patterns and not sel.stryker_ranges:
        lines.append("no mutable code changed: 0 mutants selected")
    lines.append(f"not covered by the selection: {len(sel.gaps)}")
    lines.extend(f"  {g.render()}" for g in sel.gaps)
    lines.append(f"outside the critical modules: {len(sel.skipped)}")
    lines.extend(f"  {s}" for s in sel.skipped)
    for tool, result in (results or {}).items():
        lines.append(f"{tool}: {result.mutants} mutants run")
        if result.note:
            lines.append(f"  {result.note}")
        lines.append(f"{tool} functions that need one unit test: {len(result.functions)}")
        for unit, mutants in sorted(result.functions.items()):
            lines.append(f"  {unit}")
            lines.extend(f"    {m}" for m in mutants)
        lines.append(f"{tool} message-only survivors, to Deferred: {len(result.messages)}")
        lines.extend(f"  {m}" for m in result.messages)
        for status, names in sorted(result.inconclusive.items()):
            lines.append(f"{tool} inconclusive, {status}: {len(names)}")
            lines.extend(f"  {n}" for n in names)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--head", required=True, help="GATE_SHA")
    parser.add_argument("--base", help="default: merge-base of --main and --head")
    parser.add_argument("--main", default="main")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--run", action="store_true", help="run mutmut and Stryker on the selection")
    parser.add_argument("--json", action="store_true", help="print the selection as JSON")
    args = parser.parse_args()

    repo = Path(git(args.repo, "rev-parse", "--show-toplevel").strip())
    head = git(repo, "rev-parse", args.head).strip()
    base = args.base or git(repo, "merge-base", args.main, head).strip()
    try:
        sel = select(repo, base, head)
    except SetupError as error:
        print(f"mutation setup missing: {error}. Stage 0 owns it: see references/mutation.md, Setup.", file=sys.stderr)
        return 2
    if args.json:
        payload = {
            "base": base,
            "head": head,
            "mutmut_root": sel.mutmut_root,
            "patterns": sel.patterns,
            "stryker_root": sel.stryker_root,
            "stryker_ranges": sel.stryker_ranges,
            "gaps": [g.render() for g in sel.gaps],
            "skipped": sel.skipped,
        }
        print(json.dumps(payload, indent=2))
        return 0
    results: dict[str, ToolResult] = {}
    try:
        if args.run and sel.patterns:
            results["mutmut"] = run_mutmut(repo, sel)
        if args.run and sel.stryker_ranges:
            results["stryker"] = run_stryker(repo, sel)
    except SetupError as error:
        print(report(sel, results))
        print(f"mutation setup broken: {error}. Stage 0 owns it: see references/mutation.md, Setup.", file=sys.stderr)
        return 2
    print(report(sel, results if args.run else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
