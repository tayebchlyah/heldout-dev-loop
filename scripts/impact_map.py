#!/usr/bin/env python3
"""Test impact map of this repo (#327).

select [--map <file>]: changed paths on stdin, one per line. Prints two lines, `py` then `node`, each with
  `all`, the selected modules (py: module names, node: repo paths) or no word.
trace [--out <file>] [--jobs <n>]: Linux with strace only. Runs each test module under strace and writes the
  map of each tracked file to the test modules that read or ran it.
"""

from __future__ import annotations

import argparse
import codecs
import json
import os
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


PID_LINE = re.compile(r"(\d+)\s+(.*)")
CALL = re.compile(r"(\w+)\((.*)")
RESUMED = re.compile(r"<\.\.\. (\w+) resumed>(.*)")
RESULT = re.compile(r"\)\s+=\s+(-?\d+)")
QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')
DIRFD = re.compile(r"(AT_FDCWD|\d+)(?:<([^>]*)>)?,\s*$")
UNFINISHED = " <unfinished ...>"


def unescape(text: str) -> str:
    raw = codecs.escape_decode(text.encode("utf-8", "surrogateescape"))[0]
    return raw.decode("utf-8", "replace")


def parse_trace(text: str, start_cwd: str, repo: str, tracked: set[str]) -> set[str]:
    cwd: dict[str, str] = {}
    pending: dict[str, str] = {}
    seen: set[str] = set()
    for raw in text.splitlines():
        match = PID_LINE.fullmatch(raw)
        if not match:
            continue
        pid, rest = match[1], match[2]
        if rest.endswith(UNFINISHED):
            pending[pid] = rest[:-len(UNFINISHED)]
            continue
        resumed = RESUMED.fullmatch(rest)
        if resumed:
            head = pending.pop(pid, None)
            if head is None:
                continue
            rest = head + resumed[2]
        call = CALL.fullmatch(rest)
        if not call:
            continue
        name, args = call[1], call[2]
        here = cwd.setdefault(pid, start_cwd)
        result = RESULT.search(args)
        ok = result is not None and not result[1].startswith("-")
        if name in ("clone", "clone3", "fork", "vfork"):
            if ok and result:
                cwd[result[1]] = here
            continue
        if name == "fchdir":
            fd = re.match(r"\d+<([^>]*)>", args)
            if ok and fd:
                cwd[pid] = fd[1]
            continue
        for quoted in QUOTED.finditer(args):
            dirfd = DIRFD.search(args[:quoted.start()])
            base = here
            if dirfd and dirfd[2] is not None:
                base = dirfd[2]
            elif dirfd and dirfd[1] != "AT_FDCWD":
                continue
            path = os.path.normpath(os.path.join(base, unescape(quoted[1])))
            if name == "chdir":
                if ok:
                    cwd[pid] = path
                break
            if path.startswith(repo + "/") and path[len(repo) + 1:] in tracked:
                seen.add(path[len(repo) + 1:])
    return seen


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
