"""check_setup.py: the board vault check and the security audit check."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import CHECKER, check_setup, checker_env, write


class BoardVaultCheckTests(unittest.TestCase):
    GITIGNORE_LINE = "add dev-loop-board/ to the vault .gitignore, unless a partner receives the notes by git"

    def setup_vault(self, tmp: str, dataview: bool, js: bool, git: bool, ignore: bool) -> tuple[Path, Path, Path]:
        home = Path(tmp) / "home"
        repo = Path(tmp) / "repo"
        vault = Path(tmp) / "vault"
        notes = vault / "Project"
        notes.mkdir(parents=True)
        write(repo / "dev-loop.json", "{}")
        write(repo / "dev-loop.local.json", json.dumps({"notes": str(notes)}))
        (vault / ".obsidian").mkdir()
        if dataview:
            write(vault / ".obsidian" / "community-plugins.json", json.dumps(["dataview"]))
            write(vault / ".obsidian" / "plugins" / "dataview" / "data.json", json.dumps({"enableDataviewJs": js}))
        if git:
            subprocess.run(["git", "init", "-q", str(vault)], check=True)
            if ignore:
                write(vault / ".gitignore", "dev-loop-board/\nsecurity-audit/\n")
        return home, repo, vault

    def actions(self, **kw: bool) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            home, repo, _ = self.setup_vault(tmp, **kw)
            return check_setup.check_board_vault(home, repo, {})

    def test_missing_dataview_plugin_names_dataview_and_javascript_queries(self) -> None:
        lines = self.actions(dataview=False, js=False, git=False, ignore=False)
        self.assertEqual(len(lines), 1)
        self.assertIn("Dataview", lines[0])
        self.assertIn("JavaScript queries", lines[0])

    def test_dataview_without_javascript_queries_is_reported(self) -> None:
        lines = self.actions(dataview=True, js=False, git=False, ignore=False)
        self.assertEqual(len(lines), 1)
        self.assertIn("JavaScript queries", lines[0])

    def test_vault_git_without_the_board_ignore_is_reported(self) -> None:
        lines = self.actions(dataview=True, js=True, git=True, ignore=False)
        self.assertEqual(lines, [self.GITIGNORE_LINE, "add security-audit/ to the vault .gitignore"])

    def test_ready_vault_has_no_lines(self) -> None:
        self.assertEqual(self.actions(dataview=True, js=True, git=True, ignore=True), [])

    def test_opt_outs_in_dev_loop_local_json_skip_their_line(self) -> None:
        """B1: board_dataview false skips the Dataview line, board_vault_git shared skips the .gitignore line."""
        both = {"board_dataview": False, "board_vault_git": "shared"}
        rows = [({}, 3), ({"board_dataview": False}, 2), ({"board_vault_git": "shared"}, 1), (both, 0)]
        for opts, count in rows:
            with self.subTest(opts=opts), tempfile.TemporaryDirectory() as tmp:
                home, repo, _ = self.setup_vault(tmp, dataview=False, js=False, git=True, ignore=False)
                notes = json.loads((repo / "dev-loop.local.json").read_text())
                write(repo / "dev-loop.local.json", json.dumps({**notes, **opts}))
                self.assertEqual(len(check_setup.check_board_vault(home, repo, {})), count)

    def test_missing_git_or_a_fake_git_dir_skips_the_gitignore_line(self) -> None:
        """B2: no git on PATH, or a .git that is not a repository (exit 128): no line, no exception."""
        with tempfile.TemporaryDirectory() as tmp:
            home, repo, vault = self.setup_vault(tmp, dataview=True, js=True, git=False, ignore=False)
            (vault / ".git").mkdir()
            self.assertEqual(check_setup.check_board_vault(home, repo, {}), [])
            real = subprocess.run(["git", "init", "-q", str(vault)], check=True)
            self.assertEqual(real.returncode, 0)
            with mock.patch.dict(os.environ, {"PATH": str(Path(tmp) / "empty")}):
                self.assertEqual(check_setup.check_board_vault(home, repo, {}), [])

    def test_unresolved_notes_path_has_no_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            write(repo / "dev-loop.json", "{}")
            self.assertEqual(check_setup.check_board_vault(Path(tmp) / "home", repo, {}), [])

    def test_board_note_at_the_kit_version_with_another_body_is_reported(self) -> None:
        """D11, D24: a board note at the kit version whose body is not the kit template: one line to delete it."""
        from board import writer

        line = "Dev Loop Board.md differs from the dev-loop template: delete it, the next board run writes it again"
        kit = writer.board_text("2.1.0")
        planted = kit + "\n```dataviewjs\ndv.el('p', 'planted')\n```\n"
        rows = [(kit, []), (planted, [line]), (planted.replace('"2.1.0"', '"2.0.9"'), []),
                (planted.replace('"2.1.0"', '"99.0.0"'), [])]
        for text, expected in rows:
            with self.subTest(text=text[:30]), tempfile.TemporaryDirectory() as tmp:
                home, repo, vault = self.setup_vault(tmp, dataview=True, js=True, git=False, ignore=False)
                write(repo / ".dev-loop" / "MANIFEST.json", json.dumps({"version": "2.1.0", "files": {}}))
                write(vault / "Project" / "dev-loop-board" / "Dev Loop Board.md", text)
                self.assertEqual(check_setup.check_board_vault(home, repo, {}), expected)

    def test_lines_print_under_user_actions_not_as_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, repo, _ = self.setup_vault(tmp, dataview=False, js=False, git=True, ignore=False)
            result = subprocess.run(
                [sys.executable, str(CHECKER), "--harness", "claude", "--home", str(home), "--repo", str(repo)],
                capture_output=True, text=True, check=False, env=checker_env(Path(tmp) / "bin"))
            head, _, actions = result.stdout.partition("User actions required")
            self.assertIn("Dataview", actions)
            self.assertIn(self.GITIGNORE_LINE, actions)
            self.assertNotIn("Dataview", head)
            self.assertNotIn(self.GITIGNORE_LINE, head)


class SecurityAuditCheckTests(unittest.TestCase):
    COMMIT = "c1c8a8c1471069fb0e188eeaff69b8e8db6564a8"
    INSTALL = (f"npx skills add https://github.com/cloudflare/security-audit-skill/tree/{COMMIT} "
               "--skill security-audit --global --agent claude-code codex")

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_skill_names_the_commit(self) -> None:
        heading = f"Install the Cloudflare security-audit skill at {self.COMMIT}: {self.INSTALL}"
        for harness, root in (("claude", ".claude"), ("codex", ".agents")):
            skill = self.home / root / "skills" / "security-audit" / "SKILL.md"
            self.assertEqual(
                check_setup.security_audit_groups(self.home, harness, {}), [(heading, [f"missing {skill}"])]
            )

    def test_wrong_tree_names_the_tree(self) -> None:
        folder = self.home / ".agents" / "skills" / "security-audit"
        write(folder / "SKILL.md", "x\n")
        (group,) = check_setup.security_audit_groups(self.home, "codex", {})
        self.assertEqual(group[1], [f"{folder} is tree {check_setup.tree_id(folder)}"])

    def test_expected_tree_override_passes(self) -> None:
        folder = self.home / ".agents" / "skills" / "security-audit"
        write(folder / "SKILL.md", "x\n")
        env = {"DEVLOOP_TEST_SECURITY_AUDIT_TREE": check_setup.tree_id(folder)}
        self.assertEqual(check_setup.security_audit_groups(self.home, "codex", env), [])

    def test_security_audit_through_a_link_passes(self) -> None:
        folder = self.home / ".agents" / "skills" / "security-audit"
        write(folder / "SKILL.md", "x\n")
        link = self.home / ".claude" / "skills" / "security-audit"
        link.parent.mkdir(parents=True)
        link.symlink_to(folder)
        env = {"DEVLOOP_TEST_SECURITY_AUDIT_TREE": check_setup.tree_id(folder)}
        self.assertEqual(check_setup.security_audit_groups(self.home, "claude", env), [])

    def reference_tree(self, scratch: Path) -> str:
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
        git = ["git", "-C", str(scratch)]
        subprocess.run([*git, "init", "-q"], check=True, env=env)
        subprocess.run([*git, "add", "-A"], check=True, env=env)
        return subprocess.run([*git, "write-tree"], text=True, capture_output=True, check=True, env=env).stdout.strip()

    def make_files(self, *roots: Path) -> None:
        for root in roots:
            for name, text in {"SKILL.md": "a\r\n", "sub/deep/b.txt": "b\n", "run.sh": "#!/bin/sh\n"}.items():
                write(root / name, text)
            (root / "run.sh").chmod(0o755)

    def test_tree_id_matches_git(self) -> None:
        folder = self.home / "skill"
        scratch = Path(self._tmp.name) / "scratch"
        self.make_files(folder, scratch)
        self.assertEqual(check_setup.tree_id(folder), self.reference_tree(scratch))

    def test_tree_id_ignores_a_ds_store(self) -> None:
        folder = self.home / "skill"
        self.make_files(folder)
        before = check_setup.tree_id(folder)
        write(folder / ".DS_Store", "junk\n")
        write(folder / "sub" / ".DS_Store", "junk\n")
        self.assertEqual(check_setup.tree_id(folder), before)

    def test_tree_id_ignores_inherited_git_env(self) -> None:
        folder = self.home / "skill"
        scratch = Path(self._tmp.name) / "scratch"
        self.make_files(folder, scratch)
        stray = {"GIT_DIR": str(self.home / "nope"), "GIT_INDEX_FILE": str(self.home / "idx"),
                 "GIT_WORK_TREE": str(self.home), "GIT_OBJECT_DIRECTORY": str(self.home / "obj")}
        expected = self.reference_tree(scratch)
        with unittest.mock.patch.dict(os.environ, stray):
            self.assertEqual(check_setup.tree_id(folder), expected)

    def test_tree_id_ignores_the_user_git_config(self) -> None:
        folder = self.home / "skill"
        scratch = Path(self._tmp.name) / "scratch"
        self.make_files(folder, scratch)
        write(self.home / "attrs", "*.md text eol=crlf\n")
        write(self.home / ".gitconfig", f"[core]\n\tattributesFile = {self.home / 'attrs'}\n")
        env = {k: v for k, v in os.environ.items() if k != "GIT_CONFIG_GLOBAL"}
        with unittest.mock.patch.dict(os.environ, {**env, "HOME": str(self.home)}, clear=True):
            self.assertEqual(check_setup.tree_id(folder), self.reference_tree(scratch))
