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
import functools
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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
        if path in RUNNERS:
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
FORKS = ("clone", "clone3", "fork", "vfork")


def unescape(text: str) -> str:
    raw = codecs.escape_decode(text.encode("utf-8", "surrogateescape"))[0]
    return raw.decode("utf-8", "replace")


def parse_trace(text: str, start_cwd: str, repo: str, tracked: set[str]) -> set[str]:
    cwd: dict[str, list[str]] = {}
    pending: dict[str, str] = {}
    forking: list[tuple[str, bool]] = []
    seen: set[str] = set()

    def inherit(child: str, parent: str, share: bool) -> None:
        cell = cwd.setdefault(parent, [start_cwd])
        cwd[child] = cell if share else [cell[0]]

    for raw in text.splitlines():
        match = PID_LINE.fullmatch(raw)
        if not match:
            continue
        pid, rest = match[1], match[2]
        if rest.endswith(UNFINISHED):
            head = rest[:-len(UNFINISHED)]
            pending[pid] = head
            if head.split("(")[0] in FORKS:
                forking.append((pid, "CLONE_FS" in head))
            continue
        resumed = RESUMED.fullmatch(rest)
        if resumed:
            head = pending.pop(pid, None)
            if head is None:
                continue
            forking = [f for f in forking if f[0] != pid]
            rest = head + resumed[2]
        call = CALL.fullmatch(rest)
        if not call:
            continue
        name, args = call[1], call[2]
        if pid not in cwd:
            if forking:
                inherit(pid, *forking[-1])
            else:
                cwd[pid] = [start_cwd]
        here = cwd[pid][0]
        result = RESULT.search(args)
        ok = result is not None and not result[1].startswith("-")
        if name in FORKS:
            if ok and result and result[1] not in cwd:
                inherit(result[1], pid, "CLONE_FS" in args)
            continue
        if name == "fchdir":
            fd = re.match(r"\d+<([^>]*)>", args)
            if ok and fd:
                cwd[pid][0] = fd[1]
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
                    cwd[pid][0] = path
                break
            if path.startswith(repo + "/") and path[len(repo) + 1:] in tracked:
                seen.add(path[len(repo) + 1:])
    return seen


TIMEOUT = 900
TAIL = 40
HELD_OUT = "tests/acceptance/"
STRACE = ["strace", "-f", "--seccomp-bpf", "-qq", "-y", "-s", "1000", "-e", "trace=%file,fchdir,clone,clone3,fork,vfork"]


def test_modules(tracked: set[str]) -> list[str]:
    return sorted(p for p in tracked if (PY_MODULE.fullmatch(p) or NODE_MODULE.fullmatch(p))
                  and p != "tests/unit/python/test_dev_loop.py")


def run_module(repo: Path, module: str, traced: bool) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp, "tmp")
        scratch.mkdir()
        trace, output = Path(tmp, "trace.txt"), Path(tmp, "output.txt")
        if PY_MODULE.fullmatch(module):
            command, cwd = [sys.executable, "-m", "unittest", Path(module).stem], repo / "tests" / "unit" / "python"
        else:
            command, cwd = ["node", "--test", module], repo
        if traced:
            command = [*STRACE, "-o", str(trace), *command]
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "KIT_ROOT", "KIT_MAIN")}
        env.update(PYTHONPATH=f"{repo}/skill/scripts:{repo}/tests/unit/python", TMPDIR=str(scratch))
        timed_out = False
        with output.open("wb") as sink:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=sink,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                ok = process.wait(timeout=TIMEOUT) == 0
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                ok, timed_out = False, True
        if traced:
            return ok, trace.read_text(errors="replace") if trace.exists() else ""
        if ok:
            return ok, ""
        tail = output.read_text(errors="replace").splitlines()[-TAIL:]
        return ok, "\n".join([*tail, f"timeout: {TIMEOUT} s"] if timed_out else tail)


def write_map(out: Path, source: str, paths: dict[str, list[str]]) -> bool:
    if load_map(out) == paths:
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"source": source, "paths": paths}, indent=1, sort_keys=True) + "\n")
    return True


def trace_command(repo: Path, out: Path, jobs: int, run: Callable[[str, bool], tuple[bool, str]]) -> int:
    listing = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, text=True, check=True)
    tracked = {p for p in listing.stdout.split("\0") if p and p != MAP and not p.startswith(HELD_OUT)}
    source = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                            check=True).stdout.strip()
    top = str(repo.resolve())

    def one(module: str) -> tuple[str, str, set[str]]:
        ok, text = run(module, True)
        start = f"{top}/tests/unit/python" if PY_MODULE.fullmatch(module) else top
        seen = parse_trace(text, start, top, tracked)
        if ok:
            return module, "ok", seen
        ok, tail = run(module, False)
        tails[module] = tail
        return module, ("trace-only" if ok else "failed"), seen

    tails: dict[str, str] = {}
    with ThreadPoolExecutor(max(jobs, 1)) as pool:
        results = list(pool.map(one, test_modules(tracked)))
    failed = sorted(m for m, state, _ in results if state == "failed")
    if failed:
        print("".join(f"failed: {m}\n" + "".join(f"  {line}\n" for line in tails[m].splitlines()) for m in failed),
              end="", file=sys.stderr)
        return 1
    paths: dict[str, list[str]] = {}
    for module, _, seen in results:
        for path in seen:
            paths.setdefault(path, []).append(module)
    paths = {path: sorted(modules) for path, modules in sorted(paths.items())}
    for module in sorted(m for m, state, _ in results if state == "trace-only"):
        print(f"trace-only: {module}")
    print("written" if write_map(out, source, paths) else "unchanged")
    return 0


def line(name: str, words: list[str] | str) -> str:
    return " ".join([name, *([words] if isinstance(words, str) else words)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="impact_map.py")
    commands = parser.add_subparsers(dest="command", required=True)
    pick = commands.add_parser("select")
    pick.add_argument("--map", type=Path, default=ROOT / MAP)
    traced = commands.add_parser("trace")
    traced.add_argument("--out", type=Path, default=ROOT / MAP)
    traced.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    args = parser.parse_args(argv)
    if args.command == "trace":
        if shutil.which("strace") is None:
            print("trace needs strace on PATH", file=sys.stderr)
            return 2
        if not sys.platform.startswith("linux"):
            print("trace runs only on Linux", file=sys.stderr)
            return 2
        return trace_command(ROOT, args.out, args.jobs, functools.partial(run_module, ROOT))
    paths = [p for p in sys.stdin.read().splitlines() if p.strip()]
    py, node = select(paths, load_map(args.map), lambda p: (ROOT / p).is_file())
    print(line("py", py))
    print(line("node", node))
    return 0


if __name__ == "__main__":
    sys.exit(main())
