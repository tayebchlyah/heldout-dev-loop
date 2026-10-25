#!/usr/bin/env python3
"""Tests for retro.py: the weekly review due rule and the session list."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
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


if __name__ == "__main__":
    unittest.main()
