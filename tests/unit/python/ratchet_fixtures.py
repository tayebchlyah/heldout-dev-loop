"""Scratch repo of the time ratchet tests: `main` at M, `refs/remotes/origin/main` at M, a gate and acceptance command
that copy JUnit files with given times into `$DEVLOOP_REPORT_DIR`."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from test_devloop import git, put, run_cli

BASE = {**{c: 1.0 for c in "abcdefghijkl"}, "m": 40.0, "w": 100.0}
ACCEPTANCE_BASE = {**{c: 1.0 for c in "abcdefghijkl"}, "m": 40.0}
RUN = 'cp "$RATCHET_JUNIT"/*.xml "$DEVLOOP_REPORT_DIR"/ 2>/dev/null\nexit "${RATCHET_EXIT:-0}"\n'


def junit(times: dict, failing: tuple = ()) -> str:
    cases = "".join(
        f"<testcase name='{i}' time='{t}'>{'<failure/>' if i in failing else ''}</testcase>" for i, t in times.items()
    )
    return f"<testsuites><testsuite name='s'>{cases}</testsuite></testsuites>"


class RatchetRepo(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.junit = self.root / "junit"
        git(self.repo, "init", "-q")
        put(self.repo / "dev-loop.json", json.dumps(
            {"version": "3.0.0", "commands": {"gate": "sh run.sh", "acceptance": "sh run.sh"}}))
        put(self.repo / "run.sh", RUN)
        put(self.repo / ".gitignore", "dev-loop.local.json\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "M")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.env = {k: v for k, v in os.environ.items() if k != "GATE_SCOPE"}
        self.env.update({"HOME": str(self.root), "DEV_LOOP_NOTES": str(self.root / "notes"),
                         "RATCHET_JUNIT": str(self.junit)})
        (self.root / "notes").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def reports(self, times: dict | None, raw: tuple[str, ...] = (), failing: tuple = ()) -> None:
        shutil.rmtree(self.junit, ignore_errors=True)
        self.junit.mkdir()
        if times is not None:
            (self.junit / "r0.xml").write_text(junit(times, failing))
        for n, text in enumerate(raw, 1):
            (self.junit / f"r{n}.xml").write_text(text)

    def gate(self, times: dict | None, exit: int = 0, env: dict | None = None,
             raw: tuple[str, ...] = (), flags: tuple[str, ...] = ()) -> tuple[int, dict]:
        self.reports(times, raw)
        return run_cli("gate", "--summary", *flags, cwd=self.repo,
                       env={**self.env, "RATCHET_EXIT": str(exit), **(env or {})}, timeout=120)

    def acceptance(self, times: dict, *flags: str, failing: tuple = (), exit: int = 0) -> tuple[int, dict]:
        self.reports(times, failing=failing)
        return run_cli("acceptance", *flags, cwd=self.repo, env={**self.env, "RATCHET_EXIT": str(exit)}, timeout=120)

    def tree(self, rev: str) -> str:
        return git(self.repo, "rev-parse", f"{rev}^{{tree}}").strip()

    def store(self, kind: str) -> Path:
        common = git(self.repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
        return Path(common) / "dev-loop" / "test-times" / kind

    def seed(self, kind: str, times: dict, rev: str = "HEAD", **extra: object) -> Path:
        path = self.store(kind) / f"{self.tree(rev)}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        head = git(self.repo, "rev-parse", rev).strip()
        path.write_text(json.dumps({"tree": self.tree(rev), "head": head, "at": "2026-11-14T00:00:00Z",
                                    "times": times, **extra}))
        return path

    def branch(self, name: str = "feat/x") -> None:
        git(self.repo, "switch", "-q", "-c", name)
        put(self.repo / "branch.txt", f"{name}\n")
        git(self.repo, "add", "branch.txt")
        git(self.repo, "commit", "-q", "-m", "branch")

    def commit(self, message: str, trailer: str | None = None) -> None:
        extra = ["-m", f"Test-Time: {trailer}"] if trailer else []
        git(self.repo, "commit", "-q", "--allow-empty", "-m", message, *extra)

    def main_config(self, config: dict | None = None, raw: str | None = None) -> None:
        current = git(self.repo, "branch", "--show-current").strip()
        if current != "main":
            git(self.repo, "switch", "-q", "main")
        text = raw if raw is not None else json.dumps(
            {"version": "3.0.0", "commands": {"gate": "sh run.sh", "acceptance": "sh run.sh"}, **(config or {})})
        put(self.repo / "dev-loop.json", text)
        git(self.repo, "commit", "-q", "-am", "config")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        if current != "main":
            git(self.repo, "switch", "-q", current)
