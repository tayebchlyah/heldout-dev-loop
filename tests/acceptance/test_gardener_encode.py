#!/usr/bin/env python3
"""Held-out acceptance tests for the Gardener Encode change (spec: Gardener Encode).

One test per acceptance flow (F1 to F3); each names the `## Acceptance oracle` lines (O<n>) it proves.
Seams: `bin/install.mjs init` and `upgrade` as processes on a temporary project, the text of the kit files,
and the Codex `board.sh` as a process with hook input on stdin. Every name in here is invented.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Shared board fixtures live in tests/unit/python; the unit suite never imports this held-out file.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "unit" / "python"))
from board_fixtures import RobustnessHookFixture  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "bin" / "install.mjs"
SKILL = REPO_ROOT / "skill" / "SKILL.md"
REFS = REPO_ROOT / "skill" / "references"
GARDEN_SKILL = REPO_ROOT / "skills" / "garden" / "SKILL.md"
GARDEN_OPENAI = REPO_ROOT / "skills" / "garden" / "agents" / "openai.yaml"
STANDARDS = REPO_ROOT / "CODING_STANDARDS.md"

GARDEN_FILES = (".agents/skills/garden/SKILL.md", ".agents/skills/garden/agents/openai.yaml")
GARDEN_LINK = ".claude/skills/garden"

CLAUDE_ONLY = ("TaskCreate", "TaskUpdate", "subagent_type", "SendMessage", "TaskStop", "EnterWorktree",
               "CLAUDE_CODE_SESSION_ID", "mattpocock-skills:")
CODEX_ONLY = ("spawn_agent", "send_input", "followup_task", "resume_agent", "interrupt_agent", "close_agent",
              "apply_patch", "CODEX_THREAD_ID")


def base_env(home: Path, tmp: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("DEV_LOOP", "DEVLOOP", "CLAUDE", "CODEX"))}
    env.update({"HOME": str(home), "TMPDIR": str(tmp), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_EDITOR": "true", "GIT_TERMINAL_PROMPT": "0"})
    return env


def section(text: str, title_pattern: str) -> str:
    """The body of the first heading whose title matches `title_pattern`, up to the next heading of its level."""
    match = re.search(rf"^(#+) {title_pattern}.*$", text, re.M)
    if match is None:
        raise AssertionError(f"no section matching {title_pattern!r}")
    level = len(match.group(1))
    rest = text[match.end():]
    end = re.search(rf"^#{{1,{level}}} ", rest, re.M)
    return rest[:end.start()] if end else rest


class InstallFlowTests(unittest.TestCase):

    def test_f1_init_and_upgrade_install_the_garden_skill(self) -> None:
        """F1, O1, O2: `init` (both harnesses, then Codex only) and `upgrade` from a kit without `garden`."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            home, notes, tmpdir = root / "home", root / "notes", root / "tmp"
            for folder in (home, notes, tmpdir):
                folder.mkdir()
            env = base_env(home, tmpdir)

            def run(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(args, cwd=project, env=env, capture_output=True, text=True, timeout=180)

            def init(name: str, harness: str) -> Path:
                project = root / name
                project.mkdir()
                run(project, "git", "init", "-q", "-b", "main")
                done = run(project, "node", str(INSTALLER), "init", "--yes", f"--harness={harness}",
                           f"--notes={notes}")
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                return project

            def manifest(project: Path) -> dict:
                return json.loads((project / ".dev-loop" / "MANIFEST.json").read_text())["files"]

            def assert_installed(project: Path) -> None:
                for rel in GARDEN_FILES:
                    self.assertTrue((project / rel).is_file(), rel)
                link = project / GARDEN_LINK
                self.assertTrue(link.is_symlink(), GARDEN_LINK)
                self.assertEqual(os.readlink(link), "../../.agents/skills/garden")
                files = manifest(project)
                for rel in (*GARDEN_FILES, GARDEN_LINK):
                    self.assertIn(rel, files)

            # O1: both harnesses.
            both = init("proj-both", "both")
            assert_installed(both)

            # O1: Codex only: the two files, no link.
            codex = init("proj-codex", "codex")
            for rel in GARDEN_FILES:
                self.assertTrue((codex / rel).is_file(), rel)
                self.assertIn(rel, manifest(codex))
            self.assertFalse(os.path.lexists(codex / GARDEN_LINK), GARDEN_LINK)

            # O2: a kit without the skill (an earlier version: no garden files, link or MANIFEST entries).
            manifest_path = both / ".dev-loop" / "MANIFEST.json"
            data = json.loads(manifest_path.read_text())
            data["files"] = {k: v for k, v in data["files"].items()
                             if not k.startswith((".agents/skills/garden", GARDEN_LINK))}
            data["version"] = "2.1.0"
            manifest_path.write_text(json.dumps(data, indent=2) + "\n")
            config_path = both / "dev-loop.json"
            config = json.loads(config_path.read_text())
            config["version"] = "2.1.0"
            config_path.write_text(json.dumps(config, indent=2) + "\n")
            shutil.rmtree(both / ".agents" / "skills" / "garden")
            (both / GARDEN_LINK).unlink()
            for args in (("git", "add", "-A"),
                         ("git", "-c", "user.name=Robin Quill", "-c", "user.email=robin@example.invalid",
                          "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Kit 2.1.0"),
                         ("git", "checkout", "-q", "-b", "kit-upgrade")):
                done = run(both, *args)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            upgrade = run(both, "node", str(INSTALLER), "upgrade", "--yes")
            self.assertEqual(upgrade.returncode, 0, upgrade.stdout + upgrade.stderr)
            assert_installed(both)


class KitTextFlowTests(unittest.TestCase):

    def test_f2_kit_text_contract(self) -> None:
        """F2, O3 to O9: the garden procedure, the comment rule template, Stage 0 and 6, building.md, the guard."""
        # O3: invocation locks.
        text = GARDEN_SKILL.read_text()
        frontmatter = text.split("---", 2)[1]
        self.assertRegex(frontmatter, r"(?m)^name:\s*garden\s*$")
        self.assertRegex(frontmatter, r"(?m)^disable-model-invocation:\s*true\s*$")
        self.assertRegex(GARDEN_OPENAI.read_text(), r"(?m)^\s*allow_implicit_invocation:\s*false\s*$")

        # O4: the procedure names.
        for name in ("## Gardener", "Gardener Log.md", "Proof: red at", "stacks:", "Track S",
                     "CODING_STANDARDS.md"):
            self.assertIn(name, text)
        for verdict in ("encode", "fix", "keep", "drop"):
            self.assertRegex(text, rf"`{verdict}`")
        self.assertRegex(text, re.compile(r"\bat most 3\b", re.I))
        self.assertRegex(text, re.compile(r"structure or type", re.I))
        self.assertRegex(text, re.compile(r"lint rule", re.I))
        self.assertRegex(text, re.compile(r"guard test", re.I))
        self.assertRegex(text, re.compile(r"\bone commit\b[^.\n]*\bcheck\b", re.I))
        self.assertRegex(text, re.compile(r"\bnever builds?\b[^.\n]*\bopen PR\b", re.I))

        # O5: the log header and the run verdict.
        self.assertIn("| Date | Item | Verdict | Check | Proof |", text)
        self.assertRegex(text, r"`run`")

        # O6: the comment rule template.
        comments = section((REFS / "coding-standards.md").read_text(), r"Comments\s*$")
        for kind in ("license", "dependency", "public API", "issue", "RFC", "directive"):
            self.assertRegex(comments, re.compile(re.escape(kind), re.I), kind)
        self.assertIn("shortcut: <the limit>, <when to upgrade>", comments)

        # O7: Stage 0 and Stage 6 of the dev-loop skill.
        skill = SKILL.read_text()
        stage0 = section(skill, r"0 ")
        self.assertIn("references/coding-standards.md", stage0)
        self.assertNotIn("empty `CODING_STANDARDS.md`", skill)
        stage6 = section(skill, r"6 ")
        self.assertIn("/garden", stage6)
        self.assertIn("$garden", stage6)

        # O8: the building.md pointer.
        building = (REFS / "building.md").read_text()
        self.assertIn("coding-standards.md", building)
        self.assertIn("shortcut: <the limit>, <when to upgrade>", building)
        self.assertNotIn("Comment only the why the code cannot show", building)

        # O9: the codebase-design guard line in both harness routes.
        for ref in ("claude.md", "codex.md"):
            with self.subTest(ref=ref):
                lines = [line for line in (REFS / ref).read_text().splitlines()
                         if "codebase-design" in line and "reference only" in line and "design-it-twice" in line]
                self.assertTrue(lines, f"no codebase-design guard line in {ref}")


class HarnessNamesFlowTests(RobustnessHookFixture, unittest.TestCase):

    def test_f3_harness_names_kept_apart_and_codex_board_hook_unchanged(self) -> None:
        """F3, O10 to O12: no name of the other harness, the repo CODING_STANDARDS.md, the Codex board hook."""
        # O10: no name of the other harness in a harness's own sources.
        hits: list[str] = []
        for names, paths in ((CLAUDE_ONLY, [REFS / "codex.md", *(REPO_ROOT / "codex").rglob("*")]),
                             (CODEX_ONLY, [REFS / "claude.md", *(REPO_ROOT / "claude").rglob("*")])):
            for path in paths:
                if not path.is_file():
                    continue
                for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                    hits += [f"{path.relative_to(REPO_ROOT)}:{number} {name}" for name in names if name in line]
        self.assertEqual(hits, [])

        # O11: this repo's CODING_STANDARDS.md has the template's `## Comments` section.
        self.assertTrue(STANDARDS.is_file(), "no CODING_STANDARDS.md at the repo root")
        template = section((REFS / "coding-standards.md").read_text(), r"Comments\s*$")
        self.assertEqual(section(STANDARDS.read_text(), r"Comments\s*$").strip(), template.strip())

        # O12: the Codex `board.sh post-tool` still runs the board after `git commit`, and not after `ls`.
        result = self.board_sh("codex", "post-tool", self.repo, command="ls")
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertEqual([c for c in self.calls(wait=1.5) if "board" in c["argv"]], [])
        self.log.unlink(missing_ok=True)
        result = self.board_sh("codex", "post-tool", self.repo, command="git commit -m 'Add the widget'")
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        board = [c for c in self.calls(wait=6) if "board" in c["argv"]]
        self.assertEqual(len(board), 1, board)
        argv = board[0]["argv"]
        self.assertEqual(argv[argv.index("--event") + 1], "post-tool")


if __name__ == "__main__":
    unittest.main()
