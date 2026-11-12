#!/usr/bin/env python3
"""Held-out oracle tests for the source dirs wall (spec: Source Dirs Wall), hook flows F1 to F7.

Seams and fixture: `source_dirs_wall_support.py`. F6 also runs `merge-check` on the `Project` fixture of
`merge_check_binding_support.py` (a bare `origin`, a Track S feature worktree whose one commit is reviewed).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from merge_check_binding_support import APP_FEATURE, Project
from merge_check_binding_support import config_text as merge_config
from merge_check_binding_support import reasons
from source_dirs_wall_support import (CLAUDE_HOOKS, CODEX_HOOKS, DEFAULT_DIRS, DENY_PATH, REPO_NAME, SEAL,
                                      SourceWallRepo, bash, claude_only, codex_only, read, write)


class GoRepoTests(SourceWallRepo):
    def test_f1_a_go_repo_walls_internal_and_cmd_in_both_harnesses(self) -> None:
        """F1, O1: every path form the matcher resolves is walled, the main checkout from a worktree included; the
        four default folders are open."""
        self.main_key(["internal/", "cmd/"])
        top = self.repo
        self.expect("O1", (
            read("internal/x.go"),
            read("cmd/app/main.go"),
            read(f"{top}/internal/x.go"),
            bash("cd internal && cat x.go"),
            bash("git show HEAD:internal/x.go"),
            bash(f"cat $HOME/{REPO_NAME}/internal/x.go"),
            bash(f"cat ~/{REPO_NAME}/internal/x.go"),
            bash("cat internal/x.go > tests/acceptance/x"),
            bash("grep -rn func internal"),
            claude_only("Grep", {"pattern": "func", "path": "internal"}),
        ), walled=True)
        self.expect("O1", (read(f"{top}/internal/x.go"),), walled=True, cwd=self.worktree())
        self.expect("O1", (read("src/app.py"), read("pkg/src/y.py"), read("docs/guide.md")), walled=False)


class QuotedArgumentTests(SourceWallRepo):
    def test_f2_a_path_in_a_quoted_argument_does_not_pass(self) -> None:
        """F2, O2: a path inside a quoted argument, behind a variable, a wildcard or a command substitution."""
        self.main_key(["internal/"])
        self.expect("O2", (
            bash('bash -c "cat internal/x.go"'),
            bash("python3 -c \"print(open('internal/x.go').read())\""),
            bash('echo "$(cat internal/x.go)"'),
            bash('cat "$ROOT"/internal/x.go'),
            bash("cat */internal/x.go"),
            bash('cat "$(git rev-parse --show-toplevel)/internal/x.go"'),
        ), walled=True)
        self.expect("O2", (bash('echo "$ROOT"/docs/guide.md'),), walled=False)


class CommonNameTests(SourceWallRepo):
    def test_f3_common_names_wall_only_their_root_folder(self) -> None:
        """F3, O3: `api/`, `tests/`, `scripts/` wall their root folder only; the suite, the seal command and a
        heredoc body stay reachable; a symlink, a command substitution and a second line do not pass."""
        self.main_key(["api/", "tests/", "scripts/"])
        heredoc = 'cat > tests/acceptance/test_h.py <<\'EOF\'\nPATH = "api/spec.go"\nEOF'
        self.expect("O3", (
            write("tests/acceptance/api/test_gizmo.py"),
            bash("git add tests/acceptance/api/test_gizmo.py"),
            bash("python3 -m pytest --tb=no tests/acceptance/api/test_gizmo.py"),
            bash("python3 -m pytest --tb=no tests/acceptance"),
            claude_only("Grep", {"pattern": "api", "path": "tests/acceptance"}),
            bash(f'{SEAL} --message "cover api/ flags"'),
            bash(heredoc),
        ), walled=False)
        self.expect("O3", (
            read("api/spec.go"),
            read("tests/conftest.py"),
            read("scripts/run.sh"),
            bash(f'{SEAL} --message "$(cat api/spec.go)"'),
            bash(f'{SEAL} --message "cover api/ flags"\ncat api/spec.go'),
        ), walled=True)
        os.symlink("../..", self.repo / "tests" / "acceptance" / "up")
        self.expect("O3", (read("tests/acceptance/up/api/spec.go"),), walled=True)


class WorktreeTests(SourceWallRepo):
    def test_f4_a_worktree_inside_a_walled_folder_stays_readable(self) -> None:
        """F4, O4: the deepest checkout top decides; a walled folder that is a symlink is walled."""
        self.main_key([".claude/"])
        wt = self.worktree()
        (self.repo / ".claude" / "settings.json").write_text("{}\n")
        self.expect("O4", (read(f"{wt}/docs/guide.md"),), walled=False, cwd=wt)
        self.expect("O4", (read(f"{self.repo}/.claude/settings.json"),), walled=True, cwd=wt)
        self.main_key(["lnk/"])
        os.symlink("pkg", self.repo / "lnk")
        self.expect("O4", (read("lnk/src/y.py"),), walled=True)


class NoKeyTests(SourceWallRepo):
    def test_f5_a_repo_with_no_key_keeps_todays_wall(self) -> None:
        """F5, O5, O8 (no key), O9: the four-folder match, with the kit CLI beside the hooks and without it; the
        explicit-argument mode of `deny-path.sh`."""
        walled = tuple(read(path) for path in ("src/app.py", "app/x.py", "backend/x.py", "frontend/x.ts",
                                               "pkg/src/y.py"))
        self.expect("O5", walled, walled=True)
        self.expect("O5", (read("internal/x.go"),), walled=False)

        bare = self.root / "bare"
        folders = {"claude": bare / "claude" / "hooks", "codex": bare / "codex" / "hooks"}
        shutil.copytree(CLAUDE_HOOKS, folders["claude"])
        shutil.copytree(CODEX_HOOKS, folders["codex"])
        self.expect("O8", (read("docs/guide.md"),), walled=False, folders=folders)
        self.expect("O8", (read("src/app.py"),), walled=True, folders=folders)

        with self.subTest(oracle="O9"):
            result = self.run_hook("claude", DENY_PATH, self.payload("Read", {"file_path": "pkg/src/y.py"},
                                                                     "spec-tester", self.repo),
                                   *DEFAULT_DIRS, cwd=self.repo)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


class BranchTests(SourceWallRepo):
    def test_f6_a_branch_cannot_loosen_the_wall(self) -> None:
        """F6, O6, O14: the hooks read the main ref only; `merge-check` flags a `source_dirs` change and an invalid
        main ref value."""
        self.branch_key(["internal/"])
        self.expect("O6", (read("internal/x.go"),), walled=False)
        self.expect("O6", (read("src/app.py"),), walled=True)
        self.main_key(["internal/"])
        self.branch_key()
        self.expect("O6", (read("internal/x.go"),), walled=True)

        for oracle, main_config, branch_files, reason in (
                ("O14", merge_config(source_dirs=["internal/", "cmd/"]),
                 {"dev-loop.json": merge_config(source_dirs=["internal/"]), "src/app.py": APP_FEATURE},
                 "config-changed"),
                ("O14", merge_config(source_dirs="internal/"), {"src/app.py": APP_FEATURE}, "config-unreadable")):
            with self.subTest(oracle=oracle, reason=reason):
                tmp = tempfile.TemporaryDirectory()
                self.addCleanup(tmp.cleanup)
                project = Project(Path(tmp.name).resolve(), main_config)
                wt = project.worktree(f"feat/{reason}")
                sha = project.commit(wt, branch_files, "Build the feature")
                project.review(wt, sha, "S")
                code, out = project.merge_check(wt)
                self.assertEqual(code, 0, out)
                self.assertTrue(reasons(out, reason), out)


class BrokenConfigTests(SourceWallRepo):
    def test_f7_a_broken_config_or_no_kit_cli_denies_and_spec_tester_can_report(self) -> None:
        """F7, O7, O8 (key): an invalid main ref value denies each spec-tester call that names a path or a command;
        other tools, other agents and the main session pass; a key with no kit CLI denies."""
        self.main_key("internal/")
        self.expect("O7", (read("docs/guide.md"),), walled=True, names="source_dirs")
        self.expect("O7", (write("tests/acceptance/test_x.py"),), walled=True)
        self.expect("O7", (claude_only("SubagentHandback", {"message": "Sealed the gizmo tests."}),
                           claude_only("ToolSearch", {"query": "select:Monitor", "max_results": 1}),
                           codex_only("update_plan", {"plan": [{"step": "Write tests", "status": "completed"}]})),
                    walled=False)
        for agent in ("implementer", None):
            self.expect("O7", (read("docs/guide.md"),), walled=False, claude_agent=agent, codex_agent=agent,
                        deny_path=False)
        self.main_key(raw="[]\n")
        self.expect("O7", (read("docs/guide.md"),), walled=True)

        bare = self.root / "bare"
        folders = {"claude": bare / "claude" / "hooks", "codex": bare / "codex" / "hooks"}
        shutil.copytree(CLAUDE_HOOKS, folders["claude"])
        shutil.copytree(CODEX_HOOKS, folders["codex"])
        self.main_key(["internal/"])
        self.expect("O8", (read("docs/guide.md"),), walled=True, names="source_dirs", folders=folders)


if __name__ == "__main__":
    unittest.main()
