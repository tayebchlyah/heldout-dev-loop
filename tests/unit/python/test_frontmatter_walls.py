"""Each frontmatter wall of a project agent has a settings-level twin (#139).

Claude Code skips the frontmatter hooks of a project agent in a folder that is not trusted, and in a `-p` or SDK
session; the hooks of the project settings still run there.
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path
from typing import NamedTuple

from support import CLAUDE_HOOKS, ROOT

TWIN = "deny-acceptance-to-subagents.sh"
MCP_TOOLS = ("ListMcpResourcesTool", "ReadMcpResourceTool", "ReadMcpResourceDirTool")


class Wall(NamedTuple):
    name: str
    tools: list[str]
    folders: list[str]


def frontmatter(path: Path) -> list[str]:
    lines = path.read_text().splitlines()
    assert lines[0] == "---", path
    return lines[1:lines.index("---", 1)]


def frontmatter_hooks(head: list[str]) -> list[tuple[str, str, str]]:
    found, event, matcher, inside = [], "", "", False
    for line in head:
        if not line.startswith(" "):
            inside = line.startswith("hooks:")
        elif inside:
            if key := re.match(r"  (\w+):", line):
                event = key.group(1)
            if found_matcher := re.search(r'matcher: "([^"]*)"', line):
                matcher = found_matcher.group(1)
            if command := re.search(r"command: [\"']?([^\"'\n]+)", line):
                found.append((event, matcher, command.group(1).strip()))
    return found


def agent_heads() -> list[tuple[str, list[str]]]:
    heads = []
    for path in sorted((ROOT / "claude" / "agents").glob("*.md")):
        head = frontmatter(path)
        heads.append((next(line.split(": ", 1)[1] for line in head if line.startswith("name: ")), head))
    return heads


def frontmatter_walls() -> list[Wall]:
    walls = []
    for name, head in agent_heads():
        for event, matcher, command in frontmatter_hooks(head):
            if event == "PreToolUse" and (folders := re.search(r"deny-path\.sh (.+)", command)):
                walls.append(Wall(name, matcher.split("|"), folders.group(1).split()))
    return walls


def probe(tool: str, target: str) -> tuple[str, dict[str, str]]:
    if tool.startswith("mcp__"):
        return "mcp__x__read", {"path": target}
    return tool, {
        "Read": {"file_path": target}, "Edit": {"file_path": target}, "Write": {"file_path": target},
        "Grep": {"pattern": "x", "path": target}, "Glob": {"pattern": "*.py", "path": target},
        "Bash": {"command": f"cat {target}"}, "Monitor": {"command": f"cat {target}"},
        "LSP": {"filePath": target}, "ListMcpResourcesTool": {},
        "ReadMcpResourceTool": {"uri": target}, "ReadMcpResourceDirTool": {"uri": target},
    }[tool]


def run_twin(agent: str, tool: str, tool_input: dict[str, str]) -> subprocess.CompletedProcess[str]:
    payload = {"agent_id": "sub-1", "agent_type": agent, "tool_name": tool, "tool_input": tool_input}
    return subprocess.run(["bash", str(CLAUDE_HOOKS / TWIN)], input=json.dumps(payload), text=True,
                          capture_output=True, check=False)


class FrontmatterWallTwinTests(unittest.TestCase):
    def test_every_agent_wall_is_found(self) -> None:
        self.assertEqual([wall.name for wall in frontmatter_walls()], ["implementer", "spec-tester"])

    def test_every_agent_hook_is_a_pretooluse_deny_path_command(self) -> None:
        for name, head in agent_heads():
            for event, _, command in frontmatter_hooks(head):
                with self.subTest(agent=name, event=event, command=command):
                    self.assertEqual(event, "PreToolUse")
                    self.assertRegex(command, r"deny-path\.sh \S")

    def test_the_settings_twin_matches_every_tool_of_the_agent_wall(self) -> None:
        settings = json.loads((ROOT / "claude" / "settings.fragment.json").read_text())
        twins = [group["matcher"] for group in settings["hooks"]["PreToolUse"]
                 if any(TWIN in hook["command"] for hook in group["hooks"])]
        self.assertEqual(len(twins), 1)
        for wall in frontmatter_walls():
            with self.subTest(agent=wall.name):
                self.assertLessEqual(set(wall.tools), set(twins[0].split("|")))

    def test_the_settings_twin_denies_what_the_agent_wall_denies(self) -> None:
        for wall in frontmatter_walls():
            for folder in wall.folders:
                for tool in wall.tools:
                    name, tool_input = probe(tool, f"{folder.rstrip('/')}/probe.py")
                    if name == "LSP":
                        expected = "Blocked: LSP is closed to subagents"
                    elif name.startswith("mcp__") or name in MCP_TOOLS:
                        expected = "Blocked: MCP tools are closed to this agent"
                    elif wall.name == "implementer":
                        expected = "Blocked: held-out suite. Subagents cannot read it."
                    else:
                        expected = f"Blocked: {folder} is outside this agent's territory."
                    with self.subTest(agent=wall.name, folder=folder, tool=tool):
                        done = run_twin(wall.name, name, tool_input)
                        self.assertEqual(done.returncode, 2, done.stderr)
                        self.assertTrue(done.stderr.startswith(expected), done.stderr)
                        self.assertNotIn("guard error", done.stderr)

    def test_the_settings_twin_allows_a_path_outside_the_denied_folders(self) -> None:
        for wall in frontmatter_walls():
            with self.subTest(agent=wall.name):
                done = run_twin(wall.name, "Read", {"file_path": "docs/probe.py"})
                self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()
