#!/usr/bin/env python3
"""Held-out oracle tests for the source dirs wall (spec: Source Dirs Wall), flows F8 to F11.

Seams and fixture: `source_dirs_wall_support.py`.

Fixture choices the spec leaves open:
- F9: the verdict lines compare on a project where `init` (both harnesses) ran on the fixture checkout, its files
  left untracked, with a test `security-audit` skill in the home and fake `claude` and `codex` CLIs on `PATH`, so a
  `check_setup.py` verdict can be `PASS`. `check_setup.py` runs with `--home` a scratch home.
- F10: SubagentStart is the Codex `seal.sh start` of the kit source (the snapshot of `HEAD`); the spec-tester
  commit is a plain `git commit` of a held-out file, its output in the transcript.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from source_dirs_wall_support import (BRANCH, CHECKER, CODEX_HOOKS, DEFAULT_DIRS, GO1, GO2, GO_TEXT, INSTALLER,
                                      REPO_ROOT, SourceWallRepo, clean_env, config_text, git, names_word, put, read,
                                      source_failures, user_actions, verdict)

THIS_REPO_DIRS = ["skill/", "skills/", "lib/", "bin/", "claude/", "codex/", "tests/unit/", ".agents/", ".claude/",
                  ".codex/", ".dev-loop/"]
CLAUDE_AGENT = REPO_ROOT / "claude" / "agents" / "spec-tester.md"
SOURCE_WALL_REFERENCE = REPO_ROOT / "reference" / "cli" / "source-wall.md"
AGENTS_MD = REPO_ROOT / "AGENTS.md"


class MatcherTests(SourceWallRepo):
    def source_wall(self) -> tuple[int, dict]:
        payload = self.payload("Read", {"file_path": "internal/x.go"}, "spec-tester", self.repo)
        return self.cli("source-wall", stdin=json.dumps(payload))

    def test_f8_the_matcher_gives_its_decision(self) -> None:
        """F8, O10: `source-wall` JSON for each main ref state."""
        with self.subTest(oracle="O10", main="no key"):
            code, out = self.source_wall()
            self.assertEqual((code, out.get("mode"), out.get("deny"), out.get("ref")), (0, "default", False, "main"),
                             out)
        with self.subTest(oracle="O10", main='["internal/"]'):
            self.main_key(["internal/"])
            code, out = self.source_wall()
            self.assertEqual((code, out.get("mode"), out.get("deny")), (0, "configured", True), out)
        with self.subTest(oracle="O10", main="no key, key on the feature branch"):
            self.main_key()
            self.branch_key(["internal/"])
            code, out = self.source_wall()
            self.assertEqual((code, out.get("mode")), (0, "default"), out)
        with self.subTest(oracle="O10", main='"internal/"'):
            self.main_key("internal/")
            code, out = self.source_wall()
            self.assertEqual(code, 1, out)
            self.assertIn("source_dirs", str(out.get("error")), out)
        with self.subTest(oracle="O10", main="[]"):
            self.main_key([])
            code, out = self.source_wall()
            self.assertEqual((code, out.get("mode"), out.get("deny")), (0, "configured", False), out)
            self.expect("O10", (read("src/app.py"),), walled=False)
        with self.subTest(oracle="O10", main="no main ref"):
            self.git("branch", "-D", "main")
            code, out = self.source_wall()
            self.assertEqual(code, 0, out)
            self.assertEqual(out.get("mode"), "default", out)
            self.assertIn("ref", out)
            self.assertIsNone(out["ref"], out)


class SetupCheckTests(SourceWallRepo):
    EXTRA_FILES = {"tests/unit/test_y.py": "def test_y():\n    assert True\n", "README.md": "# Acme gizmos\n",
                   "setup.py": "from setuptools import setup\n\nsetup(name='gizmos')\n",
                   ".github/workflows/ci.yml": "on: push\n"}

    def setUp(self) -> None:
        super().setUp()
        self.home = self.root / "setup-home"
        self.home.mkdir()
        self.bin = self.root / "fake-bin"
        self.bin.mkdir()
        for name, version in (("claude", "2.1.309 (Claude Code)"), ("codex", "codex-cli 0.162.1")):
            fake = self.bin / name
            fake.write_text(f"#!/bin/sh\n[ \"$1\" = --version ] || exit 2\necho '{version}'\n")
            fake.chmod(0o755)
        self.setup_env = clean_env(self.home, self.tmp)
        self.setup_env["PATH"] = f"{self.bin}{os.pathsep}{self.setup_env.get('PATH', '')}"

    def check(self, harness: str = "claude", repo: Path | None = None) -> str:
        result = subprocess.run([sys.executable, str(CHECKER), "--harness", harness, "--home", str(self.home),
                                 "--repo", str(repo or self.repo)], env=self.setup_env, capture_output=True,
                                text=True, timeout=180)
        return result.stdout + result.stderr

    def checkout_key(self, value: object, repo: Path | None = None) -> None:
        """The checkout `dev-loop.json` (not committed): its current object with `source_dirs` set to `value`."""
        path = (repo or self.repo) / "dev-loop.json"
        data = json.loads(path.read_text())
        data["source_dirs"] = value
        path.write_text(json.dumps(data, indent=2) + "\n")

    def installed(self) -> Path:
        """A project `acme-kit`: `init` (both harnesses) committed on `main` with no `source_dirs`, the branch
        `feat/kit` checked out; a test security-audit skill in the home."""
        project = self.root / "acme-kit"
        project.mkdir()
        git(project, self.setup_env, "init", "-q", "-b", "main")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
            git(project, self.setup_env, "config", key, value)
        notes = self.root / "notes"
        (notes / "Features").mkdir(parents=True)
        init = subprocess.run(["node", str(INSTALLER), "init", "--yes", "--harness=both", f"--notes={notes}"],
                              cwd=project, env=self.setup_env, capture_output=True, text=True, timeout=300)
        self.assertEqual(init.returncode, 0, init.stdout[-600:] + init.stderr[-600:])
        data = json.loads((project / "dev-loop.json").read_text())
        data.pop("source_dirs", None)
        (project / "dev-loop.json").write_text(json.dumps(data, indent=2) + "\n")
        git(project, self.setup_env, "add", "-A")
        git(project, self.setup_env, "commit", "-q", "-m", "Install dev-loop")
        git(project, self.setup_env, "checkout", "-q", "-b", "feat/kit")
        folder = self.home / ".agents" / "skills" / "security-audit"
        put(folder / "SKILL.md", "---\nname: security-audit\ndescription: Audit a codebase.\n---\n\n# Audit\n")
        link = self.home / ".claude" / "skills" / "security-audit"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(folder, target_is_directory=True)
        scratch = self.root / "tree-scratch"
        git(self.root, self.setup_env, "init", "-q", str(scratch))
        tree_env = {**self.setup_env, "GIT_DIR": str(scratch / ".git"), "GIT_WORK_TREE": str(folder)}
        git(folder, tree_env, "add", "-A", ".")
        self.setup_env["DEVLOOP_TEST_SECURITY_AUDIT_TREE"] = git(folder, tree_env, "write-tree")
        return project

    def test_f9_the_setup_check_lists_the_folders_and_flags_bad_entries_and_pending_changes(self) -> None:
        """F9, O11, O12: the missing-key failure line with the top-level folders; one failure line per invalid
        entry; the user action lines of a pending change, which leave the verdict as it is."""
        with self.subTest(oracle="O11", case="no key"):
            output = self.check()
            lines = [line for line in source_failures(output) if "source_dirs missing in dev-loop.json" in line]
            self.assertEqual(len(lines), 1, output[-2000:])
            self.assertIn("Top-level folders: api/ cmd/ docs/ internal/ pkg/ scripts/ src/ tests/", lines[0])
            self.assertNotIn(".github/", lines[0])
            self.assertIn(lines[0], user_actions(output), output[-2000:])

        for value in (["internal"], ["/internal/"], ["../x/"], ["src//"], ["*/src/"], ["src/", "SRC/"],
                      ["tests/acceptance/"], ["tests/acceptance/x/"], ["My App/"], ["app/(admin)/"], None,
                      "internal/"):
            with self.subTest(oracle="O12", source_dirs=json.dumps(value)):
                self.checkout_key(value)
                output = self.check()
                self.assertEqual(len(source_failures(output)), 1, output[-2000:])
        for value in ([], ["tests/"], ["café/"], [".agents/skills/"]):
            with self.subTest(oracle="O12", source_dirs=json.dumps(value)):
                self.checkout_key(value)
                output = self.check()
                self.assertEqual(source_failures(output), [], output[-2000:])
        with self.subTest(oracle="O12", main='"internal/"'):
            self.checkout_key(["internal/"])
            self.main_key("internal/")
            lines = source_failures(self.check())
            self.assertEqual(len(lines), 1, lines)
            self.assertTrue(names_word(lines[0], "main"), lines)

        project = self.installed()
        self.checkout_key(["internal/"], project)
        pending = {}
        for harness in ("claude", "codex"):
            with self.subTest(oracle="O11", case="checkout key, no main ref key", harness=harness):
                output = self.check(harness, project)
                pending[harness] = verdict(output, harness)
                self.assertEqual(source_failures(output), [], output[-2000:])
                found = [line for line in user_actions(output)
                         if names_word(line, "main") and all(name in line for name in DEFAULT_DIRS)]
                self.assertTrue(found, output[-2000:])
        self.commit_on("main", {"dev-loop.json": (project / "dev-loop.json").read_text()}, "Main with the same key",
                       repo=project)
        for harness in ("claude", "codex"):
            with self.subTest(oracle="O11", case="verdict equal to the same key on main", harness=harness):
                self.assertEqual(pending[harness], verdict(self.check(harness, project), harness))

        self.checkout_key(["internal/"])
        with self.subTest(oracle="O11", case="main ref key, no checkout key"):
            self.commit_on("main", {"dev-loop.json": config_text(["internal/"])}, "Main key")
            data = json.loads((self.repo / "dev-loop.json").read_text())
            del data["source_dirs"]
            (self.repo / "dev-loop.json").write_text(json.dumps(data, indent=2) + "\n")
            output = self.check()
            self.assertFalse([line for line in output.splitlines() if "source_dirs missing" in line], output[-2000:])
            found = [line for line in user_actions(output) if names_word(line, "main") and "internal/" in line]
            self.assertTrue(found, output[-2000:])
        with self.subTest(oracle="O11", case="main ref dev-loop.json []"):
            self.checkout_key(["internal/"])
            self.main_key(raw="[]\n")
            lines = source_failures(self.check())
            self.assertEqual(len(lines), 1, lines)
            self.assertTrue(names_word(lines[0], "main"), lines)

        with self.subTest(oracle="O11", case="no top-level folder"):
            bare = self.root / "acme-bare"
            bare.mkdir()
            git(bare, self.setup_env, "init", "-q", "-b", "main")
            for path, text in (("README.md", "# Bare\n"), ("dev-loop.json", config_text()),
                               (".github/workflows/ci.yml", "on: push\n")):
                put(bare / path, text)
            git(bare, self.setup_env, "add", "-A")
            git(bare, self.setup_env, "-c", "user.name=Robin Quill", "-c", "user.email=robin@example.invalid",
                "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Base")
            self.assertEqual(source_failures(self.check(repo=bare)), [])


class AuditTests(SourceWallRepo):
    def start(self, agent_id: str) -> None:
        """SubagentStart of a spec-tester: the snapshot of `HEAD`."""
        payload = {"session_id": "session-1", "cwd": str(self.repo), "hook_event_name": "SubagentStart",
                   "agent_id": agent_id, "agent_type": "spec-tester"}
        result = self.run_hook("codex", "seal.sh", payload, "start", cwd=self.repo, folder=CODEX_HOOKS)
        self.assertEqual(result.returncode, 0, result.stdout[-400:] + result.stderr[-400:])

    def audit(self, agent_id: str) -> dict:
        """A spec-tester that saw `internal/x.go` and made one commit; the `audit` output."""
        self.start(agent_id)
        path = f"tests/acceptance/test_{agent_id.replace('-', '_')}.py"
        put(self.repo / path, "def test_serial():\n    assert True\n")
        self.git("add", "--", path)
        self.git("commit", "-q", "-m", f"Add {agent_id} tests")
        sha = self.head()
        short = self.git("rev-parse", "--short", sha)
        lines = [{"type": "user", "message": {"role": "user", "content": "Write the gizmo acceptance tests."}}]
        for use_id, tool, tool_input, result in (
                ("toolu_1", "Bash", {"command": "cat notes.txt"}, GO_TEXT),
                ("toolu_2", "Bash", {"command": f"git commit -m 'Add {agent_id} tests'"},
                 f"[{BRANCH} {short}] Add {agent_id} tests\n 1 file changed, 2 insertions(+)\n")):
            lines.append({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": use_id, "name": tool, "input": tool_input}]}})
            lines.append({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": use_id, "content": result}]}})
        transcript = self.root / f"{agent_id}.jsonl"
        transcript.write_text("".join(json.dumps(line) + "\n" for line in lines))
        payload = {"session_id": "session-1", "cwd": str(self.repo), "hook_event_name": "SubagentStop",
                   "agent_id": agent_id, "agent_type": "spec-tester", "agent_transcript_path": str(transcript)}
        code, out = self.cli("audit", stdin=json.dumps(payload))
        self.assertEqual(code, 0, out)
        return out

    def test_f10_the_taint_audit_uses_the_configured_folders(self) -> None:
        """F10, O13: a spec-tester output that quotes `internal/x.go` taints with a main ref key, and not with none;
        a byte-identical kit copy in a walled dot folder does not hide it."""
        self.assertTrue(GO1 in GO_TEXT and GO2 in GO_TEXT)
        with self.subTest(oracle="O13", main='["internal/"]'):
            self.main_key(["internal/"])
            record = self.audit("tester-1").get("taint")
            self.assertIsNotNone(record)
            self.assertEqual(record.get("set"), "source", record)
        with self.subTest(oracle="O13", main="no key"):
            self.main_key()
            self.assertIsNone(self.audit("tester-2").get("taint"))
        with self.subTest(oracle="O13", main='["internal/", ".agents/skills/"]'):
            copy = ".agents/skills/k/x.go"
            put(self.repo / copy, GO_TEXT)
            self.git("add", "--", copy)
            self.git("commit", "-q", "-m", "Add the kit copy")
            self.commit_on("main", {copy: GO_TEXT, "dev-loop.json": config_text(["internal/", ".agents/skills/"])},
                           "Main key and kit copy")
            record = self.audit("tester-3").get("taint")
            self.assertIsNotNone(record)
            self.assertEqual(record.get("set"), "source", record)


class ThisRepoTests(unittest.TestCase):
    def test_f11_dev_loop_closes_its_own_gap_and_the_kit_carries_the_entry(self) -> None:
        """F11, O15, O16: this repo's `source_dirs`, its setup check, the frontmatter entry, the CLI reference and
        the `AGENTS.md` index line."""
        with self.subTest(oracle="O15", part="dev-loop.json"):
            self.assertEqual(json.loads((REPO_ROOT / "dev-loop.json").read_text()).get("source_dirs"),
                             THIS_REPO_DIRS)
        with self.subTest(oracle="O15", part="check_setup.py"):
            with tempfile.TemporaryDirectory() as tmp:
                home, scratch = Path(tmp) / "home", Path(tmp) / "tmp"
                home.mkdir()
                scratch.mkdir()
                result = subprocess.run([sys.executable, str(CHECKER), "--harness", "claude", "--home", str(home),
                                         "--repo", str(REPO_ROOT)], env=clean_env(home, scratch),
                                        capture_output=True, text=True, timeout=300)
                self.assertEqual(source_failures(result.stdout + result.stderr), [])
        with self.subTest(oracle="O16", part="spec-tester.md"):
            frontmatter = CLAUDE_AGENT.read_text().split("---", 2)[1]
            entries = [item for item in re.split(r"\n\s*-\s", frontmatter)
                       if re.search(r"deny-path\.sh --source-dirs src/ app/ backend/ frontend/(?![\w/-])", item)]
            self.assertTrue(entries, frontmatter)
            for item in entries:
                self.assertRegex(item, r"onFailure:\s*[\"']?block[\"']?")
        with self.subTest(oracle="O16", part="reference/cli/source-wall.md"):
            self.assertTrue(SOURCE_WALL_REFERENCE.is_file())
        with self.subTest(oracle="O16", part="AGENTS.md"):
            self.assertTrue([line for line in AGENTS_MD.read_text().splitlines()
                             if line.startswith("- `source-wall")])


if __name__ == "__main__":
    unittest.main()
