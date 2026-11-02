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
MCP_NAME = "mcp__probe__read"
PROBES = {
    "Read": lambda target: {"file_path": target},
    "Edit": lambda target: {"file_path": target, "old_string": "a", "new_string": "b"},
    "Write": lambda target: {"file_path": target, "content": "x"},
    "Grep": lambda target: {"pattern": "x", "path": target},
    "Glob": lambda target: {"pattern": "*.py", "path": target},
    "Bash": lambda target: {"command": f"cat {target}"},
    "Monitor": lambda target: {"command": f"cat {target}"},
    "LSP": lambda target: {"operation": "hover", "filePath": target},
    MCP_NAME: lambda target: {"path": target},
    "ListMcpResourcesTool": lambda target: {},
    "ReadMcpResourceTool": lambda target: {"uri": f"file://{target}"},
    "ReadMcpResourceDirTool": lambda target: {"uri": f"file://{target}"},
}


class Hook(NamedTuple):
    event: str
    matcher: str
    command: str


class Wall(NamedTuple):
    name: str
    tools: frozenset[str]
    folders: tuple[str, ...]


def agent_hooks(path: Path) -> tuple[str, list[Hook]]:
    lines = path.read_text().splitlines()
    head = lines[1:lines.index("---", 1)]
    name = next(line.split(": ", 1)[1] for line in head if line.startswith("name: "))
    hooks, inside, event, matcher = [], False, "", ""
    for line in head:
        if not line.startswith(" "):
            inside = line == "hooks:"
        elif inside and (found := re.fullmatch(r"  (\w+):", line)):
            event, matcher = found[1], ""
        elif inside and (found := re.fullmatch(r'\s+- matcher: "([^"]*)"', line)):
            matcher = found[1]
        elif inside and (found := re.fullmatch(r'\s+(?:- )?command: "([^"]*)"', line)):
            hooks.append(Hook(event, matcher, found[1]))
    return name, hooks


def agents() -> list[tuple[str, list[Hook]]]:
    return [agent_hooks(path) for path in sorted((ROOT / "claude" / "agents").glob("*.md"))]


def walls() -> list[Wall]:
    return [Wall(name, frozenset(hook.matcher.split("|")), tuple(hook.command.split()[1:]))
            for name, hooks in agents() for hook in hooks if hook.command.split()[0].endswith("/deny-path.sh")]


def twin(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(CLAUDE_HOOKS / TWIN)], input=json.dumps(payload), text=True,
                          capture_output=True, check=False)


def block_message(wall: Wall, tool: str, folder: str) -> str:
    if tool == "LSP":
        return "Blocked: LSP is closed to subagents"
    if tool.startswith("mcp__") or "Mcp" in tool:
        return "Blocked: MCP tools are closed to this agent."
    if wall.name == "spec-tester":
        return f"Blocked: {folder} is outside this agent's territory.\n"
    return "Blocked: held-out suite. Subagents cannot read it.\n"


class FrontmatterWallTwinTests(unittest.TestCase):
    def test_every_agent_wall_is_found(self) -> None:
        self.assertEqual([wall.name for wall in walls()], ["implementer", "spec-tester"])

    def test_every_frontmatter_hook_is_a_deny_path_wall(self) -> None:
        for name, hooks in agents():
            for hook in hooks:
                with self.subTest(agent_file=name, hook=hook.command):
                    self.assertEqual(hook.event, "PreToolUse")
                    self.assertRegex(hook.command, r"^\S+/deny-path\.sh \S")

    def test_the_settings_twin_matches_every_tool_of_the_agent_wall(self) -> None:
        settings = json.loads((ROOT / "claude" / "settings.fragment.json").read_text())
        matchers = [group["matcher"] for group in settings["hooks"]["PreToolUse"]
                    if any(TWIN in hook["command"] for hook in group["hooks"])]
        self.assertEqual(len(matchers), 1)
        for wall in walls():
            with self.subTest(wall=wall.name):
                self.assertLessEqual(wall.tools, set(matchers[0].split("|")))

    def test_the_settings_twin_denies_every_tool_of_the_agent_wall(self) -> None:
        for wall in walls():
            for folder in wall.folders:
                target = f"{folder.rstrip('/')}/probe.py"
                for pattern in sorted(wall.tools):
                    tool = MCP_NAME if pattern == "mcp__.*" else pattern
                    payload = {"agent_id": "sub-1", "agent_type": wall.name, "tool_name": tool,
                               "tool_input": PROBES[tool](target)}
                    with self.subTest(wall=wall.name, folder=folder, tool=tool):
                        done = twin(payload)
                        self.assertEqual(done.returncode, 2, done.stderr)
                        self.assertTrue(done.stderr.startswith(block_message(wall, tool, folder)), done.stderr)

    def test_the_settings_twin_lets_each_walled_agent_read_outside_its_wall(self) -> None:
        for wall in walls():
            payload = {"agent_id": "sub-1", "agent_type": wall.name, "tool_name": "Read",
                       "tool_input": {"file_path": "docs/probe.py"}}
            with self.subTest(wall=wall.name):
                done = twin(payload)
                self.assertEqual((done.returncode, done.stderr), (0, ""))


if __name__ == "__main__":
    unittest.main()
