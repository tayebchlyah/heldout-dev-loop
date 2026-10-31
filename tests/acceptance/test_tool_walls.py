#!/usr/bin/env python3
"""Held-out oracle tests for the tool walls (spec: Tool Walls), hook flows F1 and F2.

F1 also checks the Claude parts of the doc lines O24, O25; F2 the Codex parts. Fixtures and seams:
`tool_walls_support.py`.
"""

from __future__ import annotations

import json
import re
import unittest

from tool_walls_support import (CLAUDE_HOOKS, CLAUDE_ROUTE, CODEX_HOOKS, CODEX_ROUTE, CODEX_WALL, DENY_PATH,
                                HELD_OUT, LSP_LINE, MCP_LINE, NET, SEARCH_LINE, SOURCE_FILE, SOURCE_FRAGMENTS,
                                SUITE_FILE, ToolWallRepo, header, near)

MCP = r"MCP|mcp__"

# The Claude hooks each caller runs, with their arguments (the oracle fixture).
CLAUDE_CALLERS = {
    "implementer": ((NET, ()), (DENY_PATH, (HELD_OUT,))),
    "spec-tester": ((NET, ()), (DENY_PATH, SOURCE_FRAGMENTS)),
    "general-purpose": ((NET, ()),),
    None: ((NET, ()),),
}


class ClaudeSubagentToolsTests(ToolWallRepo):
    def messages(self, agent_type: str | None, name: str, args: tuple[str, ...]) -> tuple[str, str]:
        """The held-out and source-wall messages of one hook for one caller: its deny of a `Read` of each file."""
        key = (agent_type, name)
        if key not in self.__dict__.setdefault("_messages", {}):
            held, source = (self.hook("claude", name, self.pre_tool("Read", {"file_path": path}, agent_type), *args)
                            for path in (SUITE_FILE, SOURCE_FILE))
            self._messages[key] = (held.stderr.strip(), source.stderr.strip())
        return self._messages[key]

    def claude(self, agent_type: str | None, tool: str, tool_input: dict, **kw) -> list:
        """(hook, its held-out message, its source-wall message, result) for each hook of the caller."""
        cwd = kw.get("cwd")
        out = []
        for name, args in CLAUDE_CALLERS[agent_type]:
            held, source = self.messages(agent_type, name, args)
            result = self.hook("claude", name, self.pre_tool(tool, tool_input, agent_type, cwd), *args, cwd=cwd)
            out.append((name, held, source, result))
        return out

    def test_f1_claude_subagent_tools(self) -> None:
        """O1 to O9: an implementer, a spec-tester and a reviewer call `LSP`, MCP tools and `Monitor`; the main
        session keeps every tool. O24, O25: `claude.md` and the Claude hook headers name the walls and the gaps."""
        top = self.repo
        lsp_calls = (
            {"operation": "hover", "filePath": SOURCE_FILE, "line": 1, "character": 5},
            {"operation": "findReferences", "filePath": SOURCE_FILE, "line": 1, "character": 5},
            {"operation": "workspaceSymbol", "query": "make", "filePath": SOURCE_FILE},
            {"operation": "documentSymbol", "filePath": SUITE_FILE},
        )
        # O1.
        for agent_type in ("implementer", "spec-tester", "general-purpose"):
            for tool_input in lsp_calls:
                for name, _, _, result in self.claude(agent_type, "LSP", tool_input):
                    with self.subTest(oracle="O1", caller=agent_type, hook=name, call=tool_input["operation"]):
                        self.assert_denied(result, LSP_LINE)
        # O2.
        for tool_input in ({"operation": "findReferences", "filePath": SOURCE_FILE, "line": 1, "character": 5},
                           {"operation": "hover", "filePath": SUITE_FILE, "line": 3, "character": 1}):
            for name, _, _, result in self.claude(None, "LSP", tool_input):
                with self.subTest(oracle="O2", call=tool_input["operation"]):
                    self.assert_allowed(result)

        # O3.
        closed = (("mcp__fs__read_file", {"path": "README.md"}, {}),
                  ("ListMcpResourcesTool", {}, {}),
                  ("ReadMcpResourceTool", {"server": "fs", "uri": "file:///tmp/x.md"}, {}),
                  ("ReadMcpResourceDirTool", {"server": "fs", "uri": "file:///tmp"}, {}))
        for agent_type in ("implementer", "spec-tester"):
            for tool, tool_input, kw in closed:
                for name, _, _, result in self.claude(agent_type, tool, tool_input, **kw):
                    with self.subTest(oracle="O3", caller=agent_type, hook=name, tool=tool):
                        self.assert_denied(result, MCP_LINE)
        # O4.
        named = (("mcp__fs__read_file", {"path": SUITE_FILE}, {}),
                 ("mcp__fs__read_file", {"filePath": SUITE_FILE}, {}),
                 ("mcp__fs__read_file", {"paths": [SOURCE_FILE, SUITE_FILE]}, {}),
                 ("ReadMcpResourceTool", {"server": "fs", "uri": f"file://{top}/{SUITE_FILE}"}, {}),
                 ("mcp__fs__read_file", {"path": "test_x.py"}, {"cwd": top / HELD_OUT}))
        for tool, tool_input, kw in named:
            for name, held, _, result in self.claude("general-purpose", tool, tool_input, **kw):
                with self.subTest(oracle="O4", tool=tool, call=json.dumps(tool_input), **kw):
                    self.assertTrue(held, f"{name}: no held-out message on a Read of {SUITE_FILE}")
                    self.assert_denied(result, held)
        # O5.
        for tool, tool_input in (("mcp__fs__read_file", {"path": SOURCE_FILE}),
                                 ("mcp__chat__send", {"text": f"see {SUITE_FILE}"}),
                                 ("ListMcpResourcesTool", {}),
                                 ("ReadMcpResourceTool", {"server": "fs", "uri": f"file://{top}/{SOURCE_FILE}"})):
            for name, _, _, result in self.claude("general-purpose", tool, tool_input):
                with self.subTest(oracle="O5", tool=tool, call=json.dumps(tool_input)):
                    self.assert_allowed(result)
        # O6.
        for tool, tool_input, kw in closed + named:
            for name, _, _, result in self.claude(None, tool, tool_input, **kw):
                with self.subTest(oracle="O6", tool=tool, call=json.dumps(tool_input), **kw):
                    self.assert_allowed(result)

        # O7.
        for agent_type in ("implementer", "general-purpose"):
            for name, held, _, result in self.claude(agent_type, "Monitor", {"command": f"cat {SUITE_FILE}"}):
                with self.subTest(oracle="O7", caller=agent_type, hook=name, command="cat"):
                    self.assertTrue(held, f"{name}: no held-out message on a Read of {SUITE_FILE}")
                    self.assert_denied(result, held)
            for name, _, _, result in self.claude(agent_type, "Monitor", {"command": "grep -rn make ."}):
                with self.subTest(oracle="O7", caller=agent_type, hook=name, command="grep ."):
                    self.assert_denied(result, SEARCH_LINE)
            for command in ("tail -f build.log", "grep -rn make src"):
                for name, _, _, result in self.claude(agent_type, "Monitor", {"command": command}):
                    with self.subTest(oracle="O7", caller=agent_type, hook=name, command=command):
                        self.assert_allowed(result)
        # O8.
        for name, _, source, result in self.claude("spec-tester", "Monitor", {"command": f"cat {SOURCE_FILE}"}):
            with self.subTest(oracle="O8", caller="spec-tester", hook=name):
                self.assertTrue(source, f"{name}: no source-wall message on a Read of {SOURCE_FILE}")
                self.assert_denied(result, source)
        for name, _, _, result in self.claude(None, "Monitor", {"command": f"cat {SUITE_FILE}"}):
            with self.subTest(oracle="O8", caller="main session"):
                self.assert_allowed(result)
        # O9.
        for command, deny in (("grep -rn make .", True), ("grep -rn make src", False)):
            with self.subTest(oracle="O9", command=command):
                code, out = self.cli("search-wall", stdin=json.dumps(
                    self.pre_tool("Monitor", {"command": command}, "implementer")))
                self.assertEqual((code, out.get("deny")), (0, deny), out)

        # O24, Claude route.
        route = CLAUDE_ROUTE.read_text()
        for pattern in ("LSP", "disallowedTools", "#139", near("Monitor", "Bash"), near(MCP, "implementer"),
                        near(MCP, "spec-tester"), near(MCP, "search"), near(MCP, "remote"), near(MCP, "resource")):
            with self.subTest(oracle="O24", file="claude.md", pattern=str(pattern)):
                self.assertTrue(re.search(pattern, route), f"no match for {pattern}")
        # O25, Claude hook headers.
        for script in (NET, DENY_PATH):
            text = header(CLAUDE_HOOKS / script)
            for pattern in ("LSP", "Monitor", MCP, "remote", "#139"):
                with self.subTest(oracle="O25", script=f"claude/hooks/{script}", pattern=pattern):
                    self.assertTrue(re.search(pattern, text), f"no match for {pattern}")


class CodexSubagentToolsTests(ToolWallRepo):
    def codex(self, agent_type: str | None, tool: str, tool_input: dict):
        return self.hook("codex", CODEX_WALL, self.pre_tool(tool, tool_input, agent_type))

    def message(self, agent_type: str, path: str) -> str:
        """The name-rule deny of `acceptance-wall.sh` for this caller on a `cat` of `path`."""
        result = self.codex(agent_type, "Bash", {"command": f"cat {path}"})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        return result.stderr.strip()

    def test_f2_codex_subagent_tools(self) -> None:
        """O10 to O13: an implementer, a spec_tester and a reviewer call MCP tools and `view_image`; the main
        session keeps every tool. O24, O25: `codex.md` and the Codex hook headers name the walls and the gaps."""
        top = self.repo
        closed = (("mcp__fs__read_file", {"path": "README.md"}),
                  ("read_mcp_resource", {"server": "fs", "uri": "file:///tmp/x.md"}),
                  ("list_mcp_resources", {"server": "fs"}),
                  ("list_mcp_resource_templates", {"server": "fs"}))
        # O10.
        for agent_type in ("implementer", "spec_tester"):
            for tool, tool_input in closed:
                with self.subTest(oracle="O10", caller=agent_type, tool=tool):
                    self.assert_denied(self.codex(agent_type, tool, tool_input), MCP_LINE)
        # O11.
        held = self.message("task-reviewer", SUITE_FILE)
        named = (("mcp__fs__read_file", {"path": SUITE_FILE}),
                 ("read_mcp_resource", {"server": "fs", "uri": f"file://{top}/{SUITE_FILE}"}),
                 ("view_image", {"path": f"{HELD_OUT}/shot.png"}),
                 ("view_image", {"path": f"{top}/{HELD_OUT}/shot.png"}))
        for tool, tool_input in named:
            with self.subTest(oracle="O11", tool=tool, call=json.dumps(tool_input)):
                self.assert_denied(self.codex("task-reviewer", tool, tool_input), held)
        open_calls = (("mcp__fs__read_file", {"path": SOURCE_FILE}),
                      ("list_mcp_resources", {"server": "fs"}),
                      ("view_image", {"path": "shots/home.png"}))
        for tool, tool_input in open_calls:
            with self.subTest(oracle="O11", tool=tool, call=json.dumps(tool_input)):
                self.assert_allowed(self.codex("task-reviewer", tool, tool_input))
        # O12.
        images = ((f"{HELD_OUT}/shot.png", "implementer", None),
                  ("shots/home.png", "implementer", 0),
                  ("src/logo.png", "spec_tester", self.message("spec_tester", SOURCE_FILE)),
                  (f"{HELD_OUT}/shot.png", "spec_tester", 0))
        for path, agent_type, expected in images:
            with self.subTest(oracle="O12", caller=agent_type, path=path):
                result = self.codex(agent_type, "view_image", {"path": path})
                if expected == 0:
                    self.assert_allowed(result)
                else:
                    self.assert_denied(result, expected)
        # O13.
        main_calls = closed + named + open_calls + tuple(("view_image", {"path": p}) for p, _, _ in images)
        for tool, tool_input in main_calls:
            with self.subTest(oracle="O13", tool=tool, call=json.dumps(tool_input)):
                self.assert_allowed(self.codex(None, tool, tool_input))

        # O24, Codex route.
        route = CODEX_ROUTE.read_text()
        for pattern in ("view_image", near(MCP, "implementer"), near(MCP, "spec_tester"),
                        near(r"\bexec\b", "inner"), near("write_stdin", "#140")):
            with self.subTest(oracle="O24", file="codex.md", pattern=str(pattern)):
                self.assertTrue(re.search(pattern, route), f"no match for {pattern}")
        # O25, Codex hook headers.
        for script in (DENY_PATH, CODEX_WALL):
            text = header(CODEX_HOOKS / script)
            for pattern in ("view_image", MCP, "remote", r"\bexec\b", "write_stdin"):
                with self.subTest(oracle="O25", script=f"codex/hooks/{script}", pattern=pattern):
                    self.assertTrue(re.search(pattern, text), f"no match for {pattern}")


if __name__ == "__main__":
    unittest.main()
