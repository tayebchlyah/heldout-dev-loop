#!/usr/bin/env python3
"""Regression and setup tests for the shared Claude/Codex dev-loop skill."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILL = SKILL_ROOT / "SKILL.md"
CHECKER = SKILL_ROOT / "scripts" / "check_setup.py"
CODEX_ASSETS = SKILL_ROOT.parent / "codex"
CLAUDE_HOOKS = SKILL_ROOT.parent / "claude" / "hooks"
SELECTOR = SKILL_ROOT / "scripts" / "select_mutants.py"
NOTES_PATH = SKILL_ROOT / "scripts" / "notes_path.py"
GUARD_ASKS = SKILL_ROOT.parent / "test" / "fixtures" / "git-guard-asks.json"

_spec = importlib.util.spec_from_file_location("check_setup", CHECKER)
if _spec is None or _spec.loader is None:
    # spec_from_file_location does not itself validate that CHECKER exists,
    # so this guard is here only to satisfy the type checker (_spec.loader
    # is Optional[Loader]); a missing CHECKER surfaces below instead, as
    # FileNotFoundError, once exec_module actually tries to read it.
    raise ImportError(f"cannot load check_setup module: {CHECKER}")
check_setup = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(check_setup)
except FileNotFoundError as exc:
    raise FileNotFoundError(f"cannot load check_setup module: missing {CHECKER}") from exc


_selector_spec = importlib.util.spec_from_file_location("select_mutants", SELECTOR)
if _selector_spec is None or _selector_spec.loader is None:
    raise ImportError(f"cannot load select_mutants module: {SELECTOR}")
select_mutants = importlib.util.module_from_spec(_selector_spec)
sys.modules["select_mutants"] = select_mutants
_selector_spec.loader.exec_module(select_mutants)

_notes_spec = importlib.util.spec_from_file_location("notes_path", NOTES_PATH)
if _notes_spec is None or _notes_spec.loader is None:
    raise ImportError(f"cannot load notes_path module: {NOTES_PATH}")
notes_path = importlib.util.module_from_spec(_notes_spec)
sys.modules["notes_path"] = notes_path
_notes_spec.loader.exec_module(notes_path)

# The devloop CLI tests live in test_devloop.py; importing the classes here
# keeps `python3 skill/scripts/test_dev_loop.py` the one command for the suite.
_devloop_tests_spec = importlib.util.spec_from_file_location("test_devloop", SKILL_ROOT / "scripts" / "test_devloop.py")
if _devloop_tests_spec is None or _devloop_tests_spec.loader is None:
    raise ImportError("cannot load test_devloop module")
test_devloop = importlib.util.module_from_spec(_devloop_tests_spec)
_devloop_tests_spec.loader.exec_module(test_devloop)
StatusTests = test_devloop.StatusTests
LoggedTests = test_devloop.LoggedTests
GateTests = test_devloop.GateTests
CiWaitTests = test_devloop.CiWaitTests
InstructionContractTests = test_devloop.InstructionContractTests
SkillStackContractTests = test_devloop.SkillStackContractTests
ReviewWaveContractTests = test_devloop.ReviewWaveContractTests
TracksContractTests = test_devloop.TracksContractTests
SpecGapContractTests = test_devloop.SpecGapContractTests

# The board oracle tests (spec-tester, held out) live in test_board.py.
_board_tests_spec = importlib.util.spec_from_file_location("test_board", SKILL_ROOT / "scripts" / "test_board.py")
if _board_tests_spec is None or _board_tests_spec.loader is None:
    raise ImportError("cannot load test_board module")
test_board = importlib.util.module_from_spec(_board_tests_spec)
_board_tests_spec.loader.exec_module(test_board)
BoardWriterTests = test_board.BoardWriterTests
BoardEventTests = test_board.BoardEventTests
BoardShipTests = test_board.BoardShipTests
BoardServerTests = test_board.BoardServerTests
BoardHookTests = test_board.BoardHookTests

_board_robust_spec = importlib.util.spec_from_file_location("test_board_robust", SKILL_ROOT / "scripts" / "test_board_robust.py")
if _board_robust_spec is None or _board_robust_spec.loader is None:
    raise ImportError("cannot load test_board_robust module")
test_board_robust = importlib.util.module_from_spec(_board_robust_spec)
_board_robust_spec.loader.exec_module(test_board_robust)
BoardRobustTests = test_board_robust.BoardRobustTests

# The board robustness oracle tests (spec-tester, held out) live in test_board_robustness.py.
_board_robustness_spec = importlib.util.spec_from_file_location("test_board_robustness",
                                                                SKILL_ROOT / "scripts" / "test_board_robustness.py")
if _board_robustness_spec is None or _board_robustness_spec.loader is None:
    raise ImportError("cannot load test_board_robustness module")
test_board_robustness = importlib.util.module_from_spec(_board_robustness_spec)
_board_robustness_spec.loader.exec_module(test_board_robustness)
RobustnessStateTests = test_board_robustness.RobustnessStateTests
RobustnessTimeTests = test_board_robustness.RobustnessTimeTests
RobustnessEventTests = test_board_robustness.RobustnessEventTests
RobustnessKitTests = test_board_robustness.RobustnessKitTests
RobustnessHookTests = test_board_robustness.RobustnessHookTests
RobustnessServerTests = test_board_robustness.RobustnessServerTests
RobustnessFlowTests = test_board_robustness.RobustnessFlowTests


def all_instructions() -> str:
    parts = [SKILL.read_text()]
    refs = SKILL_ROOT / "references"
    if refs.exists():
        parts.extend(path.read_text() for path in sorted(refs.glob("*.md")))
    return "\n".join(parts)


def write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class NotesCheckTests(unittest.TestCase):
    def test_repo_without_a_notes_path_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            repo = Path(tmp) / "repo"
            write(repo / "dev-loop.json", "{}")
            failures = check_setup.check_notes(home, repo, {})
            self.assertEqual(len(failures), 1)
            self.assertTrue(failures[0].startswith(f"missing notes path for {repo}. Write "), failures)
            self.assertIn("dev-loop.local.json", failures[0])
            self.assertNotIn("vault", failures[0])

    def test_notes_folder_that_does_not_exist_fails_with_the_resolver_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            repo = Path(tmp) / "repo"
            write(repo / "dev-loop.json", "{}")
            failures = check_setup.check_notes(home, repo, {"DEV_LOOP_NOTES": str(Path(tmp) / "nope")})
            self.assertEqual(len(failures), 1)
            self.assertIn("notes folder does not exist", failures[0])

    def test_repo_with_a_resolved_notes_path_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            repo = Path(tmp) / "repo"
            notes = Path(tmp) / "notes"
            notes.mkdir()
            write(repo / "dev-loop.json", "{}")
            write(repo / "dev-loop.local.json", json.dumps({"notes": str(notes)}))
            self.assertEqual(check_setup.check_notes(home, repo, {}), [])


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
                write(vault / ".gitignore", "dev-loop-board/\n")
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
        self.assertEqual(lines, [self.GITIGNORE_LINE])

    def test_ready_vault_has_no_lines(self) -> None:
        self.assertEqual(self.actions(dataview=True, js=True, git=True, ignore=True), [])

    def test_opt_outs_in_dev_loop_local_json_skip_their_line(self) -> None:
        """B1: board_dataview false skips the Dataview line, board_vault_git shared skips the .gitignore line."""
        both = {"board_dataview": False, "board_vault_git": "shared"}
        rows = [({}, 2), ({"board_dataview": False}, 1), ({"board_vault_git": "shared"}, 1), (both, 0)]
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
                capture_output=True, text=True, check=False)
            head, _, actions = result.stdout.partition("User actions required")
            self.assertIn("Dataview", actions)
            self.assertIn(self.GITIGNORE_LINE, actions)
            self.assertNotIn("Dataview", head)
            self.assertNotIn(self.GITIGNORE_LINE, head)


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


class ProjectConfigTests(unittest.TestCase):
    def check(self, config: dict) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            write(Path(tmp) / "dev-loop.json", json.dumps(config))
            return check_setup.check_project_config(Path(tmp))

    def test_unknown_commands_key_fails(self) -> None:
        self.assertEqual(
            self.check({"commands": {"gate": "make gate", "deploy": "x"}}),
            ["unknown commands key in dev-loop.json: deploy"],
        )

    def test_empty_command_fails(self) -> None:
        self.assertEqual(
            self.check({"commands": {"gate": "", "stack_up": 3}}),
            [
                "commands.gate in dev-loop.json must be a non-empty string",
                "commands.stack_up in dev-loop.json must be a non-empty string",
            ],
        )

    def test_bad_merge_value_fails(self) -> None:
        self.assertEqual(self.check({"merge": "yes"}), ["merge in dev-loop.json must be auto or human"])

    def test_valid_commands_and_merge_pass(self) -> None:
        commands = {key: f"run {key}" for key in check_setup.COMMAND_KEYS}
        self.assertEqual(self.check({"commands": commands, "merge": "human"}), [])
        self.assertEqual(self.check({"merge": "auto"}), [])
        self.assertEqual(self.check({}), [])

    def test_checker_reports_the_config_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write(Path(tmp) / "dev-loop.json", json.dumps({"merge": "yes"}))
            result = subprocess.run(
                [sys.executable, str(CHECKER), "--harness", "claude", "--home", tmp, "--repo", tmp],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertIn("- merge in dev-loop.json must be auto or human", result.stdout)

    def test_checker_without_dev_loop_json_prints_one_init_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for harness in ["claude", "codex"]:
                result = subprocess.run(
                    [sys.executable, str(CHECKER), "--harness", harness, "--home", tmp, "--repo", tmp],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(
                    (result.returncode, result.stdout),
                    (1, f"{harness}: FAIL\n- no dev-loop.json in {tmp}. Run npx github:tchlyah/dev-loop#<tag> init\n"),
                )


class SkillContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = SKILL.read_text()
        cls.instructions = all_instructions()

    def test_frontmatter_and_trigger_survive(self) -> None:
        self.assertIn("name: dev-loop", self.skill)
        self.assertIn("at the start of any coding session", self.skill)
        self.assertIn("dev-loop.json", self.skill)
        self.assertIn("notes_path.py", self.skill)
        self.assertNotIn("$VAULT", self.skill)

    def test_all_eight_stages_remain_ordered(self) -> None:
        positions = [self.skill.index(f"## {number} ") for number in range(8)]
        self.assertEqual(positions, sorted(positions))

    def test_claude_bootstrap_contract_remains(self) -> None:
        required = [
            ".dev-loop/hooks/claude/gate.sh",
            ".claude/agents/implementer.md",
            ".claude/agents/spec-tester.md",
            ".claude/settings.json",
            "mattpocock-skills:domain-modeling",
            "mattpocock-skills:grilling",
            "mattpocock-skills:prototype",
        ]
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, self.instructions)

    def test_claude_build_and_gate_contract_remains(self) -> None:
        required = [
            "subagent_type: implementer",
            "tests/acceptance/",
            "pytest tests/acceptance -q --tb=no",
            "superpowers:subagent-driven-development",
            "mattpocock-skills:pr",
            "gh pr merge --rebase",
            "Never invoke `superpowers:verification-before-completion`",
        ]
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, self.instructions)

    def test_held_out_authorship_contract_remains(self) -> None:
        self.assertIn("spec-tester", self.instructions)
        self.assertIn("implementer", self.instructions)
        self.assertIn("Plan never references `tests/acceptance/`", self.instructions)
        self.assertNotIn("Tell the user to run `/clear`", self.instructions)

    def test_codex_branch_is_declared(self) -> None:
        self.assertIn("references/codex.md", self.skill)
        self.assertIn(".codex/agents/implementer.toml", self.instructions)
        self.assertIn(".codex/agents/spec-tester.toml", self.instructions)
        self.assertIn(".codex/agents/task-reviewer.toml", self.instructions)

    def test_codex_branch_uses_real_codex_operations(self) -> None:
        codex = (SKILL_ROOT / "references" / "codex.md").read_text()
        self.assertIn("Codex `/review`", codex)
        self.assertIn("browser or computer-use tools", codex)
        self.assertNotIn("`/code-review medium`", codex)
        self.assertNotIn("`/security-review`", codex)
        self.assertNotIn("`/verify`", codex)

    def test_claude_branch_never_calls_user_only_verify(self) -> None:
        claude = (SKILL_ROOT / "references" / "claude.md").read_text()
        self.assertNotIn("then `/verify`", claude)
        self.assertIn("user-invoked only", claude)

    def test_gate_runs_reviews_once_before_acceptance(self) -> None:
        for value in [
            "Review wave runs once per feature, before any acceptance run",
            "Never rerun it",
            "Progress guard",
            "Never ship red",
            "--last-failed",
            "GATE_SHA",
            "`commands.acceptance_failed`",
        ]:
            with self.subTest(value=value):
                self.assertIn(value, self.instructions)

    def test_claude_worktree_reviewers_pin_the_sha(self) -> None:
        claude = (SKILL_ROOT / "references" / "claude.md").read_text()
        self.assertIn("`main...GATE_SHA`", claude)
        self.assertNotIn("isolation: worktree", claude)
        self.assertNotIn("Rerun step 1.", claude)

    def test_the_glossary_is_glossary_md_else_context_md(self) -> None:
        text = SKILL.read_text()
        self.assertIn("Glossary = `GLOSSARY.md` at the repo root, else `CONTEXT.md`", text)
        self.assertIn("Both exist: `GLOSSARY.md` wins.", text)
        self.assertIn("first terms of `GLOSSARY.md`", text)
        self.assertIn("spec path and glossary path", text)
        self.assertIn("or a glossary invariant;", text)
        for agent in (CLAUDE_HOOKS.parent / "agents" / "spec-tester.md", CODEX_ASSETS / "agents" / "spec-tester.toml"):
            self.assertIn("the glossary (GLOSSARY.md, else CONTEXT.md)", agent.read_text(), agent.name)

    def test_setup_checker_exists(self) -> None:
        self.assertTrue(CHECKER.is_file())

    def test_codex_agent_assets_parse_and_preserve_roles(self) -> None:
        expected = {
            "implementer.toml": ("implementer", "dev-implementer"),
            "spec-tester.toml": ("spec_tester", "dev-spec-tester"),
            "task-reviewer.toml": ("task_reviewer", "dev-read-only"),
            "final-reviewer.toml": ("final_reviewer", "dev-read-only"),
            "security-reviewer.toml": ("security_reviewer", "dev-read-only"),
        }
        for filename, (name, permissions) in expected.items():
            with self.subTest(filename=filename):
                parsed = tomllib.loads((CODEX_ASSETS / "agents" / filename).read_text())
                self.assertEqual(parsed["name"], name)
                self.assertEqual(parsed["default_permissions"], permissions)
                self.assertTrue(parsed["description"])
                self.assertTrue(parsed["developer_instructions"])


class DenyWallScopeTests(unittest.TestCase):
    """The deny walls match path fields, never the whole tool input.

    A subagent that writes a document or a configuration file whose BODY names
    the held-out suite is not reading that suite. Blocking such a write is a
    false positive, and it teaches an agent to disguise its input instead of
    reporting the block.
    """

    HELD_OUT = "tests/" + "acceptance"

    def run_claude(self, name: str, payload: dict, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(CLAUDE_HOOKS / name), *args],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

    def run_codex(self, payload: dict, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(CODEX_ASSETS / "hooks" / "deny-path.sh"), *args],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

    def subagent(self, tool_input: dict) -> dict:
        return {"agent_id": "sub-1", "agent_type": "general-purpose", "tool_input": tool_input}

    def test_subagent_read_of_the_held_out_suite_is_blocked(self) -> None:
        result = self.run_claude(
            "deny-acceptance-to-subagents.sh",
            self.subagent({"file_path": f"{self.HELD_OUT}/login_test.py"}),
        )
        self.assertEqual(result.returncode, 2)

    def test_subagent_glob_of_the_held_out_suite_is_blocked(self) -> None:
        result = self.run_claude(
            "deny-acceptance-to-subagents.sh",
            self.subagent({"pattern": f"{self.HELD_OUT}/**", "path": "."}),
        )
        self.assertEqual(result.returncode, 2)

    def test_subagent_shell_command_touching_the_held_out_suite_is_blocked(self) -> None:
        result = self.run_claude(
            "deny-acceptance-to-subagents.sh",
            self.subagent({"command": f"cat {self.HELD_OUT}/login_test.py"}),
        )
        self.assertEqual(result.returncode, 2)

    def test_subagent_grep_glob_of_the_held_out_suite_is_blocked(self) -> None:
        # Grep names a location through `glob`, which is not `path` and not
        # `pattern`. A filter that omits it reads the whole held-out suite.
        result = self.run_claude(
            "deny-acceptance-to-subagents.sh",
            self.subagent({"pattern": ".", "path": ".", "glob": f"{self.HELD_OUT}/**"}),
        )
        self.assertEqual(result.returncode, 2)

    def test_subagent_grep_split_across_path_and_glob_is_blocked(self) -> None:
        # The same location split across two fields, each innocent alone.
        result = self.run_claude(
            "deny-acceptance-to-subagents.sh",
            self.subagent({"pattern": ".", "path": "tests", "glob": "acceptance/**"}),
        )
        self.assertEqual(result.returncode, 2)

    def test_subagent_spelling_variants_are_blocked(self) -> None:
        # A trailing slash, a doubled slash, a dot segment, a parent segment
        # and a capital letter all resolve to the same held-out suite.
        variants = [
            {"pattern": ".", "path": "tests/", "glob": "acceptance/**"},
            {"file_path": "tests//acceptance/login_test.py"},
            {"file_path": "tests/./acceptance/login_test.py"},
            {"file_path": "tests/unit/../acceptance/login_test.py"},
            {"file_path": "Tests/Acceptance/login_test.py"},
        ]
        for tool_input in variants:
            with self.subTest(tool_input=tool_input):
                result = self.run_claude(
                    "deny-acceptance-to-subagents.sh", self.subagent(tool_input)
                )
                self.assertEqual(result.returncode, 2)

    def test_subagent_glob_tool_split_across_path_and_pattern_is_blocked(self) -> None:
        # The Glob tool names its location through `pattern`, not `glob`.
        result = self.run_claude(
            "deny-acceptance-to-subagents.sh",
            self.subagent({"path": "tests", "pattern": "acceptance/**"}),
        )
        self.assertEqual(result.returncode, 2)

    def test_subagent_nested_object_in_a_path_field_is_blocked(self) -> None:
        result = self.run_claude(
            "deny-acceptance-to-subagents.sh",
            self.subagent({"paths": [{"p": f"{self.HELD_OUT}/login_test.py"}]}),
        )
        self.assertEqual(result.returncode, 2)

    def test_subagent_string_path_with_array_glob_is_blocked(self) -> None:
        result = self.run_claude(
            "deny-acceptance-to-subagents.sh",
            self.subagent({"path": "tests", "glob": ["acceptance/**"]}),
        )
        self.assertEqual(result.returncode, 2)

    def test_subagent_file_url_to_the_suite_is_blocked(self) -> None:
        result = self.run_claude(
            "deny-acceptance-to-subagents.sh",
            self.subagent({"url": f"file:///repo/{self.HELD_OUT}/login_test.py"}),
        )
        self.assertEqual(result.returncode, 2)

    def test_subagent_array_valued_path_field_is_blocked(self) -> None:
        result = self.run_claude(
            "deny-acceptance-to-subagents.sh",
            self.subagent({"paths": ["src/app.py", f"{self.HELD_OUT}/login_test.py"]}),
        )
        self.assertEqual(result.returncode, 2)

    def test_subagent_write_whose_body_only_names_the_suite_is_allowed(self) -> None:
        result = self.run_claude(
            "deny-acceptance-to-subagents.sh",
            self.subagent(
                {
                    "file_path": "codex/config.permissions.toml",
                    "content": f'"{self.HELD_OUT}" = "deny"\n"{self.HELD_OUT}/**" = "deny"\n',
                }
            ),
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_a_heredoc_body_or_an_exclusion_is_not_a_read(self) -> None:
        held = self.HELD_OUT
        walls = (lambda p: self.run_claude("deny-acceptance-to-subagents.sh", p).returncode == 2,
                 lambda p: self.run_claude("deny-path.sh", p, held).returncode == 2,
                 lambda p: "deny" in self.run_codex(p, held).stdout)
        allowed = (f"cat > brief.md <<'EOF'\nNever read {held}.\nEOF", f"git diff -- . ':!{held}'",
                   f"git log -- ':(exclude){held}'", f"grep -rn --exclude-dir={held} foo .",
                   f"grep -rn foo . | grep -v {held}", f'git commit -m "$(cat <<\'EOF\'\nskip {held}\nEOF\n)"')
        blocked = (f"git diff -- . ':!x' {held}/a.py", f"cat > f <<'EOF'\nx\nEOF\ncat {held}/a.py",
                   f"grep -v x {held}/a.py")
        for wall in walls:
            for command in allowed:
                self.assertFalse(wall(self.subagent({"command": command})), command)
            for command in blocked:
                self.assertTrue(wall(self.subagent({"command": command})), command)

    def test_the_wall_ignores_a_controller_call(self) -> None:
        result = self.run_claude(
            "deny-acceptance-to-subagents.sh",
            {"tool_input": {"file_path": f"{self.HELD_OUT}/login_test.py"}},
        )
        self.assertEqual(result.returncode, 0)

    def test_subagent_relative_path_from_a_parent_directory_is_blocked(self) -> None:
        # A shell that sits in tests/ reaches the suite by a relative path.
        cases = [
            ("/repo/tests", {"command": "cat acceptance/login_test.py"}),
            ("/repo/tests", {"command": "cat 'acceptance/login_test.py'"}),
            ("/repo/tests/unit", {"file_path": "../acceptance/login_test.py"}),
        ]
        for cwd, tool_input in cases:
            with self.subTest(cwd=cwd, tool_input=tool_input):
                payload = self.subagent(tool_input)
                payload["cwd"] = cwd
                result = self.run_claude("deny-acceptance-to-subagents.sh", payload)
                self.assertEqual(result.returncode, 2)

    def test_subagent_working_inside_the_suite_is_blocked(self) -> None:
        payload = self.subagent({"command": "ls"})
        payload["cwd"] = f"/repo/{self.HELD_OUT}/api"
        result = self.run_claude("deny-acceptance-to-subagents.sh", payload)
        self.assertEqual(result.returncode, 2)

    def test_subagent_relative_path_elsewhere_is_allowed(self) -> None:
        payload = self.subagent({"command": "cat src/app.py"})
        payload["cwd"] = "/repo"
        result = self.run_claude("deny-acceptance-to-subagents.sh", payload)
        self.assertEqual(result.returncode, 0)

    def test_the_spec_tester_keeps_its_exemption(self) -> None:
        payload = self.subagent({"file_path": f"{self.HELD_OUT}/login_test.py"})
        payload["agent_type"] = "spec-tester"
        result = self.run_claude("deny-acceptance-to-subagents.sh", payload)
        self.assertEqual(result.returncode, 0)

    def test_claude_deny_path_blocks_a_path_and_allows_a_body(self) -> None:
        blocked = self.run_claude(
            "deny-path.sh", {"tool_input": {"file_path": "src/app.py"}}, "src"
        )
        allowed = self.run_claude(
            "deny-path.sh",
            {"tool_input": {"file_path": "docs/layout.md", "content": "Sources live in src/.\n"}},
            "src",
        )
        self.assertEqual(blocked.returncode, 2)
        self.assertEqual(allowed.returncode, 0)

    def test_claude_deny_path_blocks_a_glob(self) -> None:
        result = self.run_claude(
            "deny-path.sh", {"tool_input": {"path": ".", "glob": "src/**"}}, "src"
        )
        self.assertEqual(result.returncode, 2)

    def test_claude_deny_path_matches_whole_segments(self) -> None:
        for command in ("cat app/main.py", "ls /repo/app", "ls app; echo", "ls 'app'", "ls \"app\" | wc"):
            result = self.run_claude("deny-path.sh", {"tool_input": {"command": command}}, "app/")
            self.assertEqual(result.returncode, 2, command)
        for command in ("grep -r application docs", "cat src/mapper.py", "grep -r 'App Services' docs"):
            result = self.run_claude("deny-path.sh", {"tool_input": {"command": command}}, "app/")
            self.assertEqual(result.returncode, 0, command)

    def test_codex_deny_path_blocks_a_glob(self) -> None:
        result = self.run_codex(
            {"tool_input": {"path": ".", "glob": f"{self.HELD_OUT}/**"}}, self.HELD_OUT
        )
        self.assertEqual(
            json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_codex_deny_path_blocks_a_path_and_allows_a_body(self) -> None:
        blocked = self.run_codex(
            {"tool_input": {"command": f"sed -n 1,80p {self.HELD_OUT}/login.py"}}, self.HELD_OUT
        )
        allowed = self.run_codex(
            {"tool_input": {"file_path": "notes.md", "content": f"The suite is {self.HELD_OUT}.\n"}},
            self.HELD_OUT,
        )
        self.assertEqual(
            json.loads(blocked.stdout)["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertEqual(allowed.stdout, "")


class CodexHookBehaviorTests(unittest.TestCase):
    def run_hook(
        self, name: str, payload: dict, *args: str, env: dict | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(CODEX_ASSETS / "hooks" / name), *args],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )

    def test_deny_path_blocks_held_out_suite(self) -> None:
        result = self.run_hook(
            "deny-path.sh",
            {"tool_input": {"command": "sed -n 1,80p tests/acceptance/login.py"}},
            "tests/acceptance",
        )
        self.assertEqual(result.returncode, 0)
        output = json.loads(result.stdout)
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_deny_path_allows_unrelated_unit_test(self) -> None:
        result = self.run_hook(
            "deny-path.sh",
            {"tool_input": {"command": "pytest tests/unit/test_login.py"}},
            "tests/acceptance",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_git_guard_blocks_hard_reset(self) -> None:
        result = self.run_hook(
            "git-guard.sh",
            {"cwd": "/tmp/repo", "tool_input": {"command": "git reset --hard HEAD"}},
        )
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_git_guard_allows_a_git_write_in_the_old_vault_folder(self) -> None:
        # The kit no longer guards the vault: a vault key in the config changes nothing.
        with tempfile.TemporaryDirectory() as home:
            vault = os.path.join(home, "vault")
            write(Path(home) / ".agents" / "dev-loop.config.json", json.dumps({"vault": vault}))
            env = {**os.environ, "HOME": home}
            allowed = self.run_hook(
                "git-guard.sh", {"cwd": vault, "tool_input": {"command": "git add note.md"}}, env=env
            )
            self.assertEqual((allowed.returncode, allowed.stdout), (0, ""))

    def test_session_start_detects_dev_loop_json_only(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root)
            write(repo / "AGENTS.md", "vault_notes: Software/Test.md\n")
            self.assertEqual(self.run_hook("session-start.sh", {"cwd": str(repo)}).stdout, "")
            write(repo / "dev-loop.json", "{}\n")
            routed = self.run_hook("session-start.sh", {"cwd": str(repo)}).stdout
            self.assertIn("dev-loop skill", routed)
            self.assertNotIn("one stage", routed)

    def test_gate_blocks_failed_dirty_repo_once(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root)
            subprocess.run(["git", "init", "-q", str(repo)], check=True, stderr=subprocess.DEVNULL)
            write(repo / "tracked.txt", "before\n")
            subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True, stderr=subprocess.DEVNULL)
            subprocess.run(
                ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "fixture"],
                check=True,
                stderr=subprocess.DEVNULL,
            )
            write(repo / "tracked.txt", "after\n")
            gate = repo / "scripts" / "gate.sh"
            write(gate, '#!/bin/sh\necho focused failure\necho "scope=${GATE_SCOPE:-full}"\nexit 1\n')
            os.chmod(gate, 0o755)
            first = self.run_hook("gate.sh", {"cwd": str(repo), "stop_hook_active": False})
            second = self.run_hook("gate.sh", {"cwd": str(repo), "stop_hook_active": True})
            self.assertIn("focused failure", json.loads(first.stdout)["reason"])
            self.assertIn("scope=changed", json.loads(first.stdout)["reason"])
            self.assertEqual(second.stdout, "")

    def test_gate_runs_the_mapped_gate_command_first(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root)
            subprocess.run(["git", "init", "-q", str(repo)], check=True, stderr=subprocess.DEVNULL)
            write(repo / "dev-loop.json", json.dumps({"commands": {"gate": "echo mapped scope=$GATE_SCOPE; exit 1"}}))
            result = self.run_hook("gate.sh", {"cwd": str(repo), "stop_hook_active": False})
            self.assertIn("mapped scope=changed", json.loads(result.stdout)["reason"])
            gate = repo / "scripts" / "gate.sh"
            write(gate, "#!/bin/sh\necho script gate\nexit 1\n")
            os.chmod(gate, 0o755)
            result = self.run_hook("gate.sh", {"cwd": str(repo), "stop_hook_active": False})
            self.assertIn("mapped scope=changed", json.loads(result.stdout)["reason"])
            self.assertNotIn("script gate", result.stdout)


class ClaudeGateTests(unittest.TestCase):
    """The Claude Stop and SubagentStop gate, and its SubagentStart snapshot."""

    def repo(self, root: str) -> Path:
        repo = Path(root)
        subprocess.run(["git", "init", "-q", str(repo)], check=True, stderr=subprocess.DEVNULL)
        write(repo / "tracked.txt", "before\n")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True, stderr=subprocess.DEVNULL)
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "fixture"],
            check=True,
            stderr=subprocess.DEVNULL,
        )
        write(repo / "tracked.txt", "after\n")
        gate = repo / "scripts" / "gate.sh"
        write(gate, '#!/bin/sh\necho "scope=${GATE_SCOPE:-full}"\nexit 1\n')
        os.chmod(gate, 0o755)
        return repo

    def hook(self, name: str, repo: Path, payload: dict) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(CLAUDE_HOOKS / name)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo)},
        )

    def test_stop_asks_for_the_selective_gate(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.repo(root)
            result = self.hook("gate.sh", repo, {"hook_event_name": "Stop"})
            self.assertIn("scope=changed", json.loads(result.stdout)["reason"])

    def test_a_read_only_subagent_runs_no_gate(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.repo(root)
            for agent_type in ["Explore", "Plan"]:
                with self.subTest(agent_type=agent_type):
                    result = self.hook(
                        "gate.sh",
                        repo,
                        {"hook_event_name": "SubagentStop", "agent_id": "a1", "agent_type": agent_type},
                    )
                    self.assertEqual(result.stdout, "")

    def test_a_subagent_that_wrote_nothing_runs_no_gate(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.repo(root)
            start = {"hook_event_name": "SubagentStart", "agent_id": "a2", "agent_type": "general-purpose"}
            self.hook("gate-snapshot.sh", repo, start)
            result = self.hook("gate.sh", repo, {**start, "hook_event_name": "SubagentStop"})
            self.assertEqual(result.stdout, "")

    def test_a_subagent_that_wrote_a_file_runs_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.repo(root)
            start = {"hook_event_name": "SubagentStart", "agent_id": "a3", "agent_type": "implementer"}
            self.hook("gate-snapshot.sh", repo, start)
            write(repo / "new.txt", "written\n")
            result = self.hook("gate.sh", repo, {**start, "hook_event_name": "SubagentStop"})
            self.assertIn("scope=changed", json.loads(result.stdout)["reason"])

    def test_a_subagent_with_no_snapshot_runs_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.repo(root)
            result = self.hook(
                "gate.sh",
                repo,
                {"hook_event_name": "SubagentStop", "agent_id": "a4", "agent_type": "general-purpose"},
            )
            self.assertIn("scope=changed", json.loads(result.stdout)["reason"])

    def test_stop_runs_the_mapped_gate_command_first(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = self.repo(root)
            write(repo / "dev-loop.json", json.dumps({"commands": {"gate": "echo mapped scope=$GATE_SCOPE; exit 1"}}))
            result = self.hook("gate.sh", repo, {"hook_event_name": "Stop"})
            self.assertIn("mapped scope=changed", json.loads(result.stdout)["reason"])
            (repo / "scripts" / "gate.sh").unlink()
            result = self.hook("gate.sh", repo, {"hook_event_name": "Stop"})
            self.assertIn("mapped scope=changed", json.loads(result.stdout)["reason"])


class BoardHookLaunchTests(unittest.TestCase):
    """D1, D12, O1: how board.sh and gate.sh start `devloop.py board`. Hooks run from a kit layout copy;
    each repo holds a fake devloop.py that sleeps BOARD_SLEEP seconds, then logs its label, cwd and stdin."""

    FAKE = (
        "#!/usr/bin/env python3\nimport json, os, sys, time\ndata = sys.stdin.read()\n"
        "time.sleep(float(os.environ.get('BOARD_SLEEP', '0')))\n"
        "with open(os.environ['BOARD_TEST_LOG'], 'a') as log:\n"
        "    log.write(json.dumps({'label': %r, 'cwd': os.getcwd(), 'stdin': data}) + '\\n')\n"
    )
    HOOKS = {"claude": CLAUDE_HOOKS, "codex": CODEX_ASSETS / "hooks"}

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.log = self.root / "board.jsonl"

    def repo(self, path: Path, label: str) -> Path:
        subprocess.run(["git", "init", "-q", str(path)], check=True, stderr=subprocess.DEVNULL)
        write(path / ".agents" / "skills" / "dev-loop" / "scripts" / "devloop.py", self.FAKE % label)
        for harness, source in self.HOOKS.items():
            target = path / ".dev-loop" / "hooks" / harness
            target.mkdir(parents=True)
            for script in source.iterdir():
                shutil.copy2(script, target / script.name)
        return path

    def start(self, repo: Path, harness: str, script: str, args: list[str], cwd: Path, sleep: str,
              log: Path | None = None):
        payload = {"cwd": str(cwd), "hook_event_name": "Stop", "stop_hook_active": False}
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo), "BOARD_TEST_LOG": str(log or self.log), "BOARD_SLEEP": sleep}
        shell = "bash" if harness == "claude" else "sh"
        return subprocess.Popen([shell, str(repo / ".dev-loop" / "hooks" / harness / script), *args], cwd=repo,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True), payload

    def calls(self, count: int, wait: float, log: Path | None = None) -> list[dict]:
        log = log or self.log
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            lines = log.read_text().splitlines() if log.exists() else []
            if len(lines) >= count:
                break
            time.sleep(0.1)
        time.sleep(0.5)
        return [json.loads(line) for line in (log.read_text().splitlines() if log.exists() else [])]

    def test_d1_o1_hook_returns_while_board_runs(self) -> None:
        repo = self.repo(self.root / "project", "A")
        # The four boards sleep at the same time, each with its own log.
        runs = []
        for harness, script, args in [("claude", "board.sh", ["prompt"]), ("claude", "gate.sh", []),
                                      ("codex", "board.sh", ["prompt"]), ("codex", "gate.sh", [])]:
            log = self.root / f"board-{harness}-{script}.jsonl"
            began = time.monotonic()
            proc, payload = self.start(repo, harness, script, args, repo, "3", log)
            out, _ = proc.communicate(json.dumps(payload), timeout=30)
            runs.append((harness, script, proc, out, time.monotonic() - began, log))
        for harness, script, proc, out, elapsed, log in runs:
            with self.subTest(harness=harness, script=script):
                calls = self.calls(1, 6, log)
                self.assertLess(elapsed, 1.5)
                self.assertEqual((proc.returncode, out), (0, ""))
                self.assertEqual([c["label"] for c in calls], ["A"])

    def test_d12_claude_hooks_run_the_project_cli_not_the_cwd_one(self) -> None:
        repo = self.repo(self.root / "project", "A")
        nested = self.repo(repo / "nested", "B")
        for script, args in [("board.sh", ["prompt"]), ("gate.sh", [])]:
            with self.subTest(script=script):
                self.log.unlink(missing_ok=True)
                proc, payload = self.start(repo, "claude", script, args, nested, "0")
                proc.communicate(json.dumps(payload), timeout=30)
                calls = self.calls(1, 6)
                self.assertEqual([c["label"] for c in calls], ["A"])
                self.assertEqual((calls[0]["cwd"], json.loads(calls[0]["stdin"])["cwd"]), (str(nested), str(nested)))


class ClaudeGitGuardPushTests(unittest.TestCase):
    """A plain push of a feature branch runs; a push that rewrites or reaches main asks."""

    @classmethod
    def setUpClass(cls) -> None:
        # The three repos are made once per class; each test gets a fresh copy, so a test that edits one leaves no trace.
        cls.templates = tempfile.mkdtemp()
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        for name, steps in (("repo", (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "i"],
                                      ["tag", "v1.0"], ["checkout", "-q", "-b", "feat"])),
                            ("main", (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "i"])),
                            ("feature", (["init", "-q", "-b", "feat"], ["commit", "-q", "--allow-empty", "-m", "i"]))):
            os.makedirs(os.path.join(cls.templates, name))
            for args in steps:
                subprocess.run(["git", "-C", os.path.join(cls.templates, name), *args], check=True, env=env)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.templates)

    def copy(self, name: str, target: str) -> None:
        # An fsmonitor daemon socket (core.fsmonitor in a user config) cannot be copied.
        shutil.copytree(os.path.join(self.templates, name), target, symlinks=True, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("fsmonitor--daemon.ipc"))

    def setUp(self) -> None:
        self.repo = tempfile.mkdtemp()
        self.copy("repo", self.repo)
        self.branch = "feat"
        # A second repo on main. Its path sorts after self.repo, so a guard that checks
        # only the first possible repo checks the feature one.
        self.main_repo = self.repo + "-main"
        self.copy("main", self.main_repo)
        # A second feature repo, for a session on main whose line visits a feature repo.
        self.feature_repo = self.repo + "-feature"
        self.copy("feature", self.feature_repo)

    def tearDown(self) -> None:
        shutil.rmtree(self.repo)
        shutil.rmtree(self.main_repo)
        shutil.rmtree(self.feature_repo)

    def on(self, branch: str) -> None:
        """Check out `branch` in self.repo, unless it is already the branch there."""
        if branch != self.branch:
            subprocess.run(["git", "-C", self.repo, "checkout", "-q", branch], check=True)
            self.branch = branch

    def decisions(self, commands: Sequence[str], branch: str = "feat", env: dict | None = None) -> list[tuple[str, str]]:
        """Each command with its decision. The hooks run at once: a guard only reads the repos."""
        self.on(branch)
        with ThreadPoolExecutor(8) as pool:
            return list(zip(commands, pool.map(lambda command: self.decision(command, branch, env), commands)))

    def decision(self, command: str, branch: str = "feat", env: dict | None = None) -> str:
        self.on(branch)
        result = subprocess.run(
            ["bash", str(CLAUDE_HOOKS / "git-guard.sh")],
            input=json.dumps({"cwd": self.repo, "tool_input": {"command": command}}),
            text=True, capture_output=True, check=False, env=env,
        )
        if result.returncode == 2:
            return "deny"
        return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] if result.stdout else "allow"

    def test_plain_feature_branch_push_runs(self) -> None:
        for command, got in self.decisions(("git push", "git push origin feat", "git push -u origin feat", "git push origin HEAD")):
            self.assertEqual(got, "allow", command)

    def test_rewriting_deleting_tag_or_main_push_asks(self) -> None:
        for command, got in self.decisions(("git push --force origin feat", "git push --force-with-lease origin feat",
                        "git push origin +feat", "git push origin :feat", "git push origin --delete feat",
                        "git push origin main", "git push origin feat:main", "git push origin v1.0",
                        "git push --tags", "git push --mirror", "git push --all")):
            self.assertEqual(got, "ask", command)

    def test_bare_push_from_main_asks(self) -> None:
        for command, got in self.decisions(("git push", "git push origin", "git push origin HEAD"), "main"):
            self.assertEqual(got, "ask", command)

    def test_quotes_short_flags_and_global_options_do_not_walk_past(self) -> None:
        for command, got in self.decisions(("git push -uf origin feat", "git push origin \"main\"", "git push origin 'main'",
                        "git push origin feat:\"main\"", "git -c a=b push origin feat",
                        "GIT_CONFIG_COUNT=1 git push origin feat", "git --no-pager push origin main",
                        "git push origin feat && git push origin main", "sh -c 'git push origin main'",
                        "git push origin 'main", "git push origin @:main")):
            self.assertEqual(got, "ask", command)

    def test_wrapped_expanded_fed_or_drifted_push_asks(self) -> None:
        for command, got in self.decisions(("if git push origin main; then :; fi", "timeout 9 git push origin main",
                        "echo main | xargs git push origin", "B=main; git push origin $B",
                        "git push origin $(echo main)", "git push origin $'\\x6dain'",
                        "git checkout main && git push", "git tag v2 && git push origin v2",
                        "git config remote.origin.push '+refs/heads/*:refs/heads/*' && git push",
                        "git -c alias.x='push -f' x", "ssh host 'git push origin main'")):
            self.assertEqual(got, "ask", command)

    def test_everyday_feature_flows_still_run(self) -> None:
        for command, got in self.decisions(("git checkout -b feat2 && git push -u origin feat2", "git status && git log --oneline -3",
                        "git commit -m 'fix login' && git push")):
            self.assertEqual(got, "allow", command)

    def test_a_cd_after_a_semicolon_or_an_assignment_moves_the_push(self) -> None:
        main = self.main_repo
        for command, got in self.decisions((f"cd {main}; git push", f"X=1 cd {main} && git push", f"cd {main} & git push")):
            self.assertEqual(got, "ask", command)
        self.assertEqual(self.decision(f"cd {main}; git status"), "allow")

    def test_a_push_from_a_folder_the_guard_cannot_follow_asks(self) -> None:
        # A cd $D the line does not set stays unknown. From a feature session the
        # unknown folder may be the main repo, so it does not fall back to the session start.
        main = self.main_repo
        for command, got in self.decisions(("cd $D && git push", f"cd {main} && cd - && git push",
                        f"pushd {main}; popd && git push")):
            self.assertEqual(got, "ask", command)

    def test_an_assignment_that_moves_a_cd_stops_it_from_narrowing(self) -> None:
        # CDPATH=<x> cd f lands in <x>/f, a main clone, not in the feature clone ./f.
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        with tempfile.TemporaryDirectory() as other:
            for repo, branch in ((os.path.join(self.repo, "f"), "feat"), (os.path.join(other, "f"), "main")):
                os.makedirs(repo)
                for args in (["init", "-q", "-b", branch], ["commit", "-q", "--allow-empty", "-m", "i"]):
                    subprocess.run(["git", "-C", repo, *args], check=True, env=env)
            self.assertEqual(self.decision("cd ./f && git push"), "allow")
            for command, got in self.decisions((f"CDPATH={other} cd f && git push", f"HOME={other} cd && git push",
                            f"OLDPWD={other}/f cd - && git push")):
                self.assertEqual(got, "ask", command)

    def test_a_cd_before_a_semicolon_keeps_the_old_folders(self) -> None:
        # cd /nope fails, so the cd after && never runs and the push runs on main.
        with tempfile.TemporaryDirectory() as feature:
            for args in (["init", "-q", "-b", "feat"], ["commit", "-q", "--allow-empty", "-m", "i"]):
                subprocess.run(["git", "-C", feature, *args], check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            command = f"cd /nope && cd {feature}; git push"
            self.assertEqual(self.decision(command, "main"), "ask", command)

    def test_a_paren_that_closes_no_subshell_keeps_the_cd(self) -> None:
        # A case pattern or an escaped paren is a bare ")" to the parser.
        main = self.main_repo
        for command, got in self.decisions((f"(cd {main} && case x in x) git push;; esac)",
                        f"(cd {main}; case x in x) git push;; esac)",
                        f"(cd {main} && echo \\) && git push)")):
            self.assertEqual(got, "ask", command)

    def test_a_cd_inside_a_subshell_does_not_move_the_push(self) -> None:
        # The push runs in self.repo on main, not in the feature repo the subshell visits.
        with tempfile.TemporaryDirectory() as feature:
            for args in (["init", "-q", "-b", "feat"], ["commit", "-q", "--allow-empty", "-m", "i"]):
                subprocess.run(["git", "-C", feature, *args], check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            for command, got in self.decisions((f"(cd {feature}; true); git push", f"echo $(cd {feature}; true); git push",
                            f"(cd {feature} && true); git push", f"echo $(cd {feature} && true) && git push",
                            "echo " + chr(96) + f"cd {feature} && true" + chr(96) + "; git push"), "main"):
                self.assertEqual(got, "ask", command)

    def test_a_paren_that_closes_no_subshell_stops_a_later_cd_from_narrowing(self) -> None:
        # The case pattern closes the subshell early to the parser, so the cd looks
        # like it runs outside, and the real ")" finds nothing open.
        with tempfile.TemporaryDirectory() as feature:
            for args in (["init", "-q", "-b", "feat"], ["commit", "-q", "--allow-empty", "-m", "i"]):
                subprocess.run(["git", "-C", feature, *args], check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            command = f"(case x in x) cd {feature} && true;; esac); git push"
            self.assertEqual(self.decision(command, "main"), "ask", command)

    def test_a_quote_escape_comment_or_heredoc_stops_a_cd_from_narrowing(self) -> None:
        # Quoted or escaped punctuation, a comment and a heredoc body are not
        # operators or commands, so a cd next to them may not have run before the push.
        with tempfile.TemporaryDirectory() as feature:
            for args in (["init", "-q", "-b", "feat"], ["commit", "-q", "--allow-empty", "-m", "i"]):
                subprocess.run(["git", "-C", feature, *args], check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            bt = chr(96)
            for command, got in self.decisions((f'echo ";" cd {feature}; git push', f"echo \\; cd {feature}; git push",
                            f"true # ; cd {feature}\ngit push", f"true # && cd {feature}\ngit push",
                            f"cat <<EOF\ncd {feature}\nEOF\ngit push", f"cat <<EOF\ncd {feature} && true\nEOF\ngit push",
                            f"cd {feature} extra; git push",
                            f"(echo \\) && cd {feature} && echo \\( && true); git push",
                            f"(echo ')' && cd {feature} && echo '(' && true); git push",
                            f"(echo \\) && cd {feature} && echo \\{bt} && true); git push",
                            f"(case x in x) cd {feature} && true;; esac; echo \\( ); git push",
                            f'echo "&&" cd {feature} && git push', f"echo '&&' cd {feature} && git push"), "main"):
                self.assertEqual(got, "ask", command)

    def test_a_cd_or_dash_c_the_push_may_not_follow_asks(self) -> None:
        # Converted from the vault cases: the session is on main, the line visits a
        # feature repo, and the push asks unless it surely runs in the feature repo.
        with tempfile.TemporaryDirectory() as feature:
            for args in (["init", "-q", "-b", "feat"], ["commit", "-q", "--allow-empty", "-m", "i"]):
                subprocess.run(["git", "-C", feature, *args], check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            bt, rel = chr(96), "../" + os.path.basename(feature)
            for command, got in self.decisions((f"cd {feature}; git push", f"cd {rel}; git push", f"(cd {feature}); git push",
                            f"(cd {feature}) && git push",
                            f"cd {feature} && cd \"$OLDPWD\" && git push", f"cd {feature} && cd - && git push",
                            f"(echo \\) && cd {feature} && true); git push",
                            f"echo $(echo \\) && cd {feature} && true); git push",
                            f"echo {bt}cd {feature}; true{bt}; git push", "sudo -u git git push"), "main"):
                self.assertEqual(got, "ask", command)
            for command, got in self.decisions((f"cd {feature} && git push", f"cd {feature} && (git status) && git push",
                            f"D={feature}; cd $D && git push",
                            f"git -C {feature} push"), "main"):
                self.assertEqual(got, "allow", command)
        main = os.path.basename(self.main_repo)
        for command, got in self.decisions((f"cd {self.main_repo} && git push", f"git -C {self.main_repo} push",
                        f"# save\ngit -C {self.main_repo} push", f"git -C ../{main} push")):
            self.assertEqual(got, "ask", command)

    def test_a_tilde_expands_only_at_the_start_of_a_path(self) -> None:
        # The old vault sat under iCloud~md~obsidian: a ~ inside a path is no expansion.
        with tempfile.TemporaryDirectory() as home:
            tilde_main = os.path.join(home, "iCloud~md~obsidian", "main")
            os.makedirs(tilde_main)
            for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "i"]):
                subprocess.run(["git", "-C", tilde_main, *args], check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            os.symlink(self.repo, os.path.join(home, "feature"))
            env = {**os.environ, "HOME": home}
            for command, got in self.decisions((f'git -C "{tilde_main}" push', f'cd "{tilde_main}" && git push',
                            "cd ~/iCloud~md~obsidian/main && git push"), env=env):
                self.assertEqual(got, "ask", command)
            self.assertEqual(self.decision("cd ~/feature && git push", "main", env=env), "ask")
            self.assertEqual(self.decision("cd ~/feature && git push", env=env), "allow")

    def test_an_unparseable_line_asks_only_for_a_push(self) -> None:
        for command, got in self.decisions(("python3 - <<'EOF'\nprint(\"it's the git log\")\nEOF",
                        "python3 - <<'EOF'\nprint(\"it's the git log\")\nEOF\ngit push",
                        "git log --grep \"it's")):
            self.assertEqual(got, "allow", command)
        self.assertEqual(self.decision("git push origin feat \"it's"), "ask")

    def test_only_what_the_shell_runs_is_checked(self) -> None:
        # A heredoc body is text unless its line feeds it to a shell. A quoted string
        # is a command only in a segment that runs a shell.
        for command, got in self.decisions(("cat > notes.md <<'EOF'\ngit push origin main\nEOF",
                        "cat <<-EOF\n\tgit push origin main\n\tEOF\ngit status",
                        'echo "cd x && git push origin main"', "grep -n 'git push origin main' notes.md",
                        'run "cd x; git push origin main"')):
            self.assertEqual(got, "allow", command)
        for command, got in self.decisions(("bash <<'EOF'\ngit push origin main\nEOF", "ssh host <<'EOF'\ngit push origin main\nEOF",
                        "cat <<'EOF' | sh\ngit push origin main\nEOF", "cat > x <<'EOF'\nx\nEOF\ngit push origin main",
                        "eval 'git push origin main'", "su -c 'git push origin main'",
                        "cat <<'EOF'\ngit status\ngit push origin main")):
            self.assertEqual(got, "ask", command)

    def test_a_cd_into_a_variable_the_line_surely_set_is_followed(self) -> None:
        # The session is on main. The push runs in the feature repo only when the
        # variable surely holds its path.
        feature, main = self.feature_repo, self.main_repo
        parent, name = os.path.dirname(feature), os.path.basename(feature)
        for command, got in self.decisions((f'F={feature}; cd "$F" && git push', f"F={feature} && cd $F && git push",
                        f"export F={feature}; cd ${{F}} && git push", f'P={parent}; cd "$P/{name}" && git push',
                        f'F={feature}; git -C "$F" push', f"W={feature}\ngit add x && git commit -F - <<'EOF'\nmsg\nEOF\ncd $W && git push"), "main"):
            self.assertEqual(got, "allow", command)
        for command, got in self.decisions((f"true && F={feature}; cd $F && git push", f"true || F={feature}; cd $F && git push",
                        f"(F={feature}); cd $F && git push", f"sh -c 'F={feature}'; cd $F && git push",
                        f"F={feature}; F={main}; cd $F && git push", f"F={feature}; read F; cd $F && git push",
                        f"F={feature}; unset F; cd $F && git push", f"for F in {main}; do :; done; F={feature}; cd $F && git push",
                        f"F=../{name}; cd $F && git push", f"F={feature}$X; cd $F && git push",
                        f"F={feature}; F+=x; cd $F && git push", f"cd $F && F={feature}; git push", f"F={feature}; git commit -F x && cd $F && git push",
                        f"bash <<'EOF'\ncd {feature} && true\nEOF\ngit push"), "main"):
            self.assertEqual(got, "ask", command)

    def test_a_variable_holding_a_tilde_inside_its_path_is_followed(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            feature = os.path.join(root, "iCloud~md~obsidian", "f")
            os.makedirs(feature)
            for args in (["init", "-q", "-b", "feat"], ["commit", "-q", "--allow-empty", "-m", "i"]):
                subprocess.run(["git", "-C", feature, *args], check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            self.assertEqual(self.decision(f'F="{feature}"; cd "$F" && git push', "main"), "allow")
            # An assignment expands a ~ after ":": F=/a:~+ is /a:$PWD, not the folder "/a:~+".
            colon = os.path.join(root, "p:~+")
            os.makedirs(colon)
            for args in (["init", "-q", "-b", "feat"], ["commit", "-q", "--allow-empty", "-m", "i"]):
                subprocess.run(["git", "-C", colon, *args], check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            self.assertEqual(self.decision(f'F={colon}; cd "$F" && git push', "main"), "ask")

    def test_the_reset_and_rm_walls_skip_a_heredoc_body(self) -> None:
        reset, rm = "git re" + "set --hard", "rm -rf " + "/"
        for command, got in self.decisions((f"cat > n.md <<'EOF'\nnever {reset} or {rm}\nEOF", f"cat <<-EOF\n\t{reset}\n\tEOF")):
            self.assertEqual(got, "allow", command)
        for command, got in self.decisions((f"bash <<'EOF'\n{reset}\nEOF", f"cat > n.md <<'EOF'\nx\nEOF\n{reset}",
                        f"cat > n.md <<'EOF'\nx\nEOF\n{rm}", f"cat <<'EOF'\nno end line\n{reset}")):
            self.assertIn(got, ("deny", "ask"), command)

    def test_a_bare_push_from_an_unknown_repository_asks(self) -> None:
        # A folder made earlier in the line does not exist yet, so a named feature
        # branch still runs.
        self.assertEqual(self.decision(f"git -C {self.repo}/nope push"), "ask")
        self.assertEqual(self.decision(f"git -C {self.repo}/nope push origin feat"), "allow")

    def test_the_regression_commands_ask_only_for_a_real_push(self) -> None:
        # The 53 commands the 1.4 guard asked on (permission prompt analysis,
        # 2026-10-13). One is a real push to main.
        cases = json.loads(GUARD_ASKS.read_text())
        for case, (_, got) in zip(cases, self.decisions([case["cmd"] for case in cases])):
            expected = "ask" if case["real_push"] else "allow"
            self.assertEqual(got, expected, case["cmd"][:300])

    def test_remote_config_that_reaches_other_branches_asks(self) -> None:
        subprocess.run(["git", "-C", self.repo, "config", "remote.origin.push", "+refs/heads/*:refs/heads/*"], check=True)
        self.assertEqual(self.decision("git push"), "ask")
        self.assertEqual(self.decision("git push origin feat"), "allow")

    def test_a_push_alias_asks(self) -> None:
        subprocess.run(["git", "-C", self.repo, "config", "alias.p", "push --force"], check=True)
        self.assertEqual(self.decision("git p"), "ask")
        self.assertEqual(self.decision("git commit -m 'push fix'"), "allow")


    def test_a_push_alias_in_any_possible_repo_asks(self) -> None:
        # After cd <main>; the line may be in either repo, and only main has the alias.
        subprocess.run(["git", "-C", self.main_repo, "config", "alias.p", "push"], check=True)
        self.assertEqual(self.decision(f"cd {self.main_repo}; git p"), "ask")

    def test_a_cd_that_may_be_skipped_does_not_narrow(self) -> None:
        # true || cd F && git push is (true || cd F) && git push: the push runs
        # without the cd. Only a cd after the line start, && or ; surely ran.
        feature = self.feature_repo
        for command, got in self.decisions((f"true || cd {feature} && git push", f"false | cd {feature} && git push",
                        f"sleep 0 & cd {feature} && git push"), "main"):
            self.assertEqual(got, "ask", command)
        for command, got in self.decisions((f"cd {feature} && git push", f"true; cd {feature} && git push",
                        f"true && cd {feature} && git push"), "main"):
            self.assertEqual(got, "allow", command)

    def test_a_cd_behind_a_wrapper_is_unknown(self) -> None:
        # The guard does not follow a cd that is not the first word, so it cannot
        # say where the push runs.
        main = self.main_repo
        for command, got in self.decisions((f"builtin cd {main} && git push", f"command cd {main} && git push",
                        f"time cd {main} && git push", f"eval cd {main} && git push",
                        f"if cd {main}; then git push; fi", f"{{ cd {main}; }} && git push",
                        f"! cd {main} && git push", f"exec cd {main} && git push")):
            self.assertEqual(got, "ask", command)

    def test_a_cd_with_an_option_or_a_stack_move_is_unknown(self) -> None:
        # cd -P M lands in M, not in ./-P. pushd +1 and a bare pushd move to a folder
        # from the directory stack.
        main, feature = self.main_repo, self.feature_repo
        for command, got in self.decisions((f"cd -P {main} && git push origin v1", f"cd -- {main} && git push origin v1",
                        f"cd -L {main} && git push origin v1", f"pushd -n {main} && git push origin v1",
                        f"pushd {main} && pushd {feature} && pushd +1 && git push origin v1",
                        f"pushd {main} && pushd {feature} && pushd && git push origin v1")):
            self.assertEqual(got, "ask", command)

    def test_a_relative_cd_does_not_narrow(self) -> None:
        # CDPATH, set earlier in the line or in the user's shell profile, sends cd f
        # to <x>/f, a main clone, not to the feature clone ./f.
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        with tempfile.TemporaryDirectory() as other:
            for repo, branch in ((os.path.join(self.repo, "f"), "feat"), (os.path.join(other, "f"), "main")):
                os.makedirs(repo)
                for args in (["init", "-q", "-b", branch], ["commit", "-q", "--allow-empty", "-m", "i"]):
                    subprocess.run(["git", "-C", repo, *args], check=True, env=env)
            for command, got in self.decisions((f"CDPATH={other}; cd f && git push", f"export CDPATH={other}; cd f && git push",
                            "cd f && git push", "pushd f && git push")):
                self.assertEqual(got, "ask", command)
            for command, got in self.decisions(("cd ./f && git push", "cd ../" + os.path.basename(self.repo) + " && git push")):
                self.assertEqual(got, "allow", command)

    def test_a_home_set_in_the_line_makes_a_home_cd_unknown(self) -> None:
        # The hook's HOME is the feature repo, the line's HOME the main repo.
        env, main = {**os.environ, "HOME": self.repo}, self.main_repo
        for command, got in self.decisions((f"HOME={main}; cd && git push origin v1", f"export HOME={main}; cd ~ && git push",
                        f"export HOME={main}; cd ~/. && git push"), env=env):
            self.assertEqual(got, "ask", command)
        self.assertEqual(self.decision("cd ~ && git push", env=env), "allow")

    def test_a_push_with_git_dir_or_work_tree_asks(self) -> None:
        main = self.main_repo
        for command, got in self.decisions((f"git --git-dir={main}/.git push", f"git --git-dir {main}/.git push",
                        f"git --work-tree={main} push", f"git --work-tree {main} push")):
            self.assertEqual(got, "ask", command)

    def test_a_git_behind_a_chdir_wrapper_asks(self) -> None:
        main = self.main_repo
        for command, got in self.decisions((f"env -C {main} git push", f"env --chdir={main} git push", f"env --chdir {main} git push")):
            self.assertEqual(got, "ask", command)

    def test_an_alias_from_a_folder_the_guard_cannot_follow_asks(self) -> None:
        # Only the main repo holds the alias, and the guard cannot see the line is there.
        main = self.main_repo
        subprocess.run(["git", "-C", main, "config", "alias.p", "push"], check=True)
        for command, got in self.decisions(("cd $D && git p", f"git --git-dir={main}/.git p", f"env -C {main} git p")):
            self.assertEqual(got, "ask", command)
        self.assertEqual(self.decision("cd $D && git status"), "allow")

    def test_send_pack_and_subtree_push_ask(self) -> None:
        for command, got in self.decisions(("git send-pack origin feat", "git send-pack --all origin",
                        "git subtree push --prefix=lib origin feat", "git subtree -P lib push origin feat",
                        "git subtree --prefix lib -q push origin feat")):
            self.assertEqual(got, "ask", command)
        self.assertEqual(self.decision("git subtree split -P lib"), "allow")

class ClaudeGitGuardSubagentTests(unittest.TestCase):
    """A hook ask from a subagent reaches the human, so a subagent gets a deny instead."""

    def run_guard(self, payload: dict) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(CLAUDE_HOOKS / "git-guard.sh")], input=json.dumps(payload),
                              text=True, capture_output=True, check=False)

    def test_a_subagent_push_that_would_ask_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(["git", "-C", repo, "init", "-q", "-b", "feat"], check=True)
            command = {"command": "git push origin main"}
            sub = self.run_guard({"cwd": repo, "agent_id": "a1", "tool_input": command})
            self.assertEqual((sub.returncode, sub.stdout), (2, ""))
            self.assertIn("Push from the main session", sub.stderr)
            main = self.run_guard({"cwd": repo, "tool_input": command})
            self.assertEqual(json.loads(main.stdout)["hookSpecificOutput"]["permissionDecision"], "ask")
            feature = self.run_guard({"cwd": repo, "agent_id": "a1", "tool_input": {"command": "git push origin feat"}})
            self.assertEqual((feature.returncode, feature.stdout), (0, ""))


class CodexGitGuardPushTests(ClaudeGitGuardPushTests):
    """The Codex guard holds the same push rule. Codex cannot ask, so every ask is a deny."""

    def decision(self, command: str, branch: str = "feat", env: dict | None = None) -> str:
        self.on(branch)
        result = subprocess.run(
            ["sh", str(CODEX_ASSETS / "hooks" / "git-guard.sh")],
            input=json.dumps({"cwd": self.repo, "tool_input": {"command": command}}),
            text=True, capture_output=True, check=False, env=env,
        )
        decision = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] if result.stdout else "allow"
        return "ask" if decision == "deny" else decision


class GitGuardParityTests(unittest.TestCase):
    """The two guards differ only in how they deny and ask, so the push rule cannot drift."""

    @staticmethod
    def body(path: Path) -> list[str]:
        return [line for line in path.read_text().splitlines()
                if not line.startswith(("#", "deny() ", "ask() "))]

    def test_codex_guard_matches_claude_guard(self) -> None:
        self.assertEqual(self.body(CODEX_ASSETS / "hooks" / "git-guard.sh"), self.body(CLAUDE_HOOKS / "git-guard.sh"))

    def test_a_git_write_in_the_old_vault_folder_is_allowed(self) -> None:
        # The kit holds no vault rule: a vault key in the config changes nothing.
        with tempfile.TemporaryDirectory() as home:
            vault = os.path.join(home, "Mobile Documents", "iCloud~md~obsidian", "Notes")
            outside = os.path.join(home, "repo")
            os.makedirs(vault)
            os.makedirs(outside)
            write(Path(home) / ".agents" / "dev-loop.config.json", json.dumps({"vault": vault}))
            env = {**os.environ, "HOME": home}
            cases = [(vault, "git add note.md"), (vault, "git commit -am x"), (vault, "git branch -D old"),
                     (vault, "cd /tmp; git commit -am x"), (vault, "sh -c 'git commit -m x'"),
                     (outside, f'git -C "{vault}" commit -am x'), (outside, f'cd "{vault}" && git commit -am x')]
            for shell, guard in (("bash", CLAUDE_HOOKS / "git-guard.sh"), ("sh", CODEX_ASSETS / "hooks" / "git-guard.sh")):
                for cwd, command in cases:
                    result = subprocess.run(
                        [shell, str(guard)], env=env, text=True, capture_output=True, check=False,
                        input=json.dumps({"cwd": cwd, "tool_input": {"command": command}}),
                    )
                    self.assertEqual((result.returncode, result.stdout), (0, ""), f"{guard.parent.parent.name}: {command!r}")


PACKAGE_ROOT = SKILL_ROOT.parent
INSTALLER = PACKAGE_ROOT / "bin" / "install.mjs"


@unittest.skipUnless(shutil.which("node"), "node is required to run the installer")
class ProjectSetupCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def init(self, harness: str = "both") -> None:
        notes = Path(self._tmp.name) / "notes"
        notes.mkdir(exist_ok=True)
        result = subprocess.run(
            ["node", str(INSTALLER), "init", "--yes", f"--harness={harness}", f"--notes={notes}"],
            cwd=self.repo,
            env={**os.environ, "HOME": str(self.home)},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def run_checker(self, harness: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CHECKER), "--harness", harness, "--home", str(self.home), "--repo", str(self.repo)],
            text=True,
            capture_output=True,
            check=False,
        )

    def main_checkout(self) -> str:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        return str(Path(common).parent)

    def trust_codex(self) -> None:
        main = self.main_checkout()
        lines = [f'[projects."{main}"]', 'trust_level = "trusted"']
        for event in ["session_start", "user_prompt_submit", "pre_tool_use", "post_tool_use", "stop", "subagent_stop"]:
            lines += [f'[hooks.state."{main}/.codex/hooks.json:{event}:0:0"]', 'trusted_hash = "sha256:test"']
        write(self.home / ".codex" / "config.toml", "\n".join(lines) + "\n")
        for name in check_setup.CODEX_SKILLS:
            write(self.home / ".agents" / "skills" / name / "SKILL.md", f"name: {name}\n")

    def test_claude_project_passes(self) -> None:
        self.init("claude")
        # Stage 0 writes the repo walls; init does not.
        path = self.repo / ".claude" / "settings.json"
        settings = json.loads(path.read_text())
        settings["permissions"]["deny"] += [
            "Agent(caveman:cavecrew-reviewer)",
            "Agent(caveman:cavecrew-builder)",
            "Agent(caveman:cavecrew-investigator)",
        ]
        settings.setdefault("env", {}).update({"ENABLE_STOP_REVIEW": "0", "ENABLE_COMMIT_REVIEW": "0", "SG_PUSH_SWEEP": "0", "CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"})
        path.write_text(json.dumps(settings))
        result = self.run_checker("claude")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("claude: PASS", result.stdout)

    def test_the_checker_writes_no_python_cache(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            scripts = Path(root) / "scripts"
            shutil.copytree(CHECKER.parent, scripts, ignore=shutil.ignore_patterns("__pycache__", "test_*"))
            subprocess.run([sys.executable, str(scripts / "check_setup.py"), "--harness", "claude", "--repo", root],
                           capture_output=True, text=True, check=False, env={**os.environ, "HOME": root})
            self.assertFalse((scripts / "__pycache__").exists())

    def test_codex_project_lists_only_user_actions(self) -> None:
        self.init("codex")
        result = self.run_checker("codex")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(
            result.stdout.startswith("codex: FAIL\n\nUser actions required"), result.stdout
        )
        self.assertIn("Trust the project, then run /hooks in Codex.", result.stdout)
        self.assertIn("trust_level", result.stdout)
        self.assertIn("missing trusted Codex hook hash: stop", result.stdout)
        self.assertIn("grilling", result.stdout)

    def test_codex_project_with_trust_and_skills_passes(self) -> None:
        self.init()
        self.trust_codex()
        result = self.run_checker("codex")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("codex: PASS", result.stdout)

    def test_codex_project_reads_the_project_codex_folder(self) -> None:
        self.init()
        self.trust_codex()
        config = self.repo / ".codex" / "config.toml"
        write(config, config.read_text().replace("hooks = true", "hooks = false"))
        (self.repo / ".codex" / "agents" / "spec-tester.toml").unlink()
        result = self.run_checker("codex")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Codex hooks are not enabled", result.stdout)
        self.assertIn("spec-tester.toml", result.stdout)

    def test_codex_project_needs_a_trusted_hash_for_the_project_hooks_file(self) -> None:
        self.init()
        self.trust_codex()
        config = self.home / ".codex" / "config.toml"
        write(config, config.read_text().replace(".codex/hooks.json:stop:", "/elsewhere/hooks.json:stop:"))
        result = self.run_checker("codex")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing trusted Codex hook hash: stop", result.stdout)

    def test_claude_project_needs_the_hook_events_and_denies(self) -> None:
        self.init("claude")
        path = self.repo / ".claude" / "settings.json"
        settings = json.loads(path.read_text())
        del settings["hooks"]["Stop"]
        settings["permissions"]["deny"].remove("Read(./.env)")
        write(path, json.dumps(settings))
        result = self.run_checker("claude")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing Claude hook event: Stop", result.stdout)
        self.assertIn("missing Claude deny: Read(./.env)", result.stdout)

    def test_claude_project_keeps_the_repo_walls(self) -> None:
        # init writes the Claude repo contract; the checker still fails without it.
        walls = [
            "missing Claude repo deny: Agent(caveman:cavecrew-reviewer)",
            "missing Claude repo deny: Agent(caveman:cavecrew-builder)",
            "missing Claude repo deny: Agent(caveman:cavecrew-investigator)",
            "missing Claude repo env: ENABLE_STOP_REVIEW=0",
            "missing Claude repo env: ENABLE_COMMIT_REVIEW=0",
            "missing Claude repo env: SG_PUSH_SWEEP=0",
            "missing Claude repo env: CLAUDE_CODE_ENABLE_TODO_TOOLS=1",
        ]
        self.init("claude")
        result = self.run_checker("claude")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Claude repo", result.stdout)

        path = self.repo / ".claude" / "settings.json"
        settings = json.loads(path.read_text())
        settings["permissions"]["deny"] = [d for d in settings["permissions"]["deny"] if "cavecrew" not in d]
        del settings["env"]
        write(path, json.dumps(settings))
        result = self.run_checker("claude")
        self.assertEqual(result.returncode, 1)
        for wall in walls:
            self.assertIn(f"- {wall}\n", result.stdout)

    def test_a_changed_kit_file_fails(self) -> None:
        self.init()
        path = self.repo / ".dev-loop" / "hooks" / "claude" / "gate.sh"
        path.write_bytes(path.read_bytes() + b"x")
        for harness in ["claude", "codex"]:
            with self.subTest(harness=harness):
                result = self.run_checker(harness)
                self.assertEqual(result.returncode, 1)
                self.assertIn(
                    "- kit file changed locally: .dev-loop/hooks/claude/gate.sh. "
                    "Move the change to dev-loop, then run upgrade",
                    result.stdout,
                )

    def test_a_deleted_kit_file_fails(self) -> None:
        self.init("claude")
        (self.repo / ".claude" / "agents" / "spec-tester.md").unlink()
        result = self.run_checker("claude")
        self.assertEqual(result.returncode, 1)
        self.assertIn("- missing kit file: .claude/agents/spec-tester.md", result.stdout)

    def test_a_user_level_skill_fails(self) -> None:
        self.init()
        self.trust_codex()
        folder = self.home / ".agents" / "skills" / "dev-loop"
        dangling_link = self.home / ".claude" / "skills" / "dev-loop"
        folder.mkdir(parents=True)
        dangling_link.parent.mkdir(parents=True)
        dangling_link.symlink_to(self.home / "nowhere")
        for harness in ["claude", "codex"]:
            with self.subTest(harness=harness):
                result = self.run_checker(harness)
                self.assertEqual(result.returncode, 1)
                for location in [folder, dangling_link]:
                    self.assertIn(
                        f"- user-level dev-loop skill found: {location}. "
                        "Run npx github:tchlyah/dev-loop#<tag> uninstall-global",
                        result.stdout,
                    )

    def edit_codex_config(self, old: str, new: str) -> None:
        config = self.repo / ".codex" / "config.toml"
        text = config.read_text()
        self.assertIn(old, text)
        write(config, text.replace(old, new))

    def assert_codex_failure(self, line: str) -> None:
        result = self.run_checker("codex")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"- {line}\n", result.stdout)

    def test_codex_legacy_sandbox_mode_fails(self) -> None:
        self.init("codex")
        self.edit_codex_config('default_permissions = "dev-workspace"\n',
                               'default_permissions = "dev-workspace"\nsandbox_mode = "workspace-write"\n')
        self.assert_codex_failure("legacy sandbox_mode disables permission-profile enforcement")

    def test_codex_missing_env_filesystem_deny_fails(self) -> None:
        self.init("codex")
        self.edit_codex_config('"**/.env.*" = "deny"\n', "")
        self.assert_codex_failure("missing Codex filesystem deny: **/.env.*")

    def test_codex_missing_implementer_acceptance_wall_fails(self) -> None:
        self.init("codex")
        self.edit_codex_config('"tests/acceptance/**" = "deny"\n', "")
        self.assert_codex_failure("missing Codex role deny in dev-implementer: tests/acceptance/**")

    def test_codex_missing_push_prompt_rule_fails(self) -> None:
        self.init("codex")
        write(
            self.repo / ".codex" / "rules" / "dev-loop.rules",
            'prefix_rule(pattern=["git", "reset", "--hard"], decision="forbidden")\n',
        )
        self.assert_codex_failure('missing git push prompt: decision="prompt"')

    def test_codex_missing_agent_toml_fails(self) -> None:
        self.init("codex")
        path = self.repo / ".codex" / "agents" / "security-reviewer.toml"
        path.unlink()
        self.assert_codex_failure(f"missing {path}")

    def test_codex_missing_stop_hook_event_fails(self) -> None:
        self.init("codex")
        path = self.repo / ".codex" / "hooks.json"
        hooks = json.loads(path.read_text())
        del hooks["hooks"]["Stop"]
        write(path, json.dumps(hooks))
        self.assert_codex_failure("missing Codex hook event: Stop")

    def test_claude_missing_env_glob_deny_fails(self) -> None:
        self.init("claude")
        path = self.repo / ".claude" / "settings.json"
        settings = json.loads(path.read_text())
        settings["permissions"]["deny"].remove("Read(./.env.*)")
        write(path, json.dumps(settings))
        result = self.run_checker("claude")
        self.assertEqual(result.returncode, 1)
        self.assertIn("- missing Claude deny: Read(./.env.*)\n", result.stdout)

class ManifestCheckTests(unittest.TestCase):
    CHANGED = ". Move the change to dev-loop, then run upgrade"

    def manifest(self, repo: Path, files: dict) -> None:
        write(repo / ".dev-loop" / "MANIFEST.json", json.dumps({"version": "1.5.0", "files": files}))

    def digest(self, data: bytes) -> str:
        return "sha256:" + hashlib.sha256(data).hexdigest()

    def test_matching_files_and_link_pass(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root)
            write(repo / "a" / "b.txt", "hello\n")
            (repo / "link").symlink_to("a")
            self.manifest(repo, {"a/b.txt": self.digest(b"hello\n"), "link": "link:a"})
            self.assertEqual(check_setup.check_manifest(repo), [])

    def test_changed_missing_and_wrong_link_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root)
            write(repo / "a.txt", "edited\n")
            (repo / "link").symlink_to("elsewhere")
            self.manifest(
                repo,
                {"a.txt": self.digest(b"hello\n"), "gone.txt": self.digest(b"x"), "link": "link:a"},
            )
            self.assertEqual(
                check_setup.check_manifest(repo),
                [
                    f"kit file changed locally: a.txt{self.CHANGED}",
                    "missing kit file: gone.txt",
                    f"kit file changed locally: link{self.CHANGED}",
                ],
            )

    def test_a_file_entry_that_is_a_symlink_is_changed(self) -> None:
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            repo = Path(root)
            write(Path(outside) / "secret", "hello\n")
            (repo / "a.txt").symlink_to(Path(outside) / "secret")
            self.manifest(repo, {"a.txt": self.digest(b"hello\n")})
            self.assertEqual(check_setup.check_manifest(repo), [f"kit file changed locally: a.txt{self.CHANGED}"])

    def test_unsafe_keys_are_rejected_and_not_read(self) -> None:
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            repo = Path(root) / "repo"
            repo.mkdir()
            write(Path(outside) / "secret", "hello\n")
            (repo / "out").symlink_to(outside)
            keys = [
                "/etc/hosts",
                "../x",
                "a/../b",
                "./a",
                "a//b",
                "a/",
                "",
                "a\\b",
                "out/secret",
            ]
            self.manifest(repo, {key: self.digest(b"hello\n") for key in keys})
            self.assertEqual(
                check_setup.check_manifest(repo),
                [f"invalid kit path in MANIFEST.json: {key}" for key in keys],
            )

    def test_missing_or_invalid_manifest_fails(self) -> None:
        for content in [None, "{not json", json.dumps({"version": "1.5.0"}), json.dumps([1])]:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as root:
                repo = Path(root)
                if content is not None:
                    write(repo / ".dev-loop" / "MANIFEST.json", content)
                self.assertEqual(
                    check_setup.check_manifest(repo), ["missing or invalid .dev-loop/MANIFEST.json"]
                )


def git_in(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "init.defaultBranch=main", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def make_git_repo(root: Path, files: dict[str, str]) -> Path:
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    for path, text in files.items():
        write(repo / path, text)
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-q", "-m", "base")
    return repo


def commit_changes(repo: Path, files: dict[str, str]) -> str:
    base = git_in(repo, "rev-parse", "HEAD").strip()
    for path, text in files.items():
        write(repo / path, text)
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-q", "-m", "feature")
    return base


PY_AGENTS = "stacks: python\n"
MUTMUT_CONFIG = '[tool.mutmut]\nsource_paths = ["src"]\npytest_add_cli_args_test_selection = ["tests/unit"]\n'
MODULE_BASE = """LIMIT = 1


def changed(x):
    return x + 1


def untouched(x):
    return x - 1


class Box:
    size = 2

    def grow(self):
        return self.size * 2

    @staticmethod
    def make():
        return 1


@register
def routed():
    return 2
"""
MODULE_HEAD = (
    MODULE_BASE.replace("LIMIT = 1", "LIMIT = 2")
    .replace("x + 1", "x + 2")
    .replace("size = 2", "size = 3")
    .replace("self.size * 2", "self.size * 3")
    .replace("return 1\n\n\n@register", "return 4\n\n\n@register")
    .replace("    return 2\n", "    return 5\n")
)


class MutationContractTests(unittest.TestCase):
    def test_no_route_runs_mutation_in_the_gate(self) -> None:
        for route in ["claude.md", "codex.md"]:
            with self.subTest(route=route):
                self.assertNotIn("mutation.md", (SKILL_ROOT / "references" / route).read_text())
        mutation = (SKILL_ROOT / "references" / "mutation.md").read_text()
        self.assertIn("select_mutants.py --base", mutation)

    def test_nightly_base_falls_back_to_the_root_commit(self) -> None:
        line = ("BASE=$(git rev-list -1 --before='24 hours ago' main); "
                "python3 .agents/skills/dev-loop/scripts/select_mutants.py "
                '--base "${BASE:-$(git rev-list --max-parents=0 main | tail -n 1)}" --head main --run')
        for heading in ["## Run", "## Nightly CI"]:
            with self.subTest(section=heading):
                self.assertIn(line, test_devloop.section(SKILL_ROOT / "references" / "mutation.md", heading))

    def test_fix_wave_commits_a_test_only_when_it_kills_a_listed_mutant(self) -> None:
        fix = test_devloop.section(SKILL_ROOT / "references" / "mutation.md", "## Fix")
        for value in [
            "home test file",
            "public entry",
            "select_mutants.py --base <report base> --head HEAD --run --only",
            "kills no listed mutant",
        ]:
            with self.subTest(value=value):
                self.assertIn(value, fix)
        self.assertIn("kills one or more listed mutants", (SKILL_ROOT / "references" / "tests.md").read_text())

    def test_fix_wave_writes_one_test_per_function_in_parallel(self) -> None:
        mutation = (SKILL_ROOT / "references" / "mutation.md").read_text()
        for value in ["one unit test per listed function", "in parallel", "Run mutmut once per night", "only_mutate"]:
            with self.subTest(value=value):
                self.assertIn(value, mutation)

    def test_no_route_uses_a_stryker_flag_that_does_not_exist(self) -> None:
        self.assertNotIn("--since", all_instructions())

    def test_reference_names_the_selection_gap_and_the_setup_owner(self) -> None:
        mutation = (SKILL_ROOT / "references" / "mutation.md").read_text()
        for value in ["What the selection does not cover", "Stage 0 owns this setup", "runtime_checkable", "mutants/"]:
            with self.subTest(value=value):
                self.assertIn(value, mutation)
        self.assertIn("references/mutation.md", SKILL.read_text())

    def test_no_em_dash_in_the_mutation_payload(self) -> None:
        for path in [
            SKILL_ROOT / "references" / "mutation.md",
            SELECTOR,
            CHECKER,
        ]:
            with self.subTest(path=path.name):
                self.assertNotIn("\u2014", path.read_text())


class ModelAliasContractTests(unittest.TestCase):
    def test_subagent_models_are_named_by_alias(self) -> None:
        claude = (SKILL_ROOT / "references" / "claude.md").read_text()
        self.assertIn("name them by alias only", claude)

    def test_no_payload_pins_a_full_claude_model_id(self) -> None:
        import re

        pinned = re.compile(r"^model:\s*claude-", re.MULTILINE)
        for path in [*(SKILL_ROOT.parent / "claude" / "agents").glob("*.md"), SKILL, *(SKILL_ROOT / "references").glob("*.md")]:
            with self.subTest(path=path.name):
                self.assertIsNone(pinned.search(path.read_text()))

class PlanToolContractTests(unittest.TestCase):
    def test_skill_keeps_the_checklist_in_the_native_plan_tool(self) -> None:
        section = test_devloop.section(SKILL, "## Detect the stage")
        for value in ["`TaskCreate` item per stage", "subject `Task N: <plan heading>`", "Codex: no plan tool", "`task-start` for each task"]:
            with self.subTest(value=value):
                self.assertIn(value, section)


class FeatureWorktreeContractTests(unittest.TestCase):
    def test_stages_three_to_seven_run_in_the_feature_worktree(self) -> None:
        skill = SKILL.read_text()
        self.assertIn("## Where each stage runs", skill)
        self.assertIn("Stages 3 to 7 run in the feature's own worktree", skill)
        self.assertIn("Never ask the user to change directory.", skill)
        self.assertNotIn(".worktrees/", skill)

    def test_rebase_points_exclude_the_build(self) -> None:
        skill = SKILL.read_text()
        self.assertIn("Never during Stages 4 and 5", skill)
        self.assertIn("git fetch origin`, `git switch main`, `git rebase origin/main`", skill)
        for route in ["claude.md", "codex.md"]:
            with self.subTest(route=route):
                text = (SKILL_ROOT / "references" / route).read_text()
                self.assertIn("First rebase onto `origin/main`", text)

    def test_superseded_oracle_lines_flow_from_spec_to_tests(self) -> None:
        skill = SKILL.read_text()
        self.assertIn("Supersedes: read the earlier specs", skill)
        self.assertIn("Amended by <feature> on <date>", skill)
        self.assertIn("a Supersedes line or an escaped bug", skill)
        self.assertIn("a regression", skill)
        claude_tester = (SKILL_ROOT.parent / "claude" / "agents" / "spec-tester.md").read_text()
        codex_tester = (SKILL_ROOT.parent / "codex" / "agents" / "spec-tester.toml").read_text()
        self.assertIn("Change no other existing test.", claude_tester)
        self.assertIn("Change no other existing test.", codex_tester)

    def test_the_spec_tester_proves_flows_not_combinations(self) -> None:
        claude_tester = (SKILL_ROOT.parent / "claude" / "agents" / "spec-tester.md").read_text()
        codex_tester = (SKILL_ROOT.parent / "codex" / "agents" / "spec-tester.toml").read_text()
        for text in [claude_tester, codex_tester]:
            self.assertIn("Prove each flow once.", text)
            self.assertNotIn("combinations", text)
        self.assertIn("Never dispatch a subagent", claude_tester)

    def test_a_stage_ends_with_a_full_gate(self) -> None:
        self.assertIn(
            "End every stage that changed the repo with a direct gate: `commands.gate` from `dev-loop.json` "
            "when mapped, else `scripts/gate.sh`",
            SKILL.read_text(),
        )

class SelectMutantsTests(unittest.TestCase):
    def select(self, repo: Path, base: str, *extra: str) -> tuple[int, dict, str]:
        result = subprocess.run(
            [sys.executable, str(SELECTOR), "--repo", str(repo), "--head", "HEAD", "--base", base, "--json", *extra],
            text=True,
            capture_output=True,
            check=False,
        )
        payload = json.loads(result.stdout) if result.returncode == 0 else {}
        return result.returncode, payload, result.stderr

    def python_repo(self, root: Path) -> tuple[Path, str]:
        repo = make_git_repo(
            root,
            {
                "AGENTS.md": PY_AGENTS,
                "pyproject.toml": MUTMUT_CONFIG,
                "src/pkg/mod.py": MODULE_BASE,
                "tests/unit/test_mod.py": "def test_x():\n    assert True\n",
            },
        )
        base = commit_changes(
            repo,
            {"src/pkg/mod.py": MODULE_HEAD, "tests/unit/test_mod.py": "def test_x():\n    assert 1\n"},
        )
        return repo, base

    def test_selects_only_changed_functions_and_methods(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo, base = self.python_repo(Path(root))
            code, sel, err = self.select(repo, base)
            self.assertEqual(code, 0, err)
            self.assertEqual(sel["mutmut_root"], ".")
            self.assertEqual(
                sel["patterns"],
                [
                    "pkg.mod.x_changed__mutmut_*",
                    "pkg.mod.x\u01c1Box\u01c1grow__mutmut_*",
                    "pkg.mod.x\u01c1Box\u01c1make__mutmut_*",
                ],
            )

    def test_reports_what_the_selection_does_not_cover(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo, base = self.python_repo(Path(root))
            _, sel, _ = self.select(repo, base)
            gaps = " | ".join(sel["gaps"])
            self.assertIn("src/pkg/mod.py:1 outside any function", gaps)
            self.assertIn("class body outside any method", gaps)
            self.assertIn("decorated function, mutmut skips it", gaps)
            self.assertNotIn("tests/unit", gaps)

    def test_same_diff_gives_the_same_selection(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo, base = self.python_repo(Path(root))
            self.assertEqual(self.select(repo, base)[1], self.select(repo, base)[1])

    def test_mutmut_root_is_the_package_with_the_config(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_git_repo(
                Path(root),
                {
                    "AGENTS.md": PY_AGENTS,
                    "pyproject.toml": "[tool.uv.workspace]\nmembers = [\"backend\"]\n",
                    "backend/pyproject.toml": MUTMUT_CONFIG,
                    "backend/src/pkg/__init__.py": "def hook():\n    return 1\n",
                },
            )
            base = commit_changes(repo, {"backend/src/pkg/__init__.py": "def hook():\n    return 2\n"})
            code, sel, err = self.select(repo, base)
            self.assertEqual(code, 0, err)
            self.assertEqual(sel["mutmut_root"], "backend")
            self.assertEqual(sel["patterns"], ["pkg.x_hook__mutmut_*"])

    def test_pure_deletion_inside_a_function_selects_it(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_git_repo(
                Path(root),
                {
                    "AGENTS.md": PY_AGENTS,
                    "pyproject.toml": MUTMUT_CONFIG,
                    "src/pkg/mod.py": "def trim(x):\n    y = x\n    y = y + 1\n    return y\n",
                },
            )
            base = commit_changes(repo, {"src/pkg/mod.py": "def trim(x):\n    y = x\n    return y\n"})
            _, sel, _ = self.select(repo, base)
            self.assertEqual(sel["patterns"], ["pkg.mod.x_trim__mutmut_*"])

    def test_missing_mutmut_config_is_a_setup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_git_repo(
                Path(root), {"AGENTS.md": PY_AGENTS, "pyproject.toml": "[project]\nname = 'x'\n", "src/a.py": "A = 1\n"}
            )
            base = commit_changes(repo, {"src/a.py": "A = 2\n"})
            code, _, err = self.select(repo, base)
            self.assertEqual(code, 2)
            self.assertIn("Stage 0", err)

    def test_ui_selects_changed_line_ranges_outside_tests(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_git_repo(
                Path(root),
                {
                    "AGENTS.md": "stacks: ui\n",
                    "frontend/stryker.config.json": json.dumps({"mutate": ["src/**/*.ts", "!src/**/*.test.ts"]}),
                    "frontend/src/a.ts": "export const a = 1;\nexport const b = 2;\nexport const c = 3;\n",
                    "frontend/src/a.test.ts": "test('a', () => {});\n",
                    "frontend/src/Screen.tsx": "export const S = 1;\n",
                },
            )
            base = commit_changes(
                repo,
                {
                    "frontend/src/a.ts": "export const a = 1;\nexport const b = 20;\nexport const c = 30;\n",
                    "frontend/src/a.test.ts": "test('a', () => { expect(1); });\n",
                    "frontend/src/Screen.tsx": "export const S = 2;\n",
                },
            )
            code, sel, err = self.select(repo, base)
            self.assertEqual(code, 0, err)
            self.assertEqual(sel["stryker_root"], "frontend")
            self.assertEqual(sel["stryker_ranges"], ["src/a.ts:2-3"])
            self.assertEqual(sel["skipped"], ["frontend/src/Screen.tsx outside the Stryker mutate list"])

    def test_only_critical_modules_are_selected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            config = MUTMUT_CONFIG + 'only_mutate = ["src/pkg/core/*"]\ndo_not_mutate = ["src/pkg/core/proto.py"]\n'
            repo = make_git_repo(
                Path(root),
                {
                    "AGENTS.md": PY_AGENTS,
                    "pyproject.toml": config,
                    "src/pkg/core/score.py": "def score(x):\n    return x > 1\n",
                    "src/pkg/core/proto.py": "def proto(x):\n    return x\n",
                    "src/pkg/api/routes.py": "def route(x):\n    return x\n",
                },
            )
            base = commit_changes(
                repo,
                {
                    "src/pkg/core/score.py": "def score(x):\n    return x >= 1\n",
                    "src/pkg/core/proto.py": "def proto(x):\n    return x + 0\n",
                    "src/pkg/api/routes.py": "def route(x):\n    return x + 0\n",
                },
            )
            code, sel, err = self.select(repo, base)
            self.assertEqual(code, 0, err)
            self.assertEqual(sel["patterns"], ["pkg.core.score.x_score__mutmut_*"])
            self.assertEqual(
                sel["skipped"],
                ["src/pkg/api/routes.py outside only_mutate", "src/pkg/core/proto.py in do_not_mutate"],
            )

    def test_survivors_split_into_decisions_and_messages(self) -> None:
        cases = {
            "message": [
                '-    raise ValueError("bad window")\n+    raise ValueError("XXbad windowXX")\n',
                '-    logger.info("fetched %s", name)\n+    logger.info(None, name)\n',
                '-    return Finding(detail="gap")\n+    return Finding(detail="XXgapXX")\n',
            ],
            "decision": [
                '-    return {"status": "ok"}\n+    return {"XXstatusXX": "ok"}\n',
                '-    if kind == "gauge":\n+    if kind == "XXgaugeXX":\n',
                "-    if count >= limit:\n+    if count > limit:\n",
                "-    return total\n+    return None\n",
            ],
        }
        for expected, diffs in cases.items():
            for diff in diffs:
                with self.subTest(diff=diff):
                    self.assertEqual(select_mutants.classify(diff), expected)

    def test_base_option_sets_the_diff_base(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_git_repo(
                Path(root), {"AGENTS.md": PY_AGENTS, "pyproject.toml": MUTMUT_CONFIG, "src/pkg/mod.py": MODULE_BASE}
            )
            first = commit_changes(repo, {"src/pkg/mod.py": MODULE_BASE.replace("x + 1", "x + 2")})
            second = commit_changes(repo, {"src/pkg/mod.py": MODULE_BASE.replace("x + 1", "x + 2").replace("x - 1", "x - 2")})
            _, older, _ = self.select(repo, first)
            _, newer, _ = self.select(repo, second)
            self.assertEqual(older["patterns"], ["pkg.mod.x_changed__mutmut_*", "pkg.mod.x_untouched__mutmut_*"])
            self.assertEqual(newer["patterns"], ["pkg.mod.x_untouched__mutmut_*"])

    def test_no_mutable_change_selects_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_git_repo(
                Path(root), {"AGENTS.md": PY_AGENTS, "pyproject.toml": MUTMUT_CONFIG, "README.md": "a\n"}
            )
            base = commit_changes(repo, {"README.md": "b\n"})
            code, sel, err = self.select(repo, base)
            self.assertEqual(code, 0, err)
            self.assertEqual((sel["patterns"], sel["stryker_ranges"]), ([], []))

    def test_only_keeps_the_listed_functions_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_git_repo(
                Path(root),
                {
                    "AGENTS.md": "stacks: python, ui\n",
                    "pyproject.toml": MUTMUT_CONFIG,
                    "src/pkg/mod.py": MODULE_BASE,
                    "frontend/stryker.config.json": json.dumps({"mutate": ["src/**/*.ts"]}),
                    "frontend/src/a.ts": "export const a = 1;\n",
                    "frontend/src/b.ts": "export const b = 1;\n",
                },
            )
            base = commit_changes(
                repo,
                {
                    "src/pkg/mod.py": MODULE_HEAD,
                    "frontend/src/a.ts": "export const a = 2;\n",
                    "frontend/src/b.ts": "export const b = 2;\n",
                },
            )
            code, sel, err = self.select(repo, base, "--only", "pkg.mod.x_changed", "src/b.ts")
            self.assertEqual(code, 0, err)
            self.assertEqual(
                (sel["patterns"], sel["stryker_ranges"]), (["pkg.mod.x_changed__mutmut_*"], ["src/b.ts:1-1"])
            )

    def test_only_outside_the_selection_is_a_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo, base = self.python_repo(Path(root))
            code, _, err = self.select(repo, base, "--only", "pkg.mod.x_changed", "pkg.mod.x_gone")
            self.assertEqual(code, 2)
            self.assertIn("pkg.mod.x_gone", err)
            self.assertNotIn("x_changed", err)

    def test_report_names_the_base_and_head_for_the_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo, base = self.python_repo(Path(root))
            head = git_in(repo, "rev-parse", "HEAD").strip()
            result = subprocess.run(
                [sys.executable, str(SELECTOR), "--repo", str(repo), "--head", "HEAD", "--base", base],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines()[:2], [f"base: {base}", f"head: {head}"])


class MutationSetupTests(unittest.TestCase):
    PYPROJECT = (
        '[dependency-groups]\ndev = ["pytest==9.1.1", "mutmut==3.8.0"]\n\n'
        + MUTMUT_CONFIG
        + 'only_mutate = ["src/pkg/core/*"]\n'
    )

    def python_setup(self, root: Path, **overrides: str) -> Path:
        files = {
            "AGENTS.md": PY_AGENTS,
            "dev-loop.json": "{}",
            "pyproject.toml": self.PYPROJECT,
            ".gitignore": "mutants/\n",
        }
        files.update(overrides)
        return make_git_repo(root, files)

    def test_complete_python_setup_passes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(check_setup.check_mutation(self.python_setup(Path(root))), [])

    def test_each_missing_python_piece_fails(self) -> None:
        cases = {
            "pin": ({"pyproject.toml": MUTMUT_CONFIG}, "mutmut pinned"),
            "range pin": (
                {"pyproject.toml": self.PYPROJECT.replace("mutmut==3.8.0", "mutmut>=3.8")},
                "mutmut pinned",
            ),
            "config": ({"pyproject.toml": '[dependency-groups]\ndev = ["mutmut==3.8.0"]\n'}, "[tool.mutmut]"),
            "acceptance": (
                {"pyproject.toml": self.PYPROJECT.replace('["tests/unit"]', '["tests/acceptance"]')},
                "unit tests only",
            ),
            "ignore": ({".gitignore": "\n"}, "not ignored"),
            "critical list": (
                {"pyproject.toml": self.PYPROJECT.replace('only_mutate = ["src/pkg/core/*"]\n', "")},
                "only_mutate",
            ),
        }
        for label, (overrides, expected) in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as root:
                failures = check_setup.check_mutation(self.python_setup(Path(root), **overrides))
                self.assertTrue(any(expected in f for f in failures), failures)

    def test_ui_setup_needs_pinned_stryker_and_ignored_output(self) -> None:
        package = {"devDependencies": {"@stryker-mutator/core": "10.0.0", "@stryker-mutator/vitest-runner": "10.0.0"}}
        with tempfile.TemporaryDirectory() as root:
            repo = make_git_repo(
                Path(root),
                {
                    "AGENTS.md": "stacks: ui\n",
                    "dev-loop.json": "{}",
                    "frontend/stryker.config.json": json.dumps({"mutate": ["src/logic/**/*.ts"]}),
                    "frontend/package.json": json.dumps(package),
                    ".gitignore": ".stryker-tmp/\nreports/\n",
                },
            )
            self.assertEqual(check_setup.check_mutation(repo), [])
            package["devDependencies"]["@stryker-mutator/core"] = "^10.0.0"
            write(repo / "frontend" / "package.json", json.dumps(package))
            self.assertTrue(any("@stryker-mutator/core" in f for f in check_setup.check_mutation(repo)))

    def test_ui_setup_needs_a_json_config_with_a_mutate_list(self) -> None:
        package = {"devDependencies": {"@stryker-mutator/core": "10.0.0", "@stryker-mutator/vitest-runner": "10.0.0"}}
        for label, files, expected in [
            ("mjs", {"frontend/stryker.config.mjs": "export default {};\n"}, "stryker.config.json"),
            ("no mutate", {"frontend/stryker.config.json": "{}"}, "mutate list"),
        ]:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as root:
                base = {
                    "AGENTS.md": "stacks: ui\n",
                    "dev-loop.json": "{}",
                    "frontend/package.json": json.dumps(package),
                    ".gitignore": ".stryker-tmp/\nreports/\n",
                }
                repo = make_git_repo(Path(root), {**base, **files})
                self.assertTrue(any(expected in f for f in check_setup.check_mutation(repo)))

    def test_dev_loop_json_only_repo_gets_the_mutation_checks(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_git_repo(Path(root), {"AGENTS.md": "stacks: python\n", "dev-loop.json": "{}"})
            self.assertTrue(any("mutmut" in f for f in check_setup.check_mutation(repo)))

    def test_repos_without_declared_stacks_are_not_checked(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_git_repo(Path(root), {"dev-loop.json": "{}"})
            self.assertEqual(check_setup.check_mutation(repo), [])


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

    def resolve(self, repo: Path | None = None) -> Path:
        return notes_path.resolve(repo or self.repo, self.env, self.home)

    def set_local(self, repo: Path, target: Path | str) -> None:
        write(repo / "dev-loop.local.json", json.dumps({"notes": str(target)}))

    def test_each_source_wins_over_every_later_source(self) -> None:
        linked = self.linked()
        steps = [
            (lambda: write(linked / "dev-loop.json", json.dumps({"notes": str(self.folder("shared"))})), self.folder("shared")),
            (lambda: self.set_local(self.repo, self.folder("main")), self.folder("main")),
            (lambda: self.set_local(linked, self.folder("tree")), self.folder("tree")),
            (lambda: self.env.update(DEV_LOOP_NOTES=str(self.folder("env"))), self.folder("env")),
        ]
        for apply, expected in steps:
            apply()
            self.assertEqual(self.resolve(linked), expected)

    def test_linked_worktree_resolves_from_main_worktree(self) -> None:
        linked = self.linked()
        self.set_local(self.repo, self.folder("main"))
        self.assertEqual(self.resolve(linked), self.folder("main"))

    def test_local_file_in_linked_worktree_wins_over_main_file(self) -> None:
        linked = self.linked()
        self.set_local(self.repo, self.folder("main"))
        self.set_local(linked, self.folder("tree"))
        self.assertEqual(self.resolve(linked), self.folder("tree"))

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
        write(linked / "dev-loop.json", json.dumps({"notes": "n"}))
        (self.repo / "dev-loop.local.json").unlink()
        self.assertEqual(self.resolve(linked), linked / "n")
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

    def test_bad_local_file_raises_naming_the_file(self) -> None:
        target = self.repo / "dev-loop.local.json"
        write(target, "{not json")
        with self.assertRaises(notes_path.NotesPathError) as ctx:
            self.resolve()
        self.assertTrue(str(ctx.exception).startswith(f"invalid {target}: "))
        write(target, json.dumps({"other": 1}))
        with self.assertRaises(notes_path.NotesPathError) as ctx:
            self.resolve()
        self.assertTrue(str(ctx.exception).startswith(f"invalid {target}: "))

    def test_folder_that_is_not_a_git_repository(self) -> None:
        plain = self.root / "plain"
        plain.mkdir()
        self.env["DEV_LOOP_NOTES"] = str(self.folder("env"))
        self.assertEqual(self.resolve(plain), self.folder("env"))
        del self.env["DEV_LOOP_NOTES"]
        write(plain / "dev-loop.json", json.dumps({"notes": "rel"}))
        (plain / "rel").mkdir()
        self.assertEqual(self.resolve(plain), plain / "rel")

    def test_malformed_lower_priority_file_is_not_read(self) -> None:
        linked = self.linked()
        write(self.repo / "dev-loop.local.json", "{not json")
        self.env["DEV_LOOP_NOTES"] = str(self.folder("env"))
        self.assertEqual(self.resolve(linked), self.folder("env"))
        del self.env["DEV_LOOP_NOTES"]
        self.set_local(linked, self.folder("tree"))
        self.assertEqual(self.resolve(linked), self.folder("tree"))

    def test_shared_file_without_notes_falls_through(self) -> None:
        write(self.repo / "dev-loop.json", json.dumps({"other": 1}))
        with self.assertRaises(notes_path.NotesPathError) as ctx:
            self.resolve()
        self.assertTrue(str(ctx.exception).startswith(f"no notes path for {self.repo}."))

    def test_notes_path_has_no_vault_fallback(self) -> None:
        write(self.home / ".agents" / "dev-loop.config.json", json.dumps({"vault": str(self.folder("vault"))}))
        write(self.repo / "AGENTS.md", "vault_notes: fb\n")
        (self.folder("vault") / "fb").mkdir()
        with self.assertRaises(notes_path.NotesPathError) as ctx:
            self.resolve()
        self.assertTrue(str(ctx.exception).startswith(f"no notes path for {self.repo}."))

    def run_main(self, env_notes: str | None) -> subprocess.CompletedProcess:
        env = {**os.environ, "HOME": str(self.home)}
        env.pop("DEV_LOOP_NOTES", None)
        if env_notes is not None:
            env["DEV_LOOP_NOTES"] = env_notes
        return subprocess.run(
            [sys.executable, str(NOTES_PATH), "--repo", str(self.repo)],
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
