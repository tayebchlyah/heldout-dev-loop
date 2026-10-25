#!/usr/bin/env python3
"""Tests for release.py: parsing the project note (security items, rulings, audit records)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
import release  # noqa: E402

SHA = "a" * 40
OLD = "b" * 40


def deferred(*lines: str) -> str:
    return "\n".join(["## Deferred", "", *lines, "", "## Gardener", "- Security: the token is logged", ""])


class ReleaseParseTests(unittest.TestCase):
    def items(self, text: str) -> tuple[list[dict], list[str]]:
        return release.security_items(text)

    def test_needs_validation_rulings(self) -> None:
        item = "- 2026-10-25 (security, needs-validation, run-1): x"
        cases = [
            ([], True),
            (["  - Ruling 2026-10-26: accept the risk"], False),
            (["  - Ruling 2026-10-26: close not reachable"], False),
            (["  - Ruling 2026-10-26: fix in a slice"], True),
            (["\t- Ruling 2026-10-26: accept the risk"], False),
            (["- Ruling 2026-10-26: accept the risk"], True),
        ]
        for rulings, is_open in cases:
            with self.subTest(rulings=rulings):
                found, _ = self.items(deferred(item, *rulings))
                self.assertEqual([i["line"] for i in found], [item] if is_open else [])

    def test_confirmed_is_never_closed_by_a_ruling(self) -> None:
        item = "- 2026-10-25 (security, confirmed, run-1): x"
        found, _ = self.items(deferred(item, "  - Ruling 2026-10-26: accept"))
        self.assertEqual([(i["verdict"], i["source"]) for i in found], [("confirmed", "run-1")])

    def test_checkbox_and_subsection_and_fingerprint(self) -> None:
        text = "## Deferred\n### Some gate\n- [ ] 2026-10-25 (security, needs-validation, my-feature): y. Fingerprint `abc123`. Blocker: z.\n- [x] 2026-10-25 (security, confirmed, run-2): w\n"
        found, unparsed = self.items(text)
        self.assertEqual([(i["source"], i["fingerprint"]) for i in found], [("my-feature", "abc123"), ("run-2", None)])
        self.assertEqual(unparsed, [])

    def test_unparsed_lines(self) -> None:
        text = deferred("- Security: the token is logged", "- Insecurely named thing", "Security: column 0 prose", "  - Security nested")
        found, unparsed = self.items(text)
        self.assertEqual((found, unparsed), ([], ["- Security: the token is logged"]))

    def test_column_zero_ruling_is_unparsed(self) -> None:
        item = "- 2026-10-25 (security, needs-validation, run-1): x"
        ruling = "- Ruling 2026-10-26: accept the security risk"
        found, unparsed = self.items(deferred(item, ruling))
        self.assertEqual(([i["line"] for i in found], unparsed), ([item], [ruling]))

    def test_crlf_note(self) -> None:
        text = deferred("- 2026-10-25 (security, needs-validation, run-1): x", "\t- Ruling 2026-10-26: close fine").replace("\n", "\r\n")
        self.assertEqual(self.items(text), ([], []))

    def test_current_record_is_the_last_match(self) -> None:
        line = lambda n, sha: f"- 2026-10-2{n} run-{n} at {sha}: confirmed 0, needs validation 3, hardening 1, profile quick, scope whole"
        text = f"## Release audits\n{line(1, OLD)}\nnot a record\n{line(2, SHA)}\n"
        rec = release.current_record(text)
        self.assertEqual((rec["run"], rec["sha"], rec["scope"], rec["line"]), ("run-2", SHA, "whole", line(2, SHA)))
        self.assertIsNone(release.current_record("## Release audits\n- junk\n"))
        ranged = f"## Release audits\n- 2026-10-25 run-3 at {SHA}: confirmed 0, needs validation 0, hardening 0, profile standard, scope {OLD}..{SHA}\n"
        self.assertEqual(release.current_record(ranged)["scope"], f"{OLD}..{SHA}")

    def test_project_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            notes = Path(tmp) / "Demo"
            notes.mkdir()
            self.assertIsNone(release.project_note(notes))
            (notes / "Demo.md").write_text("a")
            self.assertEqual(release.project_note(notes), notes / "Demo.md")
            (notes / "Project Note.md").write_text("b")
            self.assertEqual(release.project_note(notes), notes / "Project Note.md")


DEVLOOP = HERE.parents[2] / "skill" / "scripts" / "devloop.py"
RECORD = "- 2026-10-25 run-1 at {sha}: confirmed 0, needs validation 0, hardening 0, profile quick, scope whole"


class ReleaseCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "repo"
        self.notes = self.root / "Demo"
        self.repo.mkdir()
        self.notes.mkdir()
        self.git("init", "-q", "-b", "main")
        self.a = self.commit("a.txt", "a")
        self.b = self.commit("src/app.py", "b")

    def git(self, *args: str) -> str:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        done = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True, env=env)
        return done.stdout.strip()

    def commit(self, path: str, text: str, *more: str) -> str:
        for name, body in ((path, text), *zip(more[::2], more[1::2])):
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(body)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", path)
        return self.git("rev-parse", "HEAD")

    def note(self, record: str | None, deferred: str = "", name: str = "Project Note.md") -> None:
        text = f"## Deferred\n{deferred}\n\n## Gardener\n- Security: gardener text\n"
        if record is not None:
            text += f"\n## Release audits\n{record}\n"
        (self.notes / name).write_text(text)

    def run_folder(self, run: str, sha: str, status: str = "complete") -> None:
        folder = self.notes / "security-audit" / run
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "run-metadata.json").write_text(json.dumps({"source_ref": {"commit": sha}, "run_status": status}))

    def check(self, *args: str) -> tuple[dict, int, str]:
        env = {**os.environ, "DEV_LOOP_NOTES": str(self.notes)}
        done = subprocess.run([sys.executable, str(DEVLOOP), "release-check", "--repo", str(self.repo), *args],
                              capture_output=True, text=True, env=env)
        return json.loads(done.stdout), done.returncode, done.stderr

    def reasons(self, *args: str) -> list[str]:
        return [r["reason"] for r in self.check(*args)[0]["reasons"]]

    def audited(self, sha: str | None = None, deferred: str = "") -> None:
        sha = sha or self.a
        self.note(RECORD.format(sha=sha), deferred)
        self.run_folder("run-1", sha)

    def test_tag_on_audited_commit_is_ok(self) -> None:
        self.git("tag", "v1.0.0", self.a)
        self.audited()
        out, code, _ = self.check("--tag", "v1.0.0")
        self.assertEqual((out["ok"], out["reasons"], out["target"], out["tag"], out["record"], code),
                         (True, [], self.a, "v1.0.0", RECORD.format(sha=self.a), 0))
        out, code, _ = self.check("--sha", self.a)
        self.assertEqual((out["ok"], out["tag"], out["target"], code), (True, None, self.a, 0))

    def test_no_record(self) -> None:
        self.note(None)
        out, code, _ = self.check("--sha", self.a)
        self.assertEqual(([r["reason"] for r in out["reasons"]], out["ok"], out["record"], code), (["no-audit"], False, None, 1))

    def test_staleness(self) -> None:
        self.audited()
        child = self.commit("tests/unit/test_x.py", "x", "README.md", "r")
        self.assertEqual(self.check("--sha", self.b)[0]["reasons"], [{"reason": "stale-audit", "paths": ["src/app.py"]}])
        self.assertEqual(self.check("--sha", self.a)[0]["reasons"], [])
        self.git("checkout", "-q", "-b", "side", self.a)
        side = self.commit("side.txt", "s")
        self.git("checkout", "-q", "main")
        out = self.check("--sha", side)[0]["reasons"]
        self.assertEqual(out, [{"reason": "stale-audit", "paths": ["side.txt"]}])
        self.audited(sha=side)
        out = self.check("--sha", child)[0]["reasons"]
        self.assertEqual(out[0]["reason"], "stale-audit")
        self.assertEqual(set(out[0]["paths"]), {"README.md", "side.txt", "src/app.py", "tests/unit/test_x.py"})

    def test_test_and_doc_changes_pass(self) -> None:
        self.audited()
        child = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "-b", "doc", self.a)
        doc = self.commit("tests/unit/test_x.py", "x", "README.md", "r")
        self.assertEqual(self.check("--sha", doc)[0]["ok"], True)
        self.assertNotEqual(child, doc)

    def test_last_record_is_current(self) -> None:
        old = f"- 2026-10-24 run-1 at {self.b}: confirmed 0, needs validation 0, hardening 0, profile quick, scope whole"
        self.note(old + "\n" + RECORD.format(sha=self.a).replace("run-1", "run-2"))
        self.run_folder("run-2", self.a)
        self.assertEqual(self.check("--sha", self.a)[0]["ok"], True)

    def test_non_ancestor_with_an_empty_diff_is_stale(self) -> None:
        self.git("checkout", "-q", "-b", "twin", self.a)
        self.git("commit", "-q", "--allow-empty", "-m", "twin")
        twin = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        self.audited(sha=twin)
        self.assertEqual(self.check("--sha", self.a)[0]["reasons"], [{"reason": "stale-audit", "paths": []}])

    def test_option_like_rev_is_not_an_option(self) -> None:
        self.note(None)
        out, code, _ = self.check("--sha=--foo")
        self.assertEqual((code, "error" in out), (1, True))

    def test_non_release_tag_needs_no_notes_path(self) -> None:
        self.git("tag", "release-1", self.b)
        env = {k: v for k, v in os.environ.items() if k != "DEV_LOOP_NOTES"}
        env["HOME"] = str(self.root)
        done = subprocess.run([sys.executable, str(DEVLOOP), "release-check", "--repo", str(self.repo), "--tag", "release-1"],
                              capture_output=True, text=True, env=env)
        out = json.loads(done.stdout)
        self.assertEqual((done.returncode, out["ok"], out["skipped"]), (0, True, "not-a-release-tag"))

    def test_record_mismatch(self) -> None:
        for label, make in (
            ("other commit", lambda: self.run_folder("run-1", self.b)),
            ("incomplete", lambda: self.run_folder("run-1", self.a, "incomplete")),
            ("no folder", lambda: None),
            ("bad json", lambda: (self.notes / "security-audit" / "run-1").mkdir(parents=True) or
             (self.notes / "security-audit" / "run-1" / "run-metadata.json").write_text("[")),
            ("not an object", lambda: (self.notes / "security-audit" / "run-1").mkdir(parents=True) or
             (self.notes / "security-audit" / "run-1" / "run-metadata.json").write_text("[1]")),
        ):
            with self.subTest(label):
                self.note(RECORD.format(sha=self.a))
                make()
                out, code, _ = self.check("--sha", self.a)
                self.assertEqual(([r["reason"] for r in out["reasons"]], code), (["record-mismatch"], 1))
                self.assertTrue(out["reasons"][0]["detail"])
                subprocess.run(["rm", "-rf", str(self.notes / "security-audit")], check=True)

    def test_open_and_unparsed_security(self) -> None:
        item = "- 2026-10-25 (security, needs-validation, run-1): x"
        cases = [
            (item, ["open-security"]),
            (item + "\n  - Ruling 2026-10-26: accept the risk", []),
            (item + "\n  - Ruling 2026-10-26: close not reachable", []),
            (item + "\n  - Ruling 2026-10-26: fix in a slice", ["open-security"]),
            ("- 2026-10-25 (security, confirmed, run-1): x\n  - Ruling 2026-10-26: accept", ["open-security"]),
            ("### Some gate\n- [ ] 2026-10-25 (security, needs-validation, review-wave-measurement): y", ["open-security"]),
            ("- Security: the token is logged", ["unparsed-security"]),
            ("- Insecurely named thing", []),
        ]
        for deferred, expected in cases:
            with self.subTest(deferred=deferred):
                self.audited(deferred=deferred)
                self.assertEqual(self.reasons("--sha", self.a), expected)
        self.audited(deferred=item)
        reason = self.check("--sha", self.a)[0]["reasons"][0]
        self.assertEqual(reason["items"], [item])
        self.audited(deferred="- Security: the token is logged")
        self.assertEqual(self.check("--sha", self.a)[0]["reasons"][0]["lines"], ["- Security: the token is logged"])

    def test_non_release_tag_is_skipped(self) -> None:
        self.git("tag", "release-1", self.b)
        out, code, _ = self.check("--tag", "release-1")
        self.assertEqual((out["ok"], out["skipped"], out["tag"], out["target"], out["reasons"], code),
                         (True, "not-a-release-tag", "release-1", self.b, [], 0))

    def test_project_note_fallback_and_errors(self) -> None:
        self.run_folder("run-1", self.a)
        self.note(RECORD.format(sha=self.a), name="Demo.md")
        self.assertEqual(self.check("--sha", self.a)[1], 0)
        self.note(None, name="Project Note.md")
        self.assertEqual(self.reasons("--sha", self.a), ["no-audit"])
        (self.notes / "Project Note.md").unlink()
        (self.notes / "Demo.md").unlink()
        out, code, _ = self.check("--sha", self.a)
        self.assertEqual((out["error"], code, "ok" in out), ("no-project-note", 1, False))
        (self.notes / "Project Note.md").mkdir()
        out, code, err = self.check("--sha", self.a)
        self.assertEqual((code, "error" in out, "Traceback" in err), (1, True, False))
        (self.notes / "Project Note.md").rmdir()
        (self.notes / "Project Note.md").write_bytes(b"\xff\xfe## Deferred\n")
        out, code, err = self.check("--sha", self.a)
        self.assertEqual((code, "error" in out, "Traceback" in err), (1, True, False))

    def test_usage_and_unresolved(self) -> None:
        self.note(None)
        env = {**os.environ, "DEV_LOOP_NOTES": str(self.notes)}
        for args in ([], ["--tag", "v1", "--sha", "x"]):
            done = subprocess.run([sys.executable, str(DEVLOOP), "release-check", "--repo", str(self.repo), *args],
                                  capture_output=True, text=True, env=env)
            self.assertEqual(done.returncode, 2)
        out, code, _ = self.check("--tag", "v9.9.9")
        self.assertEqual((code, "error" in out), (1, True))
        out, code, _ = self.check("--sha", "nope")
        self.assertEqual((code, "error" in out), (1, True))
        done = subprocess.run([sys.executable, str(DEVLOOP), "release-check", "--repo", str(self.root), "--sha", "HEAD"],
                              capture_output=True, text=True, env=env)
        self.assertEqual((done.returncode, "error" in json.loads(done.stdout)), (1, True))


if __name__ == "__main__":
    unittest.main()
