"""check_setup.py: the project setup checks, the MANIFEST, the project config and the notes path check."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import CHECKER, PACKAGE_ROOT, check_setup, checker_env, write


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

    def test_merge_human_paths_shape(self) -> None:
        message = "merge_human_paths in dev-loop.json must be a list of non-empty strings"
        for value in ("src/*", [""], [" "], [1], {}, ["src/auth/"]):
            self.assertEqual(self.check({"merge_human_paths": value}), [message], value)
        self.assertEqual(self.check({"merge_human_paths": ["src/auth/*"]}), [])

    def test_file_size_exempt_shape(self) -> None:
        message = "file_size_exempt in dev-loop.json must be a list of non-empty strings"
        for value in ("docs", [""], [" "], [1], {}, ["docs/"]):
            self.assertEqual(self.check({"file_size_exempt": value}), [message], value)
        self.assertEqual(self.check({"file_size_exempt": ["docs/*"]}), [])

    def test_release_notes_values(self) -> None:
        failures = self.check({"release_notes": "ai"})
        self.assertEqual(len(failures), 1)
        for word in ("release_notes", "cliff", "written"):
            self.assertIn(word, failures[0])
        for config in ({"release_notes": "cliff"}, {"release_notes": "written"}, {}):
            self.assertEqual([f for f in self.check(config) if "release_notes" in f], [], config)
        self.assertEqual(len(self.check({"release_notes": 1})), 1)

    def test_version_files_shape(self) -> None:
        self.assertIn("VERSION", "\n".join(self.check({"version_files": ["VERSION"]})))
        for value in (["/etc/passwd"], ["../x"], ["a/../b"], "package.json", [""]):
            self.assertEqual(len(self.check({"version_files": value})), 1, value)
        self.assertEqual(self.check({"version_files": []}), [])
        self.assertEqual(self.check({}), [])
        with tempfile.TemporaryDirectory() as tmp:
            write(Path(tmp) / "dev-loop.json", json.dumps({"version_files": ["package.json"]}))
            write(Path(tmp) / "package.json", "{}")
            self.assertEqual(check_setup.check_project_config(Path(tmp)), [])

    def test_release_commands(self) -> None:
        self.assertEqual(
            self.check({"commands": {"release_verify": ""}}),
            ["commands.release_verify in dev-loop.json must be a non-empty string"],
        )
        commands = {key: "scripts/x.sh" for key in ("release_prepare", "release_check", "release_verify")}
        self.assertEqual(self.check({"commands": commands}), [])

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
                env=checker_env(Path(tmp) / "bin"),
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


INSTALLER = PACKAGE_ROOT / "bin" / "install.mjs"


@unittest.skipUnless(shutil.which("node"), "node is required to run the installer")
class ProjectSetupCheckerTests(unittest.TestCase):
    versions: dict[str, str | None] = {}

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

    def install_security_audit(self) -> None:
        for root in (".claude", ".agents"):
            write(self.home / root / "skills" / "security-audit" / "SKILL.md", "name: security-audit\n")

    def run_checker(self, harness: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CHECKER), "--harness", harness, "--home", str(self.home), "--repo", str(self.repo)],
            text=True,
            capture_output=True,
            check=False,
            env=checker_env(Path(self._tmp.name) / "bin", **self.versions, **self.audit_tree()),
        )

    def audit_tree(self) -> dict[str, str]:
        folder = self.home / ".agents" / "skills" / "security-audit"
        return {"DEVLOOP_TEST_SECURITY_AUDIT_TREE": check_setup.tree_id(folder)} if folder.exists() else {}

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
        events = json.loads((self.repo / ".codex" / "hooks.json").read_text())["hooks"]
        for event, groups in events.items():
            for i, group in enumerate(groups):
                for j in range(len(group["hooks"])):
                    key = f"{main}/.codex/hooks.json:{check_setup.snake_case(event)}:{i}:{j}"
                    lines += [f'[hooks.state."{key}"]', 'trusted_hash = "sha256:test"']
        write(self.home / ".codex" / "config.toml", "\n".join(lines) + "\n")
        for name in check_setup.CODEX_SKILLS:
            write(self.home / ".agents" / "skills" / name / "SKILL.md", f"name: {name}\n")

    def test_claude_project_passes(self) -> None:
        self.init("claude")
        self.install_security_audit()
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

    def test_a_disabled_rebase_merge_is_a_user_action(self) -> None:
        self.init()
        subprocess.run(["git", "remote", "add", "origin", "git@github.com:o/r.git"], cwd=self.repo, check=True)
        gh = Path(self._tmp.name) / "bin" / "gh"
        write(gh, f"#!{sys.executable}\nimport sys\nprint('false' if sys.argv[1:3] == ['api', 'repos/o/r'] else '')\n")
        gh.chmod(0o755)
        for harness in ("claude", "codex"):
            with self.subTest(harness=harness):
                result = self.run_checker(harness)
                self.assertEqual(result.returncode, 1)
                actions = result.stdout.split("User actions required", 1)[-1]
                self.assertIn("allow rebase merging in the GitHub settings of o/r (Stage 7 runs gh pr merge --rebase)",
                              actions)

    def test_the_checker_writes_no_python_cache(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            scripts = Path(root) / "scripts"
            shutil.copytree(CHECKER.parent, scripts, ignore=shutil.ignore_patterns("__pycache__", "test_*"))
            subprocess.run([sys.executable, str(scripts / "check_setup.py"), "--harness", "claude", "--repo", root],
                           capture_output=True, text=True, check=False, env=checker_env(Path(root) / "bin", HOME=root))
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
        self.assertIn("security-audit", result.stdout)

    def test_codex_project_with_trust_and_skills_passes(self) -> None:
        self.init()
        self.trust_codex()
        self.install_security_audit()
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

    def test_codex_root_without_the_planner_agent_names_it(self) -> None:
        self.init()
        root = self.repo / ".codex"
        rules = root / "rules" / "default.rules"
        self.assertNotIn(f"missing {root / 'agents' / 'planner.toml'}", check_setup.check_codex_root(root, rules))
        (root / "agents" / "planner.toml").unlink()
        self.assertIn(f"missing {root / 'agents' / 'planner.toml'}", check_setup.check_codex_root(root, rules))

    def test_codex_project_needs_a_trusted_hash_for_the_project_hooks_file(self) -> None:
        self.init()
        self.trust_codex()
        config = self.home / ".codex" / "config.toml"
        write(config, config.read_text().replace(".codex/hooks.json:stop:", "/elsewhere/hooks.json:stop:"))
        result = self.run_checker("codex")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing trusted Codex hook hash: stop", result.stdout)

    def test_codex_project_needs_a_trusted_hash_for_each_hook_entry(self) -> None:
        self.init()
        self.trust_codex()
        config = self.home / ".codex" / "config.toml"
        write(config, config.read_text().replace(".codex/hooks.json:stop:0:1", ".codex/hooks.json:stop:9:9"))
        result = self.run_checker("codex")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing trusted Codex hook hash: stop", result.stdout)
        self.assertNotIn("missing trusted Codex hook hash: session_start", result.stdout)

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
        self.install_security_audit()
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

    def test_codex_checker_asks_for_no_agent_profiles(self) -> None:
        self.init("codex")
        config = self.repo / ".codex" / "config.toml"
        text = config.read_text()
        self.assertNotIn("dev-implementer", text)
        self.assertNotIn("default_permissions", (self.repo / ".codex" / "agents" / "implementer.toml").read_text())
        result = self.run_checker("codex")
        for word in ("dev-implementer", "dev-spec-tester", "dev-read-only", "permission profile: expected"):
            self.assertNotIn(word, result.stdout)

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

    def test_claude_older_than_the_minimum_fails(self) -> None:
        self.init("claude")
        for found, ok in [("2.1.294", False), ("2.1.295", True), ("2.2.0", True), ("2.1.309", True), ("1.9.999", False)]:
            with self.subTest(found=found):
                self.versions = {"claude": f"{found} (Claude Code)"}
                result = self.run_checker("claude")
                line = f"- Claude Code {found} is older than 2.1.295 (onFailure needs it). Update Claude Code.\n"
                self.assertEqual(line in result.stdout, not ok, result.stdout)

    def test_codex_older_than_the_minimum_fails(self) -> None:
        self.init("codex")
        for found, ok in [("0.161.0", False), ("0.162.0", False), ("0.162.1", True), ("0.200.0", True), ("0.99.9", False)]:
            with self.subTest(found=found):
                self.versions = {"codex": f"codex-cli {found}"}
                result = self.run_checker("codex")
                line = f"- codex-cli {found} is older than 0.162.1 (hooks need agent_type). Update Codex.\n"
                self.assertEqual(line in result.stdout, not ok, result.stdout)

    def test_only_the_checked_harness_version_is_read(self) -> None:
        self.init("claude")
        self.install_security_audit()
        self.versions = {"codex": None}
        self.assertEqual(self.run_checker("claude").returncode, 0)

    def test_an_unreadable_version_is_one_failure_line(self) -> None:
        self.init("claude")
        for text, reason in [(None, "No such file"), ("FAIL", "non-zero exit"), ("no digits here", "no version")]:
            with self.subTest(text=text):
                self.versions = {"claude": text}
                result = self.run_checker("claude")
                lines = [l for l in result.stdout.splitlines() if "cannot read the claude version" in l]
                self.assertEqual(len(lines), 1, result.stdout)
                self.assertTrue(lines[0].startswith("- cannot read the claude version: "))
                self.assertIn(lines[0], result.stdout.partition("User actions required")[0])

    def test_a_guard_without_on_failure_block_fails(self) -> None:
        self.init("claude")
        path = self.repo / ".claude" / "settings.json"
        settings = json.loads(path.read_text())
        commands = []
        for entry in settings["hooks"]["PreToolUse"]:
            for hook in entry["hooks"]:
                if "onFailure" in hook:  # The guards; the ask wall fails open by design.
                    commands.append(hook["command"])
                    del hook["onFailure"]
        write(path, json.dumps(settings))
        self.assertEqual(len(commands), 2)
        result = self.run_checker("claude")
        for command in commands:
            self.assertIn(f'- missing onFailure: "block" on {command}\n', result.stdout)

    def test_a_missing_kit_skill_link_is_named(self) -> None:
        self.init("claude")
        self.assertNotIn("continue", self.run_checker("claude").stdout)
        (self.repo / ".claude" / "skills" / "continue").unlink()
        self.assertIn("- missing kit file: .claude/skills/continue\n", self.run_checker("claude").stdout)

    def test_an_agent_deny_path_hook_without_on_failure_block_fails(self) -> None:
        self.init("claude")
        self.assertNotIn("onFailure", self.run_checker("claude").stdout)
        for name in ("implementer", "spec-tester"):
            path = self.repo / ".claude" / "agents" / f"{name}.md"
            text = path.read_text()
            self.assertIn("          onFailure: block\n", text)
            write(path, text.replace("          onFailure: block\n", ""))
        result = self.run_checker("claude")
        self.assertEqual(result.stdout.count('missing onFailure: "block" on '), 2, result.stdout)
        self.assertIn("deny-path.sh\" tests/acceptance", result.stdout)

    def test_codex_needs_the_subagent_start_hook_event(self) -> None:
        self.init("codex")
        path = self.repo / ".codex" / "hooks.json"
        hooks = json.loads(path.read_text())
        del hooks["hooks"]["SubagentStart"]
        write(path, json.dumps(hooks))
        self.assert_codex_failure("missing Codex hook event: SubagentStart")


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
