"""Board shipped notes kept per project: the default 5 and `board.shipped_kept` of `dev-loop.json`."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from board_fixtures import MACHINE, PROJECT, VERSION, Board, git, note_name, note_text, put, read_note, status_note  # noqa: E402


class BoardShippedTests(Board):
    def shipped_notes(self, count: int, kept: int | None = None) -> list[Path]:
        paths = []
        for day in range(1, count + 1):
            slug = f"done{day}"
            path = self.board_dir / "Features" / note_name(MACHINE["id"], PROJECT, slug)
            put(path, note_text(status_note(feature=slug, machine_id=MACHINE["id"], owner=MACHINE["owner"],
                                            machine=MACHINE["machine"], shipped_at=f"2026-10-0{day}T12:00:00Z",
                                            **({} if kept is None else {"shipped_kept": kept}))))
            paths.append(path)
        return paths

    def main_config(self, extra: dict) -> None:
        put(self.repo / "dev-loop.json", json.dumps({"version": VERSION, "harnesses": ["claude"], **extra}) + "\n")
        git(self.repo, "commit", "-q", "-am", "config")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "origin")

    def test_six_own_shipped_notes_keep_the_five_latest(self) -> None:
        """Board Ledger D5: 6 own shipped notes in one project, the next run keeps the 5 latest shipped_at."""
        paths = self.shipped_notes(6)
        self.ok()
        self.assertEqual([p.exists() for p in paths], [False, True, True, True, True, True])

    def test_board_shipped_kept_on_the_main_ref_sets_the_shipped_notes_kept(self) -> None:
        """#230: `board.shipped_kept` 2 in `dev-loop.json` on the main ref: the 2 latest shipped notes stay, and each
        written note carries `shipped_kept` 2. A worktree copy of `dev-loop.json` does not change it."""
        self.main_config({"board": {"shipped_kept": 2}})
        paths = self.shipped_notes(4)
        wt = self.worktree("tiny", prefix="fix")
        self.ok()
        self.assertEqual([p.exists() for p in paths], [False, False, True, True])
        self.assertEqual(read_note(self.own_notes()["tiny"])["shipped_kept"], 2)
        put(wt / "dev-loop.json", json.dumps({"version": VERSION, "board": {"shipped_kept": 9}}) + "\n")
        self.ok(cwd=wt)
        self.assertEqual(read_note(self.own_notes()["tiny"])["shipped_kept"], 2)

    def test_bad_board_shipped_kept_keeps_five(self) -> None:
        """#230: a `board.shipped_kept` that is not a whole number of 0 or more: 5 shipped notes stay."""
        for value in ("3", True, -1, 2.5, None):
            with self.subTest(value=value):
                self.main_config({"board": {"shipped_kept": value}})
                paths = self.shipped_notes(6)
                self.ok()
                self.assertEqual([p.exists() for p in paths], [False, True, True, True, True, True])

    def test_changed_shipped_kept_rewrites_an_active_note_without_moving_updated(self) -> None:
        """#230: `board.shipped_kept` changes on main: an active note gets the new value, `updated` stays."""
        wt = self.worktree("tiny", prefix="fix")
        self.ok(cwd=wt)
        before = read_note(self.own_notes()["tiny"])
        time.sleep(1.1)
        self.main_config({"board": {"shipped_kept": 3}})
        self.ok(cwd=wt)
        after = read_note(self.own_notes()["tiny"])
        self.assertEqual((before["shipped_kept"], after["shipped_kept"]), (5, 3))
        self.assertEqual(after["updated"], before["updated"])

    def test_lowered_shipped_kept_reaches_the_kept_shipped_notes(self) -> None:
        """#230: 3 kept shipped notes carrying 5, main says 3: all stay, each carries 3, `shipped_at` and `updated` stay."""
        self.main_config({"board": {"shipped_kept": 3}})
        paths = self.shipped_notes(3, kept=5)
        before = [read_note(p) for p in paths]
        self.ok()
        after = [read_note(p) for p in paths]
        self.assertEqual([n["shipped_kept"] for n in after], [3, 3, 3])
        self.assertEqual([(n["shipped_at"], n["updated"]) for n in after], [(n["shipped_at"], n["updated"]) for n in before])

    def test_note_pruned_in_the_run_that_ships_it_is_never_written(self) -> None:
        """#230: `shipped_kept` 0, a Track S note ships in this run: it is removed, never written, not in `notes`."""
        self.main_config({"board": {"shipped_kept": 0}})
        wt = self.worktree("tiny", prefix="fix")
        put(wt / "tiny.txt", "work\n")
        git(wt, "add", "tiny.txt")
        git(wt, "commit", "-q", "-m", "tiny")
        self.ok(cwd=wt)
        path = self.own_notes()["tiny"]
        git(self.repo, "cherry-pick", "fix/tiny")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "origin")
        git(self.repo, "worktree", "remove", "--force", str(wt))
        git(self.repo, "branch", "-D", "fix/tiny")
        out = self.ok()
        self.assertFalse(path.exists())
        self.assertIn(str(path), [str(Path(p).resolve()) for p in out["removed"]])
        self.assertNotIn(str(path), [str(Path(p).resolve()) for p in out["notes"]])
