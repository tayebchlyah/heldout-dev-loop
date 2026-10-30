#!/usr/bin/env python3
"""The board folder names: the writer uses `Dev Loop Board/Features/`, the server reads both spellings."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skill" / "scripts"))
sys.path.insert(1, str(Path(__file__).resolve().parent))
from board import server
from board_fixtures import Board, ISO_Z, MACHINE, git, note_text, put, read_note, snapshot, status_note
from support import check_setup


class BoardLayoutTests(Board):
    def test_writes_dev_loop_board_features(self) -> None:
        self.spec("demo-work", "Demo Work")
        self.ok("--claim", "demo-work")
        written = sorted(p.name for p in (self.notes / "Dev Loop Board" / "Features").glob("*.md"))
        self.assertEqual(len(written), 1, written)
        self.assertFalse((self.notes / "dev-loop-board").exists())
        self.assertEqual(list(self.own_notes()), ["demo-work"])

    def test_old_board_folder_untouched(self) -> None:
        self.spec("demo-work", "Demo Work")
        fields = status_note(feature="old", machine_id=MACHINE["id"], owner=MACHINE["owner"], machine=MACHINE["machine"])
        put(self.notes / "dev-loop-board" / "x.md", note_text(fields))
        before = snapshot(self.notes / "dev-loop-board")
        self.ok("--claim", "demo-work")
        self.assertEqual(snapshot(self.notes / "dev-loop-board"), before)

    def test_note_in_the_old_folder_ships_after_the_move(self) -> None:
        """Issue #114: a run that does not see an own note keeps its slug, so the note ships once it is moved back."""
        wt = self.worktree("tiny", prefix="fix")
        put(wt / "tiny.txt", "work\n")
        git(wt, "add", "tiny.txt")
        git(wt, "commit", "-q", "-m", "tiny")
        self.ok(cwd=wt)
        path = self.own_notes()["tiny"]
        old = self.notes / "dev-loop-board" / "features" / path.name
        old.parent.mkdir(parents=True)
        path.rename(old)
        git(self.repo, "cherry-pick", "fix/tiny")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "origin")
        git(self.repo, "worktree", "remove", "--force", str(wt))
        git(self.repo, "branch", "-D", "fix/tiny")
        self.ok()
        old.rename(path)
        self.ok()
        self.assertRegex(read_note(path)["shipped_at"], ISO_Z)

    def test_claim_error_names_the_features_folder(self) -> None:
        code, payload = self.board("--claim", "nothing")
        self.assertEqual(code, 1)
        self.assertIn(str(self.notes / "Features"), payload["error"])

    def test_evicted_feature_spec_skips_the_run(self) -> None:
        self.spec("demo-work", "Demo Work")
        put(self.notes / "Features" / "Demo Work" / ".Spec.md.icloud", "")
        payload = self.ok()
        self.assertTrue(self.skipped(payload, "placeholder"))
        self.assertEqual(self.own_notes(), {})

    def test_evicted_old_spec_skips_while_specs_exists(self) -> None:
        (self.notes / "specs").mkdir()
        put(self.notes / "specs" / ".20261001 Old.md.icloud", "")
        self.assertTrue(self.skipped(self.ok(), "placeholder"))

    def test_server_lists_both_names_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a", Path(tmp) / "b"
            put(a / "Dev Loop Board" / "Features" / "a.md", note_text(status_note(feature="a")))
            put(b / "dev-loop-board" / "features" / "b.md", note_text(status_note(feature="b")))
            features, _ = server.Index([a, b]).status()
            self.assertEqual(sorted(f["feature"] for f in features), ["a", "b"])

    def test_server_dedup_same_inode(self) -> None:
        """A case-folding disk answers `features` with the folder `Features`: each note is listed once."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            put(root / "Dev Loop Board" / "Features" / "a.md", note_text(status_note(feature="a")))
            real = {name: getattr(os, name) for name in ("stat", "listdir", "open")}

            def fold(name: str):
                def call(path, *args, **kwargs):
                    return real[name](os.fspath(path).replace("/features", "/Features"), *args, **kwargs)
                return call

            with mock.patch.multiple(os, **{name: fold(name) for name in real}):
                features, _ = server.Index([root]).status()
            self.assertEqual([f["feature"] for f in features], ["a"])


class VaultCheckNamesTests(unittest.TestCase):
    def test_vault_check_new_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            notes = vault / "Project"
            (vault / ".obsidian").mkdir(parents=True)
            notes.mkdir(parents=True)
            repo = Path(tmp) / "repo"
            put(repo / "dev-loop.json", "{}")
            put(repo / "dev-loop.local.json", '{"notes": "%s"}' % notes)
            subprocess.run(["git", "init", "-q", str(vault)], check=True)
            put(vault / ".gitignore", "security-audit/\n")
            lines = check_setup.check_board_vault(Path(tmp) / "home", repo, {})
            self.assertIn("add Dev Loop Board/ to the vault .gitignore, unless a partner receives the notes by git", lines)
            self.assertIn("add Security Audit/ to the vault .gitignore", lines)


if __name__ == "__main__":
    unittest.main()
