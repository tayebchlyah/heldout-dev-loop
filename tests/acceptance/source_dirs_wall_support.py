#!/usr/bin/env python3
"""Shared fixtures of the held-out source dirs wall tests (spec: Source Dirs Wall).

One test per `## Acceptance flows` item (F<n>); each docstring names the `## Acceptance oracle` lines (O<n>) it
checks. Seam: kit commands and hooks run as processes against a scratch git repo (the Tool Walls and Held-Out Seal
seam). The hooks run from the kit source: `deny-acceptance-to-subagents.sh` with `agent_type` `spec-tester` and
`deny-path.sh --source-dirs src/ app/ backend/ frontend/` (the Claude hooks), `acceptance-wall.sh` with
`agent_type` `spec_tester` (the Codex hook), each fed PreToolUse JSON on stdin, `agent_id` set; the exit code is
the decision. `devloop.py source-wall`, `audit` and `merge-check` stdout JSON; `check_setup.py` output.

Fixture (the oracle fixture): a scratch repo `acme-gizmos` with branch `main` and the feature branch `feat/gizmo`
checked out, `cwd` and `CLAUDE_PROJECT_DIR` its top, `HOME` its parent folder. The scratch path holds no segment
`src`, `app`, `backend` or `frontend`. "Main ref key": the `source_dirs` of the `dev-loop.json` committed on `main`,
written by a commit made on `main` without a checkout, so the feature checkout file stays as the branch holds it.

Fixture choices the spec leaves open:
- "Read X": Claude `Read` with `file_path` X, Codex Bash `cat X`. "Write X": Claude `Write`, Codex `apply_patch`
  `*** Add File: X` (the patch text under `command` and `patch`). A Claude-only tool call (`Grep`,
  `SubagentHandback`, `ToolSearch`) runs through the two Claude hooks; a Codex-only call (`update_plan`) through
  the Codex hook.
- A linked worktree: `<repo top>/.claude/worktrees/w`, branch `feat/w` from `main`; a call from it has its `cwd`
  and `CLAUDE_PROJECT_DIR` there.
- A walled call is any exit 2; an open call is exit 0 with no JSON deny on stdout.
Every name in the scratch repos is invented.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
CHECKER = SKILL / "scripts" / "check_setup.py"
INSTALLER = REPO_ROOT / "bin" / "install.mjs"
CLAUDE_HOOKS = REPO_ROOT / "claude" / "hooks"
CODEX_HOOKS = REPO_ROOT / "codex" / "hooks"
KIT_VERSION = json.loads((REPO_ROOT / "package.json").read_text())["version"]

NET = "deny-acceptance-to-subagents.sh"
DENY_PATH = "deny-path.sh"
CODEX_WALL = "acceptance-wall.sh"
DEFAULT_DIRS = ("src/", "app/", "backend/", "frontend/")
SOURCE_DIRS_ARGS = ("--source-dirs", *DEFAULT_DIRS)

SLUG = "gizmo"
BRANCH = f"feat/{SLUG}"
REPO_NAME = "acme-gizmos"
BASE_CONFIG = {"version": KIT_VERSION, "harnesses": ["claude", "codex"]}
SEAL = "python3 .agents/skills/dev-loop/scripts/devloop.py seal --feature gizmo"

# Distinctive lines (30 characters or more) of internal/x.go, in no other fixture file.
GO1 = 'return fmt.Sprintf("quartz-heron-%04d-ledger", serialNumber)'
GO2 = 'const fallbackRegistry = "/var/lib/acme/gizmo-registry-7731.db"'
GO_TEXT = f"package internal\n\nimport \"fmt\"\n\nfunc Serial(serialNumber int) string {{\n\t{GO1}\n}}\n\n{GO2}\n"

FILES = {
    "internal/x.go": GO_TEXT,
    "cmd/app/main.go": "package main\n\nfunc main() {}\n",
    "src/app.py": "def make(n):\n    return n + 1\n",
    "pkg/src/y.py": "def y():\n    return 'y'\n",
    "api/spec.go": "package api\n\nconst Spec = \"v1\"\n",
    "docs/guide.md": "# Guide\n",
    "scripts/run.sh": "#!/bin/sh\necho run\n",
    "tests/acceptance/test_x.py": "def test_x():\n    assert 1 == 1\n",
}

MISSING = object()


def put(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def clean_env(home: Path, tmp: Path) -> dict[str, str]:
    """The caller's env without the dev-loop, harness and kit variables; HOME and TMPDIR set."""
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("DEV_LOOP", "DEVLOOP", "CLAUDE", "CODEX", "KIT_ROOT"))}
    env.update({"HOME": str(home), "TMPDIR": str(tmp), "GIT_CONFIG_NOSYSTEM": "1", "GIT_EDITOR": "true",
                "GIT_TERMINAL_PROMPT": "0", "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def git(cwd: Path, env: dict[str, str], *args: str, stdin: str | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, env=env, input=stdin, capture_output=True, text=True,
                            timeout=60)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
    return result.stdout.strip()


def config_text(value: object = MISSING) -> str:
    data = dict(BASE_CONFIG)
    if value is not MISSING:
        data["source_dirs"] = value
    return json.dumps(data, indent=2) + "\n"


def patch_add(path: str) -> str:
    return f"*** Begin Patch\n*** Add File: {path}\n+def test_gizmo():\n+    assert True\n*** End Patch\n"


def denies(result: subprocess.CompletedProcess[str]) -> bool:
    """Exit 2, or a JSON deny or block decision on stdout."""
    if result.returncode == 2:
        return True
    try:
        out = json.loads(result.stdout) if result.stdout.strip() else {}
    except ValueError:
        return False
    if not isinstance(out, dict):
        return False
    decision = (out.get("hookSpecificOutput") or {}).get("permissionDecision") or out.get("decision")
    return decision in ("deny", "block")


class Call:
    """One tool call, as each harness makes it: `(tool, tool_input)` per harness, or None."""

    def __init__(self, label: str, claude: tuple[str, dict] | None, codex: tuple[str, dict] | None) -> None:
        self.label, self.claude, self.codex = label, claude, codex

    def __repr__(self) -> str:
        return self.label


def read(path: str) -> Call:
    return Call(f"Read {path}", ("Read", {"file_path": path}), ("Bash", {"command": f"cat {shlex.quote(path)}"}))


def write(path: str) -> Call:
    text = patch_add(path)
    return Call(f"Write {path}", ("Write", {"file_path": path, "content": "def test_gizmo():\n    assert True\n"}),
                ("apply_patch", {"command": text, "patch": text}))


def bash(command: str) -> Call:
    return Call(f"Bash {command!r}", ("Bash", {"command": command}), ("Bash", {"command": command}))


def claude_only(tool: str, tool_input: dict) -> Call:
    return Call(f"Claude {tool} {json.dumps(tool_input)}", (tool, tool_input), None)


def codex_only(tool: str, tool_input: dict) -> Call:
    return Call(f"Codex {tool} {json.dumps(tool_input)}", None, (tool, tool_input))


class SourceWallRepo(unittest.TestCase):
    """The oracle fixture: `acme-gizmos` on `feat/gizmo`, `main` with the base config and no `source_dirs`."""

    EXTRA_FILES: dict[str, str] = {}

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        for part in self.root.parts:
            assert part not in ("src", "app", "backend", "frontend"), self.root
        self.tmp = self.root / "tmp"
        self.tmp.mkdir()
        self.repo = self.root / REPO_NAME
        self.repo.mkdir()
        self.env = clean_env(self.root, self.tmp)
        self.env["CLAUDE_PROJECT_DIR"] = str(self.repo)
        self.git("init", "-q", "-b", "main")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
            self.git("config", key, value)
        for path, text in {"dev-loop.json": config_text(), **FILES, **self.EXTRA_FILES}.items():
            put(self.repo / path, text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Base")
        self.git("checkout", "-q", "-b", BRANCH)

    # Git

    def git(self, *args: str, cwd: Path | None = None, stdin: str | None = None) -> str:
        return git(cwd or self.repo, self.env, *args, stdin=stdin)

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def commit_on(self, branch: str, files: dict[str, str], message: str, repo: Path | None = None) -> str:
        """A commit of `files` on `branch` made without a checkout: the worktree and its index stay as they are."""
        repo = repo or self.repo
        index = self.root / "commit-index"
        env = {**self.env, "GIT_INDEX_FILE": str(index)}
        try:
            git(repo, env, "read-tree", f"refs/heads/{branch}")
            for path, text in files.items():
                blob = git(repo, env, "hash-object", "-w", "--stdin", stdin=text)
                git(repo, env, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}")
            tree = git(repo, env, "write-tree")
            sha = git(repo, env, "commit-tree", tree, "-p", f"refs/heads/{branch}", "-m", message)
            git(repo, env, "update-ref", f"refs/heads/{branch}", sha)
        finally:
            index.unlink(missing_ok=True)
        return sha

    def main_key(self, value: object = MISSING, raw: str | None = None) -> None:
        """The `dev-loop.json` of `main`: the base config with `source_dirs` set to `value` (none: no key), or
        `raw`."""
        self.commit_on("main", {"dev-loop.json": raw if raw is not None else config_text(value)},
                       "Set the main dev-loop.json")

    def branch_key(self, value: object = MISSING) -> str:
        """A feature branch commit of `dev-loop.json` with `source_dirs` set to `value` (none: no key)."""
        put(self.repo / "dev-loop.json", config_text(value))
        self.git("add", "--", "dev-loop.json")
        self.git("commit", "-q", "--allow-empty", "-m", "Change dev-loop.json")
        return self.head()

    def worktree(self) -> Path:
        path = self.repo / ".claude" / "worktrees" / "w"
        if not path.is_dir():
            self.git("worktree", "add", "-q", "-b", "feat/w", str(path), "main")
        return path.resolve()

    # Hooks

    def payload(self, tool: str, tool_input: dict, agent_type: str | None, cwd: Path) -> dict:
        payload = {"session_id": "session-1", "cwd": str(cwd), "hook_event_name": "PreToolUse",
                   "tool_name": tool, "tool_input": tool_input}
        if agent_type is not None:
            payload.update({"agent_id": f"agent-{agent_type}", "agent_type": agent_type})
        return payload

    def run_hook(self, harness: str, name: str, payload: dict, *args: str, cwd: Path,
                 folder: Path | None = None) -> subprocess.CompletedProcess[str]:
        source = folder or (CLAUDE_HOOKS if harness == "claude" else CODEX_HOOKS)
        shell = "bash" if harness == "claude" else "sh"
        env = {**self.env, "CLAUDE_PROJECT_DIR": str(cwd)}
        return subprocess.run([shell, str(source / name), *args], cwd=cwd, input=json.dumps(payload), env=env,
                              capture_output=True, text=True, timeout=120)

    def hooks(self, call: Call, cwd: Path | None = None, folders: dict[str, Path] | None = None,
              claude_agent: str | None = "spec-tester", codex_agent: str | None = "spec_tester",
              deny_path: bool = True) -> list[tuple[str, subprocess.CompletedProcess[str]]]:
        """(hook, result) for each hook that sees `call`: the Claude hooks, then the Codex hook."""
        cwd = cwd or self.repo
        folders = folders or {}
        out = []
        if call.claude is not None:
            tool, tool_input = call.claude
            payload = self.payload(tool, tool_input, claude_agent, cwd)
            out.append((NET, self.run_hook("claude", NET, payload, cwd=cwd, folder=folders.get("claude"))))
            if deny_path:
                out.append((f"{DENY_PATH} --source-dirs", self.run_hook(
                    "claude", DENY_PATH, payload, *SOURCE_DIRS_ARGS, cwd=cwd, folder=folders.get("claude"))))
        if call.codex is not None:
            tool, tool_input = call.codex
            payload = self.payload(tool, tool_input, codex_agent, cwd)
            out.append((CODEX_WALL, self.run_hook("codex", CODEX_WALL, payload, cwd=cwd,
                                                  folder=folders.get("codex"))))
        return out

    def expect(self, oracle: str, calls: tuple[Call, ...] | list[Call], walled: bool, names: str | None = None,
               **kw) -> None:
        """Each hook that sees each call denies it (`walled`) or lets it pass; a deny names `names` on stderr."""
        for call in calls:
            for hook, result in self.hooks(call, **kw):
                with self.subTest(oracle=oracle, hook=hook, call=call.label, cwd=str(kw.get("cwd") or "")):
                    detail = f"exit {result.returncode}: {result.stdout[-400:]} {result.stderr[-400:]}"
                    if walled:
                        self.assertEqual(result.returncode, 2, detail)
                        if names is not None:
                            self.assertIn(names, result.stderr, detail)
                    else:
                        self.assertEqual(result.returncode, 0, detail)
                        self.assertFalse(denies(result), detail)

    # CLI

    def cli(self, *args: str, stdin: str | None = None, cwd: Path | None = None) -> tuple[int, dict]:
        result = subprocess.run([sys.executable, str(DEVLOOP), *args], cwd=cwd or self.repo, env=self.env,
                                input=stdin, capture_output=True, text=True, timeout=180)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            raise AssertionError(f"devloop {' '.join(args)}: stdout is not one JSON object "
                                 f"(exit {result.returncode}): {result.stdout[-400:]!r} {result.stderr[-400:]!r}")
        if not isinstance(payload, dict):
            raise AssertionError(f"devloop {' '.join(args)}: stdout is not a JSON object: {payload!r}")
        return result.returncode, payload


# check_setup.py output

SOURCE_FAILURE = re.compile(r"source_dirs missing in dev-loop\.json|source_dirs (?:in dev-loop\.json|at \S+) is "
                            r"invalid|dev-loop\.json at \S+ is not a JSON object")


def source_failures(output: str) -> list[str]:
    """The `source_dirs` failure lines of a `check_setup.py` output."""
    return [line for line in output.splitlines() if SOURCE_FAILURE.search(line)]


def user_actions(output: str) -> list[str]:
    """The lines under the `User actions required` heading."""
    lines = output.splitlines()
    heads = [n for n, line in enumerate(lines) if line.startswith("User actions required")]
    return lines[heads[0] + 1:] if heads else []


def verdict(output: str, harness: str) -> str:
    """The `<harness>: PASS` or `<harness>: FAIL` line."""
    found = [line.strip() for line in output.splitlines() if re.fullmatch(rf"{harness}: (PASS|FAIL)", line.strip())]
    assert len(found) == 1, f"no single {harness} verdict line in: {output[-1500:]}"
    return found[0]


def names_word(line: str, word: str) -> bool:
    return re.search(rf"(?<![\w/.-]){re.escape(word)}(?![\w-])", line) is not None
