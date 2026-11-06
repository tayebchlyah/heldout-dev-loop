#!/usr/bin/env python3
"""Held-out oracle tests for the nested routes of the walled agents (spec: Walled Agents Nested Routes).

One test per `## Acceptance flows` item (F<n>); each docstring names the `## Acceptance oracle` lines (O<n>) it
checks. F1 and F2 also check the text lines O15 to O17 of their harness, F3 the `reference/kit.md` part of O15.
Seams: the hook scripts `deny-acceptance-to-subagents.sh` (Claude) and `acceptance-wall.sh` (Codex) fed PreToolUse
JSON on stdin (exit code and stderr), on the scratch repo of `tool_walls_support.py`; `bin/install.mjs init` and
`upgrade` on scratch projects; `scripts/canary.sh --self-test-nested-verdict` on fixture transcript folders; the kit
file text. The superseded Walled Agents No Web lines 7 to 10 (now O11 to O14) stay in `test_tool_walls_install.py`,
amended there. Every name in the scratch repos is invented.
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

from test_tool_walls_install import (USER_GROUP, entry_matcher, frontmatter, frontmatter_entries, frontmatter_names,
                                     groups_running)
from test_walled_agents_no_web import CANARY, CLAUDE_WEB_CALLS, CLOSED, WebWallHooks, keys_named
from tool_walls_support import (AGENT_MATCHER, CLAUDE_AGENTS, CLAUDE_FRAGMENT, CLAUDE_HOOKS, CLAUDE_MATCHER,
                                CLAUDE_ROUTE, CODEX_FRAGMENT, CODEX_HOOKS, CODEX_MATCHER, CODEX_ROUTE, CODEX_WALL,
                                DENY_PATH, DISALLOWED, IMPLEMENTER_TOOLS, INSTALLER, KIT_REFERENCE, LSP_LINE, MCP_LINE,
                                NET, SOURCE_FILE, SPEC_TESTER_TOOLS, SUITE_FILE, call, custom_call, custom_output, git,
                                header, near, put, rollout, transcript)

CLAUDE_WALLED = ("implementer", "spec-tester")
CODEX_WALLED = ("implementer", "spec_tester")
AGENT_TOOLS = (("implementer.md", IMPLEMENTER_TOOLS), ("spec-tester.md", SPEC_TESTER_TOOLS))

# O1.
NESTED_CALLS = (("SendMessage", {"to": "main", "message": "x"}),
                ("RemoteTrigger", {"action": "list"}),
                ("CronCreate", {"cron": "* * * * *", "prompt": "x"}),
                ("ListAgents", {}),
                ("EnterWorktree", {"name": "x"}),
                ("TaskStop", {"task_id": "x"}),
                ("PushNotification", {"message": "x"}),
                ("FutureTool", {}))
# O2, D4.
ALLOWED_SKILLS = ("mattpocock-skills:tdd", "mattpocock-skills:diagnosing-bugs", "mattpocock-skills:codebase-design")
# O3.
DENIED_SKILL_INPUTS = ({"skill": "probe-fork"}, {"skill": "tdd"}, {"skill": "codebase-design"}, {})
# O8, D5.
BARE_NAMES = ("spawn_agent", "send_input", "send_message", "followup_task", "wait_agent", "close_agent",
              "interrupt_agent", "list_agents")
COLLABORATION_TOOLS = ("collaborationspawn_agent", "collaborationsend_message", "collaboration.future", *BARE_NAMES)
COLLABORATION_INPUT = {"message": "x"}
# O14: the matchers of Walled Agents No Web, as a project holds them before this upgrade.
OLD_CLAUDE_MATCHER = ("Read|Edit|Write|NotebookEdit|Grep|Glob|Bash|Monitor|LSP|mcp__.*|ListMcpResourcesTool|"
                      "ReadMcpResourceTool|ReadMcpResourceDirTool|WebFetch|WebSearch|Agent")
OLD_CODEX_MATCHER = ("Bash|apply_patch|view_image|mcp__.*|read_mcp_resource|list_mcp_resources|"
                     "list_mcp_resource_templates|web.*")

# O17, D9.
VERDICTS = re.compile(r"\b(blocked|LEAK|refused|ERROR)\b")
DENIAL = "Blocked: this call is closed to this agent."
OPEN = "done"


def claude_run(*results: str) -> str:
    """A transcript with one `Skill` call per result."""
    return transcript(*(call(f"toolu_{n}", "Skill", {"skill": "probe-fork"}, result)
                        for n, result in enumerate(results)))


def codex_named_call(n: int, name: str, result: str) -> list[dict]:
    """A `call_id` pair of a tool called by name."""
    stamp = f"2026-11-06T09:00:{n:02d}Z"
    return [{"timestamp": stamp, "type": "response_item", "payload": {
                "type": "function_call", "call_id": f"call_{n}", "name": name, "arguments": "{}"}},
            {"timestamp": stamp, "type": "response_item", "payload": {
                "type": "function_call_output", "call_id": f"call_{n}", "output": result}}]


def codex_run(named: str, code_mode: str) -> str:
    """A rollout with the results of one call by name and one code-mode call."""
    return rollout(*codex_named_call(1, "collaborationspawn_agent", named),
                   custom_call(2, "await tools.collaborationspawn_agent({message: 'x'})"), custom_output(2, code_mode))


class NestedWallHooks(WebWallHooks):
    def assert_nested_verdicts(self, harness: str, run, no_call: str) -> None:
        """O17 for one harness: `run` gives a transcript from the results of its 2 calls."""
        file_name = "agent-x.jsonl" if harness == "claude" else "rollout-1.jsonl"
        for n, (rc, text, verdict) in enumerate(((0, run(DENIAL, DENIAL), "blocked"),
                                                 (0, run(DENIAL, OPEN), "LEAK"),
                                                 (0, run(OPEN, DENIAL), "LEAK"),
                                                 (0, no_call, "refused"),
                                                 (1, run(DENIAL, DENIAL), "ERROR"),
                                                 (0, None, "ERROR"))):
            folder = self.root / "fixtures" / f"{harness}-{n}"
            folder.mkdir(parents=True)
            if text is not None:
                put(folder / file_name, text)
            with self.subTest(oracle="O17", harness=harness, rc=rc, case=n, expected=verdict):
                command = ["bash", str(CANARY), "--self-test-nested-verdict", harness, str(rc), str(folder)]
                done = subprocess.run(command, cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=120)
                self.assertEqual(VERDICTS.findall(done.stdout), [verdict], done.stdout + done.stderr)


class ClaudeNestedWallTests(NestedWallHooks):
    def claude(self, agent_type: str | None, tool: str, tool_input: dict) -> subprocess.CompletedProcess[str]:
        return self.hook("claude", NET, self.pre_tool(tool, tool_input, agent_type))

    def test_f1_claude_walled_agents_lose_the_nested_routes(self) -> None:
        """O1 to O7: an implementer, a spec-tester, a reviewer and the main session call the nested tools, `Skill`
        and a future tool. D2, with Tool Walls and Walled Agents No Web: the `LSP`, MCP, web and `Agent` messages
        stay and come first. O15 to O17, Claude part: `claude.md`, `implementer.md`, the hook header, the canary."""
        # O1.
        for agent_type in CLAUDE_WALLED:
            for tool, tool_input in NESTED_CALLS:
                with self.subTest(oracle="O1", caller=agent_type, tool=tool):
                    self.assert_web_denied(self.claude(agent_type, tool, tool_input))
        # O2.
        for skill in ALLOWED_SKILLS:
            with self.subTest(oracle="O2", skill=skill):
                result = self.claude("implementer", "Skill", {"skill": skill})
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # O3.
        for tool_input in DENIED_SKILL_INPUTS:
            with self.subTest(oracle="O3", call=json.dumps(tool_input)):
                result = self.claude("implementer", "Skill", tool_input)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn(CLOSED, result.stderr)
                self.assertIn("mattpocock-skills:tdd", result.stderr)
        # O4.
        with self.subTest(oracle="O4"):
            result = self.claude("spec-tester", "Skill", {"skill": "mattpocock-skills:tdd"})
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn(CLOSED, result.stderr)
        # O5.
        for agent_type in CLAUDE_WALLED:
            for tool, tool_input in (("ToolSearch", {"query": "Monitor"}), ("SubagentHandback", {}),
                                     ("Read", {"file_path": "README.md"}), ("Bash", {"command": "echo ok"})):
                with self.subTest(oracle="O5", caller=agent_type, tool=tool):
                    result = self.claude(agent_type, tool, tool_input)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # O6.
        skill_calls = tuple(("Skill", tool_input) for tool_input in DENIED_SKILL_INPUTS)
        for agent_type in (None, "general-purpose"):
            for tool, tool_input in NESTED_CALLS + skill_calls:
                with self.subTest(oracle="O6", caller=agent_type or "main session", tool=tool,
                                  call=json.dumps(tool_input)):
                    result = self.claude(agent_type, tool, tool_input)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # O7.
        with self.subTest(oracle="O7", caller="implementer"):
            held = self.claude("general-purpose", "Read", {"file_path": SUITE_FILE})
            self.assertEqual(held.returncode, 2, held.stdout + held.stderr)
            self.assert_denied(self.claude("implementer", "Read", {"file_path": SUITE_FILE}), held.stderr.strip())
        with self.subTest(oracle="O7", caller="spec-tester"):
            self.assert_denied(self.claude("spec-tester", "Read", {"file_path": SOURCE_FILE}))
        # D2.
        for agent_type in CLAUDE_WALLED:
            with self.subTest(decision="D2", caller=agent_type, tool="LSP"):
                self.assert_denied(self.claude(agent_type, "LSP", {"operation": "hover", "filePath": SOURCE_FILE}),
                                   LSP_LINE)
            with self.subTest(decision="D2", caller=agent_type, tool="mcp__fs__read_file"):
                self.assert_denied(self.claude(agent_type, "mcp__fs__read_file", {"path": "README.md"}), MCP_LINE)
            for tool, tool_input in CLAUDE_WEB_CALLS:
                with self.subTest(decision="D2", caller=agent_type, tool=tool):
                    self.assert_web_denied(self.claude(agent_type, tool, tool_input))

        # O15, Claude route and agent file.
        self.assert_text(CLAUDE_ROUTE, "SendMessage", re.compile(r"\bSkill\b"), "mattpocock-skills:diagnosing-bugs",
                         re.compile(r"`tools\b"),
                         near(r"general-purpose", r"\bfork"),
                         near(r"plugin\s+cache", r"\bBash\b"),
                         near(r"project\s+skill", r"\bnames?\b"))
        self.assert_text(CLAUDE_AGENTS / "implementer.md", near(r"skill denial|denied skill|skill is denied",
                                                                r"(go on|continue|proceed) without the skill"))
        # O16, Claude hook header.
        script = CLAUDE_HOOKS / NET
        self.assert_text(script, "SendMessage", re.compile(r"\bSkill\b"), text=header(script))
        # O17, Claude.
        self.assert_nested_verdicts("claude", claude_run,
                                    transcript(call("toolu_9", "Read", {"file_path": "README.md"}, "# Acme gizmos")))


class CodexNestedWallTests(NestedWallHooks):
    def codex(self, agent_type: str | None, tool: str) -> subprocess.CompletedProcess[str]:
        return self.hook("codex", CODEX_WALL, self.pre_tool(tool, COLLABORATION_INPUT, agent_type))

    def test_f2_codex_walled_roles_lose_the_collaboration_tools(self) -> None:
        """O8 to O10: an implementer, a spec_tester, the other roles and the main session call the collaboration
        tools. O15 to O17, Codex part: `codex.md`, the hook header, the canary."""
        # O8.
        for agent_type in CODEX_WALLED:
            for tool in COLLABORATION_TOOLS:
                with self.subTest(oracle="O8", caller=agent_type, tool=tool):
                    self.assert_web_denied(self.codex(agent_type, tool))
        # O9, O10.
        for agent_type in (None, "default", "planner", "task_reviewer", "final_reviewer", "security_reviewer"):
            for tool in COLLABORATION_TOOLS:
                with self.subTest(oracle="O10" if agent_type else "O9", caller=agent_type or "main session", tool=tool):
                    result = self.codex(agent_type, tool)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # O15, Codex route.
        self.assert_text(CODEX_ROUTE, "collaboration.*")
        # O16, Codex hook header.
        script = CODEX_HOOKS / CODEX_WALL
        self.assert_text(script, "collaboration", text=header(script))
        # O17, Codex.
        self.assert_nested_verdicts("codex", codex_run, rollout(*codex_named_call(1, "shell", "ok")))


class InstallAndUpgradeTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.notes = self.root / "notes"
        home, scratch = self.root / "home", self.root / "tmp"
        for folder in (home, scratch, self.notes):
            folder.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("DEV_LOOP", "DEVLOOP", "CLAUDE", "CODEX", "GIT_"))}
        self.env.update(HOME=str(home), TMPDIR=str(scratch), GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
                        PYTHONDONTWRITEBYTECODE="1")

    def install(self, project: Path, *args: str) -> None:
        done = subprocess.run(["node", str(INSTALLER), *args], cwd=project, env=self.env, capture_output=True,
                              text=True, timeout=300)
        self.assertEqual(done.returncode, 0, done.stdout[-1500:] + done.stderr[-1500:])

    def project(self, name: str) -> Path:
        project = self.root / name
        project.mkdir()
        git(project, "init", "-q", "-b", "main")
        self.install(project, "init", "--yes", "--harness=both", f"--notes={self.notes}")
        return project

    def assert_one_entry(self, path: Path, script: str, matcher: str) -> None:
        with self.subTest(file=path.name, script=script):
            found = groups_running(json.loads(path.read_text()), script)
            self.assertEqual([group.get("matcher") for _, group in found], [matcher])

    def assert_agents(self, folder: Path) -> None:
        """O11, O12 on the 2 agent files of `folder`."""
        for name, tools in AGENT_TOOLS:
            with self.subTest(agent=str(folder / name)):
                text = frontmatter(folder / name)
                self.assertEqual(sorted(frontmatter_names(text, "tools") or []), sorted(tools))
                self.assertEqual(sorted(frontmatter_names(text, "disallowedTools") or []), sorted(DISALLOWED))
                entries = [entry for entry in frontmatter_entries(text) if DENY_PATH in entry]
                self.assertTrue(entries, f"no {DENY_PATH} entry")
                self.assertEqual({entry_matcher(entry) for entry in entries}, {AGENT_MATCHER})

    def test_f3_init_and_upgrade_write_the_nested_route_matchers_and_agent_files(self) -> None:
        """O11 to O14, with Walled Agents No Web: the kit source; `init`; `upgrade` of the No Web matchers, a user
        entry beside each, and a project `web_search` kept. O15, kit part: the `/hooks` note of `reference/kit.md`."""
        # O11, O12.
        self.assert_one_entry(CLAUDE_FRAGMENT, NET, CLAUDE_MATCHER)
        for _, group in groups_running(json.loads(CLAUDE_FRAGMENT.read_text()), NET):
            for hook in group["hooks"]:
                self.assertEqual(hook.get("onFailure"), "block", hook)
        self.assert_one_entry(CODEX_FRAGMENT, CODEX_WALL, CODEX_MATCHER)
        self.assert_agents(CLAUDE_AGENTS)

        # O13.
        fresh = self.project("fresh-gizmos")
        self.assert_one_entry(fresh / ".claude" / "settings.json", NET, CLAUDE_MATCHER)
        self.assert_one_entry(fresh / ".codex" / "hooks.json", CODEX_WALL, CODEX_MATCHER)
        self.assert_agents(fresh / ".claude" / "agents")
        config = tomllib.loads((fresh / ".codex" / "config.toml").read_text())
        self.assertEqual(keys_named(config, "web_search"), [])

        # O14.
        old = self.project("old-gizmos")
        files = ((old / ".claude" / "settings.json", NET, OLD_CLAUDE_MATCHER, CLAUDE_MATCHER),
                 (old / ".codex" / "hooks.json", CODEX_WALL, OLD_CODEX_MATCHER, CODEX_MATCHER))
        for path, script, old_matcher, _ in files:
            hooks = json.loads(path.read_text())
            found = groups_running(hooks, script)
            self.assertEqual(len(found), 1, f"{script} groups in {path}")
            holder, group = found[0]
            group["matcher"] = old_matcher
            holder.append(json.loads(json.dumps(USER_GROUP)))
            path.write_text(json.dumps(hooks, indent=2) + "\n")
        codex_config = old / ".codex" / "config.toml"
        codex_config.write_text('web_search = "live"\n' + codex_config.read_text())
        self.install(old, "upgrade", "--yes")
        for path, script, _, matcher in files:
            self.assert_one_entry(path, script, matcher)
            with self.subTest(oracle="O14", file=path.name):
                user = groups_running(json.loads(path.read_text()), "audit-my-shell.sh")
                self.assertEqual([group for _, group in user], [USER_GROUP])
        self.assert_agents(old / ".claude" / "agents")
        with self.subTest(oracle="O14", file="config.toml"):
            self.assertEqual(tomllib.loads(codex_config.read_text()).get("web_search"), "live")

        # O15, kit part.
        self.assertRegex(KIT_REFERENCE.read_text(), near(r"/hooks", r"collaboration|nested"))


if __name__ == "__main__":
    unittest.main()
