"""#239: the board writes and reads no file through a link: state files, the server log, linked specs and plans."""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / "skill" / "scripts"))
import devloop  # noqa: E402
from board import state  # noqa: E402
from board_fixtures import Board, put  # noqa: E402

STATE_FILES = ("board.lock", "board-claims.json", "board-features.json", "board-state.json", "board-events.jsonl")


class BoardStateLinkTests(Board):
    def test_link_at_a_state_file_leaves_its_target_untouched(self) -> None:
        """NV8: a link at any state file, to a file or dangling: the target is unchanged or absent, skip `state-link`."""
        self.worktree("a/$(>marker)", prefix="fix")
        state_dir = self.repo / ".git" / "dev-loop"
        for name in STATE_FILES:
            for dangling in (False, True):
                with self.subTest(name=name, dangling=dangling):
                    shutil.rmtree(state_dir, ignore_errors=True)
                    state_dir.mkdir()
                    victim = self.root / f"victim-{name}"
                    victim.unlink(missing_ok=True)
                    if not dangling:
                        put(victim, "keep\n")
                    os.symlink(victim, state_dir / name)
                    code, out = self.board()
                    self.assertEqual(code, 0, out)
                    self.assertTrue(self.skipped(out, "state-link"), out)
                    if dangling:
                        self.assertFalse(victim.exists(), name)
                    else:
                        self.assertEqual(victim.read_text(), "keep\n", name)

    def test_linked_state_folder_is_refused(self) -> None:
        """NV8: the state folder itself a link: nothing is written through it."""
        target = self.root / "elsewhere"
        target.mkdir()
        os.symlink(target, self.repo / ".git" / "dev-loop")
        self.worktree("tiny", prefix="fix")
        code, out = self.board()
        self.assertEqual(code, 0, out)
        self.assertTrue(self.skipped(out, "state-link"), out)
        self.assertEqual(list(target.iterdir()), [])

    def test_state_folder_of_another_user_is_refused(self) -> None:
        """NV8: a state folder this user does not own gives `state-owner`; `save_last_run` writes nothing there."""
        folder = self.root / "state"
        folder.mkdir()
        with mock.patch.object(os, "getuid", return_value=os.getuid() + 1):
            self.assertEqual(state.unsafe(folder), "state-owner")
            state.save_last_run(folder, "2026-11-04T00:00:00Z", [])
        self.assertEqual(list(folder.iterdir()), [])
        self.assertIsNone(state.unsafe(folder))
        self.assertIsNone(state.unsafe(self.root / "missing"))


class BoardLogLinkTests(Board):
    def test_dangling_log_link_creates_nothing(self) -> None:
        """F9: a dangling link at the old temp log and at the per-user log: no target is created, the server starts."""
        targets = [self.root / "made-by-temp-log", self.root / "made-by-home-log"]
        os.symlink(targets[0], self.root / "dev-loop-board.log")
        os.symlink(targets[1], self.home / ".config" / "dev-loop" / "board.log")
        self.worktree("tiny", prefix="fix")
        code, out = self.board(serve=True, timeout=90)
        self.assertEqual((code, out["server"]["state"]), (0, "started"), out)
        self.assertEqual([t.exists() for t in targets], [False, False])

    def test_log_is_a_private_file_beside_the_pidfile(self) -> None:
        """F9: the server log is `~/.config/dev-loop/board.log`, mode 0600."""
        self.worktree("tiny", prefix="fix")
        code, out = self.board(serve=True, timeout=90)
        self.assertEqual((code, out["server"]["state"]), (0, "started"), out)
        log = self.home / ".config" / "dev-loop" / "board.log"
        self.assertEqual(stat.S_IMODE(log.lstat().st_mode), 0o600)
        self.assertFalse((self.root / "dev-loop-board.log").exists())


class LinkedSpecTests(Board):
    def setUp(self) -> None:
        super().setUp()
        follow = mock.patch.object(devloop, "FOLLOW_NOTE_LINKS", False)
        follow.start()
        self.addCleanup(follow.stop)

    def test_linked_spec_plan_or_folder_gives_no_entry(self) -> None:
        """F10: a linked Spec.md, feature folder, old `specs/` file or folder: no specs entry; a linked plan: none.
        Outside the board (`decide`, #51) a linked spec still counts."""
        secret = self.repo / ".env"
        put(secret, "---\nfeature: leak\n---\n# SECRET-TOKEN-123\n")
        real = self.spec("real", "Real Thing")
        (self.notes / "Features" / "Leak").mkdir()
        os.symlink(secret, self.notes / "Features" / "Leak" / "Spec.md")
        outside = self.root / "outside"
        put(outside / "Spec.md", "---\nfeature: far\n---\n# Far\n")
        os.symlink(outside, self.notes / "Features" / "Far")
        self.assertEqual(devloop.read_specs(self.notes), [("real", real)])
        with mock.patch.object(devloop, "FOLLOW_NOTE_LINKS", True):
            self.assertEqual([slug for slug, _ in devloop.read_specs(self.notes)], ["far", "leak", "real"])
        os.symlink(secret, real.parent / "Plan.md")
        self.assertIsNone(devloop.find_plan(self.notes, real))

    def test_linked_old_layout_folders_give_nothing(self) -> None:
        """F10: old layout: a linked `specs/` or `plans/` folder, or a linked file in them, is skipped."""
        secret = self.repo / ".env"
        put(secret, "---\nfeature: leak\nspec: old.md\n---\n# SECRET-TOKEN-123\n")
        put(self.notes / "specs" / "old.md", "---\nfeature: old\n---\n# Old\n")
        os.symlink(secret, self.notes / "specs" / "leak.md")
        (self.notes / "plans").mkdir()
        os.symlink(secret, self.notes / "plans" / "leak.md")
        old = self.notes / "specs" / "old.md"
        self.assertEqual(devloop.read_specs(self.notes), [("old", old)])
        self.assertIsNone(devloop.find_plan(self.notes, old))
        shutil.rmtree(self.notes / "specs")
        shutil.rmtree(self.notes / "plans")
        far = self.root / "far"
        put(far / "specs" / "x.md", "---\nfeature: x\n---\n# X\n")
        put(far / "plans" / "x.md", "---\nspec: x.md\n---\n# Plan\n")
        os.symlink(far / "specs", self.notes / "specs")
        os.symlink(far / "plans", self.notes / "plans")
        self.assertEqual(devloop.read_specs(self.notes), [])
        self.assertIsNone(devloop.find_plan(self.notes, far / "specs" / "x.md"))

    def test_linked_spec_never_reaches_a_board_note(self) -> None:
        """F10: the first `#` line of a linked spec's target is not a note title."""
        put(self.repo / ".env", "---\nfeature: leak\n---\n# SECRET-TOKEN-123\n")
        (self.notes / "Features" / "Leak").mkdir()
        os.symlink(self.repo / ".env", self.notes / "Features" / "Leak" / "Spec.md")
        self.worktree("leak")
        self.ok()
        for path in (self.board_dir / "Features").glob("*.md"):
            self.assertNotIn("SECRET-TOKEN-123", path.read_text(), path)
        self.assertNotEqual(self.note("leak").get("title"), "SECRET-TOKEN-123")
