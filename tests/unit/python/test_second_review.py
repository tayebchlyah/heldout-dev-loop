#!/usr/bin/env python3
"""Tests for devloop.py second-review: the locks and the decision order up to the CLI run."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[3] / "skill" / "scripts"
DEVLOOP = SCRIPTS / "devloop.py"
sys.path.insert(0, str(SCRIPTS))
GIT = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "commit.gpgsign=false"]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class SecondReviewDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make(self, project: str = '{"second_review": true}', branch: str = "main", local: str | None = '{"second_review": {"codex": {}}}',
             commit: bool = True) -> Path:
        """A bare origin, a main checkout with the local file, a feature worktree. Returns the worktree."""
        origin, main = self.tmp / "origin.git", self.tmp / "main"
        git(self.tmp, "init", "-q", "--bare", "-b", branch, str(origin))
        git(self.tmp, "clone", "-q", str(origin), str(main))
        git(main, "checkout", "-q", "-b", branch)
        (main / "dev-loop.json").write_text(project)
        git(main, "add", "."), git(main, "commit", "-qm", "base"), git(main, "push", "-q", "origin", branch)
        if local is not None:
            (main / "dev-loop.local.json").write_text(local)
        tree = self.tmp / "feature"
        git(main, "worktree", "add", "-q", "-b", "feat/x", str(tree))
        if commit:
            (tree / "a.txt").write_text("a\n")
            git(tree, "add", "."), git(tree, "commit", "-qm", "change")
        return tree

    def result(self, tree: Path, harness: str = "claude") -> tuple[dict, int]:
        import second_review
        def review(*args: object) -> tuple[dict, int]:
            return {**args[-1], "state": "done"}, 0  # type: ignore[dict-item]

        with mock.patch.object(second_review, "_review", side_effect=review) as patched:
            out, code = second_review.run(tree, harness, 15, {})
        out["reviewed"] = patched.called
        return out, code

    def check(self, tree: Path, state: str, reason: str | None, code: int, harness: str = "claude") -> dict:
        out, got = self.result(tree, harness)
        self.assertEqual((out["state"], out["reason"], got), (state, reason, code), out)
        return out

    def test_base_locks_on_reaches_review(self) -> None:
        out = self.check(self.make(), "done", None, 0)
        self.assertTrue(out["reviewed"])

    def test_project_lock_table(self) -> None:
        for project, state, reason, code in [
            ("{}", "skipped", "project-off", 0), ('{"second_review": false}', "skipped", "project-off", 0),
            ('{"second_review": "yes"}', "failed", "bad-config", 1), ('{"second_review": 1}', "failed", "bad-config", 1),
            ('{"second_review": null}', "failed", "bad-config", 1), ("not json", "failed", "bad-config", 1),
            ('{"second_review": true, "second_review": true}', "failed", "bad-config", 1)]:
            with self.subTest(project=project):
                self.setUp()
                self.check(self.make(project), state, reason, code)

    def test_main_ref_only_and_no_replace(self) -> None:
        tree = self.make('{"second_review": false}')
        (tree / "dev-loop.json").write_text('{"second_review": true}')
        git(tree, "commit", "-qam", "on")
        self.check(tree, "skipped", "project-off", 0)
        git(tree, "tag", "origin/main")
        git(tree, "branch", "origin/main")
        self.check(tree, "skipped", "project-off", 0)
        git(tree, "replace", git(tree, "rev-parse", "refs/remotes/origin/main:dev-loop.json"), git(tree, "rev-parse", "HEAD:dev-loop.json"))
        self.check(tree, "skipped", "project-off", 0)

    def test_local_lock_table(self) -> None:
        for local, state, reason, code in [
            ('{"second_review": false}', "skipped", "user-off", 0),
            ('{"second_review": {"codex": false}}', "skipped", "user-off", 0),
            (None, "skipped", "not-chosen", 0), ('{"second_review": {"claude": {}}}', "skipped", "not-chosen", 0),
            ("{}", "skipped", "not-chosen", 0),
            ('{"second_review": {"codex": {"model": "--oss"}}}', "failed", "bad-config", 1),
            ('{"second_review": {"codex": {"model": "gpt\\n"}}}', "failed", "bad-config", 1),
            ('{"second_review": {"codex": {"effort": "max"}}}', "failed", "bad-config", 1),
            ('{"second_review": {"codex": {"temp": 1}}}', "failed", "bad-config", 1),
            ('{"second_review": {"codex": null}}', "failed", "bad-config", 1),
            ('{"second_review": {"gemini": {}}}', "failed", "bad-config", 1),
            ('{"second_review": true}', "failed", "bad-config", 1), ("not json", "failed", "bad-config", 1),
            ('{"second_review": {"codex": {}}, "second_review": false}', "failed", "bad-config", 1)]:
            with self.subTest(local=local):
                self.setUp()
                self.check(self.make(local=local), state, reason, code)

    def test_claude_accepts_bracket_and_slash_models(self) -> None:
        for model in ("opus[1m]", "vendor/model"):
            with self.subTest(model=model):
                self.setUp()
                local = json.dumps({"second_review": {"claude": {"model": model, "effort": "max"}}})
                out = self.check(self.make(local=local), "done", None, 0, harness="codex")
                self.assertEqual(out["reviewer"], "claude")

    def test_repo_state(self) -> None:
        self.check(self.make(commit=False), "skipped", "nothing-to-review", 0)
        self.setUp()
        tree = self.make()
        (Path(git(tree, "rev-parse", "--path-format=absolute", "--git-dir")) / "rebase-merge").mkdir()
        self.check(tree, "skipped", "rebase", 0)
        self.setUp()
        self.check(self.make(branch="master"), "skipped", "no-main-ref", 0)
        outside = self.tmp / "plain"
        outside.mkdir()
        self.check(outside, "failed", "git", 1)

    def test_payload_fields_fill_as_rules_pass(self) -> None:
        tree = self.make(local='{"second_review": {"codex": {"model": "m1", "effort": "low"}}}')
        out, _ = self.result(tree)
        self.assertEqual(out["head"], git(tree, "rev-parse", "HEAD"))
        self.assertEqual(out["base"], git(tree, "rev-parse", "refs/remotes/origin/main"))
        self.assertEqual((out["reviewer"], out["model"], out["effort"], out["endpoint"]), ("codex", "m1", "low", "default"))

    def test_timeout_must_be_finite_above_zero(self) -> None:
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(value=value):
                done = subprocess.run([sys.executable, str(DEVLOOP), "second-review", "--harness", "claude", "--timeout", value],
                                      capture_output=True, text=True)
                self.assertEqual(done.returncode, 2)


if __name__ == "__main__":
    unittest.main()
