"""The deny walls, the Codex hook behaviour, the git guard for subagents and its parity, the gate guard."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import CLAUDE_HOOKS, CODEX_ASSETS, check_setup, write


class GateGuardTests(unittest.TestCase):
    GUARD = (
        "GIT_CONFIG_N=${GIT_CONFIG_COUNT:-0}\n"
        'export "GIT_CONFIG_KEY_$GIT_CONFIG_N=core.fsmonitor" "GIT_CONFIG_VALUE_$GIT_CONFIG_N=false" '
        "GIT_CONFIG_COUNT=$((GIT_CONFIG_N + 1))\n"
    )
    AFTER_GIT = (
        "scripts/gate.sh runs git before the core.fsmonitor guard: move the GIT_CONFIG_* export above its first git call"
    )
    MESSAGE = (
        "scripts/gate.sh lacks the core.fsmonitor guard: export core.fsmonitor=false through "
        "GIT_CONFIG_COUNT, GIT_CONFIG_KEY_<n> and GIT_CONFIG_VALUE_<n>, before its first git call"
    )

    def check(self, gate: str | None, config: dict | None = None) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(repo / "dev-loop.json", json.dumps(config or {}))
            if gate is not None:
                write(repo / "scripts" / "gate.sh", gate)
            return check_setup.check_gate_guard(repo)

    def test_guard_present_passes(self) -> None:
        self.assertEqual(self.check("#!/bin/sh\n" + self.GUARD), [])

    def test_guard_missing_fails(self) -> None:
        self.assertEqual(self.check("#!/bin/sh\ngit status\n"), [self.MESSAGE])

    def test_count_without_fsmonitor_fails(self) -> None:
        self.assertEqual(self.check("export GIT_CONFIG_COUNT=1\n"), [self.MESSAGE])

    def test_comment_only_mention_fails(self) -> None:
        self.assertEqual(self.check("# " + self.GUARD.replace("\n", "\n# ")), [self.MESSAGE])

    def test_value_true_fails(self) -> None:
        self.assertEqual(self.check(self.GUARD.replace("=false", "=true")), [self.MESSAGE])

    def test_guard_after_git_call_fails(self) -> None:
        self.assertEqual(self.check("git status\n" + self.GUARD), [self.AFTER_GIT])

    def test_source_line_before_guard_passes(self) -> None:
        self.assertEqual(self.check(". scripts/x.sh\n" + self.GUARD + "git status\n"), [])

    def test_mapped_gate_script_is_checked(self) -> None:
        for command in ("scripts/gate.sh", "./scripts/gate.sh", "bash scripts/gate.sh"):
            with self.subTest(command=command):
                self.assertEqual(self.check("git status\n", {"commands": {"gate": command}}), [self.MESSAGE])

    def test_mapped_gate_command_skips(self) -> None:
        self.assertEqual(self.check("git status\n", {"commands": {"gate": "make gate"}}), [])

    def test_no_gate_script_skips(self) -> None:
        self.assertEqual(self.check(None), [])

    def test_check_project_runs_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(repo / "dev-loop.json", "{}")
            write(repo / "scripts" / "gate.sh", "git status\n")
            failures, _ = check_setup.check_project(repo / "home", repo, "claude")
            self.assertIn(self.MESSAGE, failures)


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

    def test_a_subagent_lease_push_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(["git", "-C", repo, "init", "-q", "-b", "topic/x"], check=True)
            command = {"command": "git push --force-with-lease"}
            sub = self.run_guard({"cwd": repo, "agent_id": "a1", "tool_input": command})
            self.assertEqual((sub.returncode, sub.stdout), (2, ""))
            main = self.run_guard({"cwd": repo, "tool_input": command})
            self.assertEqual((main.returncode, main.stdout), (0, ""))


class GitGuardParityTests(unittest.TestCase):
    """The two guards differ only in how they deny and ask and in the spec tester's name, so no rule can drift."""

    @staticmethod
    def body(path: Path) -> list[str]:
        return [line for line in path.read_text().splitlines()
                if not line.startswith(("#", "deny() ", "ask() ", "TESTER="))]

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
