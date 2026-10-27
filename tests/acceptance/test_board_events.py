#!/usr/bin/env python3
"""Held-out oracle tests for the hook events and the ship pass of `devloop.py board` (spec: Progress Board).

Each test names the `## Acceptance oracle` number (O<n>) or flow (F<n>) it checks. The tests use
only the public seams: the stdout JSON of `devloop.py board`, the files it writes, the HTTP answers
of the server it starts, and the stdout of the hook scripts. Every name in here is invented (D33).
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

# Shared board fixtures live in tests/unit/python; the unit suite never imports this held-out file.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "unit" / "python"))
from board_fixtures import (ISO_Z, MACHINE, OTHER, PROJECT, Board, git, note_name, note_text, put, read_note, slow,
                            status_note)


class BoardEventTests(Board):
    def test_o20_stop_sets_reply_in_the_session_on_its_feature_only(self) -> None:
        """O20, D16, D17: --event stop in a Stage 5 worktree sets waiting on that feature only."""
        wt_a, _ = self.stage5("alpha", "Alpha Report")
        self.stage5("beta", "Beta Import")
        self.event("stop", wt_a)
        self.assertEqual(self.note("alpha")["waiting"], "reply in the session")
        self.assertEqual(self.note("beta")["waiting"], "")

    def test_o21_stop_at_stage_7_sets_review_the_pr(self) -> None:
        """O21, D17: --event stop at Stage 7 sets waiting "review the PR"."""
        wt = self.stage7("gadget", "Gadget Export")
        self.event("stop", wt)
        note = self.note("gadget")
        self.assertEqual((note["stage"], note["waiting"]), (7, "review the PR"))

    def test_o22_prompt_clears_waiting_on_its_session_features_only(self) -> None:
        """O22, D16, D17: --event prompt clears waiting on the session's features only."""
        wt_a, _ = self.stage5("alpha", "Alpha Report")
        wt_b, _ = self.stage5("beta", "Beta Import")
        self.event("stop", wt_a)
        self.event("stop", wt_b)
        self.event("prompt", wt_a)
        self.assertEqual(self.note("alpha")["waiting"], "")
        self.assertEqual(self.note("beta")["waiting"], "reply in the session")

    def test_d17_session_start_clears_and_subagent_stop_keeps(self) -> None:
        """D17 (with O22): session-start clears waiting, subagent-stop does not change it."""
        wt, _ = self.stage5("alpha", "Alpha Report")
        self.event("stop", wt)
        self.event("subagent-stop", wt)
        self.assertEqual(self.note("alpha")["waiting"], "reply in the session")
        self.event("session-start", wt)
        self.assertEqual(self.note("alpha")["waiting"], "")
        self.event("subagent-stop", wt)
        self.assertEqual(self.note("alpha")["waiting"], "")

    def test_f1_session_a_claim_stop_approve_stop(self) -> None:
        """F1: claim, stop: Stage 2 "approve the spec"; approval, stop: Stage 3 "start Session B"."""
        spec = self.spec("gadget", "Gadget Export", review="draft")
        self.ok("--claim", "gadget")
        self.event("stop", self.repo)
        note = self.note("gadget")
        self.assertEqual((note["stage"], note["waiting"]), (2, "approve the spec"))
        self.set_field(spec, "review_status", "approved")
        self.event("stop", self.repo)
        note = self.note("gadget")
        self.assertEqual((note["stage"], note["waiting"]), (3, "start Session B"))

    @slow
    def test_f2_session_b_continues_the_claimed_note(self) -> None:
        """F2, O9, O19: the worktree note continues the Stage 2 time; tasks 1 and 2 done, task 3 running."""
        spec = self.spec("gadget", "Gadget Export", review="draft")
        self.ok("--claim", "gadget")
        self.ok()
        path = self.own_notes()["gadget"]
        spec_time = read_note(path)["stage_times"]["2"]
        self.set_field(spec, "review_status", "approved")
        self.ok()
        wt = self.worktree("gadget")
        self.acceptance(wt, "gadget")
        plan = self.plan(spec, "Gadget Export", 3)
        self.ok(cwd=wt)
        time.sleep(1.1)
        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        time.sleep(1.1)
        self.ledger(wt, plan, "Task 1: complete", "Task 2: complete")
        self.ok(cwd=wt)
        self.assertEqual(self.own_notes(), {"gadget": path})
        note = read_note(path)
        self.assertEqual(note["stage"], 5)
        self.assertEqual(note["stage_times"]["2"], spec_time)
        tasks = note["tasks"]
        self.assertEqual([t["state"] for t in tasks], ["done", "done", "doing"])
        self.assertTrue(tasks[0]["done"] and tasks[1]["done"])
        self.assertEqual(tasks[2]["started"], tasks[1]["done"])

    @slow
    def test_f3_another_machine_note_is_left_alone(self) -> None:
        """F3, O10: machine A (claimed Stage 3) never changes the Stage 5 note of machine B."""
        spec = self.spec("gadget", "Gadget Export", review="draft")
        self.ok("--claim", "gadget")
        self.set_field(spec, "review_status", "approved")
        self.ok()
        clone = self.root / "bench" / PROJECT
        clone.parent.mkdir()
        git(self.root, "clone", "-q", str(self.origin), str(clone))
        home_b = self.root / "home-b"
        env_b = self.make_env(home_b, self.notes)
        self.write_machine(home_b, OTHER)
        wt_b = self.worktree("gadget", repo=clone)
        self.acceptance(wt_b, "gadget")
        self.plan(spec, "Gadget Export", 3)
        self.ok(cwd=wt_b, env=env_b)
        self.assertEqual(self.note("gadget", OTHER["id"])["stage"], 5)
        path_b = self.own_notes(OTHER["id"])["gadget"]
        before = (path_b.read_bytes(), path_b.stat().st_mtime_ns)
        self.event("stop", self.repo)
        self.ok()
        self.assertEqual((path_b.read_bytes(), path_b.stat().st_mtime_ns), before)
        self.assertEqual(self.note("gadget")["stage"], 3)


class BoardShipTests(Board):
    def ship(self, title: str, day: str = "2026-10-15") -> None:
        self.set_field(self.notes / "specs" / f"20261001 {title}.md", "shipped", day)

    @slow
    def test_o24_ship_sets_shipped_at_once(self) -> None:
        """O24, D12: the next run after the ship sets shipped_at; later runs leave the note unchanged."""
        wt, _ = self.stage5("gadget", "Gadget Export")
        self.ok(cwd=wt)
        path = self.own_notes()["gadget"]
        self.ship("Gadget Export")
        self.ok(cwd=wt)
        self.assertRegex(read_note(path)["shipped_at"], ISO_Z)
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        time.sleep(1.1)
        self.ok(cwd=wt)
        self.event("stop", wt)
        self.event("prompt", wt)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_o25_no_new_note_for_a_shipped_feature(self) -> None:
        """O25, D12: a run never creates a note for a feature that is already shipped."""
        self.spec("gadget", "Gadget Export", shipped="2026-10-10")
        wt = self.worktree("gadget")
        self.ok(cwd=wt)
        self.event("stop", wt)
        self.assertNotIn("gadget", self.own_notes())

    def test_o61_shipped_note_is_never_rewritten(self) -> None:
        """O61, D12: a run does not rewrite an existing own shipped note."""
        self.worktree("tiny", prefix="fix")
        self.board_dir.mkdir(parents=True)
        path = self.board_dir / note_name(MACHINE["id"], PROJECT, "old-thing")
        put(path, note_text(status_note(feature="old-thing", machine_id=MACHINE["id"], owner="Robin", machine="lab-mac",
                                        stage=7, shipped_at="2026-10-01T10:00:00Z")))
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        time.sleep(0.05)
        self.ok()
        self.event("stop", self.repo)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    @slow
    def test_o26_o62_six_shipped_notes_keep_the_five_latest(self) -> None:
        """O26, O62, D12, amended by board-ledger: 6 own shipped notes in one project: the next run keeps the 5
        latest shipped_at. Rows: O26, six features shipped one by one; O62, a folder with six shipped notes and a
        foreign one, where the run deletes the oldest and the foreign note stays."""

        def shipped_one_by_one() -> set[str]:
            slugs = ["one", "two", "three", "four", "five", "six"]
            for slug in slugs:
                self.spec(slug, f"Feature {slug.title()}")
                self.worktree(slug)
            self.ok()
            self.assertEqual(set(self.own_notes()), set(slugs))
            for slug in slugs:
                time.sleep(1.1)
                self.ship(f"Feature {slug.title()}")
                self.ok()
            self.ok()
            for path in self.own_notes().values():
                self.assertTrue(read_note(path)["shipped_at"])
            return {"two", "three", "four", "five", "six"}

        def folder_with_six_shipped() -> set[str]:
            self.worktree("tiny", prefix="fix")
            self.board_dir.mkdir(parents=True)
            shipped = {"north": "2026-10-03", "south": "2026-10-01", "east": "2026-10-04", "west": "2026-10-02",
                       "upper": "2026-10-06", "lower": "2026-10-05"}
            for slug, day in shipped.items():
                put(self.board_dir / note_name(MACHINE["id"], PROJECT, slug),
                    note_text(status_note(feature=slug, machine_id=MACHINE["id"], owner="Robin", machine="lab-mac",
                                          stage=7, shipped_at=f"{day}T10:00:00Z")))
            foreign = self.board_dir / note_name(OTHER["id"], PROJECT, "ancient")
            put(foreign, note_text(status_note(feature="ancient", shipped_at="2026-09-01T10:00:00Z")))
            foreign_before = (foreign.read_bytes(), foreign.stat().st_mtime_ns)
            out = self.ok()
            south = self.board_dir / note_name(MACHINE["id"], PROJECT, "south")
            self.assertIn(str(south), [str(Path(p).resolve()) for p in out["removed"]])
            self.assertEqual((foreign.read_bytes(), foreign.stat().st_mtime_ns), foreign_before)
            return {"north", "east", "west", "upper", "lower", "tiny"}

        for n, (name, run) in enumerate((("O26 shipped one by one", shipped_one_by_one),
                                         ("O62 folder with six shipped notes", folder_with_six_shipped))):
            with self.subTest(name):
                if n:
                    self.setUp()  # a fresh fixture per row, removed by doCleanups below
                try:
                    expected = run()
                    self.assertEqual(set(self.own_notes()), expected)
                finally:
                    self.doCleanups()

if __name__ == "__main__":
    unittest.main()
