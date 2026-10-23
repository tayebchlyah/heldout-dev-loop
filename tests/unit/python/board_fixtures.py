#!/usr/bin/env python3
"""Board test fixtures shared by the board unit tests and the held-out board oracle files.

Sandboxes, helpers and hook mixins only: no test and no oracle assertion lives here, so the gate
(which runs the unit suite) never imports a held-out file.
"""

from __future__ import annotations

import fcntl
import hashlib
import http.client
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar

SCRIPTS = Path(__file__).resolve().parents[3] / "skill" / "scripts"
DEVLOOP = SCRIPTS / "devloop.py"
REPO_ROOT = SCRIPTS.parents[1]
CLAUDE_HOOKS = REPO_ROOT / "claude" / "hooks"
CODEX_HOOKS = REPO_ROOT / "codex" / "hooks"

VERSION = "2.1.0"
PROJECT = "acme-widgets"
MACHINE = {"id": "0123456789abcdef", "machine": "lab-mac", "owner": "Robin"}
OTHER = {"id": "fedcba9876543210", "machine": "bench-pc", "owner": "Casey"}
BOARD_NOTE = "Dev Loop Board.md"
ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
HOOK_NAMES = {"session-start": "SessionStart", "prompt": "UserPromptSubmit", "stop": "Stop", "subagent-stop": "SubagentStop"}

# Cost marker (tests.md rule 6): `slow` only tags a test that runs over the 1 s cheap budget; nothing skips it.
Test = TypeVar("Test", bound=Callable)


def slow(test: Test) -> Test:
    setattr(test, "slow", True)
    return test


GIT = ["git", "-c", "user.email=robin@example.invalid", "-c", "user.name=Robin Quill", "-c", "init.defaultBranch=main",
       "-c", "commit.gpgsign=false"]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def put(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def note_hash(machine_id: str, project: str, feature: str) -> str:
    """D5: first 8 hex characters of SHA-256 of id, project and feature joined by NUL."""
    return hashlib.sha256("\0".join([machine_id, project, feature]).encode()).hexdigest()[:8]


def note_name(machine_id: str, project: str, feature: str) -> str:
    return f"{project}-{feature}-{note_hash(machine_id, project, feature)}.md"


def read_note(path: Path) -> dict:
    """Schema 1: frontmatter only, one key per line, each value JSON (O17)."""
    lines = path.read_text().splitlines()
    assert lines and lines[0] == "---", f"no frontmatter in {path}"
    end = lines.index("---", 1)
    fields = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        assert sep, f"not a key line in {path}: {line!r}"
        fields[key.strip()] = json.loads(value.strip())
    return fields


def note_text(fields: dict) -> str:
    return "---\n" + "".join(f"{k}: {json.dumps(v)}\n" for k, v in fields.items()) + "---\n"


def status_note(**over) -> dict:
    fields = {
        "type": "dev-loop-status", "schema": 1, "project": PROJECT, "feature": "sample", "title": "Sample",
        "description": "", "machine_id": OTHER["id"], "owner": OTHER["owner"], "machine": OTHER["machine"],
        "track": "M", "stage": 3, "stage_times": {"2": "2026-10-01T08:00:00Z", "3": "2026-10-01T09:00:00Z"},
        "tasks": [], "waiting": "", "decisions": [], "assumptions_baseline": [], "shipped_at": "",
        "updated": "2026-10-01T09:00:00Z",
    }
    fields.update(over)
    return fields


def frontmatter_version(path: Path) -> str | None:
    for line in path.read_text().splitlines()[1:]:
        if line.strip() == "---":
            return None
        if line.startswith("version:"):
            return line.partition(":")[2].strip().strip("\"'")
    return None


def snapshot(base: Path) -> dict[str, tuple]:
    """Every file and link under base, `.git` excluded: link target, or bytes and mtime."""
    found: dict[str, tuple] = {}
    if not base.exists():
        return found
    for top, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in [*files, *[d for d in dirs if os.path.islink(os.path.join(top, d))]]:
            if name == ".git":
                continue
            path = Path(top) / name
            rel = str(path.relative_to(base))
            if path.is_symlink():
                found[rel] = ("link", os.readlink(path))
            else:
                found[rel] = ("file", path.read_bytes(), path.stat().st_mtime_ns)
    return found


def request(port: int, path: str, host: str | None = None, headers: dict | None = None,
            timeout: float = 5, address: str = "127.0.0.1") -> tuple[int, dict, bytes]:
    conn = http.client.HTTPConnection(address, port, timeout=timeout)
    try:
        conn.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", host or f"127.0.0.1:{port}")
        for key, value in (headers or {}).items():
            conn.putheader(key, value)
        conn.endheaders()
        response = conn.getresponse()
        body = response.read()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, body
    finally:
        conn.close()


def health(port: int, timeout: float = 2) -> dict | None:
    try:
        status, _, body = request(port, "/health", timeout=timeout)
    except OSError:
        return None
    if status != 200:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


class Board(unittest.TestCase):
    """A main checkout `acme-widgets` with a bare origin, committed `dev-loop.json` and MANIFEST,
    a notes folder inside a vault, a sandboxed HOME with a machine.json, and a free board port."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.vault = self.root / "vault"
        self.notes = self.vault / "Projects" / "Acme Widgets"
        (self.notes / "specs").mkdir(parents=True)
        (self.notes / "plans").mkdir()
        self.origin = self.root / "remotes" / f"{PROJECT}.git"
        self.origin.parent.mkdir()
        git(self.root, "init", "-q", "--bare", str(self.origin))
        self.repo = self.root / PROJECT
        self.make_repo(self.repo, self.origin)
        self.home = self.root / "home"
        self.port = free_port()
        self.env = self.make_env(self.home, self.notes)
        self.write_machine(self.home, MACHINE)
        self.addCleanup(self.kill_stray_server)

    # sandbox

    def make_repo(self, repo: Path, origin: Path) -> None:
        repo.mkdir(parents=True)
        git(repo, "init", "-q")
        put(repo / "GLOSSARY.md", "# Glossary\n")
        put(repo / "dev-loop.json", json.dumps({"version": VERSION, "harnesses": ["claude"]}) + "\n")
        put(repo / ".dev-loop" / "MANIFEST.json", json.dumps({"version": VERSION, "files": {}}) + "\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "base")
        git(repo, "remote", "add", "origin", str(origin))
        git(repo, "push", "-q", "origin", "main")
        git(repo, "fetch", "-q", "origin")

    def make_env(self, home: Path, notes: Path) -> dict[str, str]:
        home.mkdir(parents=True, exist_ok=True)
        put(home / ".gitconfig", "[user]\n\tname = Robin Quill\n\temail = robin@example.invalid\n")
        env = {k: v for k, v in os.environ.items()
               if k not in ("GIT_CONFIG_GLOBAL", "XDG_CONFIG_HOME", "GIT_DIR", "GIT_WORK_TREE", "DEV_LOOP_NOTES")}
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), DEV_LOOP_NOTES=str(notes),
                   DEVLOOP_BOARD_PORT=str(self.port))
        return env

    def write_machine(self, home: Path, machine: dict) -> Path:
        path = home / ".config" / "dev-loop" / "machine.json"
        put(path, json.dumps(machine) + "\n")
        return path

    def kill_stray_server(self) -> None:
        found = health(self.port, timeout=1)
        if found and isinstance(found.get("pid"), int):
            try:
                os.kill(found["pid"], signal.SIGKILL)
            except OSError:
                pass

    # runs

    def board(self, *args: str, cwd: Path | None = None, stdin: dict | None = None, env: dict | None = None,
              serve: bool = False, timeout: float = 60) -> tuple[int, dict]:
        cmd = [sys.executable, str(DEVLOOP), "board", *args]
        if not serve and "--stop-server" not in args:
            cmd.append("--no-serve")
        result = subprocess.run(cmd, cwd=cwd or self.repo, env=env or self.env, capture_output=True, text=True,
                                input=json.dumps(stdin) if stdin is not None else "", timeout=timeout)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            raise AssertionError(f"stdout is not one JSON object: {result.stdout!r} {result.stderr!r}")
        return result.returncode, payload

    def ok(self, *args: str, **kwargs) -> dict:
        code, payload = self.board(*args, **kwargs)
        self.assertEqual(code, 0, payload)
        return payload

    def event(self, name: str, cwd: Path, env: dict | None = None) -> dict:
        stdin = {"session_id": "session-1", "cwd": str(cwd), "hook_event_name": HOOK_NAMES[name]}
        return self.ok("--event", name, cwd=cwd, stdin=stdin, env=env)

    # notes folder and git state

    def spec(self, slug: str, title: str, review: str = "approved", track: str = "M",
             assumptions: tuple[str, ...] = ("Widgets are blue.",), shipped: str = "") -> Path:
        path = self.notes / "specs" / f"20261001 {title}.md"
        put(path, f"---\ntype: spec\nreview_status: {review}\nfeature: {slug}\ntrack: {track}\nshipped: {shipped}\n---\n\n"
                  f"# {title}\n\n## Goal\n\n{title} for the widget shop.\n\n## Assumptions\n\n"
                  + "".join(f"- {a}\n" for a in assumptions))
        return path

    def set_field(self, path: Path, key: str, value: str) -> None:
        path.write_text(re.sub(rf"^{key}:.*$", f"{key}: {value}", path.read_text(), count=1, flags=re.MULTILINE))

    def worktree(self, slug: str, prefix: str = "feat", repo: Path | None = None) -> Path:
        repo = repo or self.repo
        path = repo.parent / "worktrees" / f"{repo.name}-{slug}"
        git(repo, "worktree", "add", "-q", "-b", f"{prefix}/{slug}", str(path), "origin/main")
        return path.resolve()

    def acceptance(self, wt: Path, slug: str) -> None:
        put(wt / "tests" / "acceptance" / f"test_{slug}.py", "def test_flow():\n    pass\n")

    def plan(self, spec: Path, title: str, tasks: int) -> Path:
        path = self.notes / "plans" / f"20261002 {title} Plan.md"
        put(path, f"---\nspec: {spec.name}\n---\n\n# Plan\n\n" + "".join(f"### Task {n}: Step {n}\n\n" for n in range(1, tasks + 1)))
        return path

    def ledger(self, wt: Path, plan: Path, *lines: str) -> None:
        put(wt / ".superpowers" / "sdd" / plan.stem / "progress.md", "# Ledger\n" + "".join(f"{line}\n" for line in lines))

    def stage5(self, slug: str, title: str, tasks: int = 3, repo: Path | None = None) -> tuple[Path, Path]:
        spec = self.spec(slug, title)
        wt = self.worktree(slug, repo=repo)
        self.acceptance(wt, slug)
        return wt, self.plan(spec, title, tasks)

    def stage7(self, slug: str, title: str) -> Path:
        wt, plan = self.stage5(slug, title, tasks=2)
        self.ledger(wt, plan, "Task 1: complete", "Task 2: complete", "Feature gate: green at abc123")
        put(wt / f"{slug}.txt", "done\n")
        git(wt, "add", f"{slug}.txt")
        git(wt, "commit", "-q", "-m", slug)
        return wt

    @property
    def board_dir(self) -> Path:
        return self.notes / "dev-loop-board"

    @property
    def claims(self) -> Path:
        return self.repo / ".git" / "dev-loop" / "board-claims.json"

    def own_notes(self, machine_id: str = MACHINE["id"]) -> dict[str, Path]:
        """Status notes of machine_id in `dev-loop-board/features/` and `dev-loop-board/` (Board Robustness D4)."""
        found = {}
        if not self.board_dir.is_dir():
            return found
        for path in [*sorted(self.board_dir.glob("features/*.md")), *sorted(self.board_dir.glob("*.md"))]:
            if path.name == BOARD_NOTE or path.name.startswith(".") or path.is_symlink():
                continue
            fields = read_note(path)
            if fields.get("machine_id") == machine_id:
                self.assertNotIn(fields["feature"], found, f"two own notes for {fields['feature']}")
                found[fields["feature"]] = path
        return found

    def note(self, slug: str, machine_id: str = MACHINE["id"]) -> dict:
        notes = self.own_notes(machine_id)
        self.assertIn(slug, notes, f"no own note for {slug}: {sorted(notes)}")
        return read_note(notes[slug])

    def skipped(self, payload: dict, reason: str, feature: object = "any") -> bool:
        return any(s.get("reason") == reason and (feature == "any" or s.get("feature") == feature)
                   for s in payload.get("skipped", []))


FAKE_DEVLOOP = """#!/usr/bin/env python3
import json, os, sys
data = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
with open(os.environ["BOARD_TEST_LOG"], "a") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "stdin": data}) + "\\n")
print(json.dumps({"noise": "board ran"}))
print("board stderr noise", file=sys.stderr)
sys.exit(int(os.environ.get("BOARD_TEST_EXIT", "1")))
"""


class BoardHookFixture:
    """Mixin of BoardHookTests (test_board): a kit layout copy in a temp repo, a fake devloop.py that
    logs each call and fails."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.repo = self.root / PROJECT
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        put(self.repo / "tracked.txt", "before\n")
        git(self.repo, "add", "tracked.txt")
        git(self.repo, "commit", "-q", "-m", "fixture")
        put(self.repo / "tracked.txt", "after\n")
        put(self.repo / "dev-loop.json", json.dumps({"version": VERSION, "harnesses": ["claude", "codex"]}))
        for harness, source in (("claude", CLAUDE_HOOKS), ("codex", CODEX_HOOKS)):
            target = self.repo / ".dev-loop" / "hooks" / harness
            target.mkdir(parents=True)
            for script in source.iterdir():
                shutil.copy2(script, target / script.name)
        fake = self.repo / ".agents" / "skills" / "dev-loop" / "scripts" / "devloop.py"
        put(fake, FAKE_DEVLOOP)
        fake.chmod(0o755)
        self.log = self.root / "board-calls.jsonl"
        self.home = self.root / "home"
        self.home.mkdir()

    def gate(self, passes: bool) -> None:
        gate = self.repo / "scripts" / "gate.sh"
        put(gate, f"#!/bin/sh\necho gate output\nexit {0 if passes else 1}\n")
        gate.chmod(0o755)

    def hook(self, harness: str, script: str, event: str, *args: str) -> subprocess.CompletedProcess[str]:
        path = self.repo / ".dev-loop" / "hooks" / harness / script
        payload = {"session_id": "session-1", "cwd": str(self.repo), "hook_event_name": event,
                   "stop_hook_active": False, "agent_id": "a1", "agent_type": "general-purpose"}
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.repo), "BOARD_TEST_LOG": str(self.log),
               "HOME": str(self.home), "BOARD_TEST_EXIT": "1"}
        return subprocess.run(["bash" if harness == "claude" else "sh", str(path), *args], cwd=self.repo,
                              input=json.dumps(payload), text=True, capture_output=True, env=env, timeout=60)

    def calls(self, wait: float) -> list[dict]:
        deadline = time.monotonic() + wait
        while not self.log.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        time.sleep(0.2)
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]

    def assert_board_event(self, calls: list[dict], event: str) -> None:
        board = [c for c in calls if "board" in c["argv"]]
        self.assertEqual(len(board), 1, calls)
        argv = board[0]["argv"]
        self.assertIn("--event", argv)
        self.assertEqual(argv[argv.index("--event") + 1], event)
        self.assertEqual(json.loads(board[0]["stdin"]).get("cwd"), str(self.repo))


ROBUST_HOOK_NAMES = {"session-start": "SessionStart", "prompt": "UserPromptSubmit", "stop": "Stop",
              "subagent-stop": "SubagentStop", "post-tool": "PostToolUse"}


def epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def birth(path: Path) -> float:
    st = path.stat()
    return getattr(st, "st_birthtime", st.st_mtime)


def set_time(path: Path, when: float) -> None:
    os.utime(path, (when, when))


def yaml_block(fields: dict) -> str:
    """The Obsidian properties form (Fact 5): plain scalars, nested block maps and block lists."""

    def scalar(value: object) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if value == "":
            return '""'
        return str(value)

    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, dict) and value:
            lines.append(f"{key}:")
            lines += [f'  "{k}": {scalar(v)}' for k, v in value.items()]
        elif isinstance(value, list) and value:
            lines.append(f"{key}:")
            for item in value:
                if isinstance(item, dict):
                    for i, (k, v) in enumerate(item.items()):
                        lines.append(f"  {'- ' if i == 0 else '  '}{k}: {scalar(v)}")
                else:
                    lines.append(f"  - {scalar(item)}")
        elif isinstance(value, (dict, list)):
            lines.append(f"{key}: {'{}' if isinstance(value, dict) else '[]'}")
        else:
            lines.append(f"{key}: {scalar(value)}")
    return "\n".join([*lines, "---"]) + "\n"


class RobustBoard(Board):
    """The test_board sandbox, plus a temporary CLAUDE_CONFIG_DIR for fake native task lists."""

    def setUp(self) -> None:
        super().setUp()
        self.claude_dir = self.root / "claude-config"
        self.claude_dir.mkdir()
        self.env["CLAUDE_CONFIG_DIR"] = str(self.claude_dir)

    @property
    def features_dir(self) -> Path:
        return self.board_dir / "features"

    @property
    def state_file(self) -> Path:
        return self.repo / ".git" / "dev-loop" / "board-state.json"

    def own_name(self, slug: str) -> str:
        return note_name(MACHINE["id"], PROJECT, slug)

    def own_path(self, slug: str) -> Path:
        return self.features_dir / self.own_name(slug)

    def own_note(self, slug: str) -> dict:
        path = self.own_path(slug)
        listing = sorted(str(p.relative_to(self.board_dir)) for p in self.board_dir.rglob("*")) if self.board_dir.exists() else []
        self.assertTrue(path.is_file(), f"no note at features/{path.name}: {listing}")
        return read_note(path)

    def state(self) -> dict:
        self.assertTrue(self.state_file.is_file(), f"no {self.state_file.name}")
        return json.loads(self.state_file.read_text())

    def old_note(self, slug: str, **over) -> dict:
        """An own note in the 2.1 form: own name, own machine_id, in `dev-loop-board/`."""
        fields = status_note(feature=slug, machine_id=MACHINE["id"], owner=MACHINE["owner"], machine=MACHINE["machine"],
                             **over)
        put(self.board_dir / self.own_name(slug), note_text(fields))
        return fields

    def sdd(self, wt: Path, plan: Path) -> Path:
        return wt / ".superpowers" / "sdd" / plan.stem

    def native(self, session: str, *items: tuple[str, str]) -> None:
        folder = self.claude_dir / "tasks" / session
        for n, (subject, status) in enumerate(items, 1):
            put(folder / f"{n}.json", json.dumps({"id": str(n), "subject": subject, "status": status}) + "\n")

    def hook_event(self, name: str, cwd: Path, session: str, command: str | None = None) -> dict:
        stdin: dict = {"session_id": session, "cwd": str(cwd), "hook_event_name": ROBUST_HOOK_NAMES[name]}
        if name == "post-tool":
            stdin.update(tool_name="Bash", tool_input={"command": command or "task-done 1"})
        return self.ok("--event", name, cwd=cwd, stdin=stdin)

    def status(self, cwd: Path | None = None, env: dict | None = None) -> dict:
        result = subprocess.run([sys.executable, str(DEVLOOP), "status"], cwd=cwd or self.repo, env=env or self.env,
                                capture_output=True, text=True, timeout=60)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            raise AssertionError(f"status stdout is not one JSON object: {result.stdout!r} {result.stderr!r}")
        self.assertEqual(result.returncode, 0, payload)
        return payload

    def assert_time(self, value: object, expected: float, what: str) -> None:
        self.assertIsInstance(value, str, what)
        assert isinstance(value, str)
        self.assertRegex(value, ISO_Z, what)
        self.assertLess(abs(epoch(value) - expected), 1.0, f"{what}: {value} is not {datetime.fromtimestamp(expected, timezone.utc)}")

    def states(self, slug: str) -> list[str]:
        return [t["state"] for t in self.own_note(slug)["tasks"]]

    def hold_lock(self):
        lock = self.repo / ".git" / "dev-loop" / "board.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock, "a+")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle


FAKE_CLI = (
    "#!/usr/bin/env python3\nimport json, os, sys\ndata = sys.stdin.read()\n"
    "with open(os.environ['BOARD_TEST_LOG'], 'a') as log:\n"
    "    log.write(json.dumps({'label': %r, 'argv': sys.argv[1:], 'cwd': os.getcwd(), 'stdin': data}) + '\\n')\n"
)


class RobustnessHookFixture:
    """Mixin of RobustnessHookTests (test_board_robustness): `board.sh` from a kit layout copy in a temp
    repo, a fake devloop.py that logs each call with its label."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.repo = self.root / PROJECT
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        put(self.repo / "dev-loop.json", json.dumps({"version": VERSION, "harnesses": ["claude", "codex"]}) + "\n")
        for harness, source in (("claude", CLAUDE_HOOKS), ("codex", CODEX_HOOKS)):
            target = self.repo / ".dev-loop" / "hooks" / harness
            target.mkdir(parents=True)
            for script in source.iterdir():
                shutil.copy2(script, target / script.name)
        self.fake(self.repo, "MAIN")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "kit")
        self.log = self.root / "board-calls.jsonl"
        self.home = self.root / "home"
        self.home.mkdir()

    def fake(self, checkout: Path, label: str) -> None:
        path = checkout / ".agents" / "skills" / "dev-loop" / "scripts" / "devloop.py"
        put(path, FAKE_CLI % label)
        path.chmod(0o755)

    def board_sh(self, harness: str, arg: str, cwd: Path, command: str | None = None,
                 checkout: Path | None = None) -> subprocess.CompletedProcess[str]:
        payload: dict = {"session_id": "sess-hook", "cwd": str(cwd), "hook_event_name": "PostToolUse"}
        if command is not None:
            payload.update(tool_name="Bash", tool_input={"command": command})
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        env.update(BOARD_TEST_LOG=str(self.log), HOME=str(self.home))
        if harness == "claude":
            env["CLAUDE_PROJECT_DIR"] = str(self.repo)
        script = (checkout or self.repo) / ".dev-loop" / "hooks" / harness / "board.sh"
        return subprocess.run(["bash" if harness == "claude" else "sh", str(script), arg], cwd=cwd,
                              input=json.dumps(payload), text=True, capture_output=True, env=env, timeout=60)

    def calls(self, wait: float) -> list[dict]:
        deadline = time.monotonic() + wait
        while not self.log.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        time.sleep(0.3)
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]
