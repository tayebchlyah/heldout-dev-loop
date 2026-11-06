#!/usr/bin/env python3
"""Shared fixtures of the held-out tool walls tests (spec: Tool Walls).

One test per `## Acceptance flows` item (F<n>); each docstring names the `## Acceptance oracle` lines (O<n>) it
checks. The seams are the ones the spec declares: the hook scripts `deny-acceptance-to-subagents.sh`,
`deny-path.sh <fragments>` and `acceptance-wall.sh` fed PreToolUse JSON on stdin (exit code and stderr);
`devloop.py search-wall`, `audit` and `seal-check` stdout JSON on a scratch repo with a Claude transcript or a
Codex rollout; the installer `init` and `upgrade` on scratch projects; the kit file text. The scratch repo holds
`dev-loop.json`, `README.md`, `src/app.py`, `tests/acceptance/test_x.py` and the kit CLI under
`.agents/skills/dev-loop/scripts`. Every name in the scratch repos is invented.

The held-out and source-wall messages are the existing name-rule denies of each hook: each test reads them from
the same hook, for the same caller, on a `Read` (Claude) or a `cat` (Codex) of the walled file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "unit" / "python"))
from board_fixtures import free_port, health

# core.fsmonitor in a user or repo config starts a daemon in every throwaway repo these tests make.
# The override wins over every config file, appends to the caller's GIT_CONFIG_* entries, skips if already set.
_GIT_CONFIG_COUNT = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
if not any(
    os.environ.get(f"GIT_CONFIG_KEY_{i}") == "core.fsmonitor" and os.environ.get(f"GIT_CONFIG_VALUE_{i}") == "false"
    for i in range(_GIT_CONFIG_COUNT)
):
    os.environ.update({
        f"GIT_CONFIG_KEY_{_GIT_CONFIG_COUNT}": "core.fsmonitor",
        f"GIT_CONFIG_VALUE_{_GIT_CONFIG_COUNT}": "false",
        "GIT_CONFIG_COUNT": str(_GIT_CONFIG_COUNT + 1),
    })

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = REPO_ROOT / "skill"
DEVLOOP = SKILL / "scripts" / "devloop.py"
INSTALLER = REPO_ROOT / "bin" / "install.mjs"
CLAUDE_HOOKS = REPO_ROOT / "claude" / "hooks"
CODEX_HOOKS = REPO_ROOT / "codex" / "hooks"
CLAUDE_AGENTS = REPO_ROOT / "claude" / "agents"
CLAUDE_FRAGMENT = REPO_ROOT / "claude" / "settings.fragment.json"
CODEX_FRAGMENT = REPO_ROOT / "codex" / "hooks.fragment.json"
CLAUDE_ROUTE = SKILL / "references" / "claude.md"
CODEX_ROUTE = SKILL / "references" / "codex.md"
KIT_REFERENCE = REPO_ROOT / "reference" / "kit.md"
AUDIT_REFERENCE = REPO_ROOT / "reference" / "cli" / "audit.md"
KIT_VERSION = json.loads((REPO_ROOT / "package.json").read_text())["version"]

HELD_OUT = "tests/acceptance"
SUITE_FILE = f"{HELD_OUT}/test_x.py"
SOURCE_FILE = "src/app.py"
SOURCE_FRAGMENTS = ("src/", "app/", "backend/", "frontend/")
SLUG = "gizmo"
BRANCH = f"feat/{SLUG}"

NET = "deny-acceptance-to-subagents.sh"
DENY_PATH = "deny-path.sh"
CODEX_WALL = "acceptance-wall.sh"

# D12.
LSP_LINE = ("Blocked: LSP is closed to subagents: its results name files the call does not. "
            "Search named folders with Grep or Bash.")
MCP_LINE = ("Blocked: MCP tools are closed to this agent. "
            "Read and search named folders with the file tools or the shell.")
# The search-rule line (Kit And Search Walls D8).
SEARCH_LINE = ("Blocked: this search reaches the held-out suite tests/acceptance. Search the source folders by name "
               "(grep -rn <name> src lib, Grep path src), or exclude the suite (grep --exclude-dir=acceptance, "
               "rg -g '!tests/acceptance/**', git grep <name> -- . ':!tests/acceptance', "
               "find . -path ./tests/acceptance -prune -o ..., Grep glob !tests/acceptance/**).")

# D8, D9, as amended by Walled Agents No Web (its O7, O8, D4): the Codex matcher and `disallowedTools` add the web
# tools and `Agent`; the agent frontmatter matcher keeps D8. Amended again by Walled Agents Nested Routes (its O11,
# O12, D1, D6): the settings net is `.*`, the Codex matcher adds the collaboration tools, the agents get `tools`.
CLAUDE_MATCHER = ".*"
AGENT_MATCHER = ("Read|Edit|Write|Grep|Glob|Bash|Monitor|LSP|mcp__.*|ListMcpResourcesTool|ReadMcpResourceTool|"
                 "ReadMcpResourceDirTool")
CODEX_MATCHER = ("Bash|apply_patch|view_image|mcp__.*|read_mcp_resource|list_mcp_resources|list_mcp_resource_templates|"
                 "web.*|collaboration.*|spawn_agent|send_input|send_message|followup_task|wait_agent|close_agent|"
                 "interrupt_agent|list_agents|resume_agent")
DISALLOWED = ["LSP", "mcp__*", "ListMcpResourcesTool", "ReadMcpResourceTool", "ReadMcpResourceDirTool", "WebFetch",
              "WebSearch", "Agent"]
IMPLEMENTER_TOOLS = ["Read", "Edit", "Write", "NotebookEdit", "Grep", "Glob", "Bash", "Monitor", "Skill", "ToolSearch"]
SPEC_TESTER_TOOLS = [tool for tool in IMPLEMENTER_TOOLS if tool != "Skill"]

# Distinctive lines (30 characters or more) of the held-out file, in no other file.
ACC1 = "assert make(41) == 42, 'quartz-heron-ledger-0091-tool-walls'"
ACC2 = "assert make(912) == 913, 'gleaming-otter-polish-7731-walls'"
SUITE_TEXT = f"from app import make\n\nr = make(1)\n\n\ndef test_make():\n    assert r == 2\n    {ACC1}\n    {ACC2}\n"
QUOTE = f"    {ACC1}\n    {ACC2}\n"
SUITE_HIT = "./tests/acceptance/test_x.py:3:r = make(1)"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def put(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def same_sha(a: str, b: str) -> bool:
    return bool(a) and bool(b) and (a.startswith(b) or b.startswith(a))


def record_shas(record: dict) -> list[str]:
    """The commit ids of a taint record: a sha string, or an object with the sha under `sha`, `commit` or `id`."""
    shas = []
    for item in record.get("commits") or []:
        if isinstance(item, str):
            shas.append(item)
        elif isinstance(item, dict):
            shas.append(next(str(item[k]) for k in ("sha", "commit", "id") if k in item))
    return shas


def header(path: Path) -> str:
    """The leading comment block of a hook script, shebang skipped."""
    lines = []
    for line in path.read_text().splitlines():
        if line.startswith("#!"):
            continue
        if line.startswith("#"):
            lines.append(line)
        elif line.strip() or lines:
            break
    return "\n".join(lines)


def near(a: str, b: str, span: int = 300) -> re.Pattern[str]:
    """`a` and `b` within `span` characters of each other, either order, case-insensitive."""
    return re.compile(rf"(?:{a})[\s\S]{{0,{span}}}(?:{b})|(?:{b})[\s\S]{{0,{span}}}(?:{a})", re.IGNORECASE)


# Claude transcripts.

def call(use_id: str, tool: str, tool_input: dict, result: str) -> list[dict]:
    """A `tool_use` and its `tool_result`, paired by `id`."""
    return [{"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": use_id, "name": tool, "input": tool_input}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": use_id, "content": result}]}}]


def notification(task_id: str, use_id: str, event: str) -> list[dict]:
    """A task notification attachment line of the D11 shape, one `<event>`."""
    prompt = (f"<task-notification>\n<task-id>{task_id}</task-id>\n<tool-use-id>{use_id}</tool-use-id>\n"
              f"<status>running</status>\n<event>{event}</event>\n</task-notification>")
    return [{"type": "attachment", "attachment": {"type": "queued_command", "commandMode": "task-notification",
                                                  "prompt": prompt}}]


def transcript(*groups: list[dict]) -> str:
    lines = [{"type": "user", "message": {"role": "user", "content": "Fix the make helper, task 2 of the plan."}}]
    for group in groups:
        lines.extend(group)
    return "".join(json.dumps(line) + "\n" for line in lines)


# Codex rollouts.

def command_item(n: int, command: str, output: str) -> dict:
    return {"timestamp": f"2026-10-31T09:00:{n:02d}Z", "type": "event_msg", "payload": {
        "type": "item_completed", "item": {"id": f"item_{n}", "type": "CommandExecution", "command": command,
                                           "aggregated_output": output, "exit_code": 0}}}


def mcp_item(n: int, server: str, tool: str, arguments: dict, text: str) -> dict:
    return {"timestamp": f"2026-10-31T09:00:{n:02d}Z", "type": "event_msg", "payload": {
        "type": "item_completed", "item": {"id": f"item_{n}", "type": "McpToolCall", "server": server, "tool": tool,
                                           "arguments": arguments, "status": "completed",
                                           "result": {"content": [{"type": "text", "text": text}]}}}}


def custom_call(n: int, code: str) -> dict:
    return {"timestamp": f"2026-10-31T09:00:{n:02d}Z", "type": "response_item", "payload": {
        "type": "custom_tool_call", "call_id": f"call_{n}", "name": "exec", "input": code}}


def custom_output(n: int, text: str) -> dict:
    return {"timestamp": f"2026-10-31T09:00:{n:02d}Z", "type": "response_item", "payload": {
        "type": "custom_tool_call_output", "call_id": f"call_{n}", "output": [{"type": "input_text", "text": text}]}}


def rollout(*items: dict) -> str:
    lines = [{"timestamp": "2026-10-31T09:00:00Z", "type": "session_meta", "payload": {"id": "rollout-1"}}, *items]
    return "".join(json.dumps(line) + "\n" for line in lines)


class ToolWallRepo(unittest.TestCase):
    """A dev-loop project with the kit installed, on branch feat/gizmo, its base on main."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.notes = self.root / "notes"
        put(self.notes / "Features" / "20261031 Gizmo" / "Spec.md",
            "---\nfeature: gizmo\ntrack: M\nreview_status: approved\n---\n\n# Gizmo\n")
        self.transcripts = self.root / "transcripts"
        self.transcripts.mkdir()
        self.repo = self.root / "acme-gizmos"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
            git(self.repo, "config", key, value)
        files = {
            "dev-loop.json": json.dumps({"version": KIT_VERSION, "harnesses": ["claude", "codex"]}) + "\n",
            "README.md": "# Acme gizmos\n",
            SOURCE_FILE: "def make(n):\n    return n + 1\n",
            SUITE_FILE: SUITE_TEXT,
        }
        for path, text in files.items():
            put(self.repo / path, text)
        gate = self.repo / "scripts" / "gate.sh"
        put(gate, "#!/bin/sh\necho all unit tests pass\nexit 0\n")
        gate.chmod(0o755)
        shutil.copytree(SKILL, self.repo / ".agents" / "skills" / "dev-loop",
                        ignore=shutil.ignore_patterns("__pycache__", "test_*"))
        for harness, source in (("claude", CLAUDE_HOOKS), ("codex", CODEX_HOOKS)):
            shutil.copytree(source, self.repo / ".dev-loop" / "hooks" / harness)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        git(self.repo, "checkout", "-q", "-b", BRANCH)
        board_tmp = self.root / "tmp"
        board_tmp.mkdir()
        self.port = free_port()
        self.board_hook_ran = False
        self.env = {**os.environ, "HOME": str(self.home), "DEV_LOOP_NOTES": str(self.notes),
                    "CLAUDE_PROJECT_DIR": str(self.repo), "TMPDIR": str(board_tmp),
                    "DEVLOOP_BOARD_PORT": str(self.port)}
        self.addCleanup(self.stop_board)

    def stop_board(self) -> None:
        """Kill the board server the Claude SubagentStart hook started on this test's port."""
        if not self.board_hook_ran:
            return
        deadline = time.monotonic() + 10
        found = health(self.port, timeout=1)
        while not found and time.monotonic() < deadline:
            time.sleep(0.1)
            found = health(self.port, timeout=1)
        if found and isinstance(found.get("pid"), int):
            try:
                os.kill(found["pid"], signal.SIGKILL)
            except OSError:
                pass

    # CLI

    def cli(self, *args: str, stdin: str | None = None) -> tuple[int, dict]:
        result = subprocess.run([sys.executable, str(DEVLOOP), *args], cwd=self.repo, env=self.env, input=stdin,
                                capture_output=True, text=True, timeout=120)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            raise AssertionError(f"devloop {' '.join(args)}: stdout is not one JSON object "
                                 f"(exit {result.returncode}): {result.stdout[-400:]!r} {result.stderr[-400:]!r}")
        return result.returncode, payload

    # Hooks

    def pre_tool(self, tool: str, tool_input: dict, agent_type: str | None, cwd: Path | None = None) -> dict:
        payload = {"session_id": "session-1", "cwd": str(cwd or self.repo), "hook_event_name": "PreToolUse",
                   "tool_name": tool, "tool_input": tool_input}
        if agent_type is not None:
            payload.update({"agent_id": f"agent-{agent_type}", "agent_type": agent_type})
        return payload

    def hook(self, harness: str, name: str, payload: dict, *args: str, cwd: Path | None = None,
             folder: Path | None = None) -> subprocess.CompletedProcess[str]:
        source = folder or (CLAUDE_HOOKS if harness == "claude" else CODEX_HOOKS)
        shell = "bash" if harness == "claude" else "sh"
        return subprocess.run([shell, str(source / name), *args], cwd=cwd or self.repo, input=json.dumps(payload),
                              env=self.env, capture_output=True, text=True, timeout=120)

    def assert_denied(self, result: subprocess.CompletedProcess[str], line: str | None = None) -> None:
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        if line is not None:
            self.assertEqual(result.stderr.strip(), line, result.stdout + result.stderr)

    def assert_allowed(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual((result.returncode, result.stdout.strip(), result.stderr.strip()), (0, "", ""))

    # Audit

    def head(self) -> str:
        return git(self.repo, "rev-parse", "HEAD")

    def git_dir(self) -> Path:
        return (self.repo / git(self.repo, "rev-parse", "--git-dir")).resolve()

    def start(self, agent_id: str, agent_type: str, harness: str = "claude") -> None:
        """SubagentStart: the snapshot of HEAD, by `gate-snapshot.sh` (Claude) or `seal.sh start` (Codex)."""
        payload = {"session_id": "session-1", "cwd": str(self.repo), "hook_event_name": "SubagentStart",
                   "agent_id": agent_id, "agent_type": agent_type}
        if harness == "claude":
            self.board_hook_ran = True
            self.hook("claude", "gate-snapshot.sh", payload)
        else:
            self.hook("codex", "seal.sh", payload, "start", folder=self.repo / ".dev-loop" / "hooks" / "codex")
        self.assertTrue((self.git_dir() / "dev-loop-subagents" / f"{agent_id}.head").is_file(), agent_id)

    def commit_fix(self, message: str) -> tuple[str, str]:
        """An implementer commit of src/app.py; returns its sha and the `git commit` output naming it."""
        put(self.repo / SOURCE_FILE, f"def make(n):\n    return n + 1\n\n\nFIXED = {message!r}\n")
        git(self.repo, "add", "--", SOURCE_FILE)
        git(self.repo, "commit", "-q", "-m", message)
        sha = self.head()
        short = git(self.repo, "rev-parse", "--short", sha)
        return sha, f"[{BRANCH} {short}] {message}\n 1 file changed, 3 insertions(+)\n"

    def audit(self, agent_id: str, agent_type: str, text: str) -> tuple[int, dict]:
        path = self.transcripts / f"{agent_id}.jsonl"
        path.write_text(text)
        payload = {"session_id": "session-1", "cwd": str(self.repo), "hook_event_name": "SubagentStop",
                   "agent_id": agent_id, "agent_type": agent_type, "agent_transcript_path": str(path)}
        return self.cli("audit", stdin=json.dumps(payload))

    def open_taint(self, check: dict) -> list[str]:
        return [r.get("agent_id") for r in check.get("taint") or []]
