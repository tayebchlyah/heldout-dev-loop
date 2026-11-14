#!/usr/bin/env python3
"""Held-out acceptance tests for the test time ratchet, acceptance side (spec: Test Time Ratchet).

F2 (oracle 1, 15 to 18): `devloop.py acceptance` on a `RatchetRepo` of `time_ratchet_support.py`, one subtest per
oracle line. "Acceptance base run": a green full `acceptance` in the clean main checkout at M with ids `a` to `l`
at 1 s and `m` at 40 s.

Fixture choices the spec leaves open:
- The branch `feat/x` gets one commit (`src/feature.py`) before its first run, so its tree is not the tree of M.
- The cases run in one worktree, in the order 15, 17, 16, 18: no full run has fewer executed tests than an
  earlier one, so no run is `short` by the case floor of the acceptance run.
- Case 16 checks "any id over 60 s" with a new id `n_huge` at 65 s; case 18 fails id `a`.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from time_ratchet_support import ACC_BASE, RatchetRepo, find, same_path, with_times


class AcceptanceRatchetTests(unittest.TestCase):

    def ratchet(self, payload: dict) -> dict:
        self.assertNotIn("error", payload)
        self.assertIsInstance(payload.get("ratchet"), dict, payload)
        return payload["ratchet"]

    def one(self, items: object, name: str, rule: str, payload: dict) -> dict:
        found = [item for item in find(items, name) if item.get("rule") == rule]
        self.assertEqual(len(found), 1, payload)
        return found[0]

    def run_acceptance(self, repo: RatchetRepo, cwd: Path, times: dict[str, float], *args: str,
                       exit_code: int = 0, failing: tuple[str, ...] = ()) -> tuple[int, dict, dict]:
        repo.fixture(times, exit_code, failing)
        code, payload = repo.acceptance(cwd, *args)
        return code, payload, self.ratchet(payload)

    def test_f2_slow_acceptance_ratchet(self) -> None:
        """F2, O1, O15 to O18."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = RatchetRepo(Path(tmp.name).resolve())
        tree_m = repo.tree(repo.repo)
        record_m = repo.records("acceptance") / f"{tree_m}.json"

        with self.subTest(oracle=1):
            code, out, ratchet = self.run_acceptance(repo, repo.repo, ACC_BASE)
            self.assertEqual(code, 0, out)
            self.assertEqual(ratchet.get("state"), "no-baseline", out)
            self.assertTrue(record_m.is_file(), out)
            self.assertTrue(same_path(ratchet.get("record"), record_m), out)

        wt = repo.worktree("feat/x")
        repo.commit(wt, {"src/feature.py": "def feature():\n    return 1\n"}, "Add the feature")

        with self.subTest(oracle=15, m=61):
            code, out, ratchet = self.run_acceptance(repo, wt, with_times(ACC_BASE, m=61.0))
            self.assertIs(ratchet.get("ok"), False, out)
            item = self.one(ratchet.get("red"), "m", "grown", out)
            self.assertEqual(item.get("limit"), 60, out)
            self.one(ratchet.get("attention"), "m", "max", out)
            self.assertEqual(code, 1, out)
            self.assertEqual((out.get("guard") or {}).get("verdict"), "green", out)
        with self.subTest(oracle=15, m=55):
            code, out, ratchet = self.run_acceptance(repo, wt, with_times(ACC_BASE, m=55.0))
            self.assertEqual(ratchet.get("red"), [], out)
            self.assertEqual(code, 0, out)

        with self.subTest(oracle=17):
            repo.trailer_commit(wt, "src/flow.py", "flow does more work")
            code, out, ratchet = self.run_acceptance(repo, wt, with_times(ACC_BASE, m=61.0))
            self.assertEqual(ratchet.get("red"), [], out)
            self.assertIsNone((ratchet.get("growth") or {}).get("waived"), out)
            self.assertIs(ratchet.get("ok"), True, out)
            self.assertEqual(code, 0, out)
            waived = find(ratchet.get("waived"), "m")
            self.assertEqual(len(waived), 1, out)
            self.assertEqual(waived[0].get("reason"), "flow does more work", out)

        with self.subTest(oracle=16, case="unmarked"):
            code, out, ratchet = self.run_acceptance(repo, wt, with_times(ACC_BASE, n_new=12.0))
            item = self.one(ratchet.get("attention"), "n_new", "unmarked", out)
            self.assertEqual(item.get("limit"), 10, out)
            self.assertIs(ratchet.get("ok"), True, out)
            self.assertEqual(code, 0, out)
        with self.subTest(oracle=16, case="marked and max"):
            code, out, ratchet = self.run_acceptance(repo, wt, with_times(ACC_BASE, n_slow_new=31.0, n_huge=65.0))
            item = self.one(ratchet.get("attention"), "n_slow_new", "marked", out)
            self.assertEqual(item.get("limit"), 30, out)
            self.one(ratchet.get("attention"), "n_huge", "max", out)
            self.assertIs(ratchet.get("ok"), True, out)
            self.assertEqual(code, 0, out)

        folder = repo.records("acceptance")
        before = {path.name: path.read_bytes() for path in folder.iterdir()}
        with self.subTest(oracle=18, case="failed-mode"):
            code, out, ratchet = self.run_acceptance(repo, wt, ACC_BASE, "--failed")
            self.assertEqual((ratchet.get("state"), ratchet.get("reason")), ("skipped", "failed-mode"), out)
            self.assertIsNone(ratchet.get("record"), out)
        with self.subTest(oracle=18, case="not-green"):
            code, out, ratchet = self.run_acceptance(repo, wt, with_times(ACC_BASE, n_slow_new=1.0, n_huge=1.0),
                                                     exit_code=1, failing=("a",))
            self.assertEqual((ratchet.get("state"), ratchet.get("reason")), ("skipped", "not-green"), out)
            self.assertIsNone(ratchet.get("record"), out)
        with self.subTest(oracle=18, case="no record"):
            self.assertEqual({path.name: path.read_bytes() for path in folder.iterdir()}, before)


if __name__ == "__main__":
    unittest.main()
