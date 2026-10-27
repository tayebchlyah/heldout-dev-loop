#!/usr/bin/env python3
"""Held-out oracle tests for one session and effort per stage (spec: One Session and Effort per Stage), part 3:
the installer and the kit text, acceptance flow 4.

Each docstring names the `## Acceptance oracle` lines (O<n>) or flows (F<n>) it proves. Seams: `bin/install.mjs
init` and `upgrade` run as processes on temporary projects (the files they write, their output), and the text of
the kit source. A project "installed with the previous kit" is a fresh `init` taken back to version 2.1.0: the
new keys, the wall entry, the wall hook and `planner.toml` removed, with their MANIFEST entries. Every name in
the temp projects is invented.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "bin" / "install.mjs"
SKILL = REPO_ROOT / "skill" / "SKILL.md"
CLAUDE_REF = REPO_ROOT / "skill" / "references" / "claude.md"
CODEX_REF = REPO_ROOT / "skill" / "references" / "codex.md"
WALL_HOOK = ".dev-loop/hooks/claude/worktree-wall.sh"
PLANNER = ".codex/agents/planner.toml"
WALL_MATCHER = {"Edit", "Write", "NotebookEdit"}
GIT_USER = ("-c", "user.name=Robin Quill", "-c", "user.email=robin@example.invalid", "-c", "commit.gpgsign=false")


def paragraphs(text: str) -> list[str]:
    """Blank-line separated blocks, each list item its own block."""
    out = []
    for block in re.split(r"\n\s*\n", text):
        out.extend(re.split(r"\n(?=\s*(?:[-*]|\d+\.) )", block))
    return out


def sections(text: str) -> list[str]:
    """Each heading with its body, up to the next heading of any level."""
    return re.split(r"(?m)(?=^#{1,6} )", text)


def stage(text: str, number: int) -> str:
    """The `## <number> ` section of SKILL.md, up to the next `## ` heading."""
    match = re.search(rf"(?ms)^## {number} .*?(?=^## |\Z)", text)
    if match is None:
        raise AssertionError(f"no Stage {number} section")
    return match.group(0)


def wall_commands(settings: dict) -> list[tuple[set[str], str]]:
    """(matcher set, command) of each PreToolUse hook that runs worktree-wall.sh."""
    found = []
    for group in (settings.get("hooks") or {}).get("PreToolUse") or []:
        for hook in group.get("hooks") or []:
            if "worktree-wall.sh" in hook.get("command", ""):
                found.append((set((group.get("matcher") or "").split("|")), hook["command"]))
    return found


def frontmatter(path: Path) -> str:
    text = path.read_text()
    return text.split("---", 2)[1] if text.startswith("---") else ""


class InstallCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home, self.notes, scratch = self.root / "home", self.root / "notes", self.root / "tmp"
        for folder in (self.home, self.notes / "specs", scratch):
            folder.mkdir(parents=True)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("DEV_LOOP", "DEVLOOP", "CLAUDE", "CODEX", "GIT_"))}
        self.env.update(HOME=str(self.home), TMPDIR=str(scratch), GIT_CONFIG_NOSYSTEM="1", GIT_EDITOR="true",
                        GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1")

    def run_in(self, project: Path, *args: str) -> str:
        done = subprocess.run(args, cwd=project, env=self.env, capture_output=True, text=True, timeout=180)
        self.assertEqual(done.returncode, 0, f"{args}: {done.stdout[-800:]} {done.stderr[-800:]}")
        return done.stdout + done.stderr

    def init(self, name: str, harness: str) -> Path:
        project = self.root / name
        project.mkdir()
        self.run_in(project, "git", "init", "-q", "-b", "main")
        self.run_in(project, "node", str(INSTALLER), "init", "--yes", f"--harness={harness}", f"--notes={self.notes}")
        return project

    @staticmethod
    def settings(project: Path) -> dict:
        return json.loads((project / ".claude" / "settings.json").read_text())

    @staticmethod
    def write_settings(project: Path, settings: dict) -> None:
        (project / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")

    def previous_kit(self, project: Path, settings_keys: dict) -> None:
        """Take a fresh init back to the 2.1.0 kit: no wall entry, hook or planner, `settings_keys` as the user's
        top-level effort keys (None: absent), then commit and branch for the upgrade."""
        settings = self.settings(project)
        for group in (settings.get("hooks") or {}).get("PreToolUse") or []:
            group["hooks"] = [h for h in group.get("hooks") or [] if "worktree-wall.sh" not in h.get("command", "")]
        settings["hooks"]["PreToolUse"] = [g for g in settings["hooks"]["PreToolUse"] if g["hooks"]]
        for key, value in settings_keys.items():
            settings.pop(key, None)
            if value is not None:
                settings[key] = value
        self.write_settings(project, settings)
        for rel in (WALL_HOOK, PLANNER):
            if (project / rel).exists():
                (project / rel).unlink()
        manifest_path = project / ".dev-loop" / "MANIFEST.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"] = {k: v for k, v in manifest["files"].items() if k not in (WALL_HOOK, PLANNER)}
        manifest["version"] = "2.1.0"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        config_path = project / "dev-loop.json"
        config = json.loads(config_path.read_text())
        config["version"] = "2.1.0"
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        self.run_in(project, "git", "add", "-A")
        self.run_in(project, "git", *GIT_USER, "commit", "-q", "-m", "Kit 2.1.0")
        self.run_in(project, "git", "checkout", "-q", "-b", "kit-upgrade")

    def upgrade(self, project: Path) -> str:
        return self.run_in(project, "node", str(INSTALLER), "upgrade", "--yes")


class InitTests(InstallCase):
    def test_o25_o27_o28_init_writes_levels_and_the_wall(self) -> None:
        """O25, O27 (init), O28: `init` for Claude writes the two settings keys, the wall entry and its hook file;
        `init` for Codex writes the root effort key and `planner.toml`; each agent file has its level."""
        claude = self.init("claude-project", "claude")
        settings = self.settings(claude)
        with self.subTest(oracle=25):
            self.assertEqual(settings.get("effortLevel"), "high", settings)
            self.assertEqual(settings.get("autoCompactWindow"), 400000, settings)
            entries = wall_commands(settings)
            self.assertEqual([matcher for matcher, _ in entries], [WALL_MATCHER], settings.get("hooks"))
            self.assertTrue((claude / WALL_HOOK).is_file(), WALL_HOOK)
            self.assertTrue((claude / ".dev-loop" / "hooks" / "claude" / "deny-ask.sh").is_file())
        with self.subTest(oracle="28, claude"):
            self.assertRegex(frontmatter(claude / ".claude" / "agents" / "spec-tester.md"), r"(?m)^effort:\s*high\s*$")
            self.assertRegex(frontmatter(claude / ".claude" / "agents" / "implementer.md"), r"(?m)^effort:\s*medium\s*$")

        codex = self.init("codex-project", "codex")
        with self.subTest(oracle=27):
            config = tomllib.loads((codex / ".codex" / "config.toml").read_text())
            self.assertEqual(config.get("model_reasoning_effort"), "high", config)
            self.assertTrue((codex / PLANNER).is_file(), PLANNER)
            planner = tomllib.loads((codex / PLANNER).read_text())
            self.assertEqual(planner.get("name"), "planner", planner)
            self.assertEqual(planner.get("model_reasoning_effort"), "high", planner)
        with self.subTest(oracle="28, codex"):
            for name, level in (("spec-tester", "high"), ("task-reviewer", "high"), ("implementer", "medium")):
                agent = tomllib.loads((codex / ".codex" / "agents" / f"{name}.toml").read_text())
                self.assertEqual(agent.get("model_reasoning_effort"), level, name)


class UpgradeFlowTests(InstallCase):
    def test_f4_upgrade_keeps_the_user_level(self) -> None:
        """F4, O26 (user value kept, key added and named), O27 (Codex root value kept): a project installed with the
        previous kit and a user `effortLevel`; `upgrade` adds the wall entry, keeps the user value, adds
        `autoCompactWindow`, and writes `planner.toml` for Codex."""
        project = self.init("both-project", "both")
        config_path = project / ".codex" / "config.toml"
        config = config_path.read_text()
        if re.search(r"(?m)^model_reasoning_effort\s*=", config):
            config = re.sub(r"(?m)^model_reasoning_effort\s*=.*$", 'model_reasoning_effort = "low"', config)
        else:
            config = 'model_reasoning_effort = "low"\n' + config
        config_path.write_text(config)
        self.previous_kit(project, {"effortLevel": "medium", "autoCompactWindow": None})

        output = self.upgrade(project)
        settings = self.settings(project)
        self.assertEqual(settings.get("effortLevel"), "medium", settings)
        self.assertEqual(settings.get("autoCompactWindow"), 400000, settings)
        self.assertIn("autoCompactWindow", output)
        self.assertEqual([matcher for matcher, _ in wall_commands(settings)], [WALL_MATCHER], settings.get("hooks"))
        self.assertTrue((project / WALL_HOOK).is_file(), WALL_HOOK)
        self.assertTrue((project / PLANNER).is_file(), PLANNER)
        self.assertEqual(tomllib.loads((project / PLANNER).read_text()).get("model_reasoning_effort"), "high")
        self.assertEqual(tomllib.loads(config_path.read_text()).get("model_reasoning_effort"), "low")

    def test_o26_upgrade_never_changes_a_user_value(self) -> None:
        """O26: `upgrade` of a project with `"effortLevel": "medium"` and `"autoCompactWindow": 300000`: both
        values stay. A project file without the keys: both are added, and the report names them."""
        kept = self.init("kept-project", "claude")
        self.previous_kit(kept, {"effortLevel": "medium", "autoCompactWindow": 300000})
        self.upgrade(kept)
        settings = self.settings(kept)
        self.assertEqual((settings.get("effortLevel"), settings.get("autoCompactWindow")), ("medium", 300000), settings)

        bare = self.init("bare-project", "claude")
        self.previous_kit(bare, {"effortLevel": None, "autoCompactWindow": None})
        output = self.upgrade(bare)
        settings = self.settings(bare)
        self.assertEqual((settings.get("effortLevel"), settings.get("autoCompactWindow")), ("high", 400000), settings)
        self.assertIn("effortLevel", output)
        self.assertIn("autoCompactWindow", output)


class KitTextTests(unittest.TestCase):
    def test_o29_no_session_a_or_b(self) -> None:
        """O29: no kit source file, no `reference/` file and no AGENTS.md holds "Session A" or "Session B";
        GLOSSARY.md holds them only in an `_Avoid_` line and defines **Shaping** and **Delivery**."""
        files = [REPO_ROOT / "AGENTS.md"]
        for top in ("skill", "skills", "claude", "codex", "reference"):
            files += [p for p in (REPO_ROOT / top).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
        stale = [str(p.relative_to(REPO_ROOT)) for p in files
                 if re.search(r"Session [AB]\b", p.read_bytes().decode("utf-8", "ignore"))]
        self.assertEqual(stale, [])
        glossary = (REPO_ROOT / "GLOSSARY.md").read_text()
        lines = [line for line in glossary.splitlines() if re.search(r"Session [AB]\b", line)]
        self.assertTrue(lines, "GLOSSARY.md has no _Avoid_ line for Session A and Session B")
        self.assertEqual([line for line in lines if not line.startswith("_Avoid_")], [])
        self.assertRegex(glossary, r"(?m)^\*\*Shaping\*\*:")
        self.assertRegex(glossary, r"(?m)^\*\*Delivery\*\*:")

    def test_o30_hand_off_lines(self) -> None:
        """O30: Stage 2 names the three hand-off lines, each in its own code block, line 3 after the compaction;
        Stage 1 names `/effort medium` and `Accept Track S` for Track S."""
        text = SKILL.read_text()
        spec = stage(text, 2)
        for line in ("/effort medium", "/compact", "Start Delivery of <slug>"):
            self.assertIn(line, spec)
        self.assertRegex(spec, r"(?i)\bown code block")
        self.assertRegex(spec, r"(?i)after[^.\n]{0,80}compaction")
        frame = stage(text, 1)
        self.assertIn("/effort medium", frame)
        self.assertIn("Accept Track S", frame)

    def test_o31_one_session_by_default(self) -> None:
        """O31: SKILL.md says Shaping and Delivery run in one session by default and a new session is optional."""
        blocks = paragraphs(SKILL.read_text())
        self.assertTrue([b for b in blocks if "Shaping" in b and "Delivery" in b
                         and re.search(r"(?i)\b(one|same) session\b", b) and re.search(r"(?i)\bdefault\b", b)],
                        "no paragraph says Shaping and Delivery run in one session by default")
        self.assertTrue([b for b in blocks if re.search(r"(?i)new session[^.]{0,80}\boptional\b", b)],
                        "no paragraph says a new session is optional")

    def test_o32_effort_message(self) -> None:
        """O32: SKILL.md names the `status` `effort` key and the one-line message when `level` differs from `want`."""
        blocks = paragraphs(SKILL.read_text())
        self.assertTrue([b for b in blocks if "effort" in b and re.search(r"\blevel\b", b) and re.search(r"\bwant\b", b)
                         and "/effort" in b and re.search(r"(?i)\bone[- ]line\b", b)],
                        "no paragraph names the effort key, level, want and the one-line /effort message")

    def test_o33_planner_and_reviewer_levels(self) -> None:
        """O33: claude.md Stage 4 names `general-purpose`, `model: opus`, `effort: high`, `superpowers:writing-plans`,
        and `effort: high` for task reviewers; codex.md Stage 4 names the `planner` agent and says Codex has no
        worktree wall."""
        claude = CLAUDE_REF.read_text()
        self.assertTrue([s for s in sections(claude) if "Stage 4" in s and all(
            name in s for name in ("general-purpose", "model: opus", "effort: high", "superpowers:writing-plans"))],
                        "no claude.md section names the Stage 4 planner call")
        self.assertTrue([b for b in paragraphs(claude) if re.search(r"(?i)task review", b) and "effort: high" in b],
                        "claude.md names no effort: high for task reviewers")
        codex = CODEX_REF.read_text()
        self.assertTrue([s for s in sections(codex) if "Stage 4" in s and "planner" in s],
                        "codex.md names no planner agent for Stage 4")
        self.assertRegex(codex, r"(?i)\bno worktree wall\b")

    def test_o35_kit_reference(self) -> None:
        """O35: reference/kit.md names `effortLevel`, `autoCompactWindow`, `model_reasoning_effort` and
        `.claude/settings.local.json`."""
        kit = (REPO_ROOT / "reference" / "kit.md").read_text()
        for name in ("effortLevel", "autoCompactWindow", "model_reasoning_effort", ".claude/settings.local.json"):
            self.assertIn(name, kit)

    def test_o36_exit_worktree_after_the_merge(self) -> None:
        """O36: SKILL.md Stage 7 (or claude.md) names `ExitWorktree` with `keep` after the merge, and `/clear`
        before the next feature."""
        text = stage(SKILL.read_text(), 7) + "\n\n" + CLAUDE_REF.read_text()
        self.assertTrue([b for b in paragraphs(text) if "ExitWorktree" in b and re.search(r"\bkeep\b", b)
                         and re.search(r"(?i)merge", b)], "no ExitWorktree with keep after the merge")
        self.assertIn("/clear", text)


if __name__ == "__main__":
    unittest.main()
