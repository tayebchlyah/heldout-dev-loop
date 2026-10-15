#!/usr/bin/env python3
"""Robustness tests for `devloop.py board` (plan Review Focus). Same sandbox as the oracle tests."""

from __future__ import annotations

import fcntl
import json
import subprocess
import sys
import threading
import time
import unittest

from test_board import DEVLOOP, MACHINE, PROJECT, Board, git, note_name, note_text, put, read_note, snapshot


class BoardRobustTests(Board):
    def test_malformed_board_files_are_left_alone(self) -> None:
        """Review Focus 1: a bad JSON line, no frontmatter, a sync conflict copy: unchanged, exit 0."""
        self.worktree("tiny", prefix="fix")
        ghost_tree = self.worktree("ghost", prefix="fix")
        self.ok()
        own = self.own_notes()
        # ghost leaves the write set, so a parse that skips the bad line would delete its note.
        git(self.repo, "worktree", "remove", "--force", str(ghost_tree))
        git(self.repo, "branch", "-D", "fix/ghost")
        ghost = own["ghost"]
        ghost.write_text(ghost.read_text().replace("stage: 5\n", "stage: [5\n"))
        put(self.board_dir / "plain.md", "Just some words.\n")
        copy = f"{own['tiny'].stem} 2.md"
        put(self.board_dir / copy, own["tiny"].read_text())
        names = [ghost.name, "plain.md", copy]
        before = snapshot(self.board_dir)
        self.ok()
        after = snapshot(self.board_dir)
        for name in names:
            self.assertEqual(after.get(name), before[name], name)

    def test_spec_without_goal_or_assumptions_and_plan_without_tasks(self) -> None:
        """Review Focus 4: no `## Goal`, no `## Assumptions`, no task heading: empty description, decisions, tasks."""
        spec = self.notes / "specs" / "20261001 Bare Thing.md"
        put(spec, "---\ntype: spec\nreview_status: approved\nfeature: bare\ntrack: M\nshipped: \n---\n\n# Bare Thing\n")
        wt = self.worktree("bare")
        self.acceptance(wt, "bare")
        put(self.notes / "plans" / "20261002 Bare Thing Plan.md", f"---\nspec: {spec.name}\n---\n\n# Plan\n\nNo tasks.\n")
        self.ok(cwd=wt)
        note = read_note(self.board_dir / note_name(MACHINE["id"], PROJECT, "bare"))
        self.assertEqual((note["stage"], note["title"]), (5, "Bare Thing"))
        self.assertEqual((note["description"], note["decisions"], note["tasks"]), ("", [], []))

    def test_event_with_bad_stdin_changes_no_waiting(self) -> None:
        """Review Focus 2: an event whose stdin is empty or not JSON has no scope: exit 0, waiting unchanged."""
        wt, _ = self.stage5("alpha", "Alpha Report")
        self.event("stop", wt)
        for stdin in ("", "not json"):
            with self.subTest(stdin=stdin):
                result = subprocess.run([sys.executable, str(DEVLOOP), "board", "--event", "prompt", "--no-serve"],
                                        cwd=wt, env=self.env, input=stdin, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.note("alpha")["waiting"], "reply in the session")

    def test_bad_machine_json_gives_a_json_error(self) -> None:
        """A malformed or incomplete machine.json: exit 1 with one JSON `error`, no traceback, the file unchanged."""
        self.worktree("tiny", prefix="fix")
        path = self.home / ".config" / "dev-loop" / "machine.json"
        for text in ("{not json\n", json.dumps({"id": MACHINE["id"]}) + "\n"):
            with self.subTest(text=text):
                put(path, text)
                code, payload = self.board()
                self.assertEqual(code, 1, payload)
                self.assertIsInstance(payload.get("error"), str)
                self.assertEqual(path.read_text(), text)

    def test_bad_port_env_reports_error(self) -> None:
        """Review Focus 5: DEVLOOP_BOARD_PORT that is not a port: exit 0, server.state error with a message."""
        self.worktree("tiny", prefix="fix")
        for port in ("abc", "70000"):
            with self.subTest(port=port):
                code, payload = self.board(env={**self.env, "DEVLOOP_BOARD_PORT": port}, serve=True)
                self.assertEqual(code, 0, payload)
                self.assertEqual(payload["server"]["state"], "error")
                self.assertTrue(payload["server"].get("message"))

    def hold_lock(self):
        lock = self.repo / ".git" / "dev-loop" / "board.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock, "a+")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle

    def test_busy_event_is_queued_and_applied_by_the_next_run(self) -> None:
        """D2: a stop that finds the lock busy exits 0 with `busy`; the next run applies it."""
        wt, _ = self.stage5("alpha", "Alpha Report")
        self.ok(cwd=wt)
        with self.hold_lock():
            out = self.event("stop", wt)
        self.assertTrue(self.skipped(out, "busy", None), out)
        self.assertEqual(self.note("alpha")["waiting"], "")
        self.ok()
        self.assertEqual(self.note("alpha")["waiting"], "reply in the session")
        self.assertFalse((self.repo / ".git" / "dev-loop" / "board-events.jsonl").exists())

    def test_spec_placeholder_skips_the_whole_run(self) -> None:
        """D5: an iCloud placeholder in specs/: skip `placeholder`, exit 0, claims and notes unchanged."""
        spec = self.spec("gadget", "Gadget Export", review="draft")
        self.ok("--claim", "gadget")
        claims = self.claims.read_text()
        spec.rename(spec.with_name(f".{spec.name}.icloud"))
        before = snapshot(self.notes)
        out = self.ok()
        self.assertTrue(self.skipped(out, "placeholder", None), out)
        self.assertEqual(snapshot(self.notes), before)
        self.assertEqual(self.claims.read_text(), claims)

    def test_start_session_b_clears_once_the_worktree_exists(self) -> None:
        """D6: "start Session B" goes when the claimed feature has a worktree, with no event."""
        self.spec("gadget", "Gadget Export")
        self.ok("--claim", "gadget")
        self.assertEqual(self.note("gadget")["waiting"], "start Session B")
        self.worktree("gadget")
        self.ok()
        self.assertEqual(self.note("gadget")["waiting"], "")

    def test_claim_waits_for_the_lock(self) -> None:
        """D7: --claim waits up to 10 s for the lock: saved after a release, else exit 1 and not saved."""
        self.spec("gadget", "Gadget Export", review="draft")
        for hold, code in ((1, 0), (12, 1)):
            with self.subTest(hold=hold):
                timer = threading.Timer(hold, self.hold_lock().close)
                timer.start()
                try:
                    got, out = self.board("--claim", "gadget", timeout=30)
                finally:
                    timer.join()
                self.assertEqual(got, code, out)
                if code:
                    self.assertEqual(out, {"error": "board lock busy, claim not saved"})
                    self.assertFalse(self.claims.exists())
                else:
                    self.assertIn("gadget", json.loads(self.claims.read_text()))
                    self.claims.unlink()

    def test_tasks_before_stage_5_are_todo(self) -> None:
        """D10: below Stage 5 (a plan, no acceptance file: Stage 3), every task not done is `todo`, no `started`."""
        spec = self.spec("gadget", "Gadget Export")
        wt = self.worktree("gadget")
        self.plan(spec, "Gadget Export", 2)
        self.ok(cwd=wt)
        note = self.note("gadget")
        self.assertEqual(note["stage"], 3)
        self.assertEqual(note["tasks"], [{"n": 1, "title": "Step 1", "state": "todo"},
                                         {"n": 2, "title": "Step 2", "state": "todo"}])

    def test_shipped_note_in_the_write_set_is_never_rewritten(self) -> None:
        """O5 regression (Task 3 fix 2): an own note with shipped_at, feature still in the write set: unchanged."""
        wt, _ = self.stage5("alpha", "Alpha Report")
        self.ok(cwd=wt)
        path = self.own_notes()["alpha"]
        path.write_text(note_text({**read_note(path), "shipped_at": "2026-10-01T00:00:00Z"}))
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        self.ok(cwd=wt)
        self.event("stop", wt)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_features_file_drops_a_feature_that_left_with_no_note(self) -> None:
        """O5 regression (Task 3 fix 3): a feature that left the write set and has no own note leaves the file."""
        wt = self.worktree("tiny", prefix="fix")
        self.ok()
        features = self.repo / ".git" / "dev-loop" / "board-features.json"
        self.assertEqual(json.loads(features.read_text()), ["tiny"])
        git(self.repo, "worktree", "remove", "--force", str(wt))
        git(self.repo, "branch", "-D", "fix/tiny")
        self.ok()
        self.assertEqual(self.own_notes(), {})
        self.assertEqual(json.loads(features.read_text()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
