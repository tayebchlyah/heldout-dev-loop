#!/usr/bin/env python3
"""Tests for retro.py: the weekly review due rule and the session list."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
import retro  # noqa: E402

DEVLOOP = HERE.parents[2] / "skill" / "scripts" / "devloop.py"
TODAY = date(2026, 10, 26)
HEADER = "| Date | Item | Verdict | Check | Proof |\n| --- | --- | --- | --- | --- |\n"


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class TempDir(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.notes = self.tmp / "notes"
        (self.notes / "specs").mkdir(parents=True)

    def spec(self, slug: str, shipped: str = "") -> None:
        (self.notes / "specs" / f"20261001 {slug}.md").write_text(
            f"---\nfeature: {slug}\ntrack: M\nreview_status: approved\nshipped: {shipped}\n---\n\n# {slug}\n")


def days_ago(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


class ReviewDueTests(TempDir):
    def test_due_rule(self) -> None:
        log = self.notes / "Gardener Log.md"
        due = {"last": days_ago(7), "days": 7, "log": str(log)}
        nulls = {"last": None, "days": None, "log": str(log)}
        rows = [
            ("7 days", HEADER + f"| {days_ago(7)} | 3 items | run | | |\n", due),
            ("6 days", HEADER + f"| {days_ago(6)} | 3 items | run | | |\n", None),
            ("tomorrow", HEADER + f"| {days_ago(-1)} | 3 items | run | | |\n", None),
            ("encode later", HEADER + f"| {days_ago(8)} | 1 item | run | | |\n| {TODAY} | x | encode | t | red at a |\n",
             {"last": days_ago(8), "days": 8, "log": str(log)}),
            ("columns moved", "| Verdict | Date | Item | Check | Proof |\n|---|---|---|---|---|\n"
             f"| run | {days_ago(7)} | 3 items | | |\n", due),
            ("escaped pipe", HEADER + f"| {days_ago(7)} | a \\| b | run | | |\n", due),
            ("bad dates", HEADER + "| 2026-13-40 | x | run | | |\n| yesterday | x | run | | |\n", nulls),
            ("header only", HEADER, nulls),
            ("newest of two", HEADER + f"| {days_ago(20)} | x | run | | |\n| {days_ago(3)} | x | run | | |\n", None),
        ]
        for name, text, want in rows:
            with self.subTest(name):
                log.write_text("# Gardener Log\n\n" + text + "\nafter\n")
                self.assertEqual(retro.review_due(self.tmp, self.notes, TODAY), want)
        with self.subTest("not utf-8"):
            log.write_bytes(b"\xff\xfe")
            self.assertEqual(retro.review_due(self.tmp, self.notes, TODAY), nulls)

    def test_absent_log(self) -> None:
        self.spec("open")
        self.assertIsNone(retro.review_due(self.tmp, self.notes, TODAY))
        self.spec("done", shipped="2026-10-01")
        self.assertEqual(retro.review_due(self.tmp, self.notes, TODAY),
                         {"last": None, "days": None, "log": str(self.notes / "Gardener Log.md")})


class ReviewDueStatusTests(TempDir):
    def status(self, *args: str) -> dict:
        env = {**os.environ, "DEV_LOOP_NOTES": str(self.notes)}
        out = subprocess.run([sys.executable, str(DEVLOOP), "status", "--repo", str(self.repo), *args],
                             capture_output=True, text=True, env=env, check=True)
        return json.loads(out.stdout)

    def test_status_field(self) -> None:
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "GLOSSARY.md").write_text("# Glossary\n")
        git(self.repo, "add", ".")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        self.spec("demo")
        quiet = self.status()
        self.assertNotIn("review_due", quiet)
        old = (date.today() - timedelta(days=9)).isoformat()
        (self.notes / "Gardener Log.md").write_text(HEADER + f"| {old} | x | run | | |\n")
        due = self.status()
        self.assertEqual(due["review_due"]["last"], old)
        self.assertEqual(due["next"], quiet["next"])
        self.assertNotIn("review_due", self.status("--feature", "demo"))


NOW = datetime.now(timezone.utc).replace(microsecond=0)


def stamp(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def jsonl(path: Path, records: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in records))
    return path


def claude(text: object, hours_ago: float, **extra) -> dict:
    return {"type": "user", "timestamp": stamp(hours_ago), "message": {"content": text}, **extra}


def meta(sid: str, cwd: Path | str, source: object, hours_ago: float) -> dict:
    return {"timestamp": stamp(hours_ago), "type": "session_meta", "payload": {"id": sid, "cwd": str(cwd), "source": source}}


def codex_user(text: str, hours_ago: float) -> dict:
    return {"timestamp": stamp(hours_ago), "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}


class SessionsTests(TempDir):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "GLOSSARY.md").write_text("# Glossary\n")
        git(self.repo, "add", ".")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        self.wt = self.tmp / "repo-demo"
        git(self.repo, "worktree", "add", "-q", "-b", "feat/demo", str(self.wt))
        self.claude_root = self.tmp / "claude" / "projects"
        self.codex = self.tmp / "codex" / "sessions" / "2026" / "10" / "20"
        self.env = {"CLAUDE_CONFIG_DIR": str(self.tmp / "claude"), "CODEX_HOME": str(self.tmp / "codex")}

    def folder(self, worktree: Path) -> Path:
        return self.claude_root / re.sub(r"[^A-Za-z0-9]", "-", str(worktree))

    def listed(self) -> list[dict]:
        return retro.sessions(self.repo, 7, self.env, self.tmp / "home", NOW)

    def test_claude_sessions(self) -> None:
        main = jsonl(self.folder(self.repo) / "s-main.jsonl", [
            "not json",
            claude("<command-name>/x</command-name>", 50),
            claude("hello", 49, isMeta=True),
            claude([{"type": "text", "text": "x" * 300}], 48),
        ])
        sub = jsonl(self.folder(self.repo) / "s-main" / "subagents" / "agent-1.jsonl", [claude("sub", 48)])
        jsonl(self.folder(self.repo) / "s-old.jsonl", [claude("old", 240)])
        jsonl(self.folder(self.repo) / "s-stub.jsonl", [{"type": "summary"}])
        state = self.repo / ".git" / "dev-loop" / "board-state.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"features": {"demo": {"stage_times": {"3": stamp(30), "5": stamp(10)}}}}))
        jsonl(self.folder(self.wt) / "s-feat.jsonl", [claude("Continue demo", 20), claude("more", 19)])
        got = {s["session"]: s for s in self.listed()}
        self.assertEqual(sorted(got), ["s-feat", "s-main"])
        first = got["s-main"]
        self.assertEqual(set(first), set(retro.FIELDS))
        self.assertEqual((first["harness"], first["worktree"], first["feature"], first["stage"]),
                         ("claude", str(self.repo), None, None))
        self.assertEqual((first["start"], first["end"]), (stamp(50), stamp(48)))
        self.assertEqual((first["bytes"], first["subagents"]), (main.stat().st_size + sub.stat().st_size, 1))
        self.assertEqual((first["path"], first["first_request"]), (str(main), "x" * 200))
        feat = got["s-feat"]
        self.assertEqual((feat["worktree"], feat["feature"], feat["stage"], feat["first_request"]),
                         (str(self.wt), "demo", "3", "Continue demo"))

    def test_codex_sessions(self) -> None:
        main = jsonl(self.codex / "rollout-a.jsonl", [
            meta("main-1", self.wt / "sub", "cli", 30),
            codex_user("# AGENTS.md instructions for x", 30),
            codex_user("<environment_context>x</environment_context>", 30),
            codex_user("  Build the demo  ", 29),
        ])
        child = jsonl(self.codex / "rollout-b.jsonl", [
            meta("child-1", self.wt, {"subagent": {"thread_spawn": {"parent_thread_id": "main-1"}}}, 29)])
        jsonl(self.codex / "rollout-c.jsonl", [
            meta("orphan", self.wt, {"subagent": {"thread_spawn": {"parent_thread_id": "gone"}}}, 29)])
        jsonl(self.codex / "rollout-d.jsonl", [meta("guard", self.wt, {"subagent": {"other": "guardian"}}, 29)])
        jsonl(self.codex / "rollout-e.jsonl", [meta("outside", self.tmp / "repo-other", "cli", 29)])
        jsonl(self.codex / "rollout-f.jsonl", [codex_user("no meta", 29)])
        jsonl(self.codex / "rollout-g.jsonl", [])
        got = self.listed()
        self.assertEqual([s["session"] for s in got], ["main-1"])
        item = got[0]
        self.assertEqual((item["harness"], item["worktree"], item["feature"], item["first_request"]),
                         ("codex", str(self.wt), "demo", "Build the demo"))
        self.assertEqual((item["bytes"], item["subagents"]), (main.stat().st_size + child.stat().st_size, 1))

    def test_order(self) -> None:
        jsonl(self.folder(self.repo) / "big.jsonl", [claude("b" * 500, 6)])
        jsonl(self.folder(self.repo) / "tie-late.jsonl", [claude("c", 3)])
        jsonl(self.folder(self.repo) / "tie-early.jsonl", [claude("c", 4)])
        jsonl(self.folder(self.repo) / "tie-same-b.jsonl", [claude("c", 2)])
        jsonl(self.folder(self.repo) / "tie-same-a.jsonl", [claude("c", 2)])
        self.assertEqual([s["session"] for s in self.listed()],
                         ["big", "tie-early", "tie-late", "tie-same-a", "tie-same-b"])


class SessionsCommandTests(TempDir):
    def run_cli(self, *args: str, cwd: Path) -> subprocess.CompletedProcess:
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(self.tmp / "c"), "CODEX_HOME": str(self.tmp / "x")}
        return subprocess.run([sys.executable, str(DEVLOOP), "sessions", *args], cwd=cwd, capture_output=True,
                              text=True, env=env)

    def test_usage_and_errors(self) -> None:
        for args in (["--since", "0"], ["--since", "x"], []):
            with self.subTest(args=args):
                self.assertEqual(self.run_cli(*args, cwd=self.tmp).returncode, 2)
        out = self.run_cli("--since", "7", cwd=self.tmp)
        self.assertEqual(out.returncode, 1)
        self.assertIn("error", json.loads(out.stdout))
        git(self.tmp, "init", "-q")
        out = self.run_cli("--since", "2", cwd=self.tmp)
        payload = json.loads(out.stdout)
        since = datetime.strptime(payload["since"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertLess(abs((datetime.now(timezone.utc) - timedelta(days=2) - since).total_seconds()), 60)
        self.assertEqual(payload["sessions"], [])


if __name__ == "__main__":
    unittest.main()
