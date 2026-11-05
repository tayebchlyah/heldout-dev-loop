#!/usr/bin/env python3
"""Tests for release_rulings.py: scoped rulings (`waive`), unparsed ruling lines, fences."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / "skill" / "scripts"))
import release  # noqa: E402

DEVLOOP = ROOT / "skill" / "scripts" / "devloop.py"

ITEM = "- 2026-11-01 (security, needs-validation, Run 9): x. Fingerprint `f-a`."
WAIVE = "  - Ruling 2026-11-03: waive v3.0.0-rc.5 needs a live check"
ACCEPT = "  - Ruling 2026-11-03: accept owner ok"


def note(*lines: str) -> str:
    return "\n".join(["## Deferred", "", *lines, "", "## Gardener", ""])


def scan(lines: list[str], tag: str | None = None) -> tuple[bool, list[str]]:
    found, unparsed = release.security_items(note(ITEM, *lines), tag)
    return bool(found), unparsed


class RulingParseTests(unittest.TestCase):
    def test_waive_closes_only_for_its_tag(self) -> None:
        self.assertEqual(scan([WAIVE], "v3.0.0-rc.5"), (False, []))
        for tag in ("v3.0.0", "v3.0.0-rc.6", None):
            with self.subTest(tag=tag):
                self.assertEqual(scan([WAIVE], tag), (True, []))
        ruling = release.deferred_items(note(ITEM, WAIVE))[0][0]["ruling"]
        self.assertEqual(ruling, {"date": "2026-11-03", "word": "waive", "carried": False, "tag": "v3.0.0-rc.5"})

    def test_bad_waive_is_unparsed_for_every_tag(self) -> None:
        for w in ("waive rc.5 x", "waive 3.0.0 x", "waive v3.0.0-rc.5", "waive V3.0.0-rc.5 x", "waive v3.0.0-RC.5 x",
                  "waive v3.0.0-rc.05 x", "waive v3.0.0-rc.5: x"):
            line = f"  - Ruling 2026-11-03: {w}"
            for tag in (None, "v3.0.0-rc.5", "v3.0.0"):
                with self.subTest(line=line, tag=tag):
                    self.assertEqual(scan([line], tag), (True, [line]))

    def test_waive_and_accept_order(self) -> None:
        self.assertEqual(scan([WAIVE.replace("needs a live check", "x"), "  - Ruling 2026-11-04: accept owner ok"], "v3.0.0"), (False, []))
        later = [ACCEPT, "  - Ruling 2026-11-04: waive v3.0.0-rc.5 x"]
        self.assertEqual(scan(later, "v3.0.0-rc.5"), (False, []))
        self.assertEqual(scan(later, "v3.0.0"), (True, []))
        self.assertEqual(scan(later), (True, []))

    def test_rc_scoped_accept_is_no_ruling(self) -> None:
        for word in ("accept", "close"):
            for reason in ("for rc.4 only. Validate later.", "for rc.2: check it", "x before the final release", "not for the final x"):
                line = f"  - Ruling 2026-11-03: {word} {reason}"
                for tag in (None, "v3.0.0-rc.4", "v3.0.0"):
                    with self.subTest(line=line, tag=tag):
                        self.assertEqual(scan([line], tag), (True, [line]))
                self.assertIsNone(release.deferred_items(note(ITEM, line))[0][0]["ruling"])
        self.assertEqual(scan(["  - Ruling 2026-11-03: accept for rc.4 only x", "  - Ruling 2026-11-04: close fixed by #1"], "v3.0.0"), (False, []))
        legacy = "  - Ruling 2026-11-04: accept for rc.4 only x"
        self.assertEqual(scan([WAIVE, legacy], "v3.0.0-rc.5"), (False, []))
        self.assertEqual(scan([WAIVE, legacy], "v3.0.0"), (True, [legacy]))

    def test_bad_ruling_lines_are_unparsed(self) -> None:
        for line in (" - Ruling 2026-11-05: fix #230 x", "  - Ruling 2026-11-05: reopen: x", "  - Ruling 2026-11-5: fix #230 x",
                     "  - Ruling 2026-11-05 fix #230 x", "  - ruling 2026-11-05: fix #230 x", "  - **Ruling 2026-11-05**: fix #230 x",
                     "  * Ruling 2026-11-05: fix #230 x", "    - Ruling 2026-11-05: fix #230 x", "  - _Ruling_ 2026-11-05: fix #230 x",
                     "  + Ruling 2026-11-05: fix #230 x"):
            with self.subTest(line=line):
                self.assertEqual(scan([ACCEPT, line], "v3.0.0"), (False, [line]))

    def test_other_sub_bullets_stay_quiet(self) -> None:
        self.assertEqual(scan([ACCEPT, "  - Verdict: the ruling of Run 3 stands"]), (False, []))

    def test_fenced_lines_are_never_rulings(self) -> None:
        for opening, closing in (("  ```", "  ```"), ("  ~~~", "  ~~~"), ("  ```text", "  ```"), ("\t~~~", "\t~~~")):
            with self.subTest(opening=opening):
                lines = ["  - Ruling 2026-11-02: fix #170 patch", opening, "  - Ruling 2026-11-04: accept old fixture", closing]
                self.assertEqual(scan(lines, "v3.0.0"), (True, []))

    def test_unclosed_fence_ends_at_its_item(self) -> None:
        text = note(ITEM, ACCEPT, "  ```", "  - Ruling 2026-11-05: fix #1 x",
                    "- 2026-11-02 (security, confirmed, Run 9): y. Fingerprint `f-b`.", "  - Ruling 2026-11-03: close fixed by #2")
        self.assertEqual(release.security_items(text, "v3.0.0"), ([], ["  ```"]))

    def test_reasonless_accept(self) -> None:
        self.assertEqual(scan(["  - Ruling 2026-10-26: accept"]), (True, ["  - Ruling 2026-10-26: accept"]))
        self.assertEqual(scan([]), (True, []))


class RulingCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.repo, self.notes = root / "repo", root / "Demo"
        self.repo.mkdir()
        self.notes.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.repo / "a.txt").write_text("a")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "a")
        self.head = self.git("rev-parse", "HEAD")
        for tag in ("v3.0.0-rc.5", "v3.0.0-rc.6", "v3.0.0"):
            self.git("tag", tag)
        run = self.notes / "Security Audit" / "Run 1"
        run.mkdir(parents=True)
        (run / "run-metadata.json").write_text(json.dumps({"source_ref": {"commit": self.head}, "run_status": "complete"}))

    def set_note(self, *lines: str) -> None:
        record = f"- 2026-10-25 Run 1 at {self.head}: confirmed 0, needs validation 0, hardening 0, profile quick, scope whole"
        (self.notes / "Project Note.md").write_text("\n".join(["## Deferred", *lines, "", "## Release audits", record, ""]))

    def git(self, *args: str) -> str:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True, env=env).stdout.strip()

    def check(self, *args: str) -> tuple[dict, int]:
        env = {**os.environ, "DEV_LOOP_NOTES": str(self.notes)}
        done = subprocess.run([sys.executable, str(DEVLOOP), "release-check", "--repo", str(self.repo), *args],
                              capture_output=True, text=True, env=env)
        return (json.loads(done.stdout) if done.stdout.strip() else {}), done.returncode

    def reasons(self, *args: str) -> list[dict]:
        return self.check(*args)[0].get("reasons", [])

    def test_tag_scopes_the_waive(self) -> None:
        self.set_note(ITEM, WAIVE)
        self.assertEqual(self.reasons("--tag", "v3.0.0-rc.5"), [])
        for tag in ("v3.0.0", "v3.0.0-rc.6"):
            with self.subTest(tag=tag):
                self.assertEqual(self.reasons("--tag", tag), [{"reason": "open-security", "items": [ITEM]}])

    def test_sha_for_scopes_the_waive(self) -> None:
        self.set_note(ITEM, WAIVE)
        data, code = self.check("--sha", "HEAD", "--for", "v3.0.0-rc.5")
        self.assertEqual((data["ok"], data["tag"], code), (True, "v3.0.0-rc.5", 0))
        data, code = self.check("--sha", "HEAD")
        self.assertEqual((data["tag"], code, [r["reason"] for r in data["reasons"]]), (None, 1, ["open-security"]))

    def test_for_usage_errors(self) -> None:
        self.set_note(ITEM, WAIVE)
        for args in (["--tag", "v3.0.0", "--for", "v3.0.0"], ["--for", "v3.0.0"], ["--sha", "HEAD", "--for", "3.0.0"],
                     ["--sha", "HEAD", "--for", "rc.5"], ["--sha", "HEAD", "--for", "refs/tags/v3.0.0"],
                     ["--sha", "HEAD", "--for", " v3.0.0"], ["--sha", "HEAD", "--for", "V3.0.0"],
                     ["--sha", "HEAD", "--for", "v3.0.0-rc.05"]):
            with self.subTest(args=args):
                self.assertEqual(self.check(*args)[1], 2)

    def test_unparsed_lines_reach_the_report(self) -> None:
        bad = "  - Ruling 2026-10-26: accept"
        self.set_note(ITEM, bad)
        self.assertEqual(self.reasons("--tag", "v3.0.0-rc.5"), [{"reason": "open-security", "items": [ITEM]},
                                                                {"reason": "unparsed-security", "lines": [bad]}])

    def test_contract_text(self) -> None:
        text = (ROOT / "reference" / "cli" / "release-check.md").read_text()
        for word in ("--for <tag>", "waive <tag> <reason>", "unparsed-security", "fence"):
            with self.subTest(word=word):
                self.assertIn(word, text)


if __name__ == "__main__":
    unittest.main()
