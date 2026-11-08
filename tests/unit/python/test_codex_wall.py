#!/usr/bin/env python3
"""Codex acceptance-wall.sh, fed hook JSON on stdin."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_devloop import git, put  # noqa: E402  (also sets the core.fsmonitor override)
from test_seal import SUITE  # noqa: E402
from test_seal_hooks import patch  # noqa: E402

from support import ROOT  # noqa: E402


class CodexWallTests(unittest.TestCase):
    """Codex acceptance-wall.sh, fed hook JSON on stdin."""

    def wall(self, payload: dict) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["sh", str(ROOT / "codex/hooks/acceptance-wall.sh")], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=60)

    def test_o3_o4_walls_by_agent_type(self) -> None:
        def patch(*headers: str, body: str = "+x") -> str:
            return "\n".join(["*** Begin Patch", *headers, body, "*** End Patch"])

        main: dict = {}
        implementer = {"agent_id": "a", "agent_type": "implementer"}
        tester = {"agent_id": "b", "agent_type": "spec_tester"}
        for caller, cwd, tool, tool_input, deny in [
            (main, "/repo", "apply_patch", {"command": patch("*** Update File: tests/acceptance/x.py")}, True),
            (main, "/repo", "apply_patch",
             {"command": patch("*** Add File: src/y.py", body="+# see tests/acceptance/x.py")}, False),
            (main, "/repo", "apply_patch", {"patch": patch("*** Delete File: /repo/tests/acceptance/x.py")}, True),
            (main, "/repo", "apply_patch",
             {"command": patch("*** Update File: src/y.py", "*** Move to: tests/acceptance/y.py")}, True),
            (main, "/repo/tests", "apply_patch", {"command": patch("*** Add File: acceptance/x.py")}, True),
            (main, "/repo", "Edit", {"command": patch("*** Update File: tests/acceptance/x.py")}, True),
            (main, "/repo", "apply_patch", {"command": patch("*** Add File: tests/acceptance_notes/x.md")}, False),
            (main, "/repo", "Bash", {"command": f"cat {SUITE}"}, False),
            (implementer, "/repo", "Bash", {"command": f"cat {SUITE}"}, True),
            (implementer, "/repo/tests", "Bash", {"command": "cat acceptance/x.py"}, True),
            (implementer, "/repo", "Bash", {"command": "cat src/y.py"}, False),
            (implementer, "/repo", "apply_patch", {"command": patch("*** Update File: tests/acceptance/x.py")}, True),
            (implementer, "/repo", "apply_patch",
             {"command": patch("*** Add File: docs/y.md", body="+never read tests/acceptance")}, False),
            (tester, "/repo", "Bash", {"command": "cat src/y.py"}, True),
            (tester, "/repo/src", "Bash", {"command": "cat y.py"}, True),
            (tester, "/repo", "Bash", {"command": f"cat {SUITE} tests/test_mapping.py"}, False),
            (tester, "/repo", "apply_patch", {"command": patch("*** Add File: tests/acceptance/x.py")}, False),
            (tester, "/repo", "apply_patch", {"command": patch("*** Update File: app/main.py")}, True),
        ]:
            with self.subTest(caller=caller.get("agent_type", "main"), cwd=cwd, tool=tool, tool_input=tool_input):
                result = self.wall({**caller, "cwd": cwd, "tool_name": tool, "tool_input": tool_input})
                self.assertEqual(result.returncode, 2 if deny else 0, result.stderr)
                self.assertEqual(result.stderr.count("\n"), 1 if deny else 0, result.stderr)

    def test_tool_walls_by_agent_type(self) -> None:
        """MCP tools and view_image (#97)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        top = Path(tmp.name).resolve() / "repo"
        put(top / "README.md")
        put(top / "tests" / "README.md")
        git(top, "init", "-q")
        H = "tests/" + "acceptance"
        M = "Blocked: MCP tools are closed to this agent. Read and search named folders with the file tools or the shell.\n"
        held, src = f"Blocked: this agent cannot access {H}\n", "Blocked: this agent cannot access src/\n"
        impl, tester = {"agent_id": "a", "agent_type": "implementer"}, {"agent_id": "b", "agent_type": "spec_tester"}
        reviewer, main = {"agent_id": "c", "agent_type": "task-reviewer"}, {}
        mcp_m = [("mcp__fs__read_file", {"path": "README.md"}), ("read_mcp_resource", {"server": "fs", "uri": "file:///tmp/x.md"}),
                 ("list_mcp_resources", {"server": "fs"}), ("list_mcp_resource_templates", {"server": "fs"})]
        mcp_held = [("mcp__fs__read_file", {"path": f"{H}/test_x.py"}), ("mcp__fs__read_file", {"filePath": f"{H}/test_x.py"}),
                    ("read_mcp_resource", {"server": "fs", "uri": f"file://{top}/{H}/test_x.py"}),
                    ("read_mcp_resource", {"server": "fs", "uri": f"file://localhost{top}/{H}/test_x.py"}),
                    ("view_image", {"path": f"{H}/shot.png"}), ("view_image", {"path": f"{top}/{H}/shot.png"})]
        mcp_ok = [("mcp__fs__read_file", {"path": "src/app.py"}), ("list_mcp_resources", {"server": "fs"}),
                  ("view_image", {"path": "shots/home.png"})]
        rows = [(c, *call, M, top) for c in (impl, tester) for call in mcp_m]
        rows += [(reviewer, *call, held, top) for call in mcp_held]
        rows += [(reviewer, *call, None, top) for call in mcp_ok]
        rows += [(impl, "view_image", {"path": f"{H}/shot.png"}, held, top),
                 (impl, "view_image", {"path": "acceptance/shot.png"}, held, top / "tests"),
                 (impl, "view_image", {"path": "shots/home.png"}, None, top),
                 (tester, "view_image", {"path": "src/logo.png"}, src, top),
                 (tester, "view_image", {"path": f"{H}/shot.png"}, None, top)]
        rows += [(main, *call, None, top) for call in mcp_m + mcp_held + mcp_ok + [("view_image", {"path": "src/logo.png"})]]
        for caller, tool, tool_input, expected, cwd in rows:
            with self.subTest(caller=caller.get("agent_type", "main"), tool=tool, tool_input=tool_input, cwd=cwd):
                result = self.wall({**caller, "cwd": str(cwd), "tool_name": tool, "tool_input": tool_input})
                self.assertEqual((result.returncode, result.stderr), (0, "") if expected is None else (2, expected))

    def test_web_tools_by_agent_type(self) -> None:
        """Web tools are denied to implementer and spec_tester only, with agent_id set (#240)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        top = Path(tmp.name).resolve() / "repo"
        put(top / "README.md")
        git(top, "init", "-q")
        calls = [("webrun", {"tool_input": {"search_query": [{"q": "x"}]}}),
                 ("webrun", {"tool_input": {"open": [{"ref_id": "https://example.com"}]}}),
                 ("web__run", {"tool_input": {"search_query": [{"q": "x"}]}}),
                 ("webrun", {})]
        walled = [{"agent_id": "a", "agent_type": "implementer"}, {"agent_id": "b", "agent_type": "spec_tester"}]
        open_callers = [{}, {"agent_type": "implementer"}]
        open_callers += [{"agent_id": "d", "agent_type": role}
                         for role in ("security_reviewer", "task_reviewer", "final_reviewer", "planner", "default")]
        rows = [(caller, tool, extra, True) for caller in walled for tool, extra in calls]
        rows += [(caller, tool, extra, False) for caller in open_callers for tool, extra in calls]
        rows += [({"agent_id": "c", "agent_type": "task_reviewer"}, "mcp__codex_apps__search_service_web_run", {"tool_input": {"q": "x"}}, False)]
        for caller, tool, extra, deny in rows:
            with self.subTest(caller=caller, tool=tool, extra=extra):
                result = self.wall({**caller, "cwd": str(top), "tool_name": tool, **extra})
                if deny:
                    self.assertEqual((result.returncode, result.stderr.count("\n")), (2, 1), result.stderr)
                    self.assertIn("is closed to this agent", result.stderr)
                    self.assertIn("NEEDS_FACT", result.stderr)
                else:
                    self.assertEqual((result.returncode, result.stderr), (0, ""))

    NESTED = ("collaborationspawn_agent", "collaborationsend_message", "collaboration.future", "spawn_agent", "send_input",
              "send_message", "followup_task", "wait_agent", "close_agent", "interrupt_agent", "list_agents",
              "multi_agent_v1send_input", "multi_agent_v1wait_agent", "multi_agent_v1close_agent",
              "multi_agent_v1resume_agent", "teamspawn_agent", "resume_agent")
    MATCHER = ("Bash|apply_patch|view_image|mcp__.*|read_mcp_resource|list_mcp_resources|list_mcp_resource_templates|web.*"
               "|collaboration.*|spawn_agent|send_input|send_message|followup_task|wait_agent|close_agent|interrupt_agent|list_agents|resume_agent")

    def test_collaboration_tools_by_agent_type(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        top = Path(tmp.name).resolve() / "repo"
        put(top / "README.md")
        git(top, "init", "-q")
        walled = [{"agent_id": "a", "agent_type": "implementer"}, {"agent_id": "b", "agent_type": "spec_tester"}]
        open_callers = [{}, {"agent_type": "implementer"}]
        open_callers += [{"agent_id": "d", "agent_type": role}
                         for role in ("default", "planner", "task_reviewer", "final_reviewer", "security_reviewer")]
        inputs = ({"tool_input": {"message": "x"}}, {})
        rows = [(c, t, e, True) for c in walled for t in self.NESTED for e in inputs]
        rows += [(c, t, e, False) for c in open_callers for t in self.NESTED for e in inputs]
        for caller, tool, extra, deny in rows:
            with self.subTest(caller=caller, tool=tool, extra=extra):
                result = self.wall({**caller, "cwd": str(top), "tool_name": tool, **extra})
                if deny:
                    self.assertEqual((result.returncode, result.stderr.count("\n")), (2, 1), result.stderr)
                    self.assertIn(f"{tool} is closed to this agent", result.stderr)
                    self.assertIn("NEEDS_FACT", result.stderr)
                else:
                    self.assertEqual((result.returncode, result.stderr), (0, ""))

    def test_an_mcp_tool_with_a_nested_name_keeps_the_mcp_rule(self) -> None:
        for name in ("mcp__x__spawn_agent", "mcp__x__list_agents"):
            with self.subTest(tool=name):
                payload = {"cwd": "/repo", "tool_name": name, "tool_input": {"q": "x"}}
                walled = self.wall({**payload, "agent_id": "a", "agent_type": "implementer"})
                self.assertEqual(walled.returncode, 2, walled.stderr)
                self.assertIn("MCP tools are closed to this agent", walled.stderr)
                reviewer = self.wall({**payload, "agent_id": "c", "agent_type": "task_reviewer"})
                self.assertEqual((reviewer.returncode, reviewer.stderr), (0, ""))

    def test_the_matcher_and_the_texts_name_the_collaboration_tools(self) -> None:
        fragment = json.loads((ROOT / "codex/hooks.fragment.json").read_text())
        group = next(g for g in fragment["hooks"]["PreToolUse"] if any("acceptance-wall.sh" in h["command"] for h in g["hooks"]))
        self.assertEqual(group["matcher"], self.MATCHER)
        header = (ROOT / "codex/hooks/acceptance-wall.sh").read_text().split('. "${0%/*}/git-pin.sh"')[0]
        self.assertIn("collaboration", header)
        codex = (ROOT / "skill/references/codex.md").read_text()
        self.assertIn("`collaboration.*`", codex)
        for text in (header, codex):
            self.assertIn("multi_agent", text)
            self.assertIn("nested harness", text)

    def test_a_real_repo_root_and_a_repo_under_a_source_folder_name(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name) / "src" / "repo"
        put(repo / "tests" / "README.md")
        git(repo, "init", "-q")
        tester = {"agent_id": "b", "agent_type": "spec_tester", "tool_name": "Bash"}
        implementer = {"agent_id": "a", "agent_type": "implementer", "tool_name": "Bash"}
        for caller, cwd, command, code in [
            (tester, repo, "cat README.md", 0), (tester, repo / "tests", "ls", 0),
            (implementer, repo / "tests", "cat acceptance/x.py", 2),
            (implementer, repo, "ls tests/acceptance | head", 2), (implementer, repo, "rg foo tests/acceptance -n", 2),
            (implementer, repo, "pytest tests/acceptance -q", 2), (implementer, repo, "ls tests/acceptance*", 2),
            (implementer, None, "grep -r foo tests/acceptance --include=x", 2),
            (tester, repo, "find src -name x", 2), (tester, repo, "ls src*", 2),
        ]:
            with self.subTest(caller=caller["agent_type"], cwd=cwd, command=command):
                where = {} if cwd is None else {"cwd": str(cwd)}
                result = self.wall({**caller, **where, "tool_input": {"command": command}})
                self.assertEqual(result.returncode, code, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
