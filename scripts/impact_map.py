#!/usr/bin/env python3
"""Test impact map of this repo (#327).

select [--map <file>]: changed paths on stdin, one per line. Prints two lines, `py` then `node`, each with
  `all`, the selected modules (py: module names, node: repo paths) or no word.
trace [--out <file>] [--jobs <n>]: Linux with strace only. Runs each test module under strace and writes the
  map of each tracked file to the test modules that read or ran it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from fnmatch import fnmatchcase
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP = "tests/impact-map.json"
RUNNERS = ("scripts/gate.sh", "scripts/impact_map.py", MAP, "tests/unit/python/test_dev_loop.py",
           "tests/unit/python/junit_report.py")
CASE_LIST = ("skill/*", "skills/*", "claude/*", "codex/*", "bin/*", "lib/*", "tests/unit/python/*", "reference/*",
             "AGENTS.md", "README.md", "CODING_STANDARDS.md", "package.json", "upstream.json",
             "scripts/upstream_watch.py", "scripts/acceptance.sh", "scripts/repo_rules.py", "scripts/gate.sh",
             "scripts/temp_leak.sh", "scripts/suite_lock.sh", "scripts/mutation_canary.sh", "tests/fixtures/*",
             "cliff.toml")
PY_MODULE = re.compile(r"tests/unit/python/test_[^/]*\.py")
NODE_MODULE = re.compile(r"tests/unit/node/[^/]*\.test\.mjs")


def load_map(path: Path) -> dict[str, list[str]] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    paths = data.get("paths") if isinstance(data, dict) else None
    if not isinstance(paths, dict):
        return None
    for key, modules in paths.items():
        if not (isinstance(key, str) and isinstance(modules, list) and all(isinstance(m, str) for m in modules)):
            return None
    return paths


def select(paths: list[str], impact: dict[str, list[str]] | None,
           exists: Callable[[str], bool]) -> tuple[list[str] | str, list[str] | str]:
    py: set[str] = set()
    node: set[str] = set()
    py_all = node_all = False

    def add(module: str) -> None:
        if not exists(module):
            return
        if PY_MODULE.fullmatch(module):
            py.add(Path(module).stem)
        elif NODE_MODULE.fullmatch(module):
            node.add(module)

    for path in paths:
        if impact is not None and path in RUNNERS:
            py_all = node_all = True
        elif impact is not None and (PY_MODULE.fullmatch(path) or NODE_MODULE.fullmatch(path)):
            add(path)
            for module in impact.get(path, []):
                add(module)
        elif impact is not None and path in impact:
            for module in impact[path]:
                add(module)
        else:
            node_all = True
            if any(fnmatchcase(path, pattern) for pattern in CASE_LIST):
                py_all = True
    return ("all" if py_all else sorted(py)), ("all" if node_all else sorted(node))


def line(name: str, words: list[str] | str) -> str:
    return " ".join([name, *([words] if isinstance(words, str) else words)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="impact_map.py")
    commands = parser.add_subparsers(dest="command", required=True)
    pick = commands.add_parser("select")
    pick.add_argument("--map", type=Path, default=ROOT / MAP)
    args = parser.parse_args(argv)
    paths = [p for p in sys.stdin.read().splitlines() if p.strip()]
    py, node = select(paths, load_map(args.map), lambda p: (ROOT / p).is_file())
    print(line("py", py))
    print(line("node", node))
    return 0


if __name__ == "__main__":
    sys.exit(main())
