#!/usr/bin/env python3
"""Regression and setup tests for the shared Claude/Codex dev-loop skill."""

from __future__ import annotations

import importlib.util
import json
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

_spec = importlib.util.spec_from_file_location("check_setup", CHECKER)
check_setup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_setup)


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

    def test_config_with_null_vault_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = home / ".agents" / "dev-loop.config.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"vault": None, "user_name": "ada"}))
            failures = check_setup.check_config(home)
            joined = " ".join(failures)
            self.assertIn("missing vault in dev-loop config", joined)
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
        self.assertIn("Run ONE stage per invocation", self.skill)
        self.assertIn("Never chain stages", self.skill)

    def test_claude_bootstrap_contract_remains(self) -> None:
        required = [
            "~/.claude/hooks/gate.sh",
            "~/.claude/agents/implementer.md",
            "~/.claude/agents/spec-tester.md",
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
        self.assertIn("~/.codex/agents/implementer.toml", self.instructions)
        self.assertIn("~/.codex/agents/spec-tester.toml", self.instructions)
        self.assertIn("~/.codex/agents/task-reviewer.toml", self.instructions)

    def test_codex_branch_uses_real_codex_operations(self) -> None:
        codex = (SKILL_ROOT / "references" / "codex.md").read_text()
        self.assertIn("Codex `/review`", codex)
        self.assertIn("browser or computer-use tools", codex)
        self.assertNotIn("`/code-review medium`", codex)
        self.assertNotIn("`/security-review`", codex)
        self.assertNotIn("`/verify`", codex)

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

    def test_git_guard_blocks_vault_git_write_but_allows_status(self) -> None:
        # git-guard.sh reads the vault path at run time from
        # ~/.agents/dev-loop.config.json, so point HOME at a scratch
        # directory that carries that config for the hook subprocess.
        with tempfile.TemporaryDirectory() as home:
            vault = os.path.join(home, "vault")
            write(Path(home) / ".agents" / "dev-loop.config.json", json.dumps({"vault": vault}))
            env = {**os.environ, "HOME": home}
            blocked = self.run_hook(
                "git-guard.sh", {"cwd": vault, "tool_input": {"command": "git add note.md"}}, env=env
            )
            allowed = self.run_hook(
                "git-guard.sh", {"cwd": vault, "tool_input": {"command": "git status --short"}}, env=env
            )
            self.assertEqual(json.loads(blocked.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertEqual(allowed.stdout, "")

    def test_git_guard_vault_check_is_inert_without_config(self) -> None:
        # No ~/.agents/dev-loop.config.json means no configured vault, so a
        # write anywhere (including under a directory literally named
        # "vault") must be allowed rather than matching every path.
        with tempfile.TemporaryDirectory() as home:
            cwd = os.path.join(home, "vault")
            env = {**os.environ, "HOME": home}
            allowed = self.run_hook(
                "git-guard.sh", {"cwd": cwd, "tool_input": {"command": "git add note.md"}}, env=env
            )
            self.assertEqual(allowed.stdout, "")

    def test_session_start_routes_only_declared_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root)
            write(repo / "AGENTS.md", "vault_notes: Software/Test.md\n")
            routed = self.run_hook("session-start.sh", {"cwd": str(repo)})
            write(repo / "AGENTS.md", "# No workflow\n")
            ignored = self.run_hook("session-start.sh", {"cwd": str(repo)})
            self.assertIn("dev-loop skill", routed.stdout)
            self.assertEqual(ignored.stdout, "")

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
            write(gate, "#!/bin/sh\necho focused failure\nexit 1\n")
            os.chmod(gate, 0o755)
            first = self.run_hook("gate.sh", {"cwd": str(repo), "stop_hook_active": False})
            second = self.run_hook("gate.sh", {"cwd": str(repo), "stop_hook_active": True})
            self.assertIn("focused failure", json.loads(first.stdout)["reason"])
            self.assertEqual(second.stdout, "")


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
        for name in ["gate.sh", "deny-path.sh", "deny-acceptance-to-subagents.sh", "git-guard.sh"]:
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
                    "hooks": {"SessionStart": [], "Stop": [], "SubagentStop": [], "PreToolUse": []},
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
