#!/usr/bin/env python3
"""Tests for scripts/suite_lock.sh: one suite run at a time across the worktrees of a clone (#165)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
from test_devloop import git, put  # noqa: E402

LOCK = str(ROOT / "scripts" / "suite_lock.sh")
HOLD = '. "$0"; suite_lock || exit 9; echo held; read -r _'
TAKE = '. "$0"; suite_lock || exit 9; echo got'


def env() -> dict:
    return {k: v for k, v in os.environ.items() if k != "DEVLOOP_SUITE_LOCK_HELD"}


def wait_line(stream, want: str, limit: float = 20) -> str:
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        line = stream.readline()
        if line:
            return line.strip()
        time.sleep(0.05)
    raise AssertionError(f"no line {want!r} in {limit} s")


class SuiteLockTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.main = Path(tmp.name).resolve() / "main"
        self.main.mkdir()
        git(self.main, "init", "-q")
        put(self.main / "a.txt", "a\n")
        git(self.main, "add", "-A")
        git(self.main, "commit", "-q", "-m", "base")
        self.other = Path(tmp.name).resolve() / "other"
        git(self.main, "worktree", "add", "-q", "-b", "other", str(self.other))

    def hold(self, cwd: Path) -> subprocess.Popen:
        holder = subprocess.Popen(["bash", "-c", HOLD, LOCK], cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, env=env())
        self.addCleanup(lambda: (holder.kill(), holder.communicate()))
        self.assertEqual(wait_line(holder.stdout, "held"), "held")
        return holder

    def test_a_second_worktree_waits_and_names_the_holder(self) -> None:
        holder = self.hold(self.main)
        taker = subprocess.Popen(["bash", "-c", TAKE, LOCK], cwd=self.other, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, env=env())
        self.addCleanup(lambda: (taker.kill(), taker.communicate()))
        self.assertEqual(wait_line(taker.stderr, "waiting"), f"suite lock: waiting for the suite run in {self.main}")
        time.sleep(0.5)
        self.assertIsNone(taker.poll())
        holder.communicate("\n", timeout=20)
        out, _ = taker.communicate(timeout=20)
        self.assertEqual((out, taker.returncode), ("got\n", 0))

    def test_a_free_lock_is_taken_without_a_line(self) -> None:
        run = subprocess.run(["bash", "-c", TAKE, LOCK], cwd=self.other, capture_output=True, text=True,
                             env=env(), timeout=20)
        self.assertEqual((run.stdout, run.stderr, run.returncode), ("got\n", "", 0))
        lock = Path(git(self.main, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
        self.assertEqual((lock / "dev-loop" / "suite.lock").read_text(), f"{self.other}\n")

    def test_a_nested_run_takes_no_lock(self) -> None:
        self.hold(self.main)
        run = subprocess.run(["bash", "-c", TAKE, LOCK], cwd=self.other, capture_output=True, text=True,
                             env={**env(), "DEVLOOP_SUITE_LOCK_HELD": "1"}, timeout=20)
        self.assertEqual((run.stdout, run.stderr, run.returncode), ("got\n", "", 0))

    def test_outside_a_git_repo_no_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = subprocess.run(["bash", "-c", TAKE, LOCK], cwd=tmp, capture_output=True, text=True,
                                 env=env(), timeout=20)
        self.assertEqual((run.stdout, run.returncode), ("got\n", 0), run.stderr)

    def test_a_child_left_running_keeps_no_lock_when_fd_9_is_closed(self) -> None:
        script = '. "$0"; suite_lock || exit 9; sleep 30 9>&- >/dev/null 2>&1 & echo $!'
        first = subprocess.run(["bash", "-c", script, LOCK], cwd=self.main, capture_output=True, text=True,
                               env=env(), timeout=20)
        self.addCleanup(lambda: subprocess.run(["kill", first.stdout.strip()], capture_output=True))
        run = subprocess.run(["bash", "-c", TAKE, LOCK], cwd=self.other, capture_output=True, text=True,
                             env=env(), timeout=20)
        self.assertEqual((run.stdout, run.stderr, run.returncode), ("got\n", "", 0))

    def test_gate_and_acceptance_take_the_lock_and_close_it_for_the_suites(self) -> None:
        for name, suites in (("gate.sh", 2), ("acceptance.sh", 2)):
            with self.subTest(script=name):
                text = (ROOT / "scripts" / name).read_text()
                self.assertIn(". scripts/suite_lock.sh\n", text)
                self.assertIn("suite_lock || exit 1\n", text)
                runs = [line for line in text.splitlines() if "test_dev_loop.py" in line or "node --test" in line]
                self.assertEqual(len(runs), suites, runs)
                for line in runs:
                    self.assertIn("9>&-", line)


if __name__ == "__main__":
    unittest.main()
