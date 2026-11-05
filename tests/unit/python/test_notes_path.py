"""notes_path.py and the dev-loop project marker test."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import NOTES_PATH, check_setup, git_in, make_git_repo, notes_path, write


class UsesDevLoopTests(unittest.TestCase):
    def uses(self, files: dict[str, str]) -> bool:
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in files.items():
                write(Path(tmp) / name, text)
            return check_setup.uses_dev_loop(Path(tmp))

    def test_dev_loop_json_counts(self) -> None:
        self.assertTrue(self.uses({"dev-loop.json": "{}"}))

    def test_vault_notes_in_agents_md_does_not_count(self) -> None:
        self.assertFalse(self.uses({"AGENTS.md": "vault_notes: Software/Test\n"}))

    def test_neither_does_not_count(self) -> None:
        self.assertFalse(self.uses({"AGENTS.md": "# Plain\n"}))
        self.assertFalse(self.uses({}))


class NotesPathTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.repo = make_git_repo(self.root, {"README.md": "x\n"})
        self.env: dict[str, str] = {"HOME": str(self.home)}

    def folder(self, name: str) -> Path:
        path = self.root / "notes" / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def linked(self) -> Path:
        path = self.root / "linked"
        git_in(self.repo, "worktree", "add", "-q", "-b", "side", str(path))
        return path

    def commit_shared(self, repo: Path, target: Path | str) -> None:
        write(repo / "dev-loop.json", json.dumps({"notes": str(target)}))
        git_in(repo, "add", "dev-loop.json")
        git_in(repo, "commit", "-q", "-m", "shared notes")

    def resolve(self, repo: Path | None = None) -> Path:
        return notes_path.resolve(repo or self.repo, self.env, self.home)

    def set_local(self, repo: Path, target: Path | str) -> None:
        write(repo / "dev-loop.local.json", json.dumps({"notes": str(target)}))

    def test_checked_out_branch_dev_loop_json_is_not_read(self) -> None:
        self.commit_shared(self.repo, self.folder("trusted"))
        git_in(self.repo, "checkout", "-q", "-b", "side-work")
        self.commit_shared(self.repo, self.folder("branch"))
        self.assertEqual(self.resolve(), self.folder("trusted"))

    def test_no_main_ref_or_no_file_on_it_is_no_notes_path(self) -> None:
        git_in(self.repo, "branch", "-m", "other")
        self.commit_shared(self.repo, self.folder("branch"))
        with self.assertRaises(notes_path.NotesPathError) as ctx:
            self.resolve()
        self.assertTrue(str(ctx.exception).startswith(f"no notes path for {self.repo}."))
        git_in(self.repo, "branch", "-m", "main")
        git_in(self.repo, "rm", "-q", "dev-loop.json")
        git_in(self.repo, "commit", "-q", "-m", "drop")
        write(self.repo / "dev-loop.json", json.dumps({"notes": str(self.folder("branch"))}))
        with self.assertRaises(notes_path.NotesPathError):
            self.resolve()

    def test_sources_in_order(self) -> None:
        linked = self.linked()
        self.set_local(linked, self.folder("tree"))
        write(linked / "dev-loop.json", json.dumps({"notes": str(self.folder("branch"))}))
        steps = [
            (lambda: self.commit_shared(self.repo, self.folder("shared")), self.folder("shared")),
            (lambda: self.set_local(self.repo, self.folder("main")), self.folder("main")),
            (lambda: self.env.update(DEV_LOOP_NOTES=str(self.folder("env"))), self.folder("env")),
        ]
        for apply, expected in steps:
            apply()
            self.assertEqual(self.resolve(linked), expected)

    def test_linked_worktree_resolves_from_main_worktree(self) -> None:
        linked = self.linked()
        self.set_local(self.repo, self.folder("main"))
        self.assertEqual(self.resolve(linked), self.folder("main"))

    def test_linked_worktree_files_are_never_read(self) -> None:
        linked = self.linked()
        self.set_local(linked, self.folder("tree"))
        result = self.run_main(None, repo=linked)
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(self.repo / "dev-loop.local.json"), result.stderr)
        (self.repo / "n").mkdir()
        (linked / "x").mkdir()
        self.commit_shared(self.repo, "n")
        write(linked / "dev-loop.json", json.dumps({"notes": "x"}))
        self.assertEqual(self.resolve(linked), self.repo / "n")

    def test_tilde_expands_against_home(self) -> None:
        (self.home / "x").mkdir()
        self.set_local(self.repo, "~/x")
        self.assertEqual(self.resolve(), self.home / "x")

    def test_relative_path_is_relative_to_worktree_holding_the_file(self) -> None:
        linked = self.linked()
        (self.repo / "n").mkdir()
        (linked / "n").mkdir()
        write(self.repo / "dev-loop.local.json", json.dumps({"notes": "n"}))
        self.assertEqual(self.resolve(linked), self.repo / "n")
        self.commit_shared(self.repo, "n")
        (self.repo / "dev-loop.local.json").unlink()
        self.assertEqual(self.resolve(linked), self.repo / "n")
        self.env["DEV_LOOP_NOTES"] = "n"
        self.assertEqual(self.resolve(linked), linked / "n")

    def test_empty_env_value_is_skipped(self) -> None:
        self.env["DEV_LOOP_NOTES"] = ""
        self.set_local(self.repo, self.folder("main"))
        self.assertEqual(self.resolve(), self.folder("main"))

    def test_no_value_raises_with_next_action(self) -> None:
        with self.assertRaises(notes_path.NotesPathError) as ctx:
            self.resolve()
        self.assertEqual(
            str(ctx.exception),
            f'no notes path for {self.repo}. Write {{"notes": "<path>"}} to {self.repo}/dev-loop.local.json',
        )

    def test_missing_folder_raises_naming_the_source(self) -> None:
        self.env["DEV_LOOP_NOTES"] = str(self.root / "gone")
        with self.assertRaises(notes_path.NotesPathError) as ctx:
            self.resolve()
        self.assertEqual(
            str(ctx.exception),
            f"notes folder does not exist: {self.root / 'gone'} (from DEV_LOOP_NOTES). Create it or change DEV_LOOP_NOTES",
        )
        del self.env["DEV_LOOP_NOTES"]
        self.set_local(self.repo, self.root / "gone")
        with self.assertRaises(notes_path.NotesPathError) as ctx:
            self.resolve()
        self.assertEqual(
            str(ctx.exception),
            f"notes folder does not exist: {self.root / 'gone'} (from {self.repo / 'dev-loop.local.json'})."
            f" Create it or change the path in {self.repo / 'dev-loop.local.json'}",
        )

    def test_folder_that_is_not_a_git_repository(self) -> None:
        plain = self.root / "plain"
        plain.mkdir()
        self.env["DEV_LOOP_NOTES"] = str(self.folder("env"))
        self.assertEqual(self.resolve(plain), self.folder("env"))
        del self.env["DEV_LOOP_NOTES"]
        write(plain / "dev-loop.local.json", json.dumps({"notes": "rel"}))
        (plain / "rel").mkdir()
        self.assertEqual(self.resolve(plain), plain / "rel")

    def test_malformed_lower_priority_file_is_not_read(self) -> None:
        linked = self.linked()
        write(self.repo / "dev-loop.local.json", "{not json")
        self.env["DEV_LOOP_NOTES"] = str(self.folder("env"))
        self.assertEqual(self.resolve(linked), self.folder("env"))

    def test_shared_file_without_notes_falls_through(self) -> None:
        write(self.repo / "dev-loop.json", json.dumps({"other": 1}))
        git_in(self.repo, "add", "dev-loop.json")
        git_in(self.repo, "commit", "-q", "-m", "shared")
        with self.assertRaises(notes_path.NotesPathError) as ctx:
            self.resolve()
        self.assertTrue(str(ctx.exception).startswith(f"no notes path for {self.repo}."))

    def test_local_file_without_notes_falls_through_to_main_ref(self) -> None:
        self.commit_shared(self.repo, "n")
        (self.repo / "n").mkdir()
        for text in (json.dumps({"second_review": {"codex": {}}}), json.dumps({"notes": ""}), "{}"):
            with self.subTest(text):
                write(self.repo / "dev-loop.local.json", text)
                self.assertEqual(self.resolve(), self.repo / "n")
                result = self.run_main(None)
                self.assertEqual((result.returncode, result.stdout), (0, f"{self.repo / 'n'}\n"), result.stderr)

    def test_no_git_on_path_is_no_main_ref(self) -> None:
        with mock.patch.object(notes_path.subprocess, "run", side_effect=FileNotFoundError("git")):
            with self.assertRaises(notes_path.NotesPathError) as ctx:
                self.resolve()
        self.assertTrue(str(ctx.exception).startswith(f"no notes path for {self.repo}."))

    def test_missing_folder_of_main_ref_source_names_what_the_user_can_edit(self) -> None:
        self.commit_shared(self.repo, self.root / "gone")
        with self.assertRaises(notes_path.NotesPathError) as ctx:
            self.resolve()
        self.assertEqual(
            str(ctx.exception),
            f"notes folder does not exist: {self.root / 'gone'} (from refs/heads/main:dev-loop.json)."
            f" Change notes in dev-loop.json on main, or write {self.repo / 'dev-loop.local.json'}",
        )

    def test_origin_main_wins_over_local_main(self) -> None:
        self.commit_shared(self.repo, self.folder("origin"))
        git_in(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.commit_shared(self.repo, self.folder("local"))
        self.assertEqual(self.resolve(), self.folder("origin"))

    def test_invalid_json_at_main_ref_raises(self) -> None:
        write(self.repo / "dev-loop.json", "{not json")
        git_in(self.repo, "add", "dev-loop.json")
        git_in(self.repo, "commit", "-q", "-m", "bad")
        with self.assertRaises(notes_path.NotesPathError) as ctx:
            self.resolve()
        self.assertTrue(str(ctx.exception).startswith("invalid refs/heads/main:dev-loop.json: "))

    def test_notes_path_has_no_vault_fallback(self) -> None:
        write(self.home / ".agents" / "dev-loop.config.json", json.dumps({"vault": str(self.folder("vault"))}))
        write(self.repo / "AGENTS.md", "vault_notes: fb\n")
        (self.folder("vault") / "fb").mkdir()
        with self.assertRaises(notes_path.NotesPathError) as ctx:
            self.resolve()
        self.assertTrue(str(ctx.exception).startswith(f"no notes path for {self.repo}."))

    def run_main(self, env_notes: str | None, repo: Path | None = None) -> subprocess.CompletedProcess:
        env = {**os.environ, "HOME": str(self.home)}
        env.pop("DEV_LOOP_NOTES", None)
        if env_notes is not None:
            env["DEV_LOOP_NOTES"] = env_notes
        return subprocess.run(
            [sys.executable, str(NOTES_PATH), "--repo", str(repo or self.repo)],
            capture_output=True,
            text=True,
            env=env,
        )

    def test_main_prints_path_and_exits_zero(self) -> None:
        result = self.run_main(str(self.folder("env")))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, f"{self.folder('env')}\n", ""))

    def test_main_prints_message_to_stderr_and_exits_one(self) -> None:
        result = self.run_main(None)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith(f"no notes path for {self.repo}."))

    def test_main_local_file_errors(self) -> None:
        target = self.repo / "dev-loop.local.json"
        for row in ("tracked", "tracked with env", "not JSON", "not an object"):
            with self.subTest(row):
                git_in(self.repo, "rm", "-q", "--cached", "--ignore-unmatch", "dev-loop.local.json")
                if row.startswith("tracked"):
                    self.set_local(self.repo, self.folder("main"))
                    git_in(self.repo, "add", "-f", "dev-loop.local.json")
                    git_in(self.repo, "commit", "-q", "--allow-empty", "-m", "local")
                else:
                    write(target, "{not json" if row == "not JSON" else "[]")
                result = self.run_main(str(self.folder("env")) if row == "tracked with env" else None)
                if row == "tracked with env":
                    self.assertEqual((result.returncode, result.stdout), (0, f"{self.folder('env')}\n"))
                    continue
                self.assertEqual(result.returncode, 1)
                if row == "tracked":
                    for needle in ("dev-loop.local.json", "tracked", "git rm --cached"):
                        self.assertIn(needle, result.stderr)
                else:
                    self.assertTrue(result.stderr.startswith(f"invalid {target}: "), result.stderr)
