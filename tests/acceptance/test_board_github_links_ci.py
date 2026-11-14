#!/usr/bin/env python3
"""Held-out tests for the board GitHub links (spec: 20261114 Board GitHub Links): the `pr.json` of `ci wait`.

One test per `## Acceptance oracle` line 11 to 13 (O<n>). Seam 3: `devloop.py ci wait` with a stub `gh` on
`PATH` and `DEVLOOP_TEST_CI_POLL`, run in a linked worktree on `feat/x`; the file
`<git-dir>/dev-loop/pr.json` (`git rev-parse --absolute-git-dir` of that worktree). Every name in here is
invented.

Fixture choices the spec leaves open: see board_github_links_support.py; and
- each final state comes from the `gh pr view` answer the CI Follow-up spec gives it: a failed check (`fail`),
  `CONFLICTING` (`conflicts`), an unresolved thread (`threads`), a draft (`blocked`), a check always in progress
  with `--timeout 0.001` and `DEVLOOP_TEST_CI_POLL=0.05` (`timeout`);
- `no-pr` is a first `gh pr view` that fails with "no pull requests found"; a failed `gh pr view` fails with
  another message (state `error`).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from board_github_links_support import StubGh, check, ci_wait, fails, ok, open_thread, put, threads_doc, view

PR_URL = "https://github.com/o/r/pull/318"
FAILED_LOG = "acceptance\tRun tests\t2026-11-14T10:00:10.0000000Z Error: assertion failed in test_ship\n"


def git(cwd: Path, env: dict[str, str], *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
    return result.stdout.strip()


class PrRecordCase(unittest.TestCase):
    """A main checkout on `main` and a linked worktree on `feat/x` with one commit <H>, a stub `gh`."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        home, scratch = self.root / "home", self.root / "tmp"
        home.mkdir()
        scratch.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("DEV_LOOP", "DEVLOOP", "CLAUDE", "CODEX"))
                    and k not in ("GIT_DIR", "GIT_WORK_TREE")}
        self.env.update(HOME=str(home), TMPDIR=str(scratch), GIT_CONFIG_NOSYSTEM="1", GIT_EDITOR="true",
                        PYTHONDONTWRITEBYTECODE="1")
        self.repo = self.root / "order-desk"
        self.repo.mkdir()
        git(self.repo, self.env, "init", "-q", "-b", "main")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
            git(self.repo, self.env, "config", key, value)
        put(self.repo / "README.md", "# Order desk\n")
        put(self.repo / "dev-loop.json", json.dumps({"version": "3.0.0", "harnesses": ["claude"]}) + "\n")
        git(self.repo, self.env, "add", "-A")
        git(self.repo, self.env, "commit", "-q", "-m", "Order desk")
        self.wt = self.root / "order-desk-feat-x"
        git(self.repo, self.env, "worktree", "add", "-q", "-b", "feat/x", str(self.wt))
        put(self.wt / "src" / "ship.py", "def ship(order):\n    return order\n")
        git(self.wt, self.env, "add", "-A")
        git(self.wt, self.env, "commit", "-q", "-m", "Ship orders")
        self.head = git(self.wt, self.env, "rev-parse", "HEAD")
        self.record = Path(git(self.wt, self.env, "rev-parse", "--absolute-git-dir")) / "dev-loop" / "pr.json"
        self.stub = StubGh(self.root / "stub-bin")

    def answers(self, **changes: object) -> dict:
        return {"pr_view": [ok(view(self.head, 318, PR_URL, "feat/x", **changes))], "api_graphql": [ok(threads_doc())],
                "compare": [ok({"status": "ahead", "ahead_by": 1, "behind_by": 0})], "run_view": [ok(FAILED_LOG)]}

    def wait(self, **kwargs: str) -> tuple[int, dict]:
        return ci_wait(self.wt, self.env, self.stub, **kwargs)


class PrRecordTests(PrRecordCase):
    def test_o11_every_final_state_writes_pr_json_in_the_worktree_git_dir(self) -> None:
        """O11, D2: `gh pr view` gives number 318: `pr.json` with number 318, the PR url and branch equal to
        `headRefName`, in the git dir of the cwd, for pass, fail, conflicts, threads, blocked and timeout."""
        cases = {
            "pass": (self.answers(), {}),
            "fail": (self.answers(statusCheckRollup=[check("test"), check("acceptance", conclusion="FAILURE")]), {}),
            "conflicts": (self.answers(mergeable="CONFLICTING"), {}),
            "threads": ({**self.answers(), "api_graphql": [ok(threads_doc(open_thread()))]}, {}),
            "blocked": (self.answers(isDraft=True), {}),
            "timeout": (self.answers(statusCheckRollup=[check("test", status="IN_PROGRESS")]),
                        {"timeout": "0.001", "poll": "0.05"}),
        }
        for state, (answers, run) in cases.items():
            with self.subTest(state=state):
                self.record.unlink(missing_ok=True)
                self.stub.set(**answers)
                code, verdict = self.wait(**run)
                self.assertEqual(verdict.get("state"), state, verdict)
                self.assertEqual(code, 0 if state == "pass" else 1, verdict)
                self.assertTrue(self.record.is_file(), f"no {self.record}")
                self.assertEqual(json.loads(self.record.read_text()),
                                 {"number": 318, "url": PR_URL, "branch": "feat/x"})
                self.assertFalse((self.repo / ".git" / "dev-loop" / "pr.json").exists(),
                                 "pr.json went to the main checkout's git dir")

    def test_o12_no_pr_or_failed_view_writes_nothing(self) -> None:
        """O12, D2: `no-pr`, or a failed `gh pr view`, writes no `pr.json` and leaves an existing one unchanged
        (the one an earlier `ci wait` on PR 317 wrote)."""
        cases = {"no-pr": fails('no pull requests found for branch "feat/x"'),
                 "error": fails("HTTP 502: Bad Gateway")}
        earlier_url = "https://github.com/o/r/pull/317"
        for state, answer in cases.items():
            with self.subTest(state=state, existing=False):
                self.record.unlink(missing_ok=True)
                self.stub.set(pr_view=[answer])
                code, verdict = self.wait()
                self.assertEqual((code, verdict.get("state")), (1, state), verdict)
                self.assertFalse(self.record.exists(), "pr.json written")
            with self.subTest(state=state, existing=True):
                self.stub.set(pr_view=[ok(view(self.head, 317, earlier_url, "feat/x"))], api_graphql=[ok(threads_doc())])
                code, verdict = self.wait()
                self.assertEqual((code, verdict.get("state")), (0, "pass"), verdict)
                self.assertTrue(self.record.is_file(), "the earlier ci wait on PR 317 wrote no pr.json")
                self.assertEqual(json.loads(self.record.read_text()),
                                 {"number": 317, "url": earlier_url, "branch": "feat/x"})
                earlier = self.record.read_text()
                stamp = self.record.stat().st_mtime_ns
                self.stub.set(pr_view=[answer])
                code, verdict = self.wait()
                self.assertEqual((code, verdict.get("state")), (1, state), verdict)
                self.assertEqual(self.record.read_text(), earlier)
                self.assertEqual(self.record.stat().st_mtime_ns, stamp)

    def test_o13_linked_pr_json_is_not_written_through(self) -> None:
        """O13, D2: a symbolic link at `pr.json` (to a file, or dangling): no write through it, its target
        unchanged or never made, the same verdict as without the link (which records PR 318)."""
        self.stub.set(**self.answers())
        plain = self.wait()
        self.assertEqual((plain[0], plain[1].get("state")), (0, "pass"), plain[1])
        self.assertTrue(self.record.is_file() and not self.record.is_symlink(), "the run with no link wrote no pr.json")
        self.assertEqual(json.loads(self.record.read_text())["number"], 318)
        elsewhere = self.root / "elsewhere"
        target, missing = elsewhere / "kept.json", elsewhere / "missing.json"
        put(target, "kept\n")
        stamp = target.stat().st_mtime_ns
        for to in (target, missing):
            with self.subTest(target=to.name):
                if self.record.is_symlink() or self.record.exists():
                    self.record.unlink()
                self.record.parent.mkdir(parents=True, exist_ok=True)
                self.record.symlink_to(to)
                self.stub.set(**self.answers())
                code, verdict = self.wait()
                self.assertEqual((code, verdict.get("state")), (plain[0], plain[1].get("state")), verdict)
                self.assertEqual(target.read_text(), "kept\n")
                self.assertEqual(target.stat().st_mtime_ns, stamp)
                self.assertFalse(missing.exists(), "wrote through a dangling link")


if __name__ == "__main__":
    unittest.main()
