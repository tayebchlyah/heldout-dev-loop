"""Shared fixture of the held-out tests for mutation adapters (spec: 20261113 Mutation Adapters).

Seam 1 (spec `## Interfaces and seams`): the `devloop.py mutation` process of the kit source on a fixture git repo,
its stdout JSON and exit code, with `command` entries that are fixture scripts. The kit runs from the source tree.
Every name in the fixture repos is invented.

Fixture choices the spec leaves open:
- A fixture repo (`Shop`) has a `main` branch with one commit (the base: `dev-loop.json`, the adapters and the
  code), then a branch `feat` with one commit (the head) and checked out, so the default `--base` (the merge base of
  `main` and `--head`) is the base commit, and `main`, `--head` and the working tree agree on the `mutation` key and
  the adapters (M9).
- "The adapter" of an entry is the committed Python script `<entry>/adapt` (command `./adapt`). It appends one JSON
  line `{"cwd", "stdin"}` per call to a log outside the repo and prints the output a control file outside the repo
  sets: the `run` output on a call with `run` true, the `units` output (`{"units": [...]}`) on a call with `run`
  false; it writes the control `stderr` to stderr and exits with the control `exit`. The absolute paths of the log and
  the control file are written into the script, so the adapter needs no environment variable.
- A usage error (exit 2) may print no JSON: its stdout is not read.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from stack_model_support import clean_env, commit_all, git, init_repo, put

REPO_ROOT = Path(__file__).resolve().parents[2]
DEVLOOP = REPO_ROOT / "skill" / "scripts" / "devloop.py"

COMPARE_BASE = ("def compare(a, b):\n"
                "    total = 0\n"
                "    left = a\n"
                "    right = b\n"
                "    total = left + right\n"
                "    return total\n")
COMPARE_HEAD = ("def compare(a, b):\n"
                "    total = 0\n"
                "    left = a * 2\n"
                "    return a > b\n"
                "    total = left + right\n"
                "    return total\n")

ADAPTER = '''#!/usr/bin/env python3
import json
import os
import sys

CONTROL = {control!r}
LOG = {log!r}
raw = sys.stdin.read()
with open(LOG, "a") as handle:
    handle.write(json.dumps({{"cwd": os.getcwd(), "stdin": raw}}) + "\\n")
with open(CONTROL) as handle:
    control = json.load(handle)
try:
    request = json.loads(raw)
except ValueError:
    request = {{}}
sys.stderr.write(control["stderr"])
sys.stdout.write(control["run"] if request.get("run", True) is not False else control["units"])
sys.stdout.flush()
sys.exit(control["exit"])
'''


def entry(path: str, critical: list[str], command: str = "./adapt") -> dict:
    return {"path": path, "command": command, "critical": critical}


def mutant(unit: str, file: str, line: int, status: str = "survived", diff: list[str] | None = None,
           detail: str | None = None) -> dict:
    """One adapter mutant; `diff` rows are joined by a newline (None: a null `diff`)."""
    item = {"unit": unit, "file": file, "line": line, "status": status,
            "diff": None if diff is None else "\n".join(diff)}
    if detail is not None:
        item["detail"] = detail
    return item


def gap(file: str, start: int, end: int, reason: str = "no mutable code") -> dict:
    return {"file": file, "start": start, "end": end, "reason": reason}


def output(units: list[str], mutants: list[dict], not_covered: list[dict] | None = None, **extra: object) -> dict:
    return {"units": units, "mutants": mutants, "not_covered": not_covered or [], **extra}


def report_lines(payload: dict) -> list[str]:
    report = payload.get("report")
    if not isinstance(report, str):
        raise AssertionError(f"no report text: {payload}")
    return report.splitlines()


def section(payload: dict, header: str) -> list[str]:
    """The indented lines under the report line that starts with `header` (up to the next unindented line)."""
    lines = report_lines(payload)
    starts = [n for n, line in enumerate(lines) if line.startswith(header)]
    if not starts:
        raise AssertionError(f"no report line starts with {header!r}:\n{payload.get('report')}")
    body = []
    for line in lines[starts[0] + 1:]:
        if not line.startswith(" "):
            break
        body.append(line)
    return body


def top_items(lines: list[str]) -> list[str]:
    """The lines indented by exactly 2 spaces."""
    return [line[2:] for line in lines if line.startswith("  ") and not line.startswith("   ")]


class Shop:
    """A fixture git repo under the case root, with one adapter per listed entry folder."""

    def __init__(self, case: "MutationCase", name: str = "shop") -> None:
        self.case = case
        self.path = case.root / name
        self.name = name
        self.env = case.env
        self.tags: dict[str, str] = {}

    def tag(self, folder: str) -> str:
        return f"{self.name}-{folder.strip('/').replace('/', '-') or 'root'}"

    def adapter_file(self, folder: str) -> Path:
        return self.path / ("adapt" if folder == "." else f"{folder}adapt")

    def build(self, config: dict, files: dict[str, str], adapters: tuple[str, ...] = ("app/",)) -> str:
        """Commit the base on `main`, then check out a new branch `feat`. Returns the base sha."""
        init_repo(self.path, self.env)
        for folder in adapters:
            tag = self.tag(folder)
            self.tags[folder] = tag
            control = self.case.root / "control" / f"{tag}.json"
            log = self.case.root / "calls" / f"{tag}.jsonl"
            put(self.adapter_file(folder), ADAPTER.format(control=str(control), log=str(log)))
            self.adapter_file(folder).chmod(0o755)
            self.control(folder, output([], []))
        for name, text in files.items():
            put(self.path / name, text)
        self.write_config(config)
        self.base = commit_all(self.path, self.env, "Base")
        git(self.path, self.env, "checkout", "-q", "-b", "feat")
        return self.base

    def write_config(self, config: dict) -> None:
        put(self.path / "dev-loop.json", json.dumps(config, indent=2) + "\n")

    def change(self, files: dict[str, str | None], branch: str | None = None, start: str | None = None) -> str:
        """Write (None: delete) the files and commit them, on a new branch from `start` when `branch` is given."""
        if branch:
            git(self.path, self.env, "checkout", "-q", "-b", branch, start or self.base)
        for name, text in files.items():
            if text is None:
                (self.path / name).unlink()
            else:
                put(self.path / name, text)
        return commit_all(self.path, self.env, "Change")

    def checkout(self, branch: str) -> None:
        git(self.path, self.env, "checkout", "-q", branch)

    def control(self, folder: str, run: dict | str, units: list[str] | None = None, exit: int = 0,
                stderr: str = "") -> None:
        """Set what the adapter of `folder` prints; `units` default: the `units` of a `run` dict."""
        run_text = run if isinstance(run, str) else json.dumps(run)
        if units is None:
            units = run.get("units", []) if isinstance(run, dict) else []
        put(self.case.root / "control" / f"{self.tags[folder]}.json",
            json.dumps({"run": run_text, "units": json.dumps({"units": units}), "exit": exit, "stderr": stderr}))

    def calls(self, folder: str) -> list[dict]:
        """Each adapter call: `cwd` and `stdin` (parsed when it is JSON)."""
        log = self.case.root / "calls" / f"{self.tags[folder]}.jsonl"
        if not log.exists():
            return []
        found = []
        for line in log.read_text().splitlines():
            call = json.loads(line)
            try:
                call["stdin"] = json.loads(call["stdin"])
            except ValueError:
                pass
            found.append(call)
        return found

    def run_calls(self, folder: str) -> list[dict]:
        """The stdin of each call with `run` true."""
        return [call["stdin"] for call in self.calls(folder)
                if isinstance(call["stdin"], dict) and call["stdin"].get("run") is True]

    def forget_calls(self) -> None:
        shutil.rmtree(self.case.root / "calls", ignore_errors=True)
        (self.case.root / "calls").mkdir()

    def sha(self, rev: str) -> str:
        return git(self.path, self.env, "rev-parse", f"{rev}^{{commit}}")

    def mutation(self, *args: str) -> tuple[int, dict | None, str]:
        """Run `devloop.py mutation --repo <repo> <args>`: exit, the stdout JSON object (None: not one), output."""
        result = subprocess.run([sys.executable, str(DEVLOOP), "mutation", "--repo", str(self.path), *args],
                                cwd=self.path, env=self.env, capture_output=True, text=True, timeout=300)
        text = f"exit {result.returncode}\nstdout:\n{result.stdout[-3000:]}\nstderr:\n{result.stderr[-3000:]}"
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            payload = None
        return result.returncode, payload if isinstance(payload, dict) else None, text


class MutationCase(unittest.TestCase):
    """A scratch folder (removed after the test) with a home, a clean environment, control and call folders."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="mutation-adapters-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = self.root / "home"
        self.home.mkdir()
        (self.root / "control").mkdir()
        (self.root / "calls").mkdir()
        self.env = clean_env(self.home)

    def run_json(self, shop: Shop, *args: str) -> tuple[int, dict]:
        code, payload, text = shop.mutation(*args)
        if payload is None:
            raise AssertionError(f"stdout is not one JSON object:\n{text}")
        return code, payload

    def entry_of(self, payload: dict, path: str) -> dict:
        found = [item for item in payload.get("entries") or [] if item.get("path") == path]
        if len(found) != 1:
            raise AssertionError(f"not one entry {path!r} in {json.dumps(payload)[-3000:]}")
        return found[0]

    def assertBlock(self, lines: list[str], block: list[str], payload: dict) -> None:  # noqa: N802
        """`block` appears as consecutive lines of `lines`."""
        for start in range(len(lines) - len(block) + 1):
            if lines[start:start + len(block)] == block:
                return
        self.fail(f"report lacks the lines {block!r}:\n{payload.get('report')}")
