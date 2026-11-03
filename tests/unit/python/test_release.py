#!/usr/bin/env python3
"""Tests for release.py: parsing the project note (security items, rulings, audit records)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / "skill" / "scripts"))
import release  # noqa: E402

SHA = "a" * 40
OLD = "b" * 40


def deferred(*lines: str) -> str:
    return "\n".join(["## Deferred", "", *lines, "", "## Gardener", "- Security: the token is logged", ""])


class ReleaseParseTests(unittest.TestCase):
    def items(self, text: str) -> tuple[list[dict], list[str]]:
        return release.security_items(text)

    def test_one_line_folds_every_line_break(self) -> None:
        for sep in ("\r", "\n", "\t", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "):
            text = f"a{sep}{sep}- b"
            self.assertEqual(release.one_line(text), "a - b")
        self.assertEqual(release.one_line(" `a`\x0b\x0b "), "'a'")

    def test_needs_validation_rulings(self) -> None:
        item = "- 2026-10-25 (security, needs-validation, Run 1): x"
        cases = [
            ([], True),
            (["  - Ruling 2026-10-26: accept the risk"], False),
            (["  - Ruling 2026-10-26: close not reachable"], False),
            (["  - Ruling 2026-10-26: fix in a slice"], True),
            (["\t- Ruling 2026-10-26: accept the risk"], False),
            (["- Ruling 2026-10-26: accept the risk"], True),
            (["  - Ruling 2026-10-26: accept "], True),
            (["  - Ruling 2026-10-26: close   "], True),
        ]
        for rulings, is_open in cases:
            with self.subTest(rulings=rulings):
                found, _ = self.items(deferred(item, *rulings))
                self.assertEqual([i["line"] for i in found], [item] if is_open else [])

    def test_confirmed_rulings(self) -> None:
        item = "- 2026-10-25 (security, confirmed, Run 1): x"
        cases = [
            (["  - Ruling 2026-10-30: accept low risk"], False),
            (["  - Ruling 2026-10-30: close fixed by #7"], False),
            (["  - Ruling 2026-10-30: fix #7 in a slice"], True),
            (["  - Ruling 2026-10-30: accept"], True),
            (["  - Ruling 2026-10-30: accept "], True),
            (["  - Ruling 2026-10-30: close   "], True),
            (["  - Ruling 2026-10-30: accept for rc.4 only", "  - Ruling 2026-11-03: fix #7 after the live check"], True),
        ]
        for rulings, is_open in cases:
            with self.subTest(rulings=rulings):
                found, _ = self.items(deferred(item, *rulings))
                self.assertEqual([i["line"] for i in found], [item] if is_open else [])

    def test_deferred_items_keep_closed_items_and_last_ruling(self) -> None:
        item = "- 2026-10-25 (security, needs-validation, Run 3): x. Fingerprint `f-n1`."
        accept = "  - Ruling 2026-10-30: accept single owner"
        cases = [
            ([], False, None),
            ([accept], True, {"date": "2026-10-30", "word": "accept", "carried": False}),
            ([accept, "  - Ruling 2026-10-31: close fixed by #1"], True, {"date": "2026-10-31", "word": "close", "carried": False}),
            ([accept, "  - Ruling 2026-10-31: fix in a slice"], False, {"date": "2026-10-31", "word": "fix", "carried": False}),
            ([accept, "  - Ruling 2026-10-31: fix #9 in a slice", "  - Ruling 2026-11-01: close fixed by #9"], True,
             {"date": "2026-11-01", "word": "close", "carried": False}),
            (["  - Ruling 2026-10-31: fix #9 in a slice", accept], True, {"date": "2026-10-30", "word": "accept", "carried": False}),
            ([accept, "  - Ruling 2026-10-31: fix in a slice", "  - Ruling 2026-11-01: close "], False,
             {"date": "2026-10-31", "word": "fix", "carried": False}),
            (["  - Ruling 2026-11-01: accept carried from the 2026-10-30 ruling (Run 3): x."], True, {"date": "2026-11-01", "word": "accept", "carried": True}),
            (["  - Ruling 2026-10-30: accept "], False, None),
        ]
        for subs, closed, ruling in cases:
            with self.subTest(subs=subs):
                [found], _ = release.deferred_items(deferred(item, *subs))
                self.assertEqual((found["closed"], found["ruling"], found["source"], found["fingerprint"]), (closed, ruling, "Run 3", "f-n1"))
        again = "- 2026-10-31 (security, needs-validation, Run 4): y. Fingerprint `f-n1`."
        text = deferred(item, accept, again)
        self.assertEqual([i["line"] for i in release.deferred_items(text)[0]], [item, again])
        self.assertEqual([i["line"] for i in self.items(text)[0]], [again])

    def test_checkbox_and_subsection_and_fingerprint(self) -> None:
        text = "## Deferred\n### Some gate\n- [ ] 2026-10-25 (security, needs-validation, my-feature): y. Fingerprint `abc123`. Blocker: z.\n- [x] 2026-10-25 (security, confirmed, Run 2): w\n"
        found, unparsed = self.items(text)
        self.assertEqual([(i["source"], i["fingerprint"]) for i in found], [("my-feature", "abc123"), ("Run 2", None)])
        self.assertEqual(unparsed, [])

    def test_unparsed_lines(self) -> None:
        text = deferred("- Security: the token is logged", "- Insecurely named thing", "Security: column 0 prose", "  - Security nested")
        found, unparsed = self.items(text)
        self.assertEqual((found, unparsed), ([], ["- Security: the token is logged"]))

    def test_column_zero_ruling_is_unparsed(self) -> None:
        item = "- 2026-10-25 (security, needs-validation, Run 1): x"
        ruling = "- Ruling 2026-10-26: accept the security risk"
        found, unparsed = self.items(deferred(item, ruling))
        self.assertEqual(([i["line"] for i in found], unparsed), ([item], [ruling]))

    def test_crlf_note(self) -> None:
        text = deferred("- 2026-10-25 (security, needs-validation, Run 1): x", "\t- Ruling 2026-10-26: close fine").replace("\n", "\r\n")
        self.assertEqual(self.items(text), ([], []))

    def test_current_record_is_the_last_match(self) -> None:
        line = lambda n, sha: f"- 2026-10-2{n} Run {n} at {sha}: confirmed 0, needs validation 3, hardening 1, profile quick, scope whole"
        text = f"## Release audits\n{line(1, OLD)}\nnot a record\n{line(2, SHA)}\n"
        rec = release.current_record(text)
        self.assertEqual((rec["run"], rec["sha"], rec["scope"], rec["line"]), ("Run 2", SHA, "whole", line(2, SHA)))
        self.assertIsNone(release.current_record("## Release audits\n- junk\n"))
        ranged = f"## Release audits\n- 2026-10-25 Run 3 at {SHA}: confirmed 0, needs validation 0, hardening 0, profile standard, scope {OLD}..{SHA}\n"
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


DEVLOOP = ROOT / "skill" / "scripts" / "devloop.py"
RECORD = "- 2026-10-25 Run 1 at {sha}: confirmed 0, needs validation 0, hardening 0, profile quick, scope whole"


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
        folder = self.notes / "Security Audit" / run
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
        self.run_folder("Run 1", sha)

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

    def test_ci_and_config_changes_are_stale(self) -> None:
        self.audited()
        self.git("checkout", "-q", "-b", "ci", self.a)
        self.commit(".github/workflows/ci.yml", "c", "docker-compose.yml", "d")
        self.git("tag", "v1.0.0")
        self.assertEqual(self.check("--tag", "v1.0.0")[0]["reasons"],
                         [{"reason": "stale-audit", "paths": [".github/workflows/ci.yml", "docker-compose.yml"]}])
        self.git("checkout", "-q", "-b", "fixtures", self.a)
        fixtures = self.commit("tests/unit/test_x.py", "x", "tests/fixtures/data.json", "{}", "docs/guide.md", "g")
        self.assertEqual(self.check("--sha", fixtures)[0]["ok"], True)

    def test_last_record_is_current(self) -> None:
        old = f"- 2026-10-24 Run 1 at {self.b}: confirmed 0, needs validation 0, hardening 0, profile quick, scope whole"
        self.note(old + "\n" + RECORD.format(sha=self.a).replace("Run 1", "Run 2"))
        self.run_folder("Run 2", self.a)
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
            ("other commit", lambda: self.run_folder("Run 1", self.b)),
            ("incomplete", lambda: self.run_folder("Run 1", self.a, "incomplete")),
            ("no folder", lambda: None),
            ("bad json", lambda: (self.notes / "Security Audit" / "Run 1").mkdir(parents=True) or
             (self.notes / "Security Audit" / "Run 1" / "run-metadata.json").write_text("[")),
            ("not an object", lambda: (self.notes / "Security Audit" / "Run 1").mkdir(parents=True) or
             (self.notes / "Security Audit" / "Run 1" / "run-metadata.json").write_text("[1]")),
        ):
            with self.subTest(label):
                self.note(RECORD.format(sha=self.a))
                make()
                out, code, _ = self.check("--sha", self.a)
                self.assertEqual(([r["reason"] for r in out["reasons"]], code), (["record-mismatch"], 1))
                self.assertTrue(out["reasons"][0]["detail"])
                subprocess.run(["rm", "-rf", str(self.notes / "Security Audit")], check=True)

    def test_open_and_unparsed_security(self) -> None:
        item = "- 2026-10-25 (security, needs-validation, Run 1): x"
        cases = [
            (item, ["open-security"]),
            (item + "\n  - Ruling 2026-10-26: accept the risk", []),
            (item + "\n  - Ruling 2026-10-26: close not reachable", []),
            (item + "\n  - Ruling 2026-10-26: fix in a slice", ["open-security"]),
            ("- 2026-10-25 (security, confirmed, Run 1): x\n  - Ruling 2026-10-26: accept", ["open-security"]),
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
        self.run_folder("Run 1", self.a)
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

class PlanRepo(unittest.TestCase):
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
        self.b = self.commit("b.txt", "b")
        (self.notes / "Project Note.md").write_text("## Deferred\n")

    def git(self, *args: str) -> str:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        done = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True, env=env)
        return done.stdout.strip()

    def commit(self, path: str, text: str) -> str:
        (self.repo / path).write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", path)
        return self.git("rev-parse", "HEAD")

    def record(self, sha: str) -> None:
        (self.notes / "Project Note.md").write_text(f"## Release audits\n{RECORD.format(sha=sha)}\n")

    def plan(self, *args: str, path: str | None = None) -> tuple[dict, int]:
        env = {**os.environ, "DEV_LOOP_NOTES": str(self.notes)}
        if path is not None:
            env["PATH"] = path
        done = subprocess.run([sys.executable, str(DEVLOOP), "release-plan", "--repo", str(self.repo), *args],
                              capture_output=True, text=True, env=env)
        return json.loads(done.stdout or "{}"), done.returncode


class ReleasePlanTests(PlanRepo):
    def test_first_plan_creates_run_folder(self) -> None:
        out, code = self.plan()
        folder = self.notes / "Security Audit" / "Run 1"
        self.assertEqual((code, out["run"], out["first"], out["profile"], out["budget"], out["scope"], out["base"], out["prior_runs"], out["head"]),
                         (0, "Run 1", True, "quick", 100, "whole", None, [], self.b))
        self.assertEqual(out["output"], str(folder))
        self.assertTrue(folder.is_dir())

    def test_plan_json_equals_stdout(self) -> None:
        out, code = self.plan()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((Path(out["output"]) / "plan.json").read_text()), out)

    def test_failed_plan_writes_no_plan_json(self) -> None:
        (self.repo / "a.txt").write_text("dirty")
        out, code = self.plan()
        self.assertEqual((code, out["error"]), (1, "dirty"))
        self.assertEqual(list(self.notes.rglob("plan.json")), [])

    def test_next_plan_scopes_from_record(self) -> None:
        for n in (1, 3):
            (self.notes / "Security Audit" / f"Run {n}").mkdir(parents=True)
        self.record(self.a)
        out, code = self.plan()
        base = self.notes / "Security Audit"
        self.assertEqual((code, out["run"], out["first"], out["profile"], out["budget"], out["scope"], out["base"]),
                         (0, "Run 4", False, "standard", None, f"{self.a}..{self.b}", self.a))
        self.assertEqual(out["prior_runs"], [str(base / "Run 1"), str(base / "Run 3")])

    def test_base_is_merge_base_when_record_not_ancestor(self) -> None:
        self.git("checkout", "-q", "-b", "side", self.a)
        side = self.commit("s.txt", "s")
        self.git("checkout", "-q", "main")
        self.record(side)
        out, code = self.plan()
        self.assertEqual((code, out["base"], out["scope"]), (0, self.a, f"{self.a}..{self.b}"))

    def test_overrides_and_bad_budget(self) -> None:
        out, code = self.plan("--profile", "deep", "--budget", "40")
        self.assertEqual((code, out["profile"], out["budget"]), (0, "deep", 40))
        self.assertEqual(self.plan("--budget", "0")[1], 2)

    def test_placeholder_blocks_and_creates_nothing(self) -> None:
        run = self.notes / "Security Audit" / "Run 1"
        run.mkdir(parents=True)
        (run / ".findings.json.icloud").write_text("")
        out, code = self.plan()
        self.assertEqual((code, out["error"], len(out["files"])), (1, "placeholder", 1))
        self.assertIn(".findings.json.icloud", out["files"][0])
        self.assertEqual([p.name for p in run.parent.iterdir()], ["Run 1"])

    def test_dirty_tracked_file_blocks_untracked_does_not(self) -> None:
        (self.repo / "untracked.txt").write_text("u")
        self.assertEqual(self.plan()[1], 0)
        shutil.rmtree(self.notes / "Security Audit" / "Run 1")
        (self.repo / "a.txt").write_text("changed")
        out, code = self.plan()
        self.assertEqual((code, out["error"], out["paths"]), (1, "dirty", ["a.txt"]))
        self.assertFalse((self.notes / "Security Audit").exists() and any((self.notes / "Security Audit").iterdir()))

    def test_no_project_note(self) -> None:
        (self.notes / "Project Note.md").unlink()
        out, code = self.plan()
        self.assertEqual((code, out["error"]), (1, "no-project-note"))

    def test_sandbox_without_docker(self) -> None:
        only = self.root / "bin"
        only.mkdir()
        for tool in ("git", Path(sys.executable).name):
            (only / tool).symlink_to(shutil.which(tool) or sys.executable)
        (self.repo / "dev-loop.json").write_text(json.dumps({"security_audit": {"images": {"node": "node:22"}}}))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "cfg")
        out, code = self.plan(path=str(only))
        self.assertEqual((code, out["sandbox"]["engine"], out["sandbox"]["images"]), (0, False, {"node": "node:22"}))
        self.assertTrue(Path(out["sandbox"]["runner"]).is_absolute())
        self.assertTrue(out["sandbox"]["runner"].endswith("scripts/sandbox-run.py"))

    def bin_path(self, docker: bool) -> str:
        only = self.root / ("bin-docker" if docker else "bin")
        only.mkdir(exist_ok=True)
        for tool in ("git", Path(sys.executable).name):
            if not (only / tool).exists():
                (only / tool).symlink_to(shutil.which(tool) or sys.executable)
        if docker:
            (only / "docker").write_text("#!/bin/sh\nexit 0\n")
            (only / "docker").chmod(0o755)
        return str(only)

    def config(self, config: dict) -> None:
        (self.repo / "dev-loop.json").write_text(json.dumps(config))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "cfg")

    def test_require_sandbox_refusals(self) -> None:
        audits = self.notes / "Security Audit"
        rows = [(None, False, "no-images"), ({"images": {}}, True, "no-images"), ({"images": ["x"]}, True, "no-images"),
                ({"images": "x"}, True, "no-images"), ({"images": {"t": "x"}}, False, "no-engine")]
        for audit, docker, reason in rows:
            with self.subTest(audit=audit, docker=docker):
                self.config({} if audit is None else {"security_audit": audit})
                out, code = self.plan("--require-sandbox", path=self.bin_path(docker))
                self.assertEqual((code, out), (1, {"error": "no-sandbox", "reason": reason}))
                self.assertFalse(audits.exists() and any(audits.iterdir()))
        out, code = self.plan(path=self.bin_path(False))
        self.assertEqual((code, out["sandbox"]["engine"]), (0, False))

    def test_require_sandbox_passes_with_engine(self) -> None:
        self.config({"security_audit": {"images": {"t": "x"}}})
        out, code = self.plan("--require-sandbox", path=self.bin_path(True))
        self.assertEqual((code, out["sandbox"]["engine"], out["sandbox"]["images"], out["run"]), (0, True, {"t": "x"}, "Run 1"))
        self.assertTrue((self.notes / "Security Audit" / "Run 1").is_dir())

    def test_require_sandbox_comes_after_other_checks(self) -> None:
        audits = self.notes / "Security Audit"
        (self.repo / "a.txt").write_text("changed")
        out, code = self.plan("--require-sandbox", path=self.bin_path(False))
        self.assertEqual((code, out["error"]), (1, "dirty"))
        self.assertFalse(audits.exists())
        self.git("checkout", "a.txt")
        (audits / "Run 1").mkdir(parents=True)
        (audits / "Run 1" / ".findings.json.icloud").write_text("")
        out, code = self.plan("--require-sandbox", path=self.bin_path(False))
        self.assertEqual((code, out["error"]), (1, "placeholder"))
        self.assertEqual([p.name for p in audits.iterdir()], ["Run 1"])

    def test_skill_folder_per_harness(self) -> None:
        home = Path.home()
        self.assertEqual(release.skill_dir("claude", {}), home / ".claude/skills/security-audit")
        self.assertEqual(release.skill_dir("codex", {}), home / ".agents/skills/security-audit")
        self.assertEqual(release.skill_dir("codex", {"DEVLOOP_TEST_SECURITY_AUDIT_DIR": "/x"}), Path("/x"))
        out, _ = self.plan("--harness", "codex")
        self.assertEqual(out["skill"], str(home / ".agents/skills/security-audit"))

    def test_staged_rename_lists_both_paths(self) -> None:
        self.git("mv", "a.txt", "c.txt")
        out, code = self.plan()
        self.assertEqual((code, out["error"], sorted(out["paths"])), (1, "dirty", ["a.txt", "c.txt"]))

    def test_relative_notes_give_absolute_paths(self) -> None:
        (self.notes / "Security Audit" / "Run 1").mkdir(parents=True)
        old = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, old)
        out, code = release.plan(self.repo, Path("Demo"), "claude")
        self.assertEqual(code, 0)
        self.assertTrue(Path(out["output"]).is_absolute())
        self.assertTrue(all(Path(p).is_absolute() for p in out["prior_runs"]))

    def test_run_folder_race_is_an_error(self) -> None:
        real = Path.mkdir

        def racing(path, *a, **kw):
            real(path, *a, **kw)
            raise FileExistsError(str(path))

        with unittest.mock.patch.object(Path, "mkdir", racing):
            out, code = release.plan(self.repo, self.notes, "claude")
        self.assertEqual((code, out["error"]), (1, "run-folder-exists"))


ONE, TWO = ".agents/skills/k/one.md", ".agents/skills/k/two.md"
MANIFEST = ".dev-loop/MANIFEST.json"


class ReleasePlanExcludeTests(PlanRepo):
    """Record at A (MANIFEST `3.0.0-rc.1`), B an upgrade to `3.0.0-rc.2` in a repo without the kit source."""

    def setUp(self) -> None:
        super().setUp()
        self.a = self.state("3.0.0-rc.1", {ONE: "1", TWO: "1", "src/app.py": "a", "dev-loop.json": "{}"})
        self.record(self.a)
        self.run_folder()

    def run_folder(self) -> None:
        folder = self.notes / "Security Audit" / "Run 1"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "run-metadata.json").write_text(json.dumps({"source_ref": {"commit": self.a}, "run_status": "complete"}))

    def state(self, version: str | None, files: dict[str, str | None], entries: dict[str, str] | None = None) -> str:
        """Write `files` (None deletes), then a MANIFEST of the files under `.agents`
        as they are, with `entries` over them; `version` None removes it. Commit, return the sha."""
        for path, text in files.items():
            target = self.repo / path
            if text is None:
                target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        manifest = self.repo / MANIFEST
        if version is None:
            manifest.unlink()
        else:
            found = {str(p.relative_to(self.repo)): "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in (self.repo / ".agents").rglob("*") if p.is_file()}
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps({"version": version, "files": {**found, **(entries or {})}}))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "state")
        return self.git("rev-parse", "HEAD")

    def upgrade(self, version: str = "3.0.0-rc.2", files: dict | None = None, **kw) -> str:
        return self.state(version, {ONE: "2", TWO: "2", "src/app.py": "b", "dev-loop.json": '{"v": 2}', **(files or {})}, **kw)

    def test_no_exclude_without_kit_source(self) -> None:
        head = self.upgrade(entries={ONE: "sha256:" + hashlib.sha256(b"2").hexdigest()})
        out, code = self.plan()
        self.assertEqual((code, out["scope"], out["exclude"]), (0, f"{self.a}..{head}", []))

    def test_no_exclude_without_the_record_as_base(self) -> None:
        self.upgrade()
        (self.notes / "Project Note.md").write_text("## Deferred\n")
        out, _ = self.plan()
        self.assertEqual((out["scope"], out["exclude"]), ("whole", []))
        self.git("checkout", "-q", "-b", "side", self.a)
        side = self.commit("s.txt", "s")
        self.git("checkout", "-q", "main")
        self.record(side)
        out, _ = self.plan()
        self.assertEqual((out["base"], out["exclude"]), (self.a, []))

    def test_release_check_keeps_the_kit_copies_stale(self) -> None:
        head = self.upgrade()
        env = {**os.environ, "DEV_LOOP_NOTES": str(self.notes)}
        done = subprocess.run([sys.executable, str(DEVLOOP), "release-check", "--repo", str(self.repo), "--sha", head],
                              capture_output=True, text=True, env=env)
        self.assertEqual(json.loads(done.stdout)["reasons"],
                         [{"reason": "stale-audit", "paths": [ONE, TWO, MANIFEST, "dev-loop.json", "src/app.py"]}])

    def test_kit_roots_are_the_roots_of_the_kit(self) -> None:
        script = ("import {buildKit} from './lib/kit.mjs';"
                  "const k = buildKit({packageRoot: process.cwd(), harnesses: ['claude', 'codex']});"
                  "console.log(JSON.stringify([...k.files, ...k.links].map((f) => f.path)))")
        done = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                              capture_output=True, text=True, check=True)
        paths = json.loads(done.stdout)
        for path in paths:
            self.assertEqual(sum(path.startswith(root) for root in release.KIT_ROOTS), 1, path)
        for root in release.KIT_ROOTS:
            self.assertTrue(any(p.startswith(root) for p in paths), root)


class ReleaseCommitTests(PlanRepo):
    """Record at A (version `3.0.0-rc.1`), B the release commit for `3.0.0`."""

    state = ReleasePlanExcludeTests.state
    run_folder = ReleasePlanExcludeTests.run_folder

    def setUp(self) -> None:
        super().setUp()
        self.a = self.state("3.0.0-rc.1", {ONE: "1", TWO: "1", "src/app.py": "a", "dev-loop.json": self.config("3.0.0-rc.1"),
                                          "package.json": '{"version": "3.0.0-rc.1"}\n',
                                          "CHANGELOG.md": "# Changelog\n\n## [2.1.1] - 2026-10-16\n"})
        self.record(self.a)
        self.run_folder()

    @staticmethod
    def config(version: str, files: object = ("package.json",)) -> str:
        return json.dumps({"version": version, "version_files": files})

    def release(self, files: dict | None = None, version: str = "3.0.0-rc.1") -> str:
        return self.state(version, {"dev-loop.json": self.config("3.0.0"),
                                    "package.json": '{"version": "3.0.0"}\n',
                                    "CHANGELOG.md": "# Changelog\n\n## [3.0.0] - 2026-11-01\n\n## [2.1.1] - 2026-10-16\n",
                                    **(files or {})})

    def check(self, sha: str) -> tuple[dict, int, str]:
        env = {**os.environ, "DEV_LOOP_NOTES": str(self.notes)}
        done = subprocess.run([sys.executable, str(DEVLOOP), "release-check", "--repo", str(self.repo), "--sha", sha],
                              capture_output=True, text=True, env=env)
        return json.loads(done.stdout), done.returncode, done.stderr

    def test_release_commit_after_record_is_current(self) -> None:
        out, code, _ = self.check(self.release())
        self.assertEqual((out["ok"], out["reasons"], code), (True, [], 0))

    def test_code_next_to_release_commit_is_stale(self) -> None:
        out, code, _ = self.check(self.release({"src/app.py": "b"}))
        self.assertEqual((out["reasons"], code), (
            [{"reason": "stale-audit", "paths": ["dev-loop.json", "package.json", "src/app.py"]}], 1))

    def test_kit_upgrade_without_kit_source_is_stale(self) -> None:
        out, code, _ = self.check(self.release({ONE: "2", TWO: "2"}, version="3.0.0"))
        self.assertEqual((out["reasons"], code), (
            [{"reason": "stale-audit", "paths": [ONE, TWO, MANIFEST, "dev-loop.json", "package.json"]}], 1))

    def test_bad_version_files_is_stale(self) -> None:
        out, code, err = self.check(self.release({"dev-loop.json": self.config("3.0.0", "package.json")}))
        self.assertEqual((out["reasons"][0]["reason"], code, "Traceback" in err), ("stale-audit", 1, False))


class ReleaseNotesTests(unittest.TestCase):
    CHANGELOG = (
        "# Changelog\n\n## [3.0.0] - 2026-11-01\n\n### Highlights\n\nIt does more.\n\n"
        "### Fixes\n\nIt breaks less.\n\n## [3.0.0-rc.2] - 2026-10-30\n\nRC body.\n\n"
        "## [v3.1.0] - 2026-12-01\n\nV heading body.\n\n## [2.1.1] - 2026-10-16\n\nOld text.\n\n"
        "## [3.2.0] - 2027-01-01\n\n\n## [2.0.0] - 2026-01-01\n"
    )

    def setUp(self) -> None:
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo, True)
        (self.repo / "CHANGELOG.md").write_text(self.CHANGELOG)

    def run_notes(self, *args: str) -> tuple[int, dict]:
        done = subprocess.run([sys.executable, str(DEVLOOP), "release-notes", "--repo", str(self.repo), *args],
                              capture_output=True, text=True, cwd=self.repo)
        return done.returncode, json.loads(done.stdout) if done.stdout.strip() else {}

    def test_final_section_to_file(self) -> None:
        code, out = self.run_notes("--tag", "v3.0.0", "--output", "out.md")
        self.assertEqual(code, 0)
        self.assertEqual(out, {"ok": True, "tag": "v3.0.0", "version": "3.0.0", "output": "out.md", "lines": 7})
        body = (self.repo / "out.md").read_text()
        self.assertIn("It does more.", body)
        self.assertNotIn("## [3.0.0]", body)
        self.assertNotIn("Old text", body)
        self.assertNotIn("RC body", body)
        self.assertTrue(body.startswith("### Highlights\n") and body.endswith("It breaks less.\n"))

    def test_subsections_stay_in_the_body(self) -> None:
        self.run_notes("--tag", "v3.0.0", "--output", "out.md")
        body = (self.repo / "out.md").read_text()
        self.assertIn("### Highlights", body)
        self.assertIn("### Fixes", body)

    def test_rc_section(self) -> None:
        code, out = self.run_notes("--tag", "v3.0.0-rc.2")
        self.assertEqual((code, out["ok"], out["version"], out["output"], out["lines"]), (0, True, "3.0.0-rc.2", None, 1))

    def test_v_heading(self) -> None:
        code, out = self.run_notes("--tag", "v3.1.0")
        self.assertEqual((code, out["ok"]), (0, True))

    def test_failures(self) -> None:
        for tag, reason in (("v3.0.1", "no-section"), ("v3.2.0", "empty-section")):
            with self.subTest(tag=tag):
                code, out = self.run_notes("--tag", tag, "--output", "never.md")
                self.assertEqual((code, out["ok"], out["reason"], out["tag"]), (1, False, reason, tag))
                self.assertFalse((self.repo / "never.md").exists())
        (self.repo / "CHANGELOG.md").unlink()
        code, out = self.run_notes("--tag", "v3.0.0")
        self.assertEqual((code, out["reason"]), (1, "no-changelog"))

    def test_bad_tags(self) -> None:
        for tag in ("3.0.0", "v3.0", "v3.0.0-beta.1"):
            with self.subTest(tag=tag):
                code, out = self.run_notes("--tag", tag)
                self.assertEqual(code, 2)
                self.assertIn("error", out)


if __name__ == "__main__":
    unittest.main()
