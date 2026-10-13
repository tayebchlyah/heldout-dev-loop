#!/usr/bin/env python3
"""Regression and setup tests for the shared Claude/Codex dev-loop skill."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILL = SKILL_ROOT / "SKILL.md"
CHECKER = SKILL_ROOT / "scripts" / "check_setup.py"
CODEX_ASSETS = SKILL_ROOT.parent / "codex"
CLAUDE_HOOKS = SKILL_ROOT.parent / "claude" / "hooks"
SELECTOR = SKILL_ROOT / "scripts" / "select_mutants.py"
NOTES_PATH = SKILL_ROOT / "scripts" / "notes_path.py"

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


def all_instructions() -> str:
    parts = [SKILL.read_text()]
    refs = SKILL_ROOT / "references"
    if refs.exists():
        parts.extend(path.read_text() for path in sorted(refs.glob("*.md")))
    return "\n".join(parts)


def write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class ConfigCheckTests(unittest.TestCase):
    def test_missing_config_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            failures = check_setup.check_config(home)
            self.assertIn("missing dev-loop config", " ".join(failures))
            self.assertIn("Run npx github:tchlyah/dev-loop#<tag> --global", " ".join(failures))

    def test_config_with_null_vault_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = home / ".agents" / "dev-loop.config.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"vault": None, "user_name": "ada"}))
            failures = check_setup.check_config(home)
            joined = " ".join(failures)
            self.assertIn("missing vault in dev-loop config", joined)
            self.assertIn("Rerun npx github:tchlyah/dev-loop#<tag> --global --vault=<path>", joined)
            self.assertNotIn("missing dev-loop config", joined)

    def test_config_with_absent_vault_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = home / ".agents" / "dev-loop.config.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"vault": str(home / "nope"), "user_name": "ada"}))
            failures = check_setup.check_config(home)
            self.assertIn("vault path does not exist", " ".join(failures))

    def test_valid_config_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            vault = home / "vault"
            vault.mkdir()
            path = home / ".agents" / "dev-loop.config.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"vault": str(vault), "user_name": "ada"}))
            self.assertEqual(check_setup.check_config(home), [])


class SkillContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = SKILL.read_text()
        cls.instructions = all_instructions()

    def test_frontmatter_and_trigger_survive(self) -> None:
        self.assertIn("name: dev-loop", self.skill)
        self.assertIn("at the start of any coding session", self.skill)
        self.assertIn("vault_notes", self.skill)

    def test_all_eight_stages_remain_ordered(self) -> None:
        positions = [self.skill.index(f"## {number} ") for number in range(8)]
        self.assertEqual(positions, sorted(positions))

    def test_one_stage_per_invocation_remains(self) -> None:
        self.assertIn("Run one stage per invocation", self.skill)
        self.assertIn("Never chain stages", self.skill)

    def test_claude_bootstrap_contract_remains(self) -> None:
        required = [
            ".dev-loop/hooks/claude/gate.sh",
            ".claude/agents/implementer.md",
            ".claude/agents/spec-tester.md",
            ".claude/settings.json",
            "mattpocock-skills:domain-modeling",
            "mattpocock-skills:grilling",
            "superpowers:brainstorming",
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
            "superpowers:finishing-a-development-branch",
            "Option 1 is rebase",
            "Never invoke `superpowers:verification-before-completion`",
        ]
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, self.instructions)

    def test_held_out_authorship_contract_remains(self) -> None:
        self.assertIn("spec-tester", self.instructions)
        self.assertIn("implementer", self.instructions)
        self.assertIn("Plan never references `tests/acceptance/`", self.instructions)
        self.assertIn("Tell the user to run `/clear`", self.instructions)

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
        ]:
            with self.subTest(value=value):
                self.assertIn(value, self.instructions)

    def test_claude_worktree_reviewers_pin_the_sha(self) -> None:
        claude = (SKILL_ROOT / "references" / "claude.md").read_text()
        self.assertIn("isolation: worktree", claude)
        self.assertIn("--detach GATE_SHA", claude)
        self.assertNotIn("Rerun step 1.", claude)

    def test_setup_checker_exists(self) -> None:
        self.assertTrue(CHECKER.is_file())

    def test_codex_agent_assets_parse_and_preserve_roles(self) -> None:
        expected = {
            "implementer.toml": ("implementer", "dev-implementer"),
            "spec-tester.toml": ("spec_tester", "dev-spec-tester"),
            "task-reviewer.toml": ("task_reviewer", "dev-read-only"),
            "simplifier.toml": ("simplifier", "dev-workspace"),
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

    def test_session_start_routes_only_declared_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root)
            write(repo / "AGENTS.md", "vault_notes: Software/Test.md\n")
            routed = self.run_hook("session-start.sh", {"cwd": str(repo)})
            write(repo / "AGENTS.md", "# No workflow\n")
            ignored = self.run_hook("session-start.sh", {"cwd": str(repo)})
            self.assertIn("dev-loop skill", routed.stdout)
            self.assertEqual(ignored.stdout, "")
            (repo / "AGENTS.md").unlink()
            self.assertEqual(self.run_hook("session-start.sh", {"cwd": str(repo)}).stdout, "")
            write(repo / "dev-loop.json", "{}\n")
            by_config = self.run_hook("session-start.sh", {"cwd": str(repo)})
            self.assertIn("dev-loop skill", by_config.stdout)

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


class ClaudeGitGuardPushTests(unittest.TestCase):
    """A plain push of a feature branch runs; a push that rewrites or reaches main asks."""

    def setUp(self) -> None:
        self.repo = tempfile.mkdtemp()
        for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "i"],
                     ["tag", "v1.0"], ["checkout", "-q", "-b", "feat"]):
            subprocess.run(["git", "-C", self.repo, *args], check=True,
                           env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        # A second repo on main. Its path sorts after self.repo, so a guard that checks
        # only the first possible repo checks the feature one.
        self.main_repo = self.repo + "-main"
        os.makedirs(self.main_repo)
        for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "i"]):
            subprocess.run(["git", "-C", self.main_repo, *args], check=True,
                           env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})

    def tearDown(self) -> None:
        shutil.rmtree(self.repo)
        shutil.rmtree(self.main_repo)

    def decision(self, command: str, branch: str = "feat", env: dict | None = None) -> str:
        subprocess.run(["git", "-C", self.repo, "checkout", "-q", branch], check=True)
        result = subprocess.run(
            ["bash", str(CLAUDE_HOOKS / "git-guard.sh")],
            input=json.dumps({"cwd": self.repo, "tool_input": {"command": command}}),
            text=True, capture_output=True, check=False, env=env,
        )
        return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] if result.stdout else "allow"

    def test_plain_feature_branch_push_runs(self) -> None:
        for command in ("git push", "git push origin feat", "git push -u origin feat", "git push origin HEAD"):
            self.assertEqual(self.decision(command), "allow", command)

    def test_rewriting_deleting_tag_or_main_push_asks(self) -> None:
        for command in ("git push --force origin feat", "git push --force-with-lease origin feat",
                        "git push origin +feat", "git push origin :feat", "git push origin --delete feat",
                        "git push origin main", "git push origin feat:main", "git push origin v1.0",
                        "git push --tags", "git push --mirror", "git push --all"):
            self.assertEqual(self.decision(command), "ask", command)

    def test_bare_push_from_main_asks(self) -> None:
        for command in ("git push", "git push origin", "git push origin HEAD"):
            self.assertEqual(self.decision(command, "main"), "ask", command)

    def test_quotes_short_flags_and_global_options_do_not_walk_past(self) -> None:
        for command in ("git push -uf origin feat", "git push origin \"main\"", "git push origin 'main'",
                        "git push origin feat:\"main\"", "git -c a=b push origin feat",
                        "GIT_CONFIG_COUNT=1 git push origin feat", "git --no-pager push origin main",
                        "git push origin feat && git push origin main", "sh -c 'git push origin main'",
                        "git push origin 'main", "git push origin @:main"):
            self.assertEqual(self.decision(command), "ask", command)

    def test_wrapped_expanded_fed_or_drifted_push_asks(self) -> None:
        for command in ("if git push origin main; then :; fi", "timeout 9 git push origin main",
                        "echo main | xargs git push origin", "B=main; git push origin $B",
                        "git push origin $(echo main)", "git push origin $'\\x6dain'",
                        "git checkout main && git push", "git tag v2 && git push origin v2",
                        "git config remote.origin.push '+refs/heads/*:refs/heads/*' && git push",
                        "git -c alias.x='push -f' x", "ssh host 'git push origin main'"):
            self.assertEqual(self.decision(command), "ask", command)

    def test_everyday_feature_flows_still_run(self) -> None:
        for command in ("git checkout -b feat2 && git push -u origin feat2", "git status && git log --oneline -3",
                        "git commit -m 'fix login' && git push"):
            self.assertEqual(self.decision(command), "allow", command)

    def test_a_cd_after_a_semicolon_or_an_assignment_moves_the_push(self) -> None:
        main = self.main_repo
        for command in (f"cd {main}; git push", f"X=1 cd {main} && git push", f"cd {main} & git push"):
            self.assertEqual(self.decision(command), "ask", command)
        self.assertEqual(self.decision(f"cd {main}; git status"), "allow")

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
        for command in (f"(cd {main} && case x in x) git push;; esac)",
                        f"(cd {main}; case x in x) git push;; esac)",
                        f"(cd {main} && echo \\) && git push)"):
            self.assertEqual(self.decision(command), "ask", command)

    def test_a_cd_inside_a_subshell_does_not_move_the_push(self) -> None:
        # The push runs in self.repo on main, not in the feature repo the subshell visits.
        with tempfile.TemporaryDirectory() as feature:
            for args in (["init", "-q", "-b", "feat"], ["commit", "-q", "--allow-empty", "-m", "i"]):
                subprocess.run(["git", "-C", feature, *args], check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            for command in (f"(cd {feature}; true); git push", f"echo $(cd {feature}; true); git push",
                            f"(cd {feature} && true); git push", f"echo $(cd {feature} && true) && git push",
                            "echo " + chr(96) + f"cd {feature} && true" + chr(96) + "; git push"):
                self.assertEqual(self.decision(command, "main"), "ask", command)

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
            for command in (f'echo ";" cd {feature}; git push', f"echo \\; cd {feature}; git push",
                            f"true # ; cd {feature}\ngit push", f"true # && cd {feature}\ngit push",
                            f"cat <<EOF\ncd {feature}\nEOF\ngit push", f"cat <<EOF\ncd {feature} && true\nEOF\ngit push",
                            f"cd {feature} extra; git push",
                            f"(echo \\) && cd {feature} && echo \\( && true); git push",
                            f"(echo ')' && cd {feature} && echo '(' && true); git push",
                            f"(echo \\) && cd {feature} && echo \\{bt} && true); git push",
                            f"(case x in x) cd {feature} && true;; esac; echo \\( ); git push",
                            f'echo "&&" cd {feature} && git push', f"echo '&&' cd {feature} && git push"):
                self.assertEqual(self.decision(command, "main"), "ask", command)

    def test_a_cd_or_dash_c_the_push_may_not_follow_asks(self) -> None:
        # Converted from the vault cases: the session is on main, the line visits a
        # feature repo, and the push asks unless it surely runs in the feature repo.
        with tempfile.TemporaryDirectory() as feature:
            for args in (["init", "-q", "-b", "feat"], ["commit", "-q", "--allow-empty", "-m", "i"]):
                subprocess.run(["git", "-C", feature, *args], check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
            bt, rel = chr(96), "../" + os.path.basename(feature)
            for command in (f"cd {feature}; git push", f"cd {rel}; git push", f"(cd {feature}); git push",
                            f"(cd {feature}) && git push", f"D={feature}; cd $D && git push",
                            f"cd {feature} && cd \"$OLDPWD\" && git push", f"cd {feature} && cd - && git push",
                            f"(echo \\) && cd {feature} && true); git push",
                            f"echo $(echo \\) && cd {feature} && true); git push",
                            f"echo {bt}cd {feature}; true{bt}; git push", "sudo -u git git push"):
                self.assertEqual(self.decision(command, "main"), "ask", command)
            for command in (f"cd {feature} && git push", f"cd {feature} && (git status) && git push",
                            f"git -C {feature} push"):
                self.assertEqual(self.decision(command, "main"), "allow", command)
        main = os.path.basename(self.main_repo)
        for command in (f"cd {self.main_repo} && git push", f"git -C {self.main_repo} push",
                        f"# save\ngit -C {self.main_repo} push", f"git -C ../{main} push"):
            self.assertEqual(self.decision(command), "ask", command)

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
            for command in (f'git -C "{tilde_main}" push', f'cd "{tilde_main}" && git push',
                            "cd ~/iCloud~md~obsidian/main && git push"):
                self.assertEqual(self.decision(command, env=env), "ask", command)
            self.assertEqual(self.decision("cd ~/feature && git push", "main", env=env), "ask")
            self.assertEqual(self.decision("cd ~/feature && git push", env=env), "allow")

    def test_an_unparseable_git_line_asks(self) -> None:
        # A heredoc with an apostrophe fails the parser.
        for command in ("python3 - <<'EOF'\nprint(\"it's the git log\")\nEOF",
                        "python3 - <<'EOF'\nprint(\"it's the git log\")\nEOF\ngit push"):
            self.assertEqual(self.decision(command), "ask", command)

    def test_remote_config_that_reaches_other_branches_asks(self) -> None:
        subprocess.run(["git", "-C", self.repo, "config", "remote.origin.push", "+refs/heads/*:refs/heads/*"], check=True)
        self.assertEqual(self.decision("git push"), "ask")
        self.assertEqual(self.decision("git push origin feat"), "allow")

    def test_a_push_alias_asks(self) -> None:
        subprocess.run(["git", "-C", self.repo, "config", "alias.p", "push --force"], check=True)
        self.assertEqual(self.decision("git p"), "ask")
        self.assertEqual(self.decision("git commit -m 'push fix'"), "allow")


class CodexGitGuardPushTests(ClaudeGitGuardPushTests):
    """The Codex guard holds the same push rule. Codex cannot ask, so every ask is a deny."""

    def decision(self, command: str, branch: str = "feat", env: dict | None = None) -> str:
        subprocess.run(["git", "-C", self.repo, "checkout", "-q", branch], check=True)
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


class SetupCheckerTests(unittest.TestCase):
    def run_checker(self, harness: str, home: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(CHECKER),
                "--harness",
                harness,
                "--home",
                str(home),
                "--repo",
                str(home / "repo"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def write_valid_config(self, home: Path) -> None:
        vault = home / "vault"
        vault.mkdir(exist_ok=True)
        write(
            home / ".agents" / "dev-loop.config.json",
            json.dumps({"vault": str(vault), "user_name": "ada", "harnesses": ["claude", "codex"]}),
        )

    def make_valid_claude(self, home: Path) -> None:
        self.write_valid_config(home)
        for name in ["gate.sh", "gate-snapshot.sh", "tree-hash.sh", "deny-path.sh", "deny-acceptance-to-subagents.sh", "git-guard.sh"]:
            write(home / ".claude" / "hooks" / name, "#!/bin/sh\n")
        write(home / ".claude" / "agents" / "implementer.md", "name: implementer\n")
        write(home / ".claude" / "agents" / "spec-tester.md", "name: spec-tester\n")
        write(home / "repo" / "AGENTS.md", "vault_notes: Software/Test\n")
        write(
            home / "repo" / ".claude" / "settings.json",
            json.dumps(
                {
                    "permissions": {
                        "deny": [
                            "Agent(caveman:cavecrew-reviewer)",
                            "Agent(caveman:cavecrew-builder)",
                            "Agent(caveman:cavecrew-investigator)",
                        ]
                    },
                    "env": {
                        "ENABLE_STOP_REVIEW": "0",
                        "ENABLE_COMMIT_REVIEW": "0",
                        "SG_PUSH_SWEEP": "0",
                    },
                }
            ),
        )
        write(
            home / ".claude" / "settings.json",
            json.dumps(
                {
                    "permissions": {
                        "deny": [
                            "Skill(superpowers:verification-before-completion)",
                            "Read(./.env)",
                            "Read(./.env.*)",
                        ]
                    },
                    "hooks": {"SessionStart": [], "Stop": [], "SubagentStart": [], "SubagentStop": [], "PreToolUse": []},
                }
            ),
        )

    def make_valid_codex(self, home: Path) -> None:
        self.write_valid_config(home)
        for name in ["gate.sh", "git-guard.sh", "deny-path.sh", "session-start.sh"]:
            write(home / ".codex" / "hooks" / name, "#!/bin/sh\n")
        agent_profiles = {
            "implementer": "dev-implementer",
            "spec-tester": "dev-spec-tester",
            "task-reviewer": "dev-read-only",
            "simplifier": "dev-workspace",
            "final-reviewer": "dev-read-only",
            "security-reviewer": "dev-read-only",
        }
        for name, profile in agent_profiles.items():
            write(
                home / ".codex" / "agents" / f"{name}.toml",
                f'name = "{name.replace("-", "_")}"\ndescription = "test"\n'
                f'developer_instructions = "test"\ndefault_permissions = "{profile}"\n',
            )
        write(
            home / ".codex" / "config.toml",
            'default_permissions = "dev-workspace"\n'
            '[features]\nhooks = true\n'
            '[permissions.dev-workspace]\nextends = ":workspace"\n'
            '[permissions.dev-workspace.filesystem.":workspace_roots"]\n'
            '"**/.env" = "deny"\n"**/.env.*" = "deny"\n'
            '[permissions.dev-read-only]\nextends = ":read-only"\n'
            '[permissions.dev-read-only.filesystem.":workspace_roots"]\n'
            '"**/.env" = "deny"\n"**/.env.*" = "deny"\n'
            '[permissions.dev-implementer]\nextends = "dev-workspace"\n'
            '[permissions.dev-implementer.filesystem.":workspace_roots"]\n'
            '"tests/acceptance" = "deny"\n"tests/acceptance/**" = "deny"\n'
            '[permissions.dev-spec-tester]\nextends = "dev-workspace"\n'
            '[permissions.dev-spec-tester.filesystem.":workspace_roots"]\n'
            '"src" = "deny"\n"src/**" = "deny"\n"app" = "deny"\n"app/**" = "deny"\n'
            '"backend" = "deny"\n"backend/**" = "deny"\n"frontend" = "deny"\n"frontend/**" = "deny"\n'
            f'[hooks.state."{home}/.codex/hooks.json:pre_tool_use:0:0"]\ntrusted_hash = "sha256:test"\n'
            f'[hooks.state."{home}/.codex/hooks.json:session_start:0:0"]\ntrusted_hash = "sha256:test"\n'
            f'[hooks.state."{home}/.codex/hooks.json:user_prompt_submit:0:0"]\ntrusted_hash = "sha256:test"\n'
            f'[hooks.state."{home}/.codex/hooks.json:subagent_stop:0:0"]\ntrusted_hash = "sha256:test"\n'
            f'[hooks.state."{home}/.codex/hooks.json:stop:0:0"]\ntrusted_hash = "sha256:test"\n',
        )
        write(
            home / ".codex" / "hooks.json",
            json.dumps({"hooks": {"SessionStart": [], "PreToolUse": [], "Stop": [], "SubagentStop": []}}),
        )
        write(
            home / ".codex" / "rules" / "default.rules",
            'prefix_rule(pattern=[["git", "/usr/bin/git"], "push"], decision="prompt", match=["git push"])\n'
            'prefix_rule(pattern=["git", "-C"], decision="prompt", match=["git -C /tmp/repo push"])\n'
            'prefix_rule(pattern=[["git", "/usr/bin/git"], "reset", "--hard"], decision="forbidden", match=["git reset --hard"])\n',
        )
        for name in ["domain-modeling", "grilling", "codebase-design", "code-review", "research", "handoff"]:
            write(home / ".agents" / "skills" / name / "SKILL.md", f"name: {name}\n")

    def test_valid_claude_setup_passes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            self.make_valid_claude(home)
            result = self.run_checker("claude", home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_claude_gate_fails(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            self.make_valid_claude(home)
            (home / ".claude" / "hooks" / "gate.sh").unlink()
            result = self.run_checker("claude", home)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("gate.sh", result.stdout)

    def test_missing_claude_repo_controls_fail(self) -> None:
        required = [
            "Agent(caveman:cavecrew-reviewer)",
            "Agent(caveman:cavecrew-builder)",
            "Agent(caveman:cavecrew-investigator)",
            "ENABLE_STOP_REVIEW",
            "ENABLE_COMMIT_REVIEW",
            "SG_PUSH_SWEEP",
        ]
        for missing in required:
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as root:
                home = Path(root)
                self.make_valid_claude(home)
                path = home / "repo" / ".claude" / "settings.json"
                settings = json.loads(path.read_text())
                if missing.startswith("Agent("):
                    settings["permissions"]["deny"].remove(missing)
                else:
                    del settings["env"][missing]
                write(path, json.dumps(settings))
                result = self.run_checker("claude", home)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(missing, result.stdout)

    def test_valid_codex_setup_passes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            self.make_valid_codex(home)
            result = self.run_checker("codex", home)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_codex_hooks_disabled_fails(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            self.make_valid_codex(home)
            config = (home / ".codex" / "config.toml").read_text().replace("hooks = true", "hooks = false")
            write(home / ".codex" / "config.toml", config)
            result = self.run_checker("codex", home)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("hooks", result.stdout)

    def test_codex_untrusted_hook_fails(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            self.make_valid_codex(home)
            config_path = home / ".codex" / "config.toml"
            config = config_path.read_text().replace(":stop:0:0", ":ignored:0:0")
            write(config_path, config)
            result = self.run_checker("codex", home)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("trusted Codex hook hash: stop", result.stdout)

    def test_missing_codex_agent_fails(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            self.make_valid_codex(home)
            (home / ".codex" / "agents" / "spec-tester.toml").unlink()
            result = self.run_checker("codex", home)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("spec-tester.toml", result.stdout)

    def test_missing_codex_review_agent_fails(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            self.make_valid_codex(home)
            (home / ".codex" / "agents" / "security-reviewer.toml").unlink()
            result = self.run_checker("codex", home)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("security-reviewer.toml", result.stdout)

    def test_missing_codex_skill_fails(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            self.make_valid_codex(home)
            (home / ".agents" / "skills" / "grilling" / "SKILL.md").unlink()
            result = self.run_checker("codex", home)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("grilling", result.stdout)

    def test_codex_legacy_sandbox_fails(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            self.make_valid_codex(home)
            config = (home / ".codex" / "config.toml").read_text() + 'sandbox_mode = "workspace-write"\n'
            write(home / ".codex" / "config.toml", config)
            result = self.run_checker("codex", home)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("sandbox_mode", result.stdout)

    def test_codex_missing_env_deny_fails(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            self.make_valid_codex(home)
            config = (home / ".codex" / "config.toml").read_text().replace('"**/.env.*" = "deny"\n', "")
            write(home / ".codex" / "config.toml", config)
            result = self.run_checker("codex", home)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(".env.*", result.stdout)

    def test_codex_missing_agent_path_wall_fails(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            self.make_valid_codex(home)
            config = home / ".codex" / "config.toml"
            write(config, config.read_text().replace('"tests/acceptance/**" = "deny"\n', ""))
            result = self.run_checker("codex", home)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("tests/acceptance", result.stdout)

    def test_codex_missing_stop_gate_fails(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            self.make_valid_codex(home)
            hooks = json.loads((home / ".codex" / "hooks.json").read_text())
            del hooks["hooks"]["Stop"]
            write(home / ".codex" / "hooks.json", json.dumps(hooks))
            result = self.run_checker("codex", home)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Stop", result.stdout)

    def test_missing_claude_env_deny_fails(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            self.make_valid_claude(home)
            settings = json.loads((home / ".claude" / "settings.json").read_text())
            settings["permissions"]["deny"].remove("Read(./.env.*)")
            write(home / ".claude" / "settings.json", json.dumps(settings))
            result = self.run_checker("claude", home)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Read(./.env.*)", result.stdout)

    def test_a_legacy_claude_failure_prints_once(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            self.make_valid_claude(home)
            (home / ".claude" / "hooks" / "gate.sh").unlink()
            result = self.run_checker("claude", home)
            self.assertEqual(result.stdout.count(f"- missing {home / '.claude' / 'hooks' / 'gate.sh'}\n"), 1, result.stdout)

    def test_missing_push_prompt_fails(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            self.make_valid_codex(home)
            write(
                home / ".codex" / "rules" / "default.rules",
                'prefix_rule(pattern=["git", "reset", "--hard"], decision="forbidden")\n',
            )
            result = self.run_checker("codex", home)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("git push", result.stdout)


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
        result = subprocess.run(
            ["node", str(INSTALLER), "init", "--yes", f"--harness={harness}"],
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
        for event in ["session_start", "pre_tool_use", "stop", "subagent_stop"]:
            lines += [f'[hooks.state."{main}/.codex/hooks.json:{event}:0:0"]', 'trusted_hash = "sha256:test"']
        write(self.home / ".codex" / "config.toml", "\n".join(lines) + "\n")
        for name in check_setup.CODEX_SKILLS:
            write(self.home / ".agents" / "skills" / name / "SKILL.md", f"name: {name}\n")

    def test_claude_project_passes(self) -> None:
        self.init("claude")
        result = self.run_checker("claude")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("claude: PASS", result.stdout)

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
        walls = [
            "missing Claude repo deny: Agent(caveman:cavecrew-reviewer)",
            "missing Claude repo deny: Agent(caveman:cavecrew-builder)",
            "missing Claude repo deny: Agent(caveman:cavecrew-investigator)",
            "missing Claude repo env: ENABLE_STOP_REVIEW=0",
            "missing Claude repo env: ENABLE_COMMIT_REVIEW=0",
            "missing Claude repo env: SG_PUSH_SWEEP=0",
        ]
        self.init("claude")
        write(self.repo / "AGENTS.md", "vault_notes: Software/Test\n")
        result = self.run_checker("claude")
        self.assertEqual(result.returncode, 1)
        for wall in walls:
            self.assertIn(f"- {wall}\n", result.stdout)

        path = self.repo / ".claude" / "settings.json"
        settings = json.loads(path.read_text())
        settings["permissions"]["deny"] += [
            "Agent(caveman:cavecrew-reviewer)",
            "Agent(caveman:cavecrew-builder)",
            "Agent(caveman:cavecrew-investigator)",
        ]
        settings["env"] = {"ENABLE_STOP_REVIEW": "0", "ENABLE_COMMIT_REVIEW": "0", "SG_PUSH_SWEEP": "0"}
        write(path, json.dumps(settings))
        result = self.run_checker("claude")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Claude repo", result.stdout)

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

    def test_without_dev_loop_json_the_global_checks_run(self) -> None:
        result = self.run_checker("claude")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing dev-loop config", result.stdout)


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


PY_AGENTS = "vault_notes: Software/Test\nstacks: python\n"
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
    def test_both_routes_run_the_shipped_selector(self) -> None:
        for route in ["claude.md", "codex.md"]:
            with self.subTest(route=route):
                self.assertIn("[mutation.md](mutation.md)", (SKILL_ROOT / "references" / route).read_text())
        mutation = (SKILL_ROOT / "references" / "mutation.md").read_text()
        self.assertIn("select_mutants.py --head GATE_SHA --run", mutation)

    def test_fix_wave_writes_one_test_per_function_in_parallel(self) -> None:
        mutation = (SKILL_ROOT / "references" / "mutation.md").read_text()
        for value in ["one unit test per listed function", "in parallel", "Run mutmut once per feature", "only_mutate"]:
            with self.subTest(value=value):
                self.assertIn(value, mutation)
        for route in ["claude.md", "codex.md"]:
            with self.subTest(route=route):
                self.assertIn("mutation.md) section Fix", (SKILL_ROOT / "references" / route).read_text())

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
        self.assertIn("End every stage that changed the repo with a direct `scripts/gate.sh`", SKILL.read_text())

class SelectMutantsTests(unittest.TestCase):
    def select(self, repo: Path, base: str) -> tuple[int, dict, str]:
        result = subprocess.run(
            [sys.executable, str(SELECTOR), "--repo", str(repo), "--head", "HEAD", "--base", base, "--json"],
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
                    "AGENTS.md": "vault_notes: Software/Test\nstacks: ui\n",
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

    def test_no_mutable_change_selects_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_git_repo(
                Path(root), {"AGENTS.md": PY_AGENTS, "pyproject.toml": MUTMUT_CONFIG, "README.md": "a\n"}
            )
            base = commit_changes(repo, {"README.md": "b\n"})
            code, sel, err = self.select(repo, base)
            self.assertEqual(code, 0, err)
            self.assertEqual((sel["patterns"], sel["stryker_ranges"]), ([], []))


class MutationSetupTests(unittest.TestCase):
    PYPROJECT = (
        '[dependency-groups]\ndev = ["pytest==9.1.1", "mutmut==3.8.0"]\n\n'
        + MUTMUT_CONFIG
        + 'only_mutate = ["src/pkg/core/*"]\n'
    )

    def python_setup(self, root: Path, **overrides: str) -> Path:
        files = {
            "AGENTS.md": PY_AGENTS,
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
                    "AGENTS.md": "vault_notes: Software/Test\nstacks: ui\n",
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
                    "AGENTS.md": "vault_notes: Software/Test\nstacks: ui\n",
                    "frontend/package.json": json.dumps(package),
                    ".gitignore": ".stryker-tmp/\nreports/\n",
                }
                repo = make_git_repo(Path(root), {**base, **files})
                self.assertTrue(any(expected in f for f in check_setup.check_mutation(repo)))

    def test_repos_without_declared_stacks_are_not_checked(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = make_git_repo(Path(root), {"AGENTS.md": "vault_notes: Software/Test\n"})
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
        write(self.home / ".agents" / "dev-loop.config.json", json.dumps({"vault": str(self.folder("vault"))}))
        write(linked / "AGENTS.md", "vault_notes: fb\n")
        (self.folder("vault") / "fb").mkdir()
        self.assertEqual(self.resolve(linked), self.folder("vault") / "fb")
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
        write(self.repo / "dev-loop.json", json.dumps({"notes": ""}))
        write(self.home / ".agents" / "dev-loop.config.json", json.dumps({"vault": str(self.folder("vault"))}))
        write(self.repo / "AGENTS.md", "vault_notes: fb\n")
        (self.folder("vault") / "fb").mkdir()
        self.assertEqual(self.resolve(), self.folder("vault") / "fb")

    def test_missing_legacy_folder_raises_naming_the_source(self) -> None:
        write(self.home / ".agents" / "dev-loop.config.json", json.dumps({"vault": str(self.folder("vault"))}))
        write(self.repo / "AGENTS.md", "vault_notes: fb\n")
        with self.assertRaises(notes_path.NotesPathError) as ctx:
            self.resolve()
        self.assertEqual(
            str(ctx.exception),
            f"notes folder does not exist: {self.folder('vault') / 'fb'} (from dev-loop.config.json and AGENTS.md)."
            " Create it or change the path in dev-loop.config.json and AGENTS.md",
        )

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
