"""Each frontmatter wall of a project agent has a settings-level twin (#139).

Claude Code skips the frontmatter hooks of a project agent in a folder that is not trusted, and in a `-p` or SDK
session; the hooks of the project settings still run there.
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest

from support import CLAUDE_HOOKS, ROOT

TWIN = "deny-acceptance-to-subagents.sh"


def frontmatter_walls() -> list[tuple[str, set[str], list[str]]]:
    """(agent name, tools of the PreToolUse matcher, folders `deny-path.sh` denies) of each agent with a wall."""
    walls = []
    for path in sorted((ROOT / "claude" / "agents").glob("*.md")):
        head = path.read_text().split("---")[1]
        command = re.search(r"deny-path\.sh ([^\"'\n]+)", head)
        if command:
            name = re.search(r"^name: (\S+)", head, re.M).group(1)
            matcher = re.search(r'matcher: "([^"]+)"', head).group(1)
            walls.append((name, set(matcher.split("|")), command.group(1).split()))
    return walls


class FrontmatterWallTwinTests(unittest.TestCase):
    def test_every_agent_wall_is_found(self) -> None:
        self.assertEqual([name for name, _, _ in frontmatter_walls()], ["implementer", "spec-tester"])

    def test_the_settings_twin_matches_every_tool_of_the_agent_wall(self) -> None:
        settings = json.loads((ROOT / "claude" / "settings.fragment.json").read_text())
        twins = [group["matcher"] for group in settings["hooks"]["PreToolUse"]
                 if any(TWIN in hook["command"] for hook in group["hooks"])]
        self.assertEqual(len(twins), 1)
        for name, tools, _ in frontmatter_walls():
            with self.subTest(agent=name):
                self.assertLessEqual(tools, set(twins[0].split("|")))

    def test_the_settings_twin_denies_what_the_agent_wall_denies(self) -> None:
        for name, _, folders in frontmatter_walls():
            for folder in folders:
                target = f"{folder.rstrip('/')}/probe.py"
                for tool, tool_input in (("Read", {"file_path": target}), ("Bash", {"command": f"cat {target}"}),
                                         ("Grep", {"pattern": "x", "path": target})):
                    payload = {"agent_id": "sub-1", "agent_type": name, "tool_name": tool, "tool_input": tool_input}
                    with self.subTest(agent=name, folder=folder, tool=tool):
                        done = subprocess.run(["bash", str(CLAUDE_HOOKS / TWIN)], input=json.dumps(payload), text=True,
                                              capture_output=True, check=False)
                        self.assertEqual(done.returncode, 2, done.stderr)


if __name__ == "__main__":
    unittest.main()
