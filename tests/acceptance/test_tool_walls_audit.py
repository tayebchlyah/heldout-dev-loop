#!/usr/bin/env python3
"""Held-out oracle tests for the tool walls (spec: Tool Walls), audit backup flow F4.

Seams: `devloop.py audit` and `seal-check` stdout JSON on a scratch repo, with a Claude transcript or a Codex
rollout; the `reference/cli/audit.md` text (the audit part of O25). Fixtures: `tool_walls_support.py`.
"""

from __future__ import annotations

import re
import unittest

from tool_walls_support import (AUDIT_REFERENCE, QUOTE, SOURCE_FILE, SUITE_FILE, SUITE_HIT, ToolWallRepo, call,
                                command_item, custom_call, custom_output, mcp_item, notification, record_shas,
                                rollout, same_sha, transcript)

LSP_REFERENCES = ("Found 2 references across 2 files:\n\ntests/acceptance/test_x.py:\n  Line 3:5\n"
                  "src/app.py:\n  Line 1:5")
MONITOR_STARTED = "Monitor started. Events arrive as task notifications."


class AuditBackupTests(ToolWallRepo):
    def claude_agent(self, agent_id: str, *groups: list[dict]) -> tuple[str, dict]:
        """A Claude implementer: SubagentStart, one commit, then `audit` on its transcript; the commit sha and the
        `audit` output."""
        self.start(agent_id, "implementer")
        sha, commit_output = self.commit_fix(f"Fix make for {agent_id}")
        text = transcript(*groups, call(f"{agent_id}_commit", "Bash", {"command": "git commit -am 'Fix make'"},
                                        commit_output))
        code, out = self.audit(agent_id, "implementer", text)
        self.assertEqual(code, 0, out)
        return sha, out

    def codex_agent(self, agent_id: str, *items: dict) -> tuple[str, dict]:
        """A Codex implementer: SubagentStart, one commit, then `audit` on its rollout."""
        self.start(agent_id, "implementer", harness="codex")
        sha, commit_output = self.commit_fix(f"Fix make for {agent_id}")
        code, out = self.audit(agent_id, "implementer", rollout(
            *items, command_item(50, "git commit -am 'Fix make'", commit_output)))
        self.assertEqual(code, 0, out)
        return sha, out

    def assert_taint(self, out: dict, sha: str, kind: str) -> dict:
        record = out.get("taint")
        self.assertIsNotNone(record, out)
        self.assertEqual(record.get("set"), "acceptance", record)
        if kind == "paths":
            self.assertEqual(record.get("paths"), [SUITE_FILE], record)
        else:
            self.assertTrue(record.get("lines"), record)
        self.assertTrue(any(same_sha(s, sha) for s in record_shas(record)), record)
        return record

    def assert_no_taint(self, out: dict) -> None:
        self.assertIsNone(out.get("taint"), out)

    def test_f4_tool_results_that_name_or_quote_the_suite_taint(self) -> None:
        """O19 to O23: a subagent whose `LSP`, MCP, `Monitor` or Codex code-mode result names or quotes a held-out
        file gets a taint, and `seal-check` fails; use alone gives none. O25: `audit.md` names the outputs."""
        # O23: use alone.
        _, out = self.claude_agent(
            "use-alone",
            call("u_lsp", "LSP", {"operation": "findReferences", "filePath": SOURCE_FILE, "line": 1, "character": 5},
                 "Found 1 reference across 1 file:\n\nsrc/app.py:\n  Line 1:5"),
            call("u_mcp", "mcp__fs__search", {"pattern": "make"}, "src/app.py:1:def make(n):"),
            call("u_res", "ReadMcpResourceTool", {"server": "fs", "uri": f"file://{self.repo}/{SOURCE_FILE}"},
                 "def make(n):\n    return n + 1\n"),
            call("u_mon", "Monitor", {"command": "grep -rn make src"}, MONITOR_STARTED),
            notification("task_u", "u_mon", "./src/app.py:1:def make(n):"))
        self.assert_no_taint(out)
        code, check = self.cli("seal-check")
        self.assertEqual((code, check.get("ok")), (0, True), check)

        # O19: an LSP result names the suite file.
        sha, out = self.claude_agent("lsp-1", call(
            "l_refs", "LSP", {"operation": "findReferences", "filePath": SOURCE_FILE, "line": 1, "character": 5},
            LSP_REFERENCES))
        self.assert_taint(out, sha, "paths")
        code, check = self.cli("seal-check")
        self.assertEqual((code, check.get("ok")), (1, False), check)
        self.assertIn("lsp-1", self.open_taint(check))

        # O20: an MCP search result names it; an MCP resource result quotes it.
        sha, out = self.claude_agent("mcp-1", call("m_search", "mcp__fs__search", {"pattern": "make"}, SUITE_FILE))
        self.assert_taint(out, sha, "paths")
        sha, out = self.claude_agent("mcp-2", call(
            "m_res", "ReadMcpResourceTool", {"server": "fs", "uri": "file:///tmp/notes.md"}, QUOTE))
        self.assert_taint(out, sha, "lines")

        # O21: Monitor events, paired by tool-use id; a background Bash completion counts as nothing.
        sha, out = self.claude_agent(
            "mon-1", call("mon_grep", "Monitor", {"command": "grep -rn make ."}, MONITOR_STARTED),
            notification("task_1", "mon_grep", SUITE_HIT))
        self.assert_taint(out, sha, "paths")
        sha, out = self.claude_agent(
            "mon-2", call("mon_cat", "Monitor", {"command": f"cat {SUITE_FILE}"}, MONITOR_STARTED),
            notification("task_2", "mon_cat", QUOTE.rstrip("\n")))
        self.assert_taint(out, sha, "lines")
        _, out = self.claude_agent(
            "bash-bg", call("bg_run", "Bash", {"command": "make build", "run_in_background": True},
                            "Command running in background with ID: task_3"),
            notification("task_3", "bg_run", QUOTE.rstrip("\n")))
        self.assert_no_taint(out)

        # O22: Codex McpToolCall result text; custom_tool_call_output text by the line rule only.
        sha, out = self.codex_agent("codex-mcp", mcp_item(1, "fs", "search", {"pattern": "make"},
                                                          f"{SUITE_FILE}:3:x"))
        self.assert_taint(out, sha, "paths")
        sha, out = self.codex_agent("codex-exec", custom_call(1, "print(open('notes.txt').read())"),
                                    custom_output(1, QUOTE))
        self.assert_taint(out, sha, "lines")
        _, out = self.codex_agent("codex-exec-path", custom_call(1, "print('lint')"),
                                  custom_output(1, f"{SUITE_FILE}:3:x"))
        self.assert_no_taint(out)

        # O25: the audit reference names the D11 outputs.
        text = AUDIT_REFERENCE.read_text()
        for pattern in (r"\bLSP\b", r"\bMonitor\b", r"MCP|mcp__", "McpToolCall",
                        "custom_tool_call_output"):
            with self.subTest(oracle="O25", file="reference/cli/audit.md", pattern=pattern):
                self.assertTrue(re.search(pattern, text), f"no match for {pattern}")


if __name__ == "__main__":
    unittest.main()
