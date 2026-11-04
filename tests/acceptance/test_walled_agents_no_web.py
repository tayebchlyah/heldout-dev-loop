#!/usr/bin/env python3
"""Held-out oracle tests for the web wall of the walled agents (spec: Walled Agents No Web).

One test per `## Acceptance flows` item (F<n>); each docstring names the `## Acceptance oracle` lines (O<n>) it
checks. F1 and F2 also check the text lines O12 to O14 of their harness, F3 the `reference/kit.md` part of O13.
Seams: the hook scripts `deny-acceptance-to-subagents.sh` (Claude) and `acceptance-wall.sh` (Codex) fed PreToolUse
JSON on stdin (exit code and stderr), on the scratch repo of `tool_walls_support.py`; `bin/install.mjs init` and
`upgrade` and `check_setup.py --harness codex` (a fake `codex` on `PATH`) on scratch projects; the kit file text.
The superseded Tool Walls lines 14 to 17 (now O7 to O10) stay in `test_tool_walls_install.py`, amended there.
The live canary needs a real Codex and is never run here. Every name in the scratch repos is invented.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from tool_walls_support import (CLAUDE_AGENTS, CLAUDE_HOOKS, CLAUDE_ROUTE, CODEX_HOOKS, CODEX_ROUTE, CODEX_WALL,
                                INSTALLER, KIT_REFERENCE, NET, REPO_ROOT, SUITE_FILE, ToolWallRepo, git, header,
                                near)

CODEX_AGENTS = REPO_ROOT / "codex" / "agents"
CHECKER = REPO_ROOT / "skill" / "scripts" / "check_setup.py"
CANARY = REPO_ROOT / "scripts" / "canary.sh"

CLOSED = "is closed to this agent"
NEEDS_FACT = "NEEDS_FACT"
CODEX_VERSION = "0.166.0"

# O1.
CLAUDE_WEB_CALLS = (("WebFetch", {"url": "https://example.com"}),
                    ("WebSearch", {"query": "x"}),
                    ("Agent", {"prompt": "x"}))
# O4.
CODEX_WEB_CALLS = (("webrun", {"search_query": [{"q": "x"}]}),
                   ("webrun", {"open": [{"ref_id": "https://example.com"}]}),
                   ("web__run", {"search_query": [{"q": "x"}]}))
# O3: a public raw copy of the held-out file.
SUITE_URL = f"https://raw.githubusercontent.com/acme/gizmos/main/{SUITE_FILE}"


def keys_named(value: object, name: str) -> list[str]:
    """The dotted paths of every key `name`, at any depth of a parsed TOML document."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == name:
                found.append(key)
            found.extend(f"{key}.{path}" for path in keys_named(item, name))
    elif isinstance(value, list):
        for item in value:
            found.extend(keys_named(item, name))
    return found


class WebWallHooks(ToolWallRepo):
    def assert_web_denied(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(CLOSED, result.stderr, result.stderr)
        self.assertIn(NEEDS_FACT, result.stderr, result.stderr)

    def assert_text(self, path: Path, *patterns: str | re.Pattern[str], text: str | None = None) -> None:
        body = path.read_text() if text is None else text
        for pattern in patterns:
            with self.subTest(file=str(path.relative_to(REPO_ROOT)), pattern=str(pattern)):
                if isinstance(pattern, str):
                    self.assertTrue(pattern in body, f"no {pattern!r}")
                else:
                    self.assertTrue(pattern.search(body), f"no match for {pattern.pattern}")


class ClaudeWebWallTests(WebWallHooks):
    def claude(self, agent_type: str | None, tool: str, tool_input: dict) -> subprocess.CompletedProcess[str]:
        return self.hook("claude", NET, self.pre_tool(tool, tool_input, agent_type))

    def test_f1_claude_walled_agents_lose_the_web_and_agent_tools(self) -> None:
        """O1 to O3: an implementer, a spec-tester, a reviewer and the main session call `WebFetch`, `WebSearch`
        and `Agent`; the reviewer keeps the Tool Walls name rule on `url`. O12 to O14, Claude part: the agent
        files, `claude.md` and the hook header."""
        # O1.
        for agent_type in ("implementer", "spec-tester"):
            for tool, tool_input in CLAUDE_WEB_CALLS:
                with self.subTest(oracle="O1", caller=agent_type, tool=tool):
                    self.assert_web_denied(self.claude(agent_type, tool, tool_input))
        # O2.
        for tool, tool_input in CLAUDE_WEB_CALLS:
            with self.subTest(oracle="O2", caller="main session", tool=tool):
                self.assert_allowed(self.claude(None, tool, tool_input))
        # O3, with the Tool Walls held-out message: the same hook's deny of a Read of the suite file.
        with self.subTest(oracle="O3", url="https://example.com"):
            self.assert_allowed(self.claude("general-purpose", "WebFetch", {"url": "https://example.com"}))
        with self.subTest(oracle="O3", url=SUITE_URL):
            held = self.claude("general-purpose", "Read", {"file_path": SUITE_FILE})
            self.assertEqual(held.returncode, 2, held.stdout + held.stderr)
            result = self.claude("general-purpose", "WebFetch", {"url": SUITE_URL})
            self.assert_denied(result, held.stderr.strip())

        # O12, Claude agent files.
        for name in ("implementer.md", "spec-tester.md"):
            self.assert_text(CLAUDE_AGENTS / name, NEEDS_FACT)
        # O13, Claude route.
        self.assert_text(CLAUDE_ROUTE, NEEDS_FACT, "fresh", "work from the spec", "WebFetch", "WebSearch",
                         near(r"\bAgent\b", r"WebFetch|WebSearch"),
                         near(r"\bcurl\b|gh api|\bshell\b", r"\bweb\b|network"))
        # O14, Claude hook header.
        script = CLAUDE_HOOKS / NET
        self.assert_text(script, "WebFetch", "WebSearch", re.compile(r"\bAgent\b"), text=header(script))


class CodexWebWallTests(WebWallHooks):
    def codex(self, agent_type: str | None, tool: str, tool_input: dict) -> subprocess.CompletedProcess[str]:
        return self.hook("codex", CODEX_WALL, self.pre_tool(tool, tool_input, agent_type))

    def test_f2_codex_walled_roles_lose_the_web_tool(self) -> None:
        """O4 to O6: an implementer, a spec_tester, the reviewers, the planner and the main session call
        `webrun` and `web__run`. O12 to O14, Codex part: the agent files, `codex.md`, the hook header and the
        canary."""
        # O4.
        for agent_type in ("implementer", "spec_tester"):
            for tool, tool_input in CODEX_WEB_CALLS:
                with self.subTest(oracle="O4", caller=agent_type, tool=tool, call=str(tool_input)):
                    self.assert_web_denied(self.codex(agent_type, tool, tool_input))
        # O5, O6.
        for agent_type in (None, "security_reviewer", "task_reviewer", "final_reviewer", "planner"):
            for tool, tool_input in CODEX_WEB_CALLS:
                with self.subTest(oracle="O6" if agent_type else "O5", caller=agent_type or "main session",
                                  tool=tool, call=str(tool_input)):
                    self.assertEqual(self.codex(agent_type, tool, tool_input).returncode, 0)

        # O12, Codex agent files.
        for name in ("implementer.toml", "spec-tester.toml"):
            self.assert_text(CODEX_AGENTS / name, NEEDS_FACT)
        agent_files = sorted(CODEX_AGENTS.glob("*.toml"))
        self.assertTrue(agent_files, "no agent file in codex/agents")
        for path in agent_files:
            with self.subTest(oracle="O12", file=f"codex/agents/{path.name}"):
                self.assertEqual(keys_named(tomllib.loads(path.read_text()), "web_search"), [])
        # O13, Codex route.
        self.assert_text(CODEX_ROUTE, NEEDS_FACT, "fresh", "work from the spec", "webrun", "*.githubusercontent.com",
                         near(r"hosted", r"web[_ ]search"))
        # O14, Codex hook header and canary.
        script = CODEX_HOOKS / CODEX_WALL
        self.assert_text(script, "web.*", text=header(script))
        self.assert_text(CANARY, "standalone_web_search", "codex web")


class WebSearchKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home, self.notes, scratch, self.bin = (self.root / "home", self.root / "notes", self.root / "tmp",
                                                    self.root / "bin")
        for folder in (self.home, self.notes / "Features", scratch, self.bin):
            folder.mkdir(parents=True)
        codex = self.bin / "codex"
        codex.write_text(f"#!/bin/sh\necho 'codex-cli {CODEX_VERSION}'\n")
        codex.chmod(0o755)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("DEV_LOOP", "DEVLOOP", "CLAUDE", "CODEX", "GIT_"))}
        self.env.update(HOME=str(self.home), TMPDIR=str(scratch), GIT_CONFIG_NOSYSTEM="1", GIT_EDITOR="true",
                        GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1")

    def run_in(self, project: Path, *args: str) -> subprocess.CompletedProcess[str]:
        done = subprocess.run(args, cwd=project, env=self.env, capture_output=True, text=True, timeout=300)
        self.assertEqual(done.returncode, 0, f"{args}: {done.stdout[-1500:]} {done.stderr[-1500:]}")
        return done

    def config(self, project: Path) -> dict:
        text = (project / ".codex" / "config.toml").read_text()
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            raise AssertionError(f".codex/config.toml does not parse: {error}\n{text}") from None

    def test_f3_upgrade_and_setup_leave_a_project_web_search_alone(self) -> None:
        """F3, O10, O11 on an installed project with `web_search = "live"`: `upgrade` keeps it and
        `check_setup.py --harness codex` names no `web_search` failure. O13, kit part: `reference/kit.md` says
        the Codex hooks need trust again after this upgrade and holds no `web_search = "disabled"`."""
        project = self.root / "tea-shop"
        project.mkdir()
        git(project, "init", "-q", "-b", "main")
        self.run_in(project, "node", str(INSTALLER), "init", "--yes", "--harness=both", f"--notes={self.notes}")
        path = project / ".codex" / "config.toml"
        own = [line for line in path.read_text().splitlines(keepends=True)
               if not re.match(r"\s*web_search\s*=", line)]
        path.write_text('web_search = "live"\n' + "".join(own))
        self.assertEqual(self.config(project).get("web_search"), "live")

        self.run_in(project, "node", str(INSTALLER), "upgrade", "--yes")
        with self.subTest(oracle="O10"):
            self.assertEqual(self.config(project).get("web_search"), "live")
        with self.subTest(oracle="O11"):
            env = {**self.env, "PATH": f"{self.bin}{os.pathsep}{self.env.get('PATH', '')}"}
            done = subprocess.run([sys.executable, str(CHECKER), "--harness", "codex", "--home", str(self.home),
                                   "--repo", str(project)], env=env, capture_output=True, text=True, timeout=180)
            output = done.stdout + done.stderr
            self.assertEqual([line for line in output.splitlines() if "web_search" in line], [], output)

        # O13, kit part.
        text = KIT_REFERENCE.read_text()
        self.assertFalse('web_search = "disabled"' in text, 'reference/kit.md holds web_search = "disabled"')
        section = re.search(r"^## Upgrade\b.*?(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(section, "no ## Upgrade section in reference/kit.md")
        notes = [p for p in re.split(r"\n\s*\n", section.group(0))
                 if re.search(r"webrun|web\.\*|WebFetch|WebSearch|web tool", p)]
        self.assertTrue(notes, "no upgrade note names the web wall")
        self.assertTrue(any(re.search(r"Codex", p) and re.search(r"trust|/hooks", p) for p in notes),
                        "no web wall upgrade note names the Codex hook trust step")


if __name__ == "__main__":
    unittest.main()
