#!/usr/bin/env python3
"""Held-out acceptance tests for the test time ratchet, gate side (spec: Test Time Ratchet).

F1 (oracle 1 to 7, 9 to 14, 19, 20): `devloop.py gate --summary` on the `RatchetRepo` scratch repos of
`time_ratchet_support.py`, one subtest per oracle line. "Base run": a green `gate --summary` in the clean main
checkout at M with ids `a` to `l` at 1 s, `m` at 40 s and `w` at 100 s (sum 152 s).

Fixture choices the spec leaves open:
- The branch `feat/x` gets one commit (`src/feature.py`) before its first run, so its tree is not the tree of M.
- Cases 10, 11, 13, 14, 19, 20 and the `no-main-ref` half of 11 each use their own repo, so no record of
  another case is on an ancestor tree.
- The uncommitted change of case 13 is an unstaged edit of the tracked `README.md`.
- Case 19 makes its 51 trees by one commit on the local `main` per run (`origin/main` stays at M); it waits a
  little over 1 s after the first run, so that run is the oldest by any clock.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from time_ratchet_support import BASE, LETTERS, RatchetRepo, find, same_path, with_times


class GateRatchetTests(unittest.TestCase):

    def repo(self, **kwargs: object) -> RatchetRepo:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return RatchetRepo(Path(tmp.name).resolve(), **kwargs)

    def ratchet(self, payload: dict) -> dict:
        self.assertNotIn("error", payload)
        self.assertIsInstance(payload.get("ratchet"), dict, payload)
        return payload["ratchet"]

    def one(self, items: object, name: str, payload: dict) -> dict:
        found = find(items, name)
        self.assertEqual(len(found), 1, payload)
        return found[0]

    def run_gate(self, repo: RatchetRepo, cwd: Path, times: dict[str, float] | None, exit_code: int = 0,
                 env: dict[str, str] | None = None) -> tuple[int, dict, dict]:
        repo.fixture(times, exit_code)
        code, payload = repo.gate(cwd, env=env)
        return code, payload, self.ratchet(payload)

    def test_f1_slow_gate_ratchet(self) -> None:
        """F1, O1 to O7, O9 to O14, O19, O20."""
        repo = self.repo()
        tree_m = repo.tree(repo.repo)
        record_m = repo.records("gate") / f"{tree_m}.json"

        code, out, ratchet = self.run_gate(repo, repo.repo, BASE)
        with self.subTest(oracle=1):
            self.assertEqual(ratchet.get("state"), "no-baseline", out)
            self.assertTrue(record_m.is_file(), out)
            self.assertTrue(same_path(ratchet.get("record"), record_m), out)
        with self.subTest(oracle=2):
            self.assertEqual(ratchet.get("red"), [], out)
            for name in ("m", "w"):
                item = self.one(ratchet.get("attention"), name, out)
                self.assertEqual((item.get("rule"), item.get("limit")), ("max", 30), out)
            self.assertIs(ratchet.get("ok"), True, out)
            self.assertEqual(code, 0, out)

        wt = repo.worktree("feat/x")
        repo.commit(wt, {"src/feature.py": "def feature():\n    return 1\n"}, "Add the feature")

        with self.subTest(oracle=6, case="unchanged"):
            code, out, ratchet = self.run_gate(repo, wt, BASE)
            self.assertEqual(ratchet.get("state"), "checked", out)
            self.assertEqual((ratchet.get("baseline") or {}).get("tree"), tree_m, out)
            self.assertEqual(ratchet.get("factor"), 1.0, out)
            self.assertEqual(ratchet.get("red"), [], out)
            self.assertIs(ratchet.get("ok"), True, out)
            self.assertEqual(code, 0, out)

        with self.subTest(oracle=3):
            code, out, ratchet = self.run_gate(repo, wt, with_times(BASE, n_new=3.0))
            self.assertIs(ratchet.get("ok"), False, out)
            item = self.one(ratchet.get("red"), "n_new", out)
            self.assertEqual((item.get("rule"), item.get("limit")), ("unmarked", 2), out)
            growth = ratchet.get("growth") or {}
            self.assertEqual(growth.get("share"), 0.02, out)
            self.assertIs(growth.get("ok"), True, out)
            self.assertEqual(code, 1, out)
            self.assertEqual(out.get("exit"), 0, out)

        with self.subTest(oracle=4, seconds=3):
            code, out, ratchet = self.run_gate(repo, wt, with_times(BASE, n_slow_new=3.0))
            self.assertEqual(ratchet.get("red"), [], out)
            self.assertIs(ratchet.get("ok"), True, out)
            self.assertEqual(code, 0, out)
        with self.subTest(oracle=4, seconds=11):
            code, out, ratchet = self.run_gate(repo, wt, with_times(BASE, n_slow_new=11.0))
            item = self.one(ratchet.get("red"), "n_slow_new", out)
            self.assertEqual((item.get("rule"), item.get("limit")), ("marked", 10), out)

        with self.subTest(oracle=5):
            doubled = {name: seconds * 2 for name, seconds in BASE.items()}
            code, out, ratchet = self.run_gate(repo, wt, with_times(doubled, n_new=3.0))
            self.assertEqual(ratchet.get("factor"), 2.0, out)
            self.assertEqual(find(ratchet.get("red"), "n_new"), [], out)
            self.assertEqual((ratchet.get("growth") or {}).get("share"), 0.01, out)
            self.assertIs(ratchet.get("ok"), True, out)
            self.assertEqual(code, 0, out)

        with self.subTest(oracle=6, m=61):
            code, out, ratchet = self.run_gate(repo, wt, with_times(BASE, m=61.0))
            item = self.one(ratchet.get("red"), "m", out)
            self.assertEqual((item.get("rule"), item.get("limit")), ("max", 30), out)
            self.assertEqual(item.get("seconds"), 61.0, out)
        with self.subTest(oracle=6, m=55):
            code, out, ratchet = self.run_gate(repo, wt, with_times(BASE, m=55.0))
            self.assertEqual(find(ratchet.get("red"), "m"), [], out)

        with self.subTest(oracle=9):
            looser = repo.branch_config(test_time={"gate": {"unmarked": 5}})
            repo.commit(wt, {"dev-loop.json": looser}, "Loosen the unmarked limit")
            code, out, ratchet = self.run_gate(repo, wt, with_times(BASE, n_new=3.0))
            item = self.one(ratchet.get("red"), "n_new", out)
            self.assertEqual((item.get("rule"), item.get("limit")), ("unmarked", 2), out)
            self.assertIs(ratchet.get("ok"), False, out)
            self.assertEqual(code, 1, out)

        with self.subTest(oracle=12):
            repo.advance_main({"README.md": "# Acme shop\n\nNow with doors.\n"}, "Describe the doors")
            repo.rebase(wt)
            code, out, ratchet = self.run_gate(repo, wt, BASE)
            self.assertEqual(ratchet.get("state"), "checked", out)
            self.assertEqual((ratchet.get("baseline") or {}).get("tree"), tree_m, out)
            self.assertIs(ratchet.get("ok"), True, out)
            self.assertEqual(code, 0, out)

        three = with_times(BASE, n1_slow=2.0, n2_slow=2.0, n3_slow=2.0)
        with self.subTest(oracle=7, waiver=False):
            code, out, ratchet = self.run_gate(repo, wt, three)
            self.assertEqual(ratchet.get("red"), [], out)
            growth = ratchet.get("growth") or {}
            self.assertEqual(growth.get("share"), 0.039, out)
            self.assertIs(growth.get("ok"), False, out)
            self.assertIs(ratchet.get("ok"), False, out)
            self.assertEqual(code, 1, out)
        with self.subTest(oracle=7, waiver=True):
            repo.trailer_commit(wt, "src/subsystem.py", "new subsystem")
            code, out, ratchet = self.run_gate(repo, wt, three)
            self.assertEqual((ratchet.get("growth") or {}).get("waived"), "new subsystem", out)
            self.assertIs(ratchet.get("ok"), True, out)
            self.assertEqual(code, 0, out)

        spare = self.repo()
        with self.subTest(oracle=10, case="no-junit"):
            code, out, ratchet = self.run_gate(spare, spare.repo, None)
            self.assertEqual((ratchet.get("state"), ratchet.get("reason")), ("skipped", "no-junit"), out)

        with self.subTest(oracle=14):
            code, out, ratchet = self.run_gate(spare, spare.repo, BASE, exit_code=1)
            self.assertEqual(code, 1, out)
            self.assertIsNone(ratchet.get("record"), out)
            self.assertEqual(spare.record_names("gate"), set(), out)

        with self.subTest(oracle=13, case="GATE_SCOPE"):
            code, out, ratchet = self.run_gate(spare, spare.repo, BASE, env={"GATE_SCOPE": "changed"})
            self.assertEqual(code, 0, out)
            self.assertIsNone(ratchet.get("record"), out)
            self.assertEqual(spare.record_names("gate"), set(), out)
        with self.subTest(oracle=13, case="uncommitted"):
            (spare.repo / "README.md").write_text("# Acme shop\n\nA draft line.\n")
            code, out, ratchet = self.run_gate(spare, spare.repo, BASE)
            spare.git(spare.repo, "checkout", "-q", "--", "README.md")
            self.assertEqual(code, 0, out)
            self.assertIsNone(ratchet.get("record"), out)
            self.assertEqual(spare.record_names("gate"), set(), out)

        with self.subTest(oracle=11, case="no-baseline"):
            branch = spare.worktree("feat/x")
            spare.commit(branch, {"src/feature.py": "def feature():\n    return 1\n"}, "Add the feature")
            times = {**{name: 1.0 for name in LETTERS}, "big": 31.0}
            code, out, ratchet = self.run_gate(spare, branch, times)
            self.assertEqual(ratchet.get("state"), "no-baseline", out)
            self.assertIs(ratchet.get("ok"), True, out)
            self.assertEqual(code, 0, out)
            item = self.one(ratchet.get("attention"), "big", out)
            self.assertEqual(item.get("rule"), "max", out)

        with self.subTest(oracle=11, case="no-main-ref"):
            lone = self.repo()
            lone.git(lone.repo, "remote", "remove", "origin")
            lone.git(lone.repo, "branch", "-m", "main", "trunk")
            code, out, ratchet = self.run_gate(lone, lone.repo, with_times(BASE, big=31.0))
            self.assertEqual((ratchet.get("state"), ratchet.get("reason")), ("skipped", "no-main-ref"), out)
            self.assertEqual(out.get("exit"), 0, out)
            self.assertEqual(code, 0, out)

        with self.subTest(oracle=10, case="off"):
            off = self.repo(test_time=False)
            code, out, ratchet = self.run_gate(off, off.repo, BASE)
            self.assertEqual((ratchet.get("state"), ratchet.get("reason")), ("skipped", "off"), out)
            self.assertIsNone(ratchet.get("record"), out)
            self.assertEqual(off.record_names("gate"), set(), out)

        with self.subTest(oracle=20):
            bad = self.repo(test_time={"gate": {"max": -1}})
            code, out, ratchet = self.run_gate(bad, bad.repo, BASE)
            self.assertEqual(ratchet.get("state"), "error", out)
            self.assertIs(ratchet.get("ok"), False, out)
            self.assertEqual(code, 1, out)

        with self.subTest(oracle=19):
            kept = self.repo()
            trees = []
            for number in range(51):
                if number:
                    kept.commit(kept.repo, {f"runs/run_{number}.txt": f"{number}\n"}, f"Run {number}")
                trees.append(kept.tree(kept.repo))
                code, out, ratchet = self.run_gate(kept, kept.repo, BASE)
                self.assertEqual(code, 0, out)
                if not number:
                    time.sleep(1.2)
            self.assertEqual(kept.record_names("gate"), {f"{tree}.json" for tree in trees[1:]})


if __name__ == "__main__":
    unittest.main()
