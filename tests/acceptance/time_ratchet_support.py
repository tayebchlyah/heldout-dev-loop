#!/usr/bin/env python3
"""Shared fixtures of the held-out test time ratchet tests (spec: Test Time Ratchet).

Seams: the stdout JSON and exit code of `devloop.py gate --summary`, `acceptance` and `merge-check` (the kit
source, `skill/scripts/devloop.py`), `dev-loop.json` `test_time`, and the record folder
`<git common dir>/dev-loop/test-times/<kind>/`. Each repo is the `Project` of `merge_check_binding_support.py`:
a bare `origin`, a main checkout `acme-shop` on `main` (commit M, pushed, so `refs/remotes/origin/main` is M) and
feature worktrees from `refs/remotes/origin/main`.

Fixture choices the spec leaves open:
- `commands.gate`, `commands.acceptance` and `commands.acceptance_failed` (the same value at `main` and on each
  branch) run one shell script outside the repo: it appends a line to `ran.log`, copies the fixture JUnit file
  into `$DEVLOOP_REPORT_DIR` (none when the fixture is unset) and exits with the fixture code.
- A JUnit `testcase` has a `name` and a `time` and no `classname`, so its id is the oracle id; an id is matched
  as equal to the name or ending in `::<name>`.
- The trailer commit message is a subject line, a blank line and the `Test-Time: <reason>` line.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from merge_check_binding_support import DEVLOOP, Project, config_text, put, run_json

LETTERS = "abcdefghijkl"
BASE = {**{name: 1.0 for name in LETTERS}, "m": 40.0, "w": 100.0}
ACC_BASE = {**{name: 1.0 for name in LETTERS}, "m": 40.0}
TRAILER = "Test-Time"
UNSET = object()

RUNNER = """#!/bin/sh
F='{fix}'
echo ran >> "$F/ran.log"
mkdir -p "$DEVLOOP_REPORT_DIR"
if [ -f "$F/report.xml" ]; then cp "$F/report.xml" "$DEVLOOP_REPORT_DIR/fixture.xml"; fi
exit "$(cat "$F/exit")"
"""


def junit(times: dict[str, float], failing: tuple[str, ...] = ()) -> str:
    """One JUnit file: a `testcase` per id with its `time`, a `failure` child for each failing id."""
    cases = []
    for name, seconds in times.items():
        body = '<failure message="no"/>' if name in failing else ""
        cases.append(f'<testcase name="{name}" time="{seconds}">{body}</testcase>')
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<testsuites><testsuite name="fixture" tests="{len(cases)}">'
            + "".join(cases) + "</testsuite></testsuites>\n")


def with_times(base: dict[str, float], **changes: float) -> dict[str, float]:
    return {**base, **changes}


def find(items: object, name: str) -> list[dict]:
    """The items of a ratchet list whose `id` is `name`, or ends with `::<name>`."""
    if not isinstance(items, list):
        raise AssertionError(f"not a list: {items!r}")
    return [item for item in items if isinstance(item, dict) and isinstance(item.get("id"), str)
            and (item["id"] == name or item["id"].endswith("::" + name))]


class RatchetRepo(Project):
    """A `Project` whose gate and acceptance commands write a fixture JUnit file."""

    def __init__(self, root: Path, test_time: object = UNSET) -> None:
        self.fix = root / "fixture"
        self.fix.mkdir(parents=True)
        runner = self.fix / "run.sh"
        runner.write_text(RUNNER.format(fix=self.fix))
        runner.chmod(0o755)
        self.extra: dict[str, object] = {"commands": {"gate": f"sh '{runner}'", "acceptance": f"sh '{runner}'",
                                                      "acceptance_failed": f"sh '{runner}' --failed"}}
        if test_time is not UNSET:
            self.extra["test_time"] = test_time
        super().__init__(root, config=config_text(**self.extra))
        self.fixture(BASE)

    def branch_config(self, **extra: object) -> str:
        return config_text(**{**self.extra, **extra})

    def fixture(self, times: dict[str, float] | None, exit_code: int = 0, failing: tuple[str, ...] = ()) -> None:
        report = self.fix / "report.xml"
        if times is None:
            report.unlink(missing_ok=True)
        else:
            report.write_text(junit(times, failing))
        (self.fix / "exit").write_text(f"{exit_code}\n")

    def ran(self) -> int:
        log = self.fix / "ran.log"
        return len(log.read_text().splitlines()) if log.exists() else 0

    def tree(self, cwd: Path, rev: str = "HEAD") -> str:
        return self.git(cwd, "rev-parse", f"{rev}^{{tree}}")

    def records(self, kind: str) -> Path:
        common = Path(self.git(self.repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        return common / "dev-loop" / "test-times" / kind

    def record_names(self, kind: str) -> set[str]:
        folder = self.records(kind)
        return {path.name for path in folder.iterdir()} if folder.is_dir() else set()

    def run_cli(self, cwd: Path, *args: str, env: dict[str, str] | None = None) -> tuple[int, dict]:
        before = self.ran()
        code, payload, _ = run_json([sys.executable, str(DEVLOOP), *args], cwd, {**self.env, **(env or {})})
        if self.ran() != before + 1:
            raise AssertionError(f"{' '.join(args)}: the fixture command did not run once: {payload}")
        return code, payload

    def gate(self, cwd: Path, env: dict[str, str] | None = None) -> tuple[int, dict]:
        return self.run_cli(cwd, "gate", "--summary", env=env)

    def acceptance(self, cwd: Path, *args: str) -> tuple[int, dict]:
        return self.run_cli(cwd, "acceptance", *args)

    def trailer_commit(self, cwd: Path, path: str, reason: str) -> str:
        put(cwd / path, f"VALUE = {reason!r}\n")
        self.git(cwd, "add", "--", path)
        self.git(cwd, "commit", "-q", "-m", f"Grow the work\n\n{TRAILER}: {reason}")
        return self.head(cwd)


def same_path(left: object, right: Path) -> bool:
    return isinstance(left, str) and os.path.realpath(left) == os.path.realpath(right)

