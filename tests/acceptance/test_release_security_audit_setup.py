#!/usr/bin/env python3
"""Held-out oracle tests for the git guard, the setup checks and the kit (spec: Release Security Audit).

Seams, fixture choices and shared helpers: `release_security_audit_support.py`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from release_security_audit_support import (AGENTS, APP_A, AUDIT_OPENAI, AUDIT_SKILL, base_env, CHECKER, CLAUDE_REF,
                                            CODEX_REF, hook_decision, IMAGE, INSTALLER, note_text, NOTES_NAME, put,
                                            record_line, REPO_ROOT, SKILL)


# ---------------------------------------------------------------------------------------------------------------
# git-guard.sh (O46 to O52, flow 3)
# ---------------------------------------------------------------------------------------------------------------

class GuardTests(unittest.TestCase):
    """A repo with the kit installed (init), commits A to D, tag v1.0.0 on A, branch rc on B,
    refs/remotes/origin/main at C, HEAD at D. The notes folder holds a Project Note.md."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        self.notes = self.root / "notes" / NOTES_NAME
        for folder in (self.home, self.root / "tmp", self.notes):
            folder.mkdir(parents=True)
        self.env = base_env(self.home, self.root / "tmp")
        self.repo = self.root / "acme-widgets"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
            self.git("config", key, value)
        init = subprocess.run(["node", str(INSTALLER), "init", "--yes", "--harness=both", f"--notes={self.notes}"],
                              cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
        self.env["DEV_LOOP_NOTES"] = str(self.notes)
        put(self.repo / "src" / "app.py", APP_A)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "A")
        self.A = self.git("rev-parse", "HEAD")
        self.git("tag", "-a", "-m", "Release v1.0.0", "v1.0.0")
        self.B = self.step("B")
        self.git("branch", "rc", self.B)
        self.C = self.step("C")
        origin = self.root / "origin.git"
        self.git("init", "-q", "--bare", "-b", "main", str(origin))
        self.git("remote", "add", "origin", str(origin))
        self.git("push", "-q", "origin", "HEAD:refs/heads/main")
        self.git("fetch", "-q", "origin")
        self.D = self.step("D")
        self.note(audited=False)

    def git(self, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=self.repo, env=self.env, capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
        return result.stdout.strip()

    def step(self, name: str) -> str:
        put(self.repo / "src" / f"{name.lower()}.py", f"STEP = '{name}'\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", name)
        return self.git("rev-parse", "HEAD")

    def note(self, audited: bool) -> None:
        if audited:
            folder = self.notes / "security-audit" / "run-1"
            put(folder / "run-metadata.json", json.dumps({"source_ref": {"commit": self.A}, "profile": "quick",
                                                          "scope_paths": [], "run_status": "complete"}))
            put(self.notes / "Project Note.md", note_text([record_line(1, self.A)], [], []))
        else:
            put(self.notes / "Project Note.md", note_text(None, [], []))

    def guard(self, command: str, harness: str = "claude", agent: str | None = None) -> tuple[str | None, str, float]:
        payload = {"session_id": "session-1", "cwd": str(self.repo), "hook_event_name": "PreToolUse",
                   "tool_name": "Bash", "tool_input": {"command": command}}
        if agent:
            payload.update({"agent_id": f"agent-{agent}", "agent_type": agent})
        shell = "bash" if harness == "claude" else "sh"
        script = self.repo / ".dev-loop" / "hooks" / harness / "git-guard.sh"
        start = time.monotonic()
        result = subprocess.run([shell, str(script)], cwd=self.repo, input=json.dumps(payload), env=self.env,
                                capture_output=True, text=True, timeout=60)
        seconds = time.monotonic() - start
        decision, reason = hook_decision(result)
        return decision, reason, seconds

    def cli(self) -> Path:
        return self.repo / ".agents" / "skills" / "dev-loop" / "scripts" / "devloop.py"

    def test_o46_o52_tag_push_without_audit_asks_with_reason(self) -> None:
        decision, reason, _ = self.guard("git push origin v1.0.0")
        self.assertEqual(decision, "ask", reason)
        self.assertIn("a tag push", reason)
        self.assertIn("release-check: no-audit", reason)
        with self.subTest(oracle=52):
            decision, reason, _ = self.guard("git push --tags")
            self.assertEqual(decision, "ask", reason)
            self.assertNotIn("release-check", reason)

    def test_o47_tag_push_with_audit_names_ok(self) -> None:
        self.note(audited=True)
        decision, reason, _ = self.guard("git push origin v1.0.0")
        self.assertEqual(decision, "ask", reason)
        self.assertIn("release-check: ok", reason)

    def test_o48_gh_release_create_checks_the_commit_it_tags(self) -> None:
        for command, sha in (("gh release create v1.0.0", self.A),
                             ("gh release create v2.0.0 --target rc", self.B),
                             ("gh release create v2.0.0", self.C)):
            with self.subTest(command=command):
                decision, reason, _ = self.guard(command)
                self.assertEqual(decision, "ask", reason)
                self.assertIn("release-check", reason)
                self.assertIn(sha[:7], reason)
        decision, reason, _ = self.guard("gh release create v1.0.0")
        self.assertIn("release-check:", reason)
        decision, reason, _ = self.guard("gh release create release-1")
        self.assertEqual(decision, "ask", reason)

    def test_o48a_slow_release_check_times_out(self) -> None:
        put(self.cli(), "import time\ntime.sleep(20)\n")
        decision, reason, seconds = self.guard("git push origin v1.0.0")
        self.assertEqual(decision, "ask", reason)
        self.assertLess(seconds, 5)
        self.assertIn("release-check failed: timeout", reason)

    def test_o49_o50_codex_guard_and_subagent_deny(self) -> None:
        decision, reason, _ = self.guard("git push origin v1.0.0", harness="codex")
        self.assertEqual(decision, "deny", reason)
        self.assertIn("release-check: no-audit", reason)
        with self.subTest(oracle=50):
            decision, reason, _ = self.guard("git push origin v1.0.0", agent="implementer")
            self.assertEqual(decision, "deny", reason)

    def test_o51_no_cli_in_main_worktree(self) -> None:
        self.cli().unlink()
        decision, reason, _ = self.guard("git push origin v1.0.0")
        self.assertEqual(decision, "ask", reason)
        self.assertIn("release-check failed", reason)

    def test_f3_tag_push_shows_release_check_reasons(self) -> None:
        """F3: Claude asks and Codex denies, each with the release-check reasons; an audit turns them to ok."""
        claude, claude_reason, _ = self.guard("git push origin v1.0.0")
        codex, codex_reason, _ = self.guard("git push origin v1.0.0", harness="codex")
        self.assertEqual((claude, codex), ("ask", "deny"), claude_reason + codex_reason)
        self.assertIn("release-check: no-audit", claude_reason)
        self.assertIn("release-check: no-audit", codex_reason)
        self.note(audited=True)
        claude, claude_reason, _ = self.guard("git push origin v1.0.0")
        self.assertEqual(claude, "ask", claude_reason)
        self.assertIn("release-check: ok", claude_reason)


# ---------------------------------------------------------------------------------------------------------------
# check_setup.py (O53 to O55)
# ---------------------------------------------------------------------------------------------------------------

class SetupTests(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        (self.root / "tmp").mkdir()
        self.env = base_env(self.home, self.root / "tmp")
        self.vault = self.root / "vault"
        self.notes = self.vault / "Projects" / NOTES_NAME
        (self.notes / "specs").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.vault)], env=self.env, check=True, capture_output=True)
        self.project = self.root / "acme-widgets"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.project, env=self.env, check=True,
                       capture_output=True)
        init = subprocess.run(["node", str(INSTALLER), "init", "--yes", "--harness=both", f"--notes={self.notes}"],
                              cwd=self.project, env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
        self.config = json.loads((self.project / "dev-loop.json").read_text())
        self.local = json.loads((self.project / "dev-loop.local.json").read_text())
        self.stubs = self.root / "stubs"
        self.stubs.mkdir()
        docker_stub = self.stubs / "docker"
        docker_stub.write_text("#!/bin/sh\necho 'Cannot connect to the Docker daemon' >&2\nexit 1\n")
        docker_stub.chmod(0o755)

    def set_config(self, value: object, present: bool = True) -> None:
        config = {k: v for k, v in self.config.items() if k != "security_audit"}
        if present:
            config["security_audit"] = value
        (self.project / "dev-loop.json").write_text(json.dumps(config, indent=2) + "\n")

    def checker(self, harness: str = "claude") -> str:
        env = {**self.env, "PATH": f"{self.stubs}{os.pathsep}{self.env.get('PATH', '')}"}
        result = subprocess.run([sys.executable, str(CHECKER), "--harness", harness, "--home", str(self.home),
                                 "--repo", str(self.project)], env=env, capture_output=True, text=True, timeout=120)
        return result.stdout + result.stderr

    @staticmethod
    def split(output: str) -> tuple[list[str], list[str]]:
        lines = output.splitlines()
        heads = [n for n, line in enumerate(lines) if line.startswith("User actions required")]
        if not heads:
            return lines, []
        return lines[:heads[0]], lines[heads[0] + 1:]

    def vault_lines(self, output: str) -> list[str]:
        _, actions = self.split(output)
        return [line for line in actions if "security-audit/" in line and ".gitignore" in line]

    def test_o53_vault_gitignore_line(self) -> None:
        for harness in ("claude", "codex"):
            with self.subTest(case="not ignored", harness=harness):
                output = self.checker(harness)
                self.assertTrue(self.vault_lines(output), output)
        with self.subTest(case="board_vault_git shared"):
            (self.project / "dev-loop.local.json").write_text(
                json.dumps({**self.local, "board_vault_git": "shared"}, indent=2) + "\n")
            output = self.checker()
            self.assertEqual(self.vault_lines(output), [], output)
        with self.subTest(case="ignored in the vault"):
            (self.project / "dev-loop.local.json").write_text(json.dumps(self.local, indent=2) + "\n")
            (self.vault / ".gitignore").write_text("dev-loop-board/\nsecurity-audit/\n")
            output = self.checker()
            self.assertEqual(self.vault_lines(output), [], output)

    def test_o54_bad_security_audit_shapes(self) -> None:
        for value in ({"images": {"T": "x"}}, {"images": {"t": ""}}, {"images": []}, {"other": 1}, "x"):
            with self.subTest(value=value):
                self.set_config(value)
                failures, _ = self.split(self.checker())
                self.assertTrue([line for line in failures if "security_audit" in line], "\n".join(failures))

    def test_o55_docker_engine_line(self) -> None:
        self.set_config({"images": {"t": IMAGE}})
        _, actions = self.split(self.checker())
        self.assertTrue([line for line in actions if "start a Docker engine for the release audit sandbox" in line],
                        "\n".join(actions))
        self.set_config(None, present=False)
        output = self.checker()
        self.assertFalse([line for line in output.splitlines() if re.search(r"docker engine", line, re.I)], output)


# ---------------------------------------------------------------------------------------------------------------
# Installer and kit text (O56 to O59)
# ---------------------------------------------------------------------------------------------------------------

AUDIT_FILES = (".agents/skills/release-audit/SKILL.md", ".agents/skills/release-audit/agents/openai.yaml")
AUDIT_LINK = ".claude/skills/release-audit"


class KitTests(unittest.TestCase):

    def heading_section(self, text: str, title: str) -> str:
        match = re.search(rf"^(#+) {re.escape(title)}\s*$", text, re.M)
        self.assertIsNotNone(match, f"no section {title!r}")
        level = len(match.group(1))
        rest = text[match.end():]
        end = re.search(rf"^#{{1,{level}}} ", rest, re.M)
        return rest[:end.start()] if end else rest

    def test_o56_init_and_upgrade_install_the_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            home, notes, project = root / "home", root / "notes", root / "proj"
            for folder in (home, notes, project, root / "tmp"):
                folder.mkdir()
            env = base_env(home, root / "tmp")

            def run(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(args, cwd=project, env=env, capture_output=True, text=True, timeout=180)

            run("git", "init", "-q", "-b", "main")
            init = run("node", str(INSTALLER), "init", "--yes", "--harness=both", f"--notes={notes}")
            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)

            def assert_installed() -> None:
                for rel in AUDIT_FILES:
                    self.assertTrue((project / rel).is_file(), rel)
                link = project / AUDIT_LINK
                self.assertTrue(link.is_symlink(), AUDIT_LINK)
                self.assertEqual(os.readlink(link), "../../.agents/skills/release-audit")
                files = json.loads((project / ".dev-loop" / "MANIFEST.json").read_text())["files"]
                for rel in (*AUDIT_FILES, AUDIT_LINK):
                    self.assertIn(rel, files)

            assert_installed()

            # A kit without the skill: an earlier version, no release-audit files, link or MANIFEST entries.
            manifest_path = project / ".dev-loop" / "MANIFEST.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["files"] = {k: v for k, v in manifest["files"].items()
                                 if not k.startswith((".agents/skills/release-audit", AUDIT_LINK))}
            manifest["version"] = "2.1.0"
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
            config_path = project / "dev-loop.json"
            config = json.loads(config_path.read_text())
            config["version"] = "2.1.0"
            config_path.write_text(json.dumps(config, indent=2) + "\n")
            shutil.rmtree(project / ".agents" / "skills" / "release-audit")
            (project / AUDIT_LINK).unlink()
            for args in (("git", "add", "-A"),
                         ("git", "-c", "user.name=Robin Quill", "-c", "user.email=robin@example.invalid",
                          "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Kit 2.1.0"),
                         ("git", "checkout", "-q", "-b", "kit-upgrade")):
                done = run(*args)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            upgrade = run("node", str(INSTALLER), "upgrade", "--yes")
            self.assertEqual(upgrade.returncode, 0, upgrade.stdout + upgrade.stderr)
            assert_installed()

    def test_o57_release_audit_skill_text(self) -> None:
        text = AUDIT_SKILL.read_text()
        frontmatter = text.split("---", 2)[1]
        self.assertRegex(frontmatter, r"(?m)^disable-model-invocation:\s*true\s*$")
        self.assertRegex(AUDIT_OPENAI.read_text(), r"(?m)^\s*allow_implicit_invocation:\s*false\s*$")
        for name in ("release-plan", "release-record", "release-check", "sandbox-run.py"):
            self.assertIn(name, text)
        self.assertRegex(text, re.compile(r"never\b[^.\n]*\bpush[^.\n]*\btag", re.I))
        self.assertRegex(text, re.compile(r"(never|not)\b[^.\n]*\bsubagent", re.I))

    def test_o58_dev_loop_skill_stages(self) -> None:
        text = SKILL.read_text()
        stage6 = re.search(r"^## 6 .*?(?=^## 7 )", text, re.M | re.S)
        stage7 = re.search(r"^## 7 .*", text, re.M | re.S)
        self.assertIsNotNone(stage6, "no Stage 6 section")
        self.assertIsNotNone(stage7, "no Stage 7 section")
        self.assertRegex(stage6.group(0), r"\(security, needs-validation, <[^>]*slug>\)")
        self.assertIn("/release-audit", stage7.group(0))
        self.assertIn("release-check", stage7.group(0))
        self.assertNotIn("Never merge a release PR or push a tag while one is open", text)
        grant = re.search(r"^Autonomy grant.*?(?=^## )", text, re.M | re.S)
        self.assertIsNotNone(grant, "no autonomy grant")
        self.assertRegex(grant.group(0), re.compile(r"tag push", re.I))

    def test_o59_harness_routes_and_release_steps(self) -> None:
        for ref in (CLAUDE_REF, CODEX_REF):
            with self.subTest(ref=ref.name):
                body = self.heading_section(ref.read_text(), "Release audit")
                self.assertIn("release-audit", body)
                self.assertIn("sandbox-run.py", body)
        self.assertTrue("reference/release.md" in AGENTS.read_text(), "AGENTS.md names no reference/release.md")
        steps = (REPO_ROOT / "reference" / "release.md").read_text()
        self.assertIn("/release-audit", steps)
        tag = steps.find("git tag")
        self.assertGreaterEqual(tag, 0, steps)
        self.assertLess(steps.find("/release-audit"), tag, steps)


if __name__ == "__main__":
    unittest.main()
