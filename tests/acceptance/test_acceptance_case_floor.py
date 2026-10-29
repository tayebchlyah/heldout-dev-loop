#!/usr/bin/env python3
"""Held-out acceptance tests for the acceptance case floor (spec: Acceptance Case Floor).

One test per `## Acceptance flows` item (F<n>), one subtest per `## Acceptance oracle` line (O<n>) it proves.
Seam: `devloop.py acceptance` (the kit source, `skill/scripts/devloop.py`), run as a process in the linked worktree
of the `GateRepo` fixture of `merge_check_binding_support.py`: `commands.acceptance` at `main` is a stub that copies
a fixture JUnit file into `$DEVLOOP_REPORT_DIR` and exits with a fixture code; commits land between runs.

Fixture choices the spec leaves open:
- Each scenario runs on its own fresh `GateRepo`, so no guard loop carries over from another scenario.
- The fix-round commit that narrows the runner changes `pytest.ini` at the worktree root (no `tests/acceptance/`
  path); the stub runner then copies a JUnit file with fewer cases.
- The new seal (O6) is a commit that adds `tests/acceptance/test_widgets.py` in the worktree.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from merge_check_binding_support import GateRepo, junit

NARROW = {"pytest.ini": "[pytest]\naddopts = --ignore=tests/acceptance/test_b.py\n"}
SEAL = {"tests/acceptance/test_widgets.py": "def test_widget():\n    assert True\n"}
RED_FOUR = junit(failing=("test_a", "test_b"), passing=2)


class CaseFloorTests(unittest.TestCase):

    def gate(self) -> GateRepo:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return GateRepo(Path(tmp.name).resolve())

    def run_once(self, gate: GateRepo, xml: str, exit_code: int, *args: str) -> tuple[int, dict]:
        gate.fixture(xml, exit_code)
        before = gate.ran()
        code, run, _ = gate.acceptance(*args)
        self.assertNotIn("error", run)
        self.assertEqual(gate.ran(), before + 1, f"the acceptance command did not run once: {run}")
        return code, run

    def guard(self, run: dict) -> dict:
        self.assertIsInstance(run.get("guard"), dict, run)
        return run["guard"]

    def last_record(self, run: dict) -> dict:
        data = json.loads(Path(run["record"]).read_text())
        self.assertIsInstance(data.get("runs"), list, data)
        return data["runs"][-1]

    def red_then(self, gate: GateRepo, files: dict[str, str], message: str) -> str:
        """A full run of 4 executed cases, 2 failing, then a commit; the head of the first run."""
        code, first = self.run_once(gate, RED_FOUR, 1)
        self.assertEqual((first.get("cases"), first.get("failing")), (4, 2), first)
        gate.commit(gate.wt, files, message)
        return first["head"]

    def test_f1_narrowed_runner_is_short_and_the_guard_stops(self) -> None:
        """F1, O1, O2: a red full run, a fix-round commit that skips a test file, then a passing run with fewer
        executed cases is short, not green: the guard stops, exit 1."""
        with self.subTest(oracle=1):
            gate = self.gate()
            head = self.red_then(gate, NARROW, "Skip the failing test file")
            code, run = self.run_once(gate, junit(passing=3), 0)
            self.assertEqual(run.get("cases"), 3, run)
            self.assertEqual(run.get("floor"), 4, run)
            self.assertIs(run.get("short"), True, run)
            guard = self.guard(run)
            self.assertEqual((guard.get("verdict"), guard.get("reason")), ("stop", "short"), run)
            self.assertEqual(guard.get("floor"), 4, run)
            hint = str(run.get("hint"))
            self.assertTrue(hint.startswith("ran 3 of 4 cases"), run)
            self.assertIn(f"(full run at {head[:12]})", hint, run)
            self.assertEqual(code, 1, run)
            last = self.last_record(run)
            self.assertEqual((last.get("floor"), last.get("short")), (4, True), last)

        with self.subTest(oracle=2):
            gate = self.gate()
            self.red_then(gate, NARROW, "Skip the failing test file")
            code, run = self.run_once(gate, junit(passing=3, skipped=1), 0)
            self.assertEqual(run.get("cases"), 3, run)
            self.assertEqual(run.get("floor"), 4, run)
            self.assertIs(run.get("short"), True, run)
            guard = self.guard(run)
            self.assertEqual((guard.get("verdict"), guard.get("reason")), ("stop", "short"), run)
            self.assertEqual(code, 1, run)

    def test_f2_honest_loop_goes_green_and_a_new_seal_resets_the_floor(self) -> None:
        """F2, O3, O4, O6: same or more executed cases go green; a red run with fewer cases keeps its verdict;
        a commit under `tests/acceptance/` after the earlier run drops it from the floor."""
        for passing in (4, 5):
            with self.subTest(oracle=3, passing=passing):
                gate = self.gate()
                self.red_then(gate, {"app.py": "def widget():\n    return 2\n"}, "Fix the widget")
                code, run = self.run_once(gate, junit(passing=passing), 0)
                self.assertEqual(run.get("cases"), passing, run)
                self.assertEqual(run.get("floor"), 4, run)
                self.assertIs(run.get("short"), False, run)
                self.assertEqual(self.guard(run).get("verdict"), "green", run)
                self.assertEqual(code, 0, run)
                last = self.last_record(run)
                self.assertEqual((last.get("floor"), last.get("short")), (4, False), last)

        with self.subTest(oracle=4):
            gate = self.gate()
            self.red_then(gate, {"app.py": "def widget():\n    return 2\n"}, "Fix part of the widget")
            _, run = self.run_once(gate, junit(failing=("test_a",), passing=2), 1)
            self.assertEqual((run.get("cases"), run.get("failing")), (3, 1), run)
            self.assertEqual(run.get("floor"), 4, run)
            self.assertIs(run.get("short"), False, run)
            guard = self.guard(run)
            self.assertEqual(guard.get("verdict"), "continue", run)
            self.assertNotEqual(guard.get("reason"), "short", run)

        with self.subTest(oracle=6):
            gate = self.gate()
            self.red_then(gate, SEAL, "Seal the new held-out tests")
            code, run = self.run_once(gate, junit(passing=3), 0)
            self.assertEqual(run.get("cases"), 3, run)
            self.assertIsNone(run.get("floor"), run)
            self.assertIn("floor", run)
            self.assertIs(run.get("short"), False, run)
            self.assertEqual(self.guard(run).get("verdict"), "green", run)
            self.assertEqual(code, 0, run)


if __name__ == "__main__":
    unittest.main()
