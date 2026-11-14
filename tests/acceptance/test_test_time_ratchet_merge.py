#!/usr/bin/env python3
"""Held-out acceptance tests for the test time ratchet, merge decision (spec: Test Time Ratchet).

F3 (oracle 8, 9, 17): `devloop.py merge-check` on a `RatchetRepo` of `time_ratchet_support.py`, one subtest per
oracle line.

Fixture choices the spec leaves open:
- Each branch is Track M: a spec `Features/20261029 <Slug>/Spec.md` in the notes folder, its plan, and the ledger
  line `Feature gate: green at <head>` in the worktree after the last commit, so no other reason makes the
  decision human. A first merge-check before the trailer commit proves the fixture gives `auto`.
- The `Test-Time` trailer sits on a commit that adds a code file; the config change is a later commit of the
  branch `dev-loop.json` that sets `test_time.gate.unmarked` to 5.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from merge_check_binding_support import reasons
from time_ratchet_support import RatchetRepo


class MergeDecisionTests(unittest.TestCase):

    def check(self, repo: RatchetRepo, wt: Path, slug: str) -> dict:
        repo.review(wt, repo.head(wt), "M", slug=slug)
        code, out = repo.merge_check(wt)
        self.assertNotIn("error", out)
        self.assertEqual(code, 0, out)
        return out

    def assert_waived(self, out: dict, commit: str, reason: str) -> None:
        self.assertEqual(out.get("merge"), "human", out)
        found = reasons(out, "test-time-waived")
        self.assertEqual(len(found), 1, out)
        self.assertEqual((found[0].get("commit"), found[0].get("reason")), (commit, reason), out)

    def test_f3_merge_decision_with_a_waiver_or_a_config_change(self) -> None:
        """F3, O8, O9, O17."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = RatchetRepo(Path(tmp.name).resolve())

        repo.spec("x", "M")
        wt = repo.worktree("feat/x")
        repo.commit(wt, {"src/feature.py": "def feature():\n    return 1\n"}, "Add the feature")
        out = self.check(repo, wt, "x")
        self.assertEqual((out.get("merge"), out.get("reasons")), ("auto", []), out)

        with self.subTest(oracle=8):
            commit = repo.trailer_commit(wt, "src/subsystem.py", "new subsystem")
            out = self.check(repo, wt, "x")
            self.assert_waived(out, commit, "new subsystem")
            self.assertEqual(reasons(out, "config-changed"), [], out)

        with self.subTest(oracle=9):
            looser = repo.branch_config(test_time={"gate": {"unmarked": 5}})
            repo.commit(wt, {"dev-loop.json": looser}, "Loosen the unmarked limit")
            out = self.check(repo, wt, "x")
            self.assertEqual(out.get("merge"), "human", out)
            self.assertTrue(reasons(out, "config-changed"), out)

        with self.subTest(oracle=17):
            repo.spec("y", "M")
            other = repo.worktree("feat/y")
            commit = repo.trailer_commit(other, "src/flow.py", "flow does more work")
            out = self.check(repo, other, "y")
            self.assert_waived(out, commit, "flow does more work")


if __name__ == "__main__":
    unittest.main()
