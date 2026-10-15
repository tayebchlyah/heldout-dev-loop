#!/usr/bin/env python3
"""Robustness tests for `devloop.py board` (plan Review Focus). Same sandbox as the oracle tests."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from test_board import DEVLOOP, MACHINE, PROJECT, Board, git, note_name, put, read_note, snapshot


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
