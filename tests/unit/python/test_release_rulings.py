#!/usr/bin/env python3
"""Tests for release_rulings.py: scoped rulings (`waive`), unparsed ruling lines, fences."""

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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT  # noqa: E402
from test_devloop import git, put  # noqa: E402

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

    def test_fence_closes_only_on_matching_bare_fence(self) -> None:
        first = "  - Ruling 2026-01-10: accept r"
        late = "  - Ruling 2026-02-01: fix #7 wrong"
        self.assertEqual(scan([first, "  ```", "  ```text", late, "  ```"], "v3.0.0"), (False, []))
        quoted = "  - Ruling 2026-10-27: accept quoted"
        self.assertEqual(scan(["  ~~~", "  ```", quoted, "  ```", "  ~~~"], "v3.0.0"), (True, []))
        self.assertEqual(scan(["  ````", "  ```", quoted, "  ````"], "v3.0.0"), (True, []))
        self.assertEqual(scan(["  ```", quoted, "  `````  "], "v3.0.0"), (True, []))
        self.assertEqual(scan(["  ```", "  - x", "  ```", quoted], "v3.0.0"), (False, []))

    def test_column_zero_fence_keeps_the_item(self) -> None:
        lines = [ACCEPT, "```", '{"ok": true}', "```", "  - Ruling 2026-11-05: fix #300 check shows it open"]
        self.assertEqual(scan(lines, "v3.0.0"), (True, []))

    def test_ruling_after_a_column_zero_line_is_unparsed(self) -> None:
        reopen = "  - Ruling 2026-11-05: fix #300 check shows it open"
        bad = "  - **Ruling 2026-11-05**: fix #300 x"
        for column_zero in ("Check output, pasted:", "### Release audit Run 9, 2026-11-03", "**note**"):
            for ruling in (reopen, ACCEPT, bad):
                with self.subTest(column_zero=column_zero, ruling=ruling):
                    self.assertEqual(scan([ACCEPT, column_zero, ruling], "v3.0.0"), (False, [ruling]))
        self.assertEqual(scan([ACCEPT, "Output:", "  ```", reopen, "  ```", "  - Verdict: x"], "v3.0.0"), (False, []))
        self.assertEqual(scan(["Output:", "  ```"], "v3.0.0"), (True, ["  ```"]))
        other = "- 2026-11-02 (security, confirmed, Run 9): y. Fingerprint `f-b`."
        text = note(ITEM, ACCEPT, "Output:", other, "  - Ruling 2026-11-03: close fixed by #2")
        self.assertEqual(release.security_items(text, "v3.0.0"), ([], []))
        text = note("- 2026-11-01 (perf, Run 9): z", "Output:", reopen)
        self.assertEqual(release.security_items(text, "v3.0.0"), ([], []))

    def test_other_bullet_that_names_security_is_unparsed(self) -> None:
        for bullet in ("* 2026-11-02 (security, confirmed, Run 9): y", "+ 2026-11-02 (security, confirmed, Run 9): y",
                       " - 2026-11-02 (security, confirmed, Run 9): y", " * a security note"):
            with self.subTest(bullet=bullet):
                self.assertEqual(release.security_items(note(bullet), "v3.0.0"), ([], [bullet]))
                self.assertEqual(scan([ACCEPT, bullet, "  - Ruling 2026-11-05: accept x"], "v3.0.0"),
                                 (False, [bullet, "  - Ruling 2026-11-05: accept x"]))
        self.assertEqual(scan([ACCEPT, "* a note", " - a note", "  - Verdict: security of x"], "v3.0.0"), (False, []))

    def test_pasted_output_in_a_fence_is_content(self) -> None:
        lines = ["  - Ruling 2026-11-02: fix #170 patch", "  ```", '{"ok": false}', "  - Ruling 2026-11-04: accept old", "  ```"]
        self.assertEqual(scan(lines, "v3.0.0"), (True, []))
        self.assertEqual(scan(["  ```", '{"ok": false}'], "v3.0.0"), (True, ["  ```"]))

    def test_ruling_marks_may_be_spaced(self) -> None:
        line = "  - * Ruling 2026-11-05: fix #1 x"
        self.assertEqual(scan([ACCEPT, line], "v3.0.0"), (False, [line]))

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
        reopen = "  - Ruling 2026-11-05: fix #300 check shows it open"
        self.set_note(ITEM, ACCEPT, "Output:", reopen)
        self.assertEqual(self.reasons("--tag", "v3.0.0"), [{"reason": "unparsed-security", "lines": [reopen]}])

    def test_contract_text(self) -> None:
        text = (ROOT / "reference" / "cli" / "release-check.md").read_text()
        for word in ("--for <tag>", "waive <tag> <reason>", "unparsed-security", "fence"):
            with self.subTest(word=word):
                self.assertIn(word, text)


class ScopedRulingTextTests(unittest.TestCase):
    """Oracles 14 and 15: the release text passes the tag and names waive and fences."""

    @staticmethod
    def step(path: str, n: int) -> str:
        text = (ROOT / path).read_text()
        return text.split(f"\n{n}. ")[1].split(f"\n{n + 1}. ")[0]

    def test_release_passes_the_tag(self) -> None:
        text = (ROOT / "skills/release/SKILL.md").read_text()
        step = lambda n: self.step("skills/release/SKILL.md", n)  # noqa: E731
        self.assertNotIn("release-check", step(1))
        for word in ("release-check --sha HEAD --for <tag>", "release-check --sha A --for <tag>", "ruled `fix #<n>`",
                     "close fixed by #<n>", "waive <tag> <reason>"):
            with self.subTest(word=word):
                self.assertIn(word, step(3))
        self.assertIn("release-check --sha HEAD --for <tag>", step(7))
        self.assertGreater(text.index("--for <tag>"), text.index("\n2. "))

    def test_release_audit_step_6_passes_the_tag(self) -> None:
        self.assertIn("release-check --sha HEAD --for <tag>", self.step("skills/release-audit/SKILL.md", 6))

    def test_deferred_triage_names_waive_and_fences(self) -> None:
        text = (ROOT / "skill/references/deferred.md").read_text()
        for word in ("waive <tag> <reason>", "--sha <rev> --for <tag>", "release-record", "/garden", "fenced block",
                     "unparsed-security"):
            with self.subTest(word=word):
                self.assertIn(word, text)


CLIFF = ('[changelog]\nbody = """\n## [{{ version }}]\n{% for commit in commits %}- {{ commit.message | trim }}\n{% endfor %}"""\n'
         '[git]\nconventional_commits = false\nfilter_unconventional = false\n')


class CliffBodyTests(unittest.TestCase):
    """#262: in `cliff` mode the step 8 release body holds every commit since the previous final tag, as step 4 does."""

    def test_step_8_body_equals_the_step_4_section(self) -> None:
        text = (ROOT / "skills/release/SKILL.md").read_text()
        changelog, body = (re.search(r"`(git cliff [^`]+)`", ScopedRulingTextTests.step("skills/release/SKILL.md", n)).group(1)
                           for n in (4, 8))
        entries = lambda out: sorted(line[2:] for line in out.splitlines() if line.startswith("- "))  # noqa: E731
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(repo, "init", "-q")
            put(repo / "cliff.toml", CLIFF)
            for subject, tag in (("start", "v1.0.0"), ("one", None), ("two", "v1.1.0-rc.1"), ("three", None)):
                git(repo, "commit", "-q", "--allow-empty", "-m", subject)
                if tag:
                    git(repo, "tag", tag)
            for tag in ("v1.1.0-rc.2", "v1.1.0"):
                with self.subTest(tag=tag):
                    run = lambda cmd: subprocess.run(shlex.split(cmd.replace("<tag>", tag)), cwd=repo, check=True,  # noqa: E731
                                                     capture_output=True, text=True).stdout
                    run(changelog)
                    section = [s for s in re.split(r"^## \[", (repo / "CHANGELOG.md").read_text(), flags=re.M) if s.strip()][0]
                    self.assertEqual(entries(section), ["one", "three", "two"])
                    self.assertEqual(entries(run(body)), entries(section))
        self.assertIn("before `git tag -a`", text.split("\n8. ")[1])


if __name__ == "__main__":
    unittest.main()
