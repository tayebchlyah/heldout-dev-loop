#!/usr/bin/env python3
"""Held-out oracle tests for the Codex walled-role default deny (spec: Codex TUI Task Tools).

One test per `## Acceptance flows` item (F<n>); each docstring names the `## Acceptance oracle` lines (O<n>) it
checks. F1 also checks the text line O9 and the hook header part of O10, F2 the `reference/kit.md` part of O10.
Seams: the hook script `acceptance-wall.sh` (Codex) fed PreToolUse JSON on stdin (exit code and stderr), on the
scratch repo of `tool_walls_support.py`; `bin/install.mjs init` and `upgrade` on scratch projects; the kit file
text. The superseded Walled Agents Nested Routes lines 11, 13 and 14 stay in `test_walled_agents_nested_routes.py`
and `test_tool_walls_install.py`, amended there through `CODEX_MATCHER`. Every name in the scratch repos is invented.

The held-out and source-wall messages are the Tool Walls ones: the deny of `acceptance-wall.sh`, for the same
caller, on a `cat` of the walled file.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_tool_walls_install import USER_GROUP, groups_running
from test_walled_agents_no_web import WebWallHooks
from tool_walls_support import (CODEX_FRAGMENT, CODEX_HOOKS, CODEX_ROUTE, CODEX_WALL, INSTALLER, KIT_REFERENCE,
                                MCP_LINE, SOURCE_FILE, SUITE_FILE, git, header, near)

WALLED = ("implementer", "spec_tester")
OPEN_CALLERS = (None, "default", "planner", "task_reviewer", "final_reviewer", "security_reviewer")
NEW_MATCHER = ".*"
# Walled Agents Nested Routes D6: the Codex matcher a project holds before this upgrade.
NESTED_ROUTES_MATCHER = ("Bash|apply_patch|view_image|mcp__.*|read_mcp_resource|list_mcp_resources|"
                         "list_mcp_resource_templates|web.*|collaboration.*|spawn_agent|send_input|send_message|"
                         "followup_task|wait_agent|close_agent|interrupt_agent|list_agents|resume_agent")
TOOL_WALLS_MATCHER = "Bash|apply_patch"

# O1: `None` is the call with no `tool_name`.
UNLISTED = ("codex_tuicreate_thread", "codex_tuifork_thread", "codex_tuisend_message_to_thread",
            "codex_tuiread_thread", "codex_tuiwait_threads", "codex_tuilist_threads", "collaborationread_thread",
            "request_user_input", "request_permissions", "multi_env_copy_file", "noteswrite_file", "new_context",
            "future_tool", None)
UNLISTED_INPUT = {"message": "x"}
# D2.
D2_NAMES = ("Bash", "apply_patch", "Edit", "Write", "view_image", "update_plan", "clockcurr_time", "clocksleep",
            "get_context_remaining", "skillslist", "skillsread", "skillsinstallations")
PATCH_TOOLS = ("apply_patch", "Edit", "Write")
# O2.
LISTED_CALLS = (("update_plan", {}), ("clockcurr_time", {}), ("clocksleep", {}), ("get_context_remaining", {}),
                ("skillslist", {}), ("skillsinstallations", {}),
                ("skillsread", {"name": "tdd", "path": "SKILL.md"}),
                ("Bash", {"command": "echo ok"}),
                ("view_image", {"path": "shots/home.png"}))
# The matcher `.*` as a word of its own, not the tail of `mcp__.*`, `web.*` or `collaboration.*`.
BARE_ANY = re.compile(r"(?<![\w.])\.\*")
TUI_GAP = near(r"codex_tui|\bTUI\b", r"agent_id")
# O10, kit part: what tells this matcher change from the earlier `/hooks` notes.
THIS_CHANGE = re.compile(r"codex_tui|\bTUI\b|tool list|default deny|#283", re.IGNORECASE)


def patch_input(path: str) -> dict:
    """A patch updating `path`, under `command` (Codex apply_patch hook input), also under `patch`."""
    text = f"*** Begin Patch\n*** Update File: {path}\n@@\n-def make(n):\n+def make(n: int):\n*** End Patch\n"
    return {"command": text, "patch": text}


def word(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w.]){re.escape(name)}(?![\w])")


class CodexTuiWallTests(WebWallHooks):
    def codex(self, agent_type: str | None, tool: str | None, tool_input: dict) -> subprocess.CompletedProcess[str]:
        payload = self.pre_tool(tool or "", tool_input, agent_type)
        if tool is None:
            del payload["tool_name"]
        return self.hook("codex", CODEX_WALL, payload)

    def cat_deny(self, agent_type: str, path: str) -> str:
        """The name-rule deny of `acceptance-wall.sh` for this caller on a `cat` of `path`."""
        result = self.codex(agent_type, "Bash", {"command": f"cat {path}"})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        return result.stderr.strip()

    def assert_exit(self, result: subprocess.CompletedProcess[str], code: int) -> None:
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)

    def test_f1_codex_walled_roles_keep_only_their_tool_list(self) -> None:
        """O1 to O5: an implementer, a spec_tester, the reviewers, the planner and the main session call the TUI
        task tools, other unlisted tools and the listed tools. With Tool Walls and Walled Agents No Web: the MCP
        line M, the web deny and the held-out and source walls of the listed tools stay. O9, O10 (hook header)."""
        # O1.
        for agent_type in WALLED:
            for tool in UNLISTED:
                with self.subTest(oracle="O1", caller=agent_type, tool=tool or "no tool_name"):
                    self.assert_web_denied(self.codex(agent_type, tool, UNLISTED_INPUT))
        # O2.
        for agent_type in WALLED:
            for tool, tool_input in LISTED_CALLS:
                with self.subTest(oracle="O2", caller=agent_type, tool=tool):
                    self.assert_exit(self.codex(agent_type, tool, tool_input), 0)
        for tool in PATCH_TOOLS:
            with self.subTest(oracle="O2", caller="implementer", tool=tool):
                self.assert_exit(self.codex("implementer", tool, patch_input(SOURCE_FILE)), 0)
        # O3, implementer: the held-out wall.
        held = self.cat_deny("implementer", SUITE_FILE)
        for tool in PATCH_TOOLS:
            with self.subTest(oracle="O3", caller="implementer", tool=tool):
                self.assert_exit(self.codex("implementer", tool, patch_input(SUITE_FILE)), 2)
        with self.subTest(oracle="O3", caller="implementer", tool="skillsread"):
            self.assert_denied(self.codex("implementer", "skillsread", {"path": SUITE_FILE}), held)
        # O3, spec_tester: the source wall.
        source = self.cat_deny("spec_tester", SOURCE_FILE)
        for tool, tool_input in (("view_image", {"path": "src/logo.png"}), ("skillsread", {"path": SOURCE_FILE})):
            with self.subTest(oracle="O3", caller="spec_tester", tool=tool):
                self.assert_denied(self.codex("spec_tester", tool, tool_input), source)
        # O3, both roles: line M and the web deny come first.
        for agent_type in WALLED:
            with self.subTest(oracle="O3", caller=agent_type, tool="mcp__fs__read_file"):
                self.assert_denied(self.codex(agent_type, "mcp__fs__read_file", {"path": "README.md"}), MCP_LINE)
            with self.subTest(oracle="O3", caller=agent_type, tool="webrun"):
                self.assert_web_denied(self.codex(agent_type, "webrun", {"search_query": [{"q": "x"}]}))
        # O4.
        for agent_type in OPEN_CALLERS:
            for tool in UNLISTED:
                if tool is None:
                    continue
                with self.subTest(oracle="O4", caller=agent_type or "main session", tool=tool):
                    self.assert_exit(self.codex(agent_type, tool, UNLISTED_INPUT), 0)
        # O5.
        with self.subTest(oracle="O5", caller="task_reviewer", tool="codex_tuiread_thread"):
            self.assert_exit(self.codex("task_reviewer", "codex_tuiread_thread", {"path": SUITE_FILE}), 0)

        # O9, Codex route.
        self.assert_text(CODEX_ROUTE, BARE_ANY, "codex_tui", *(word(name) for name in D2_NAMES), "write_stdin",
                         "tool_search", TUI_GAP, "collaboration.*", "multi_agent")
        # O10, hook header.
        script = CODEX_HOOKS / CODEX_WALL
        self.assert_text(script, BARE_ANY, "codex_tui", *(word(name) for name in D2_NAMES), "web.*",
                         "collaboration", text=header(script))


class CodexTuiInstallTests(unittest.TestCase):
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

    def matchers(self, path: Path, script: str) -> list[str | None]:
        return [group.get("matcher") for _, group in groups_running(json.loads(path.read_text()), script)]

    def test_f2_init_and_upgrade_write_the_any_tool_matcher(self) -> None:
        """O6 to O8: the kit fragment; `init`; `upgrade` of the Walled Agents Nested Routes D6 matcher and of the
        Tool Walls `Bash|apply_patch`, a user entry beside each. O10, kit part: the `/hooks` note."""
        # O6.
        with self.subTest(oracle="O6", script=CODEX_WALL):
            self.assertEqual(self.matchers(CODEX_FRAGMENT, CODEX_WALL), [NEW_MATCHER])
        with self.subTest(oracle="O6", script="git-guard.sh"):
            guard = self.matchers(CODEX_FRAGMENT, "git-guard.sh")
            self.assertTrue(guard, "no git-guard.sh entry")
            self.assertEqual(set(guard), {"Bash"})

        # O7.
        fresh = self.project("fresh-gizmos")
        with self.subTest(oracle="O7"):
            self.assertEqual(self.matchers(fresh / ".codex" / "hooks.json", CODEX_WALL), [NEW_MATCHER])

        # O8.
        for name, old_matcher in (("nested-gizmos", NESTED_ROUTES_MATCHER), ("walls-gizmos", TOOL_WALLS_MATCHER)):
            project = self.project(name)
            path = project / ".codex" / "hooks.json"
            hooks = json.loads(path.read_text())
            found = groups_running(hooks, CODEX_WALL)
            self.assertEqual(len(found), 1, f"{CODEX_WALL} groups in {path}")
            holder, group = found[0]
            group["matcher"] = old_matcher
            holder.append(json.loads(json.dumps(USER_GROUP)))
            path.write_text(json.dumps(hooks, indent=2) + "\n")
            self.install(project, "upgrade", "--yes")
            with self.subTest(oracle="O8", old=old_matcher):
                self.assertEqual(self.matchers(path, CODEX_WALL), [NEW_MATCHER])
                user = groups_running(json.loads(path.read_text()), "audit-my-shell.sh")
                self.assertEqual([group for _, group in user], [USER_GROUP])

        # O10, kit part.
        paragraphs = re.split(r"\n\s*\n", KIT_REFERENCE.read_text())
        self.assertTrue(any("/hooks" in p and BARE_ANY.search(p) and THIS_CHANGE.search(p) for p in paragraphs),
                        "no reference/kit.md note names /hooks with the Codex TUI task tools matcher `.*`")


if __name__ == "__main__":
    unittest.main()
