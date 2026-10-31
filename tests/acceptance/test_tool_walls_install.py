#!/usr/bin/env python3
"""Held-out oracle tests for the tool walls (spec: Tool Walls), install and upgrade flow F3.

Seams: the kit source files, and `bin/install.mjs init` and `upgrade` run on scratch projects. Fixtures:
`tool_walls_support.py`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from tool_walls_support import (AGENT_MATCHER, CLAUDE_AGENTS, CLAUDE_FRAGMENT, CLAUDE_MATCHER, CODEX_FRAGMENT,
                                CODEX_MATCHER, CODEX_WALL, DENY_PATH, DISALLOWED, INSTALLER, KIT_REFERENCE, NET, git)

OLD_CLAUDE_MATCHER = "Read|Edit|Write|NotebookEdit|Grep|Glob|Bash"
OLD_CODEX_MATCHER = "Bash|apply_patch"
USER_GROUP = {"matcher": "Bash", "hooks": [{"type": "command", "command": '"$HOME/bin/audit-my-shell.sh"',
                                             "timeout": 7}]}


def groups_running(config: object, script: str) -> list[tuple[list, dict]]:
    """(the list holding it, the group) of each hook group, at any depth, whose `hooks` run `script`."""
    found = []
    if isinstance(config, dict):
        for value in config.values():
            found.extend(groups_running(value, script))
    elif isinstance(config, list):
        for item in config:
            if isinstance(item, dict) and isinstance(item.get("hooks"), list) and any(
                    script in str(hook.get("command", "")) for hook in item["hooks"] if isinstance(hook, dict)):
                found.append((config, item))
            else:
                found.extend(groups_running(item, script))
    return found


def frontmatter(path: Path) -> str:
    return path.read_text().split("---", 2)[1]


def frontmatter_entries(text: str) -> list[str]:
    """The hook entries of a frontmatter, one text per `- matcher:` item."""
    starts = [m.start() for m in re.finditer(r"^\s*-\s*matcher\s*:", text, re.MULTILINE)]
    return [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]


def entry_matcher(entry: str) -> str:
    return re.search(r"matcher\s*:\s*[\"']?([^\"'\n]+?)[\"']?\s*$", entry, re.MULTILINE).group(1)


def disallowed_tools(text: str) -> list[str] | None:
    """The `disallowedTools` names of a frontmatter: an inline list or string, or a block list."""
    match = re.search(r"^disallowedTools\s*:[ \t]*(.*)$", text, re.MULTILINE)
    if not match:
        return None
    inline = match.group(1).strip()
    if inline:
        items = inline.strip("[]").split(",")
    else:
        items = []
        for line in text[match.end():].lstrip("\n").splitlines():
            item = re.match(r"^\s+-\s*(.+)$", line)
            if not item:
                break
            items.append(item.group(1))
    return [item.strip().strip("\"'") for item in items if item.strip()]


class InstallUpgradeTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.notes = self.root / "notes"
        self.notes.mkdir()
        self.env = {**os.environ, "HOME": str(self.home)}

    def install(self, command: str, project: Path, *args: str) -> str:
        result = subprocess.run(["node", str(INSTALLER), command, *args], cwd=project,
                                env=self.env, capture_output=True, text=True,
                                timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def project(self, name: str) -> Path:
        project = self.root / name
        project.mkdir()
        git(project, "init", "-q")
        self.install("init", project, "--yes", "--harness=both", f"--notes={self.notes}")
        return project

    def assert_agent(self, path: Path) -> None:
        """D8, D9 in an agent file: the `deny-path.sh` entry matcher, `onFailure` block, `disallowedTools`."""
        text = frontmatter(path)
        entries = [e for e in frontmatter_entries(text) if DENY_PATH in e]
        self.assertTrue(entries, f"no {DENY_PATH} entry in {path}")
        for entry in entries:
            self.assertEqual(entry_matcher(entry), AGENT_MATCHER, path)
            self.assertTrue(re.search(r"onFailure\s*:\s*[\"']?block[\"']?", entry), f"no onFailure block in {path}")
        self.assertEqual(sorted(disallowed_tools(text) or []), sorted(DISALLOWED), path)
        self.assertIsNone(re.search(r"^tools\s*:", text, re.MULTILINE), f"a tools key in {path}")

    def assert_one_entry(self, config: dict, script: str, matcher: str) -> None:
        found = groups_running(config, script)
        self.assertEqual(len(found), 1, f"{script} groups")
        self.assertEqual(found[0][1].get("matcher"), matcher, script)

    def test_f3_install_and_upgrade_write_the_wall_entries(self) -> None:
        """O14 to O18: the kit source matchers and agent keys; `init` writes them; `upgrade` replaces the earlier
        wall entries and keeps a user entry; `reference/kit.md` names the Codex hook trust step."""
        # O14, O15: the kit source.
        claude_fragment = json.loads(CLAUDE_FRAGMENT.read_text())
        self.assert_one_entry(claude_fragment, NET, CLAUDE_MATCHER)
        for script in (NET, "git-guard.sh"):
            for _, group in groups_running(claude_fragment, script):
                for hook in group["hooks"]:
                    with self.subTest(oracle="O14", script=script, hook=hook.get("command")):
                        self.assertEqual(hook.get("onFailure"), "block", script)
        self.assert_one_entry(json.loads(CODEX_FRAGMENT.read_text()), CODEX_WALL, CODEX_MATCHER)
        for name in ("implementer.md", "spec-tester.md"):
            with self.subTest(oracle="O14, O15", agent=f"claude/agents/{name}"):
                self.assert_agent(CLAUDE_AGENTS / name)

        # O16: init.
        fresh = self.project("fresh-gizmos")
        self.assert_one_entry(json.loads((fresh / ".claude" / "settings.json").read_text()), NET, CLAUDE_MATCHER)
        self.assert_one_entry(json.loads((fresh / ".codex" / "hooks.json").read_text()), CODEX_WALL, CODEX_MATCHER)
        for name in ("implementer.md", "spec-tester.md"):
            with self.subTest(oracle="O16", agent=f".claude/agents/{name}"):
                self.assert_agent(fresh / ".claude" / "agents" / name)

        # O17: upgrade of the earlier entries, a user entry beside each.
        old = self.project("old-gizmos")
        files = ((old / ".claude" / "settings.json", NET, OLD_CLAUDE_MATCHER, CLAUDE_MATCHER),
                 (old / ".codex" / "hooks.json", CODEX_WALL, OLD_CODEX_MATCHER, CODEX_MATCHER))
        for path, script, old_matcher, _ in files:
            config = json.loads(path.read_text())
            found = groups_running(config, script)
            self.assertEqual(len(found), 1, f"{script} groups in {path}")
            holder, group = found[0]
            group["matcher"] = old_matcher
            holder.append(json.loads(json.dumps(USER_GROUP)))
            path.write_text(json.dumps(config, indent=2) + "\n")
        self.install("upgrade", old)
        for path, script, _, matcher in files:
            with self.subTest(oracle="O17", file=path.name):
                config = json.loads(path.read_text())
                self.assert_one_entry(config, script, matcher)
                user = groups_running(config, "audit-my-shell.sh")
                self.assertEqual([group for _, group in user], [USER_GROUP], f"user entry in {path}")

        # O18: the upgrade notes name the Codex hook trust step for this upgrade.
        text = KIT_REFERENCE.read_text()
        section = re.search(r"^## Upgrade\b.*?(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(section, "no ## Upgrade section in reference/kit.md")
        paragraphs = [p for p in re.split(r"\n\s*\n", section.group(0))
                      if re.search(r"view_image|mcp__|\bLSP\b|\bMonitor\b", p)]
        self.assertTrue(paragraphs, "no upgrade note names the tool walls")
        self.assertTrue(any(re.search(r"Codex", p) and re.search(r"trust|/hooks", p) for p in paragraphs),
                        "no tool walls upgrade note names the Codex hook trust step")


if __name__ == "__main__":
    unittest.main()
