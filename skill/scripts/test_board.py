#!/usr/bin/env python3
"""Held-out oracle tests for `devloop.py board`, its server and its hooks (spec: Progress Board).

Each test names the `## Acceptance oracle` number (O<n>) or flow (F<n>) it checks. The tests use
only the public seams: the stdout JSON of `devloop.py board`, the files it writes, the HTTP answers
of the server it starts, and the stdout of the hook scripts. Every name in here is invented (D33).
"""

from __future__ import annotations

import fcntl
import hashlib
import http.client
import http.server
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
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
        found = {}
        if not self.board_dir.is_dir():
            return found
        for path in sorted(self.board_dir.glob("*.md")):
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


class BoardWriterTests(Board):
    def test_o1_note_path_type_schema_and_machine_id(self) -> None:
        """O1: in a feature worktree, writes <notes>/dev-loop-board/<project>-<slug>-<hash>.md."""
        wt, _ = self.stage5("gadget", "Gadget Export")
        out = self.ok(cwd=wt)
        path = self.board_dir / note_name(MACHINE["id"], PROJECT, "gadget")
        self.assertTrue(path.is_file(), sorted(p.name for p in self.board_dir.iterdir()))
        fields = read_note(path)
        self.assertEqual((fields["type"], fields["schema"], fields["machine_id"]), ("dev-loop-status", 1, MACHINE["id"]))
        self.assertIn(str(path), [str(Path(p).resolve()) for p in out["notes"]])

    def test_o2_first_run_creates_machine_json_later_runs_keep_it(self) -> None:
        """O2, D3: the first run writes machine.json (16 hex id, owner from git user.name); later runs never change it."""
        machine = self.home / ".config" / "dev-loop" / "machine.json"
        machine.unlink()
        self.worktree("tiny", prefix="fix")
        self.ok()
        data = json.loads(machine.read_text())
        self.assertRegex(data["id"], r"^[0-9a-f]{16}$")
        self.assertEqual(data["owner"], "Robin")
        self.assertIsInstance(data["machine"], str)
        self.assertTrue(data["machine"])
        before = (machine.read_bytes(), machine.stat().st_mtime_ns)
        self.ok()
        self.assertEqual((machine.read_bytes(), machine.stat().st_mtime_ns), before)
        machine.write_text(json.dumps({**data, "machine": "bench-mac"}) + "\n")
        before = (machine.read_bytes(), machine.stat().st_mtime_ns)
        self.ok()
        self.assertEqual((machine.read_bytes(), machine.stat().st_mtime_ns), before)
        self.assertEqual(self.note("tiny", data["id"])["machine"], "bench-mac")

    def test_o3_project_is_the_same_for_two_clones_in_other_folders(self) -> None:
        """O3, D4: project comes from the origin URL, not the clone folder name."""
        one = self.root / "clone-one"
        two = self.root / "desk" / "widgets-copy"
        two.parent.mkdir()
        for path, slug in ((one, "alpha"), (two, "beta")):
            git(self.root, "clone", "-q", str(self.origin), str(path))
            wt = self.worktree(slug, prefix="fix", repo=path)
            self.ok(cwd=wt)
        self.assertEqual(self.note("alpha")["project"], self.note("beta")["project"])

    def test_o4_notes_for_the_write_set_only(self) -> None:
        """O4, D9: worktree features, claimed features and Track S worktrees; no other feature."""
        self.spec("alpha", "Alpha Report")
        self.worktree("alpha")
        self.spec("beta", "Beta Import", review="draft")
        self.ok("--claim", "beta")
        self.spec("gamma", "Gamma Draft", review="draft")
        self.spec("delta", "Delta Approved")
        self.spec("epsilon", "Epsilon Done", shipped="2026-10-10")
        self.worktree("epsilon")
        self.worktree("tiny", prefix="fix")
        out = self.ok()
        own = self.own_notes()
        self.assertEqual(set(own), {"alpha", "beta", "tiny"})
        self.assertEqual(read_note(own["tiny"])["track"], "S")
        self.assertEqual({str(Path(p).resolve()) for p in out["notes"]}, {str(p) for p in own.values()})

    def test_o5_claim_then_stop_gives_stage_2_approve_the_spec(self) -> None:
        """O5: --claim on the default branch, then --event stop: Stage 2, waiting "approve the spec"."""
        self.spec("gadget", "Gadget Export", review="draft")
        self.ok("--claim", "gadget")
        self.event("stop", self.repo)
        note = self.note("gadget")
        self.assertEqual((note["stage"], note["waiting"]), (2, "approve the spec"))

    def test_o6_claim_without_a_spec_exits_1_and_writes_nothing(self) -> None:
        """O6: --claim <slug> with no spec for the slug exits 1 and writes nothing."""
        self.spec("gadget", "Gadget Export", review="draft")
        before = snapshot(self.root)
        code, _ = self.board("--claim", "nope")
        self.assertEqual(code, 1)
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(self.claims.exists())

    def test_o7_unclaimed_draft_gets_no_note(self) -> None:
        """O7: a draft spec without a claim on this machine gets no note."""
        self.spec("gamma", "Gamma Draft", review="draft")
        self.ok()
        self.event("stop", self.repo)
        self.assertNotIn("gamma", self.own_notes())

    def test_o8_approved_claim_shows_stage_3_start_session_b(self) -> None:
        """O8, D17: after approval of a claimed spec, the next run writes Stage 3 "start Session B"."""
        spec = self.spec("gadget", "Gadget Export", review="draft")
        self.ok("--claim", "gadget")
        self.ok()
        self.set_field(spec, "review_status", "approved")
        self.ok()
        note = self.note("gadget")
        self.assertEqual((note["stage"], note["waiting"]), (3, "start Session B"))

    def test_o9_worktree_drops_the_claim_and_keeps_file_and_stage_times(self) -> None:
        """O9, D10: a worktree removes the claim; the note keeps its file and stage_times."""
        spec = self.spec("gadget", "Gadget Export", review="draft")
        self.ok("--claim", "gadget")
        self.ok()
        self.set_field(spec, "review_status", "approved")
        self.ok()
        path = self.own_notes()["gadget"]
        times = read_note(path)["stage_times"]
        wt = self.worktree("gadget")
        self.ok(cwd=wt)
        self.assertTrue(not self.claims.exists() or "gadget" not in self.claims.read_text())
        self.assertEqual(self.own_notes(), {"gadget": path})
        after = read_note(path)["stage_times"]
        for stage, at in times.items():
            self.assertEqual(after.get(stage), at)

    def test_o10_note_of_another_machine_never_changes(self) -> None:
        """O10, D2: a file with another machine_id stays unchanged after any run, also after the ship."""
        wt, _ = self.stage5("gadget", "Gadget Export")
        self.board_dir.mkdir(parents=True)
        foreign = {
            "gadget": status_note(feature="gadget", title="Gadget Export", stage=5),
            **{f"old-{n}": status_note(feature=f"old-{n}", shipped_at=f"2026-09-0{n}T10:00:00Z") for n in range(1, 5)},
        }
        for slug, fields in foreign.items():
            put(self.board_dir / note_name(OTHER["id"], PROJECT, slug), note_text(fields))
        before = snapshot(self.board_dir)
        foreign_names = [note_name(OTHER["id"], PROJECT, slug) for slug in foreign]
        self.ok(cwd=wt)
        self.event("stop", wt)
        self.event("prompt", wt)
        self.set_field(self.notes / "specs" / "20261001 Gadget Export.md", "shipped", "2026-10-15")
        self.ok(cwd=wt)
        self.ok()
        after = snapshot(self.board_dir)
        for name in foreign_names:
            self.assertEqual(after.get(name), before[name], name)

    def test_o11_no_dev_loop_json_writes_nothing(self) -> None:
        """O11, D8: a repo without dev-loop.json: nothing written, exit 0, `no-dev-loop` in skipped."""
        plain = self.root / "plain-repo"
        plain.mkdir()
        git(plain, "init", "-q")
        put(plain / "GLOSSARY.md", "# Glossary\n")
        git(plain, "add", "-A")
        git(plain, "commit", "-q", "-m", "base")
        git(plain, "switch", "-q", "-c", "fix/tiny")
        before = snapshot(self.vault)
        out = self.ok(cwd=plain)
        self.assertTrue(self.skipped(out, "no-dev-loop", None), out)
        self.assertEqual(snapshot(self.vault), before)

    def test_o12_rebase_keeps_that_note_and_updates_the_others(self) -> None:
        """O12, D8: a worktree mid-rebase keeps its note unchanged, listed `rebase`; other features update."""
        wt_a, plan_a = self.stage5("alpha", "Alpha Report")
        wt_b, plan_b = self.stage5("beta", "Beta Import")
        self.ok()
        note_a = self.own_notes()["alpha"]
        before = (note_a.read_bytes(), note_a.stat().st_mtime_ns)
        put(wt_a / "shared.txt", "alpha\n")
        git(wt_a, "add", "shared.txt")
        git(wt_a, "commit", "-q", "-m", "alpha side")
        put(self.repo / "shared.txt", "main\n")
        git(self.repo, "add", "shared.txt")
        git(self.repo, "commit", "-q", "-m", "main side")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "origin")
        rebase = subprocess.run([*GIT, "rebase", "origin/main"], cwd=wt_a, capture_output=True, text=True)
        self.assertNotEqual(rebase.returncode, 0, "the fixture rebase must stop on a conflict")
        self.ledger(wt_a, plan_a, "Task 1: complete")
        self.ledger(wt_b, plan_b, "Task 1: complete")
        out = self.ok()
        self.assertTrue(self.skipped(out, "rebase", "alpha"), out)
        self.assertEqual((note_a.read_bytes(), note_a.stat().st_mtime_ns), before)
        self.assertEqual(self.note("beta")["tasks"][0]["state"], "done")

    def test_o13_notes_inside_the_repo_write_nothing(self) -> None:
        """O13, D8: the real path of the notes folder inside a worktree or the git folder: `notes-in-repo`."""
        self.worktree("tiny", prefix="fix")
        in_tree = self.repo / "docs" / "notes"
        in_git = self.repo / ".git" / "vault-notes"
        link = self.root / "notes-link"
        cases = {"worktree": in_tree, "git folder": in_git, "link to worktree": link}
        in_tree.mkdir(parents=True)
        in_git.mkdir(parents=True)
        link.symlink_to(in_tree)
        if sys.platform == "darwin":
            upper = self.repo.parent / self.repo.name.upper() / "docs" / "notes"
            if upper.is_dir():
                cases["other case"] = upper
        for name, notes in cases.items():
            with self.subTest(name):
                env = {**self.env, "DEV_LOOP_NOTES": str(notes)}
                out = self.ok(env=env)
                self.assertTrue(self.skipped(out, "notes-in-repo", None), out)
                for real in (in_tree, in_git):
                    self.assertEqual(sorted(p.name for p in real.iterdir()), [])

    def test_o14_busy_lock_writes_nothing_and_returns_at_once(self) -> None:
        """O14, D8: a busy board.lock: nothing written, `busy`, exit 0 at once."""
        self.worktree("tiny", prefix="fix")
        lock = self.repo / ".git" / "dev-loop" / "board.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                pass
            before = snapshot(self.vault)
            start = time.monotonic()
            code, out = self.board(timeout=30)
            elapsed = time.monotonic() - start
        self.assertEqual(code, 0, out)
        self.assertTrue(self.skipped(out, "busy", None), out)
        self.assertEqual(snapshot(self.vault), before)
        self.assertLess(elapsed, 10)

    def test_o15_board_folder_link_is_left_unchanged(self) -> None:
        """O15, D7: a `dev-loop-board` folder that is a link: link and target unchanged, `link` listed."""
        self.worktree("tiny", prefix="fix")
        target = self.root / "elsewhere-board"
        target.mkdir()
        self.board_dir.symlink_to(target)
        out = self.ok()
        self.assertTrue(self.board_dir.is_symlink())
        self.assertEqual(os.readlink(self.board_dir), str(target))
        self.assertEqual(list(target.iterdir()), [])
        self.assertTrue(self.skipped(out, "link"), out)

    def test_o15_note_link_is_left_unchanged(self) -> None:
        """O15, D7: an own note path that is a link: link and target unchanged, `link` listed."""
        wt = self.worktree("tiny", prefix="fix")
        self.ok()
        path = self.own_notes()["tiny"]
        path.unlink()
        target = self.root / "outside.md"
        target.write_text("outside\n")
        path.symlink_to(target)
        out = self.event("stop", wt)
        self.assertTrue(path.is_symlink())
        self.assertEqual(os.readlink(path), str(target))
        self.assertEqual(target.read_text(), "outside\n")
        self.assertTrue(self.skipped(out, "link"), out)

    def test_o16_link_above_the_board_folder_works(self) -> None:
        """O16, D7: a notes path through a link above `dev-loop-board` works."""
        self.worktree("tiny", prefix="fix")
        link = self.root / "vault-link"
        link.symlink_to(self.vault)
        env = {**self.env, "DEV_LOOP_NOTES": str(link / "Projects" / "Acme Widgets")}
        out = self.ok(env=env)
        self.assertFalse(self.skipped(out, "link"), out)
        self.assertIn("tiny", self.own_notes())

    def test_o17_every_frontmatter_value_is_json(self) -> None:
        """O17: each frontmatter value of a status note parses as JSON, and the schema 1 keys are there."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        path = self.own_notes()["gadget"]
        lines = path.read_text().splitlines()
        end = lines.index("---", 1)
        for line in lines[1:end]:
            key, _, value = line.partition(":")
            with self.subTest(key):
                json.loads(value.strip())
        note = read_note(path)
        keys = {"type", "schema", "project", "feature", "title", "description", "machine_id", "owner", "machine",
                "track", "stage", "stage_times", "tasks", "waiting", "decisions", "assumptions_baseline",
                "shipped_at", "updated"}
        self.assertLessEqual(keys, set(note))
        self.assertEqual((note["feature"], note["title"], note["track"], note["stage"]), ("gadget", "Gadget Export", "M", 5))
        self.assertEqual((note["owner"], note["machine"]), (MACHINE["owner"], MACHINE["machine"]))
        self.assertEqual(note["description"], "Gadget Export for the widget shop.")
        self.assertRegex(note["updated"], ISO_Z)

    def test_o18_second_run_keeps_stage_times(self) -> None:
        """O18, D13: a second run keeps every stage_times value of the first."""
        wt, _ = self.stage5("gadget", "Gadget Export")
        self.ok(cwd=wt)
        first = self.note("gadget")["stage_times"]
        self.assertIn("5", first)
        time.sleep(1.1)
        self.ok(cwd=wt)
        second = self.note("gadget")["stage_times"]
        for stage, at in first.items():
            self.assertEqual(second[stage], at)

    def test_o19_task_done_and_next_started_share_one_time(self) -> None:
        """O19, D14: a new `Task N: complete` sets done on N and started on N+1 to the same time."""
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.ok(cwd=wt)
        note = self.note("gadget")
        self.assertEqual([t["state"] for t in note["tasks"]], ["doing", "todo", "todo"])
        self.assertEqual(note["tasks"][0]["started"], note["stage_times"]["5"])
        time.sleep(1.1)
        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        tasks = self.note("gadget")["tasks"]
        self.assertEqual([t["n"] for t in tasks], [1, 2, 3])
        self.assertEqual([t["state"] for t in tasks], ["done", "doing", "todo"])
        self.assertRegex(tasks[0]["done"], ISO_Z)
        self.assertEqual(tasks[1]["started"], tasks[0]["done"])

    def test_o23_decisions_are_assumptions_added_after_stage_3(self) -> None:
        """O23, D18: decisions lists only the Assumptions items added after the first run at Stage 3 or later."""
        spec = self.spec("gadget", "Gadget Export", assumptions=("Widgets are blue.", "Exports are CSV."))
        wt = self.worktree("gadget")
        self.ok(cwd=wt)
        note = self.note("gadget")
        self.assertEqual(note["decisions"], [])
        self.assertEqual(len(note["assumptions_baseline"]), 2)
        spec.write_text(spec.read_text() + "- Retries use a queue.\n")
        self.ok(cwd=wt)
        note = self.note("gadget")
        self.assertEqual(len(note["decisions"]), 1)
        self.assertIn("Retries use a queue.", note["decisions"][0])
        self.assertEqual(len(note["assumptions_baseline"]), 2)

    def test_o27_note_of_a_feature_that_left_the_write_set_is_deleted(self) -> None:
        """O27, D11: an own note of a feature that left the write set and is not shipped is deleted."""
        self.spec("gadget", "Gadget Export")
        wt = self.worktree("gadget")
        self.ok(cwd=wt)
        path = self.own_notes()["gadget"]
        git(self.repo, "worktree", "remove", "--force", str(wt))
        out = self.ok()
        self.assertFalse(path.exists())
        self.assertIn(str(path), [str(Path(p).resolve()) for p in out["removed"]])

    def test_o28_writes_only_in_its_own_board_folder(self) -> None:
        """O28, D1: no write outside <notes>/dev-loop-board/ of its own project."""
        other = self.vault / "Projects" / "Other Thing" / "dev-loop-board"
        put(other / "zephyr.md", note_text(status_note(project="other-thing", feature="zephyr")))
        wt, plan = self.stage5("gadget", "Gadget Export")
        self.spec("beta", "Beta Import", review="draft")
        self.worktree("tiny", prefix="fix")
        before = {k: v for k, v in snapshot(self.root).items() if not k.startswith("remotes")}
        self.ok("--claim", "beta")
        self.ok()
        self.event("stop", wt)
        self.ledger(wt, plan, "Task 1: complete")
        self.event("prompt", wt)
        self.event("stop", self.repo)
        after = {k: v for k, v in snapshot(self.root).items() if not k.startswith("remotes")}
        own = str(self.board_dir.relative_to(self.root)) + os.sep
        changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        changed.discard(str((wt / ".superpowers" / "sdd" / plan.stem / "progress.md").relative_to(self.root)))
        self.assertTrue(changed)
        self.assertEqual({k for k in changed if not k.startswith(own)}, set())

    def test_o29_no_temporary_file_stays(self) -> None:
        """O29, D7: no temp file of the run stays; own temp files older than 1 hour are deleted."""
        self.worktree("tiny", prefix="fix")
        self.board_dir.mkdir(parents=True)
        old = self.board_dir / f".{note_name(MACHINE['id'], PROJECT, 'tiny')}.4242.tmp"
        old.write_text("partial\n")
        stamp = time.time() - 7200
        os.utime(old, (stamp, stamp))
        self.ok()
        self.assertEqual([p.name for p in self.board_dir.iterdir() if p.name.endswith(".tmp")], [])

    def test_o30_board_note_written_when_missing_or_older_only(self) -> None:
        """O30, D24: Dev Loop Board.md is written when missing or older, not when same or newer."""
        self.worktree("tiny", prefix="fix")
        self.ok()
        path = self.board_dir / BOARD_NOTE
        self.assertTrue(path.is_file())
        self.assertEqual(frontmatter_version(path), VERSION)
        path.write_text('---\nversion: "0.0.1"\n---\n\nold body marker\n')
        self.ok()
        self.assertEqual(frontmatter_version(path), VERSION)
        self.assertNotIn("old body marker", path.read_text())
        for version in (VERSION, "99.0.0"):
            with self.subTest(version):
                path.write_text(f'---\nversion: "{version}"\n---\n\nkept body marker\n')
                before = (path.read_bytes(), path.stat().st_mtime_ns)
                self.ok()
                self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_o31_board_note_holds_no_status_data(self) -> None:
        """O31, D24: Dev Loop Board.md holds code only: a dataviewjs block with renderBoard, no status data."""
        wt, _ = self.stage5("gadget", "Gadget Export")
        self.event("stop", wt)
        text = (self.board_dir / BOARD_NOTE).read_text()
        self.assertIn("```dataviewjs", text)
        self.assertIn("renderBoard", text)
        for value in ("gadget", "Gadget Export", MACHINE["id"], MACHINE["machine"], MACHINE["owner"], "reply in the session"):
            self.assertNotIn(value, text)


class BoardEventTests(Board):
    def test_o20_stop_sets_reply_in_the_session_on_its_feature_only(self) -> None:
        """O20, D16, D17: --event stop in a Stage 5 worktree sets waiting on that feature only."""
        wt_a, _ = self.stage5("alpha", "Alpha Report")
        self.stage5("beta", "Beta Import")
        self.event("stop", wt_a)
        self.assertEqual(self.note("alpha")["waiting"], "reply in the session")
        self.assertEqual(self.note("beta")["waiting"], "")

    def test_o21_stop_at_stage_7_sets_review_the_pr(self) -> None:
        """O21, D17: --event stop at Stage 7 sets waiting "review the PR"."""
        wt = self.stage7("gadget", "Gadget Export")
        self.event("stop", wt)
        note = self.note("gadget")
        self.assertEqual((note["stage"], note["waiting"]), (7, "review the PR"))

    def test_o22_prompt_clears_waiting_on_its_session_features_only(self) -> None:
        """O22, D16, D17: --event prompt clears waiting on the session's features only."""
        wt_a, _ = self.stage5("alpha", "Alpha Report")
        wt_b, _ = self.stage5("beta", "Beta Import")
        self.event("stop", wt_a)
        self.event("stop", wt_b)
        self.event("prompt", wt_a)
        self.assertEqual(self.note("alpha")["waiting"], "")
        self.assertEqual(self.note("beta")["waiting"], "reply in the session")

    def test_d17_session_start_clears_and_subagent_stop_keeps(self) -> None:
        """D17 (with O22): session-start clears waiting, subagent-stop does not change it."""
        wt, _ = self.stage5("alpha", "Alpha Report")
        self.event("stop", wt)
        self.event("subagent-stop", wt)
        self.assertEqual(self.note("alpha")["waiting"], "reply in the session")
        self.event("session-start", wt)
        self.assertEqual(self.note("alpha")["waiting"], "")
        self.event("subagent-stop", wt)
        self.assertEqual(self.note("alpha")["waiting"], "")

    def test_f1_session_a_claim_stop_approve_stop(self) -> None:
        """F1: claim, stop: Stage 2 "approve the spec"; approval, stop: Stage 3 "start Session B"."""
        spec = self.spec("gadget", "Gadget Export", review="draft")
        self.ok("--claim", "gadget")
        self.event("stop", self.repo)
        note = self.note("gadget")
        self.assertEqual((note["stage"], note["waiting"]), (2, "approve the spec"))
        self.set_field(spec, "review_status", "approved")
        self.event("stop", self.repo)
        note = self.note("gadget")
        self.assertEqual((note["stage"], note["waiting"]), (3, "start Session B"))

    def test_f2_session_b_continues_the_claimed_note(self) -> None:
        """F2, O9, O19: the worktree note continues the Stage 2 time; tasks 1 and 2 done, task 3 running."""
        spec = self.spec("gadget", "Gadget Export", review="draft")
        self.ok("--claim", "gadget")
        self.ok()
        path = self.own_notes()["gadget"]
        spec_time = read_note(path)["stage_times"]["2"]
        self.set_field(spec, "review_status", "approved")
        self.ok()
        wt = self.worktree("gadget")
        self.acceptance(wt, "gadget")
        plan = self.plan(spec, "Gadget Export", 3)
        self.ok(cwd=wt)
        time.sleep(1.1)
        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        time.sleep(1.1)
        self.ledger(wt, plan, "Task 1: complete", "Task 2: complete")
        self.ok(cwd=wt)
        self.assertEqual(self.own_notes(), {"gadget": path})
        note = read_note(path)
        self.assertEqual(note["stage"], 5)
        self.assertEqual(note["stage_times"]["2"], spec_time)
        tasks = note["tasks"]
        self.assertEqual([t["state"] for t in tasks], ["done", "done", "doing"])
        self.assertTrue(tasks[0]["done"] and tasks[1]["done"])
        self.assertEqual(tasks[2]["started"], tasks[1]["done"])

    def test_f3_another_machine_note_is_left_alone(self) -> None:
        """F3, O10: machine A (claimed Stage 3) never changes the Stage 5 note of machine B."""
        spec = self.spec("gadget", "Gadget Export", review="draft")
        self.ok("--claim", "gadget")
        self.set_field(spec, "review_status", "approved")
        self.ok()
        clone = self.root / "bench" / PROJECT
        clone.parent.mkdir()
        git(self.root, "clone", "-q", str(self.origin), str(clone))
        home_b = self.root / "home-b"
        env_b = self.make_env(home_b, self.notes)
        self.write_machine(home_b, OTHER)
        wt_b = self.worktree("gadget", repo=clone)
        self.acceptance(wt_b, "gadget")
        self.plan(spec, "Gadget Export", 3)
        self.ok(cwd=wt_b, env=env_b)
        self.assertEqual(self.note("gadget", OTHER["id"])["stage"], 5)
        path_b = self.own_notes(OTHER["id"])["gadget"]
        before = (path_b.read_bytes(), path_b.stat().st_mtime_ns)
        self.event("stop", self.repo)
        self.ok()
        self.assertEqual((path_b.read_bytes(), path_b.stat().st_mtime_ns), before)
        self.assertEqual(self.note("gadget")["stage"], 3)


class BoardShipTests(Board):
    def ship(self, title: str, day: str = "2026-10-15") -> None:
        self.set_field(self.notes / "specs" / f"20261001 {title}.md", "shipped", day)

    def test_o24_ship_sets_shipped_at_once(self) -> None:
        """O24, D12: the next run after the ship sets shipped_at; later runs leave the note unchanged."""
        wt, _ = self.stage5("gadget", "Gadget Export")
        self.ok(cwd=wt)
        path = self.own_notes()["gadget"]
        self.ship("Gadget Export")
        self.ok(cwd=wt)
        self.assertRegex(read_note(path)["shipped_at"], ISO_Z)
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        time.sleep(1.1)
        self.ok(cwd=wt)
        self.event("stop", wt)
        self.event("prompt", wt)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_o25_no_new_note_for_a_shipped_feature(self) -> None:
        """O25, D12: a run never creates a note for a feature that is already shipped."""
        self.spec("gadget", "Gadget Export", shipped="2026-10-10")
        wt = self.worktree("gadget")
        self.ok(cwd=wt)
        self.event("stop", wt)
        self.assertNotIn("gadget", self.own_notes())

    def test_o26_four_shipped_notes_keep_the_three_latest(self) -> None:
        """O26, D12: with 4 own shipped notes in one project, the next run keeps the 3 latest shipped_at."""
        slugs = ["one", "two", "three", "four"]
        for slug in slugs:
            self.spec(slug, f"Feature {slug.title()}")
            self.worktree(slug)
        self.ok()
        self.assertEqual(set(self.own_notes()), set(slugs))
        for slug in slugs:
            time.sleep(1.1)
            self.ship(f"Feature {slug.title()}")
            self.ok()
        self.ok()
        own = self.own_notes()
        self.assertEqual(set(own), {"two", "three", "four"})
        for path in own.values():
            self.assertTrue(read_note(path)["shipped_at"])

    def test_o61_shipped_note_is_never_rewritten(self) -> None:
        """O61, D12: a run does not rewrite an existing own shipped note."""
        self.worktree("tiny", prefix="fix")
        self.board_dir.mkdir(parents=True)
        path = self.board_dir / note_name(MACHINE["id"], PROJECT, "old-thing")
        put(path, note_text(status_note(feature="old-thing", machine_id=MACHINE["id"], owner="Robin", machine="lab-mac",
                                        stage=7, shipped_at="2026-10-01T10:00:00Z")))
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        time.sleep(0.05)
        self.ok()
        self.event("stop", self.repo)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_o62_folder_with_four_shipped_notes_keeps_the_three_latest(self) -> None:
        """O62, D12: 4 own shipped notes: the next run deletes the oldest shipped_at; a foreign one stays."""
        self.worktree("tiny", prefix="fix")
        self.board_dir.mkdir(parents=True)
        shipped = {"north": "2026-10-03", "south": "2026-10-01", "east": "2026-10-04", "west": "2026-10-02"}
        for slug, day in shipped.items():
            put(self.board_dir / note_name(MACHINE["id"], PROJECT, slug),
                note_text(status_note(feature=slug, machine_id=MACHINE["id"], owner="Robin", machine="lab-mac",
                                      stage=7, shipped_at=f"{day}T10:00:00Z")))
        foreign = self.board_dir / note_name(OTHER["id"], PROJECT, "ancient")
        put(foreign, note_text(status_note(feature="ancient", shipped_at="2026-09-01T10:00:00Z")))
        foreign_before = (foreign.read_bytes(), foreign.stat().st_mtime_ns)
        out = self.ok()
        self.assertEqual(set(self.own_notes()), {"north", "east", "west", "tiny"})
        south = self.board_dir / note_name(MACHINE["id"], PROJECT, "south")
        self.assertIn(str(south), [str(Path(p).resolve()) for p in out["removed"]])
        self.assertEqual((foreign.read_bytes(), foreign.stat().st_mtime_ns), foreign_before)


class BoardServerTests(Board):
    """Seam 2: the server that `board` starts. Every test stops it again."""

    def setUp(self) -> None:
        super().setUp()
        (self.vault / ".obsidian").mkdir()
        self.board_dir.mkdir(parents=True)
        self.worktree("tiny", prefix="fix")
        self.pids: list[int] = []
        self.addCleanup(self.stop_server)

    def stop_server(self) -> None:
        try:
            self.board("--stop-server", timeout=30)
        except (AssertionError, subprocess.TimeoutExpired):
            pass
        self.kill_stray_server()
        for pid in self.pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def serve(self, cwd: Path | None = None, env: dict | None = None) -> dict:
        out = self.ok(cwd=cwd, env=env, serve=True, timeout=90)
        found = self.wait_health()
        if found and isinstance(found.get("pid"), int):
            self.pids.append(found["pid"])
        return out

    def wait_health(self, seconds: float = 5) -> dict | None:
        deadline = time.monotonic() + seconds
        while True:
            found = health(self.port)
            if found or time.monotonic() > deadline:
                return found
            time.sleep(0.1)

    def set_version(self, version: str, repo: Path | None = None) -> None:
        put((repo or self.repo) / ".dev-loop" / "MANIFEST.json", json.dumps({"version": version, "files": {}}) + "\n")

    def api(self) -> dict:
        status, headers, body = request(self.port, "/api/status")
        self.assertEqual(status, 200)
        return json.loads(body)

    def listener(self) -> socket.socket:
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", self.port))
        sock.listen(8)
        self.addCleanup(sock.close)
        return sock

    def test_o34_server_failure_does_not_fail_board(self) -> None:
        """O34, D20: the port held by something else: exit 0, server.state error, port-busy or stalled."""

        class NotFound(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(404)
                self.end_headers()

            def log_message(self, *args) -> None:
                pass

        with self.subTest("another http server"):
            other = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), NotFound)
            thread = threading.Thread(target=other.serve_forever, daemon=True)
            thread.start()
            try:
                code, out = self.board(serve=True, timeout=90)
            finally:
                other.shutdown()
                other.server_close()
            self.assertEqual(code, 0, out)
            self.assertIn(out["server"]["state"], {"error", "port-busy", "stalled"})
            self.assertTrue(out["server"].get("message"))
        with self.subTest("a socket that never answers"):
            sock = self.listener()
            code, out = self.board(serve=True, timeout=90)
            sock.close()
            self.assertEqual(code, 0, out)
            self.assertIn(out["server"]["state"], {"error", "port-busy", "stalled"})

    def test_o35_no_serve_contacts_no_server(self) -> None:
        """O35: --no-serve neither contacts nor starts a server: state `disabled`."""
        sock = self.listener()
        out = self.ok()
        self.assertEqual(out["server"]["state"], "disabled")
        sock.setblocking(False)
        with self.assertRaises(BlockingIOError):
            sock.accept()

    def test_o36_health_answers_with_the_manifest_version(self) -> None:
        """O36, D19, D20: after `board`, GET /health answers name, the MANIFEST version, roots and pid."""
        out = self.serve()
        self.assertIn(out["server"]["state"], {"started", "running"})
        self.assertEqual(out["server"]["url"], f"http://127.0.0.1:{self.port}/")
        found = health(self.port)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual((found["name"], found["version"]), ("dev-loop board", VERSION))
        self.assertIsInstance(found["pid"], int)
        self.assertIn(self.vault, [Path(r).resolve() for r in found["roots"]])

    def test_o37_f8_older_server_is_restarted(self) -> None:
        """O37, F8: a running server with an older version is replaced: `restarted`."""
        self.set_version("2.0.9")
        self.serve()
        old = health(self.port)
        assert old is not None
        self.assertEqual(old["version"], "2.0.9")
        self.set_version(VERSION)
        out = self.serve()
        self.assertEqual(out["server"]["state"], "restarted")
        new = health(self.port)
        assert new is not None
        self.assertEqual(new["version"], VERSION)
        self.assertNotEqual(new["pid"], old["pid"])

    def test_o38_newer_server_keeps_running(self) -> None:
        """O38, D20: a running server with a newer version and the root keeps running: `running`."""
        self.serve()
        old = health(self.port)
        assert old is not None
        self.set_version("2.0.9")
        out = self.serve()
        self.assertEqual(out["server"]["state"], "running")
        now = health(self.port)
        assert now is not None
        self.assertEqual((now["version"], now["pid"]), (VERSION, old["pid"]))

    def test_o39_server_without_this_root_is_replaced_with_both(self) -> None:
        """O39, D20: a running server without the root of this project is replaced by one with both roots."""
        self.serve()
        old = health(self.port)
        assert old is not None
        notes_two = self.root / "vault-two" / "Zephyr"
        (notes_two / "specs").mkdir(parents=True)
        origin_two = self.root / "remotes" / "zephyr-app.git"
        git(self.root, "init", "-q", "--bare", str(origin_two))
        repo_two = self.root / "zephyr-app"
        self.make_repo(repo_two, origin_two)
        env_two = {**self.env, "DEV_LOOP_NOTES": str(notes_two)}
        self.worktree("tiny", prefix="fix", repo=repo_two)
        out = self.serve(cwd=repo_two, env=env_two)
        self.assertIn(out["server"]["state"], {"restarted", "started"})
        now = health(self.port)
        assert now is not None
        roots = [Path(r).resolve() for r in now["roots"]]
        self.assertIn(self.vault, roots)
        self.assertIn(notes_two, roots)
        self.assertNotEqual(now["pid"], old["pid"])

    def test_o40_stop_server_stops_it_and_writes_nothing(self) -> None:
        """O40: --stop-server stops the server; /health does not answer after it; nothing is written."""
        self.serve()
        self.assertIsNotNone(health(self.port))
        before = snapshot(self.vault)
        code, _ = self.board("--stop-server", timeout=30)
        self.assertEqual(code, 0)
        deadline = time.monotonic() + 5
        while health(self.port, timeout=1) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertIsNone(health(self.port, timeout=1))
        self.assertEqual(snapshot(self.vault), before)

    def test_o41_o44_o45_o46_http_surface(self) -> None:
        """O41 loopback only, O44 Host allowlist, O45 three paths only, O46 no CORS header."""
        put(self.vault / "dev-loop-board" / "x.md", "---\ntype: note\n---\nSECRET-MARKER-45\n")
        self.serve()
        status, headers, body = request(self.port, "/")
        self.assertEqual(status, 200)
        self.assertTrue(headers.get("content-type", "").startswith("text/html"))
        self.assertIn(b"renderBoard", body)
        with self.subTest("O41"):
            with self.assertRaises(OSError):
                socket.create_connection(("::1", self.port), timeout=2).close()
            lan = None
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                    probe.connect(("192.0.2.1", 9))
                    lan = probe.getsockname()[0]
            except OSError:
                pass
            if lan and not lan.startswith("127."):
                with self.assertRaises(OSError):
                    socket.create_connection((lan, self.port), timeout=2).close()
        with self.subTest("O44"):
            self.assertEqual(request(self.port, "/health", host=f"evil.example:{self.port}")[0], 403)
            self.assertEqual(request(self.port, "/api/status", host=f"evil.example:{self.port}")[0], 403)
            self.assertEqual(request(self.port, "/health", host=f"localhost:{self.port}")[0], 200)
        with self.subTest("O45"):
            for path in ("/../", "/dev-loop-board/x.md", "/api/status/../../dev-loop-board/x.md", "/index.html",
                         "/health/x", "/%2e%2e/dev-loop-board/x.md"):
                status, _, body = request(self.port, path)
                self.assertEqual(status, 404, path)
                self.assertNotIn(b"SECRET-MARKER-45", body, path)
        with self.subTest("O46"):
            for path, host in (("/", None), ("/api/status", None), ("/health", None), ("/nope", None),
                               ("/health", f"evil.example:{self.port}")):
                _, headers, _ = request(self.port, path, host=host, headers={"Origin": "http://evil.example"})
                self.assertNotIn("access-control-allow-origin", headers, path)

    def test_o42_o43_f6_f7_api_status_lists_only_status_notes_under_roots(self) -> None:
        """O42, O43, F6, F7: notes in every dev-loop-board under the roots; links, dot names, schema 2 and
        folders outside the roots are left out; schema 2 counts in newer_schema."""
        other = self.vault / "Projects" / "Other Thing" / "dev-loop-board"
        put(other / "zephyr.md", note_text(status_note(project="other-thing", feature="zephyr-sync")))
        put(other / "plain.md", "---\ntype: \"meeting\"\nfeature: \"meeting-note\"\n---\nnotes\n")
        put(other / "data.json", json.dumps(status_note(feature="json-file")))
        put(other / ".hidden.md", note_text(status_note(feature="dot-file")))
        put(other / "future.md", note_text(status_note(feature="future-thing", schema=2)))
        outside = self.root / "elsewhere"
        put(outside / "real.md", note_text(status_note(feature="via-link")))
        (other / "linked.md").symlink_to(outside / "real.md")
        put(self.vault / ".trash" / "dev-loop-board" / "trashed.md", note_text(status_note(feature="in-trash")))
        put(self.vault / "Projects" / ".archive" / "dev-loop-board" / "x.md", note_text(status_note(feature="dot-folder")))
        put(outside / "linked-project" / "dev-loop-board" / "y.md", note_text(status_note(feature="linked-folder")))
        (self.vault / "Linked").symlink_to(outside / "linked-project")
        put(outside / "Hidden" / "dev-loop-board" / "h.md", note_text(status_note(feature="secret-plan")))
        self.serve()
        api = self.api()
        features = {f.get("feature") for f in api["features"]}
        self.assertLessEqual({"tiny", "zephyr-sync"}, features)
        for left_out in ("meeting-note", "json-file", "dot-file", "future-thing", "via-link", "in-trash",
                         "dot-folder", "linked-folder", "secret-plan"):
            self.assertNotIn(left_out, features)
        self.assertEqual(api["newer_schema"], 1)
        self.assertEqual((api["here"], api["me"], api["version"]), (MACHINE["id"], MACHINE["owner"], VERSION))
        for feature in api["features"]:
            self.assertEqual((feature["type"], feature["schema"]), ("dev-loop-status", 1))

    def test_o47_at_most_500_notes(self) -> None:
        """O47, D21: the server reads at most 500 notes."""
        bulk = self.vault / "Projects" / "Bulk" / "dev-loop-board"
        for n in range(510):
            put(bulk / f"n{n:03}.md", note_text(status_note(project="bulk", feature=f"bulk-{n:03}")))
        self.serve()
        self.assertLessEqual(len(self.api()["features"]), 500)

    def test_o47_at_most_64_kb_of_a_note(self) -> None:
        """O47, D21: the server reads at most the first 64 KB of a note."""
        folder = self.vault / "Projects" / "Big" / "dev-loop-board"
        # The title line comes after a 70 KB description, so it sits past the first 64 KB.
        fields = status_note(project="big", feature="big-note", description="x" * 70000)
        fields.pop("title")
        put(folder / "big.md", note_text({**fields, "title": "Far Title"}))
        put(folder / "small.md", note_text(status_note(project="big", feature="small-one")))
        self.serve()
        status, _, body = request(self.port, "/api/status")
        self.assertEqual(status, 200)
        self.assertIn(b"small-one", body)
        self.assertNotIn(b"Far Title", body)


FAKE_DEVLOOP = """#!/usr/bin/env python3
import json, os, sys
data = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
with open(os.environ["BOARD_TEST_LOG"], "a") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "stdin": data}) + "\\n")
print(json.dumps({"noise": "board ran"}))
print("board stderr noise", file=sys.stderr)
sys.exit(int(os.environ.get("BOARD_TEST_EXIT", "1")))
"""


class BoardHookTests(unittest.TestCase):
    """O32, O33, F5. The hook scripts run from a kit layout copy in a temp repo; a fake devloop.py at
    `.agents/skills/dev-loop/scripts/devloop.py` logs each call and fails."""

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

    def test_o32_f5_blocked_stop_runs_no_board(self) -> None:
        """O32, F5: gate.sh that blocks a stop does not run `board`, so waiting does not change. The same
        stop with a passing gate runs it: the control that the fake devloop.py is reachable."""
        for harness in ("claude", "codex"):
            with self.subTest(harness):
                self.gate(passes=False)
                self.log.unlink(missing_ok=True)
                result = self.hook(harness, "gate.sh", "Stop")
                self.assertEqual(json.loads(result.stdout)["decision"], "block")
                self.assertEqual([c for c in self.calls(wait=1.5) if "board" in c["argv"]], [])
                self.gate(passes=True)
                self.hook(harness, "gate.sh", "Stop")
                self.assert_board_event(self.calls(wait=6), "stop")

    def test_o33_gate_runs_board_quietly_when_it_does_not_block(self) -> None:
        """O33, D15: a passing gate.sh runs `board --event stop|subagent-stop`; stdout empty, exit 0, also
        when board fails."""
        self.gate(passes=True)
        for harness in ("claude", "codex"):
            for event, name in (("Stop", "stop"), ("SubagentStop", "subagent-stop")):
                with self.subTest(harness=harness, event=event):
                    self.log.unlink(missing_ok=True)
                    result = self.hook(harness, "gate.sh", event)
                    self.assertEqual((result.returncode, result.stdout), (0, ""))
                    self.assert_board_event(self.calls(wait=6), name)

    def test_o33_board_sh_runs_board_quietly(self) -> None:
        """O33, D15: board.sh runs `board --event session-start` and `--event prompt`; stdout empty, exit 0,
        also when board fails."""
        for harness in ("claude", "codex"):
            for event, name in (("SessionStart", "session-start"), ("UserPromptSubmit", "prompt")):
                with self.subTest(harness=harness, event=event):
                    self.log.unlink(missing_ok=True)
                    result = self.hook(harness, "board.sh", event, name)
                    self.assertEqual((result.returncode, result.stdout), (0, ""))
                    self.assert_board_event(self.calls(wait=6), name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
