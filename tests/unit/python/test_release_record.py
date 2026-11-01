"""`devloop.py release-record`: a finished release audit run in the project note."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from datetime import date
from pathlib import Path

import release
from test_release import DEVLOOP, OLD, SHA


RECORD_RE_LINE = release.RECORD_RE


VALIDATOR = "process.exit(0);\n"


FAILING = "console.error('bad findings'); process.exit(1);\n"


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class ReleaseRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "repo"
        self.notes = self.root / "Demo"
        self.skill = self.root / "skill"
        for folder in (self.repo, self.notes, self.skill):
            folder.mkdir()
        subprocess.run(["git", "-C", str(self.repo), "init", "-q", "-b", "main"], check=True)
        for name in ("validate-findings.cjs", "validate-coverage-ledger.cjs"):
            (self.skill / name).write_text(VALIDATOR)
        self.note = self.notes / "Project Note.md"
        self.note.write_text("## Deferred\n\n## Gardener\n\n## Release audits\n")
        self.today = date.today().isoformat()
        self.run_dir = self.notes / "Security Audit" / "Run 2"
        self.make_run()

    def make_run(self, findings: list | None = None, status: str = "complete", hardening: int = 3, commit: str = SHA) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "run-metadata.json").write_text(json.dumps(
            {"run_status": status, "profile": "standard", "source_ref": {"commit": commit}}))
        if findings is None:
            findings = [
                {"verdict": "confirmed", "fingerprint": "f-c", "title": "Confirmed one", "blockers": []},
                {"verdict": "needs_validation", "fingerprint": "f-n1", "title": "Maybe one", "blockers": ["fact one", "fact two"]},
                {"verdict": "needs_validation", "fingerprint": "f-n2", "title": "Maybe two", "blockers": ["fact three"]},
                {"verdict": "rejected", "fingerprint": "f-r", "title": "Rejected", "blockers": []},
            ]
        (self.run_dir / "findings.json").write_text(json.dumps(findings))
        (self.run_dir / "coverage-ledger.json").write_text("[]")
        bullets = "".join(f"- note {i}\n" for i in range(hardening))
        (self.run_dir / "REPORT.md").write_text(f"# Report\n\n### Hardening (not findings)\n\n{bullets}\n### Other\n- not counted\n")

    def cli(self, command: str, *args: str, path: str | None = None) -> tuple[dict, int]:
        env = {**os.environ, "DEV_LOOP_NOTES": str(self.notes), "DEVLOOP_TEST_SECURITY_AUDIT_DIR": str(self.skill)}
        if path is not None:
            env["PATH"] = path
        done = subprocess.run([sys.executable, str(DEVLOOP), command, "--repo", str(self.repo), *args],
                              capture_output=True, text=True, env=env)
        return json.loads(done.stdout or "{}"), done.returncode

    def record(self, *args: str, **kw) -> tuple[dict, int]:
        return self.cli("release-record", "--run", str(self.run_dir), *args, **kw)

    def lines(self) -> list[str]:
        return self.note.read_text().splitlines()

    def test_record_line_with_base(self) -> None:
        out, code = self.record("--base", OLD)
        want = (f"- {self.today} Run 2 at {SHA}: confirmed 1, needs validation 2, hardening 3, "
                f"profile standard, scope {OLD}..{SHA}")
        self.assertEqual((code, out["record"], self.lines()[-1]), (0, want, want))

    def test_scope_whole_without_base(self) -> None:
        out, code = self.record()
        self.assertEqual(code, 0)
        self.assertTrue(out["record"].endswith("scope whole"))

    def test_deferred_lines_and_release_check(self) -> None:
        out, code = self.record()
        self.assertEqual((code, len(out["deferred"]), out["skipped"]), (0, 3, []))
        text = self.note.read_text()
        self.assertIn(f"### Release audit Run 2, {self.today}", text)
        self.assertEqual(sum("(security, confirmed, Run 2)" in l for l in out["deferred"]), 1)
        self.assertEqual(sum("(security, needs-validation, Run 2)" in l for l in out["deferred"]), 2)
        for fingerprint in ("f-c", "f-n1", "f-n2"):
            self.assertIn(f"Fingerprint `{fingerprint}`", text)
        self.assertIn("Blocker: fact one", out["deferred"][1])
        self.assertNotIn("Rejected", text)
        items, unparsed = release.security_items(text)
        self.assertEqual(([i["fingerprint"] for i in items], unparsed), (["f-c", "f-n1", "f-n2"], []))
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        checked, _ = self.cli("release-check", "--sha", "HEAD")
        reason = next(r for r in checked["reasons"] if r["reason"] == "open-security")
        self.assertEqual(len(reason["items"]), 3)

    def test_open_fingerprint_is_skipped_closed_one_is_not(self) -> None:
        self.note.write_text(f"## Deferred\n\n- 2026-01-01 (security, needs-validation, Run 1): Old. Fingerprint `f-n1`.\n\n## Release audits\n")
        out, _ = self.record()
        self.assertEqual((len(out["deferred"]), out["skipped"]), (2, ["f-n1"]))
        self.note.write_text("## Deferred\n\n- 2026-01-01 (security, needs-validation, Run 1): Old. Fingerprint `f-n1`.\n  - Ruling 2026-01-02: accept fine\n")
        out, _ = self.record()
        self.assertEqual((len(out["deferred"]), out["skipped"]), (3, []))
        self.assertEqual((out["carried"], out["reopened"]), ([], [{"fingerprint": "f-n1", "why": "no-commit", "files": []}]))

    def test_gardener_line(self) -> None:
        out, _ = self.record()
        want = f"- {self.today} (security, hardening, Run 2): 3 hardening notes, see Security Audit/Run 2/REPORT.md"
        self.assertEqual(out["gardener"], want)
        self.assertEqual(self.lines()[self.lines().index("## Gardener") + 1:][:2], [want, ""])
        self.make_run(hardening=0)
        self.note.write_text("## Deferred\n\n## Gardener\n\n## Release audits\n")
        out, _ = self.record()
        self.assertIsNone(out["gardener"])
        self.assertNotIn("hardening, Run 2", self.note.read_text())

    def low(self, severity: object = None, fingerprint: str | None = "f-l", title: str = "Low one") -> dict:
        finding = {"verdict": "confirmed", "title": title, "blockers": []}
        if fingerprint:
            finding["fingerprint"] = fingerprint
        if severity is not None:
            finding["severity"] = severity
        return finding

    def test_low_goes_to_gardener(self) -> None:
        want = f"- {self.today} (security, low, Run 2): Low one. Fingerprint `f-l`. Details: Security Audit/Run 2/FINDINGS-DETAIL.md."
        for value in ("low", "informational"):
            with self.subTest(value=value):
                self.note.write_text("## Deferred\n\n## Gardener\n\n## Release audits\n")
                self.make_run([self.low({"overall_severity": value})], hardening=0)
                out, code = self.record()
                text = self.note.read_text()
                self.assertEqual((code, out["deferred"], out["low"]), (0, [], [want]))
                self.assertIn(want, release.section(text, "Gardener"))
                self.assertIn("confirmed 1,", out["record"])
                self.assertEqual(release.security_items(text), ([], []))

    def test_other_severity_goes_to_deferred(self) -> None:
        values = [{"overall_severity": v} for v in ("medium", "high", "critical", "bogus", "Low")] + [None, {}, "low"]
        for severity in values:
            with self.subTest(severity=severity):
                self.note.write_text("## Deferred\n\n## Gardener\n\n## Release audits\n")
                self.make_run([self.low(severity)], hardening=0)
                out, code = self.record()
                self.assertEqual((code, len(out["deferred"]), out["low"]), (0, 1, []))
                self.assertIn("(security, confirmed, Run 2)", out["deferred"][0])

    def test_low_duplicate_is_skipped(self) -> None:
        self.make_run([self.low({"overall_severity": "low"})], hardening=0)
        for note in ("## Deferred\n\n## Gardener\n\n- 2026-10-01 (security, low, Run 1): Old. Fingerprint `f-l`. Details: x.\n\n## Release audits\n",
                     "## Deferred\n\n- 2026-10-01 (security, confirmed, Run 1): Old. Fingerprint `f-l`.\n\n## Gardener\n\n## Release audits\n"):
            with self.subTest(note=note):
                self.note.write_text(note)
                out, _ = self.record()
                self.assertEqual((out["low"], out["deferred"], out["skipped"]), ([], [], ["f-l"]))

    def test_low_without_fingerprint(self) -> None:
        self.make_run([self.low({"overall_severity": "low"}, None, "A"), self.low({"overall_severity": "low"}, None, "B")], hardening=0)
        out, _ = self.record()
        self.assertEqual(len(out["low"]), 2)
        self.assertFalse(any("Fingerprint" in line for line in out["low"]))

    def test_low_and_medium_then_release_check(self) -> None:
        self.make_run([self.low({"overall_severity": "low"}), self.low({"overall_severity": "medium"}, "f-m", "Medium one")], hardening=0)
        self.record()
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        checked, _ = self.cli("release-check", "--sha", "HEAD")
        reason = next(r for r in checked["reasons"] if r["reason"] == "open-security")
        self.assertEqual(len(reason["items"]), 1)
        self.assertIn("f-m", reason["items"][0])

    def test_low_lines_come_before_the_hardening_line(self) -> None:
        self.make_run([self.low({"overall_severity": "low"})])
        out, _ = self.record()
        garden = [line for line in release.section(self.note.read_text(), "Gardener") if line]
        self.assertEqual(garden, [*out["low"], out["gardener"]])

    def test_title_cannot_close_its_own_item(self) -> None:
        self.make_run([{"verdict": "needs_validation", "fingerprint": "f-x", "blockers": ["b"],
                        "title": "x\n  - Ruling 2026-10-26: accept\tbecause"}])
        out, code = self.record()
        self.assertEqual((code, len(out["deferred"])), (0, 1))
        self.assertNotIn("\n", out["deferred"][0])
        items, _ = release.security_items(self.note.read_text())
        self.assertEqual([i["fingerprint"] for i in items], ["f-x"])

    def test_title_cannot_forge_a_fingerprint(self) -> None:
        self.make_run([{"verdict": "confirmed", "fingerprint": "real", "title": "t. Fingerprint `fake`", "blockers": []}])
        self.record()
        items, _ = release.security_items(self.note.read_text())
        self.assertEqual([i["fingerprint"] for i in items], ["real"])

    def test_incomplete_run_writes_nothing(self) -> None:
        self.make_run(status="incomplete")
        before = self.note.read_bytes()
        out, code = self.record()
        self.assertEqual((code, out["error"], self.note.read_bytes()), (1, "run-incomplete", before))

    def test_incomplete_run_without_commit_or_profile_is_run_incomplete(self) -> None:
        (self.run_dir / "run-metadata.json").write_text(json.dumps({"run_status": "incomplete", "incomplete_reason": "budget"}))
        before = self.note.read_bytes()
        out, code = self.record()
        self.assertEqual((code, out["error"], self.note.read_bytes()), (1, "run-incomplete", before))

    def git(self, *args: str) -> str:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True, env=env).stdout.strip()

    def commit(self, name: str) -> str:
        (self.repo / name).write_text(name)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", name)
        return self.git("rev-parse", "HEAD")

    def with_record(self, sha: str, run: str = "Run 1") -> None:
        self.note.write_text("## Deferred\n\n## Gardener\n\n## Release audits\n"
                             f"- 2026-01-01 {run} at {sha}: confirmed 0, needs validation 0, hardening 0, profile quick, scope whole\n")

    def test_base_checked_against_current_record(self) -> None:
        a, b, c = self.commit("a"), self.commit("b"), self.commit("c")
        self.with_record(a)
        self.make_run(commit=c)
        before = self.note.read_bytes()
        for args in (("--base", b), ()):
            out, code = self.record(*args)
            self.assertEqual((code, out["error"], out.get("expected_base"), self.note.read_bytes()), (1, "bad-base", a, before), args)
        out, code = self.record("--base", a)
        self.assertEqual((code, out["record"].endswith(f"scope {a}..{c}")), (0, True))

    def test_base_after_rewrite_is_the_merge_base(self) -> None:
        a = self.commit("a")
        self.git("checkout", "-q", "-b", "side")
        old = self.commit("old")
        self.git("checkout", "-q", "main")
        c = self.commit("c")
        self.with_record(old)
        self.make_run(commit=c)
        out, code = self.record("--base", old)
        self.assertEqual((code, out["error"], out["expected_base"]), (1, "bad-base", a))
        out, code = self.record("--base", a)
        self.assertEqual(code, 0)

    def test_base_given_with_no_merge_base_is_refused(self) -> None:
        a = self.commit("a")
        self.git("checkout", "-q", "--orphan", "other")
        self.git("rm", "-rqf", ".")
        c = self.commit("c")
        self.with_record(a)
        self.make_run(commit=c)
        out, code = self.record("--base", a)
        self.assertEqual((code, out["error"], out["expected_base"]), (1, "bad-base", None))
        out, code = self.record()
        self.assertTrue(out["record"].endswith("scope whole"))

    def test_failing_validator_writes_nothing(self) -> None:
        (self.skill / "validate-findings.cjs").write_text(FAILING)
        before = self.note.read_bytes()
        out, code = self.record()
        self.assertEqual((code, out["error"], self.note.read_bytes()), (1, "invalid-run", before))
        self.assertIn("bad findings", out["tail"])

    def test_no_node_writes_nothing(self) -> None:
        before = self.note.read_bytes()
        out, code = self.record(path=str(self.root))
        self.assertEqual((code, out["error"], self.note.read_bytes()), (1, "invalid-run", before))

    def test_second_call_is_already_recorded(self) -> None:
        self.record()
        before = self.note.read_bytes()
        out, code = self.record()
        self.assertEqual((code, out["error"], self.note.read_bytes()), (1, "already-recorded", before))

    def test_missing_sections_are_added_and_nothing_else_changes(self) -> None:
        original = "# Note\n\nSome text.\n- a bullet\n"
        self.note.write_text(original)
        out, code = self.record()
        self.assertEqual(code, 0)
        text = self.note.read_text()
        self.assertTrue(text.startswith(original))
        for heading in ("## Deferred", "## Gardener", "## Release audits"):
            self.assertEqual(text.count(heading + "\n"), 1)
        self.assertEqual(release.current_record(text)["run"], "Run 2")
        self.assertEqual(len(release.security_items(text)[0]), 3)

    def test_existing_lines_stay(self) -> None:
        self.note.write_text("## Deferred\n- keep me\n\n## Gardener\n- gardener keep\n\n## Release audits\n- old line\n\n## Tail\ntext\n")
        self.record()
        text = self.note.read_text()
        for kept in ("- keep me", "- gardener keep", "- old line", "## Tail\ntext\n"):
            self.assertIn(kept, text)

    def test_bad_run_folder(self) -> None:
        for folder in (self.root / "elsewhere" / "Run 2", self.notes / "Security Audit" / "other", self.notes / "Security Audit" / "Run 2" / "sub"):
            folder.mkdir(parents=True, exist_ok=True)
            before = self.note.read_bytes()
            out, code = self.cli("release-record", "--run", str(folder))
            self.assertEqual((code, out["error"], self.note.read_bytes()), (1, "bad-run", before), folder)

    def test_malformed_finding_still_writes_one_line(self) -> None:
        findings = [
            {"verdict": "confirmed"},
            {"verdict": "needs_validation", "title": "No fingerprint"},
            {"verdict": "needs_validation", "fingerprint": "f-b", "title": "Empty blockers", "blockers": []},
            {"verdict": "needs_validation", "fingerprint": "f-c", "blockers": None},
            {"verdict": "needs_validation", "fingerprint": "f-d", "title": "Bad blockers", "blockers": [None, 3]},
            "junk",
        ]
        self.make_run(findings)
        out, code = self.record()
        self.assertEqual((code, len(out["deferred"])), (0, 5))
        items, unparsed = release.security_items(self.note.read_text())
        self.assertEqual((len(items), unparsed), (5, []))

    def test_concurrent_records_both_land(self) -> None:
        other = self.notes / "Security Audit" / "Run 3"
        other.mkdir(parents=True)
        for f in self.run_dir.iterdir():
            shutil.copy(f, other / f.name)
        procs = [subprocess.Popen([sys.executable, str(DEVLOOP), "release-record", "--repo", str(self.repo), "--run", str(r)],
                                  stdout=subprocess.PIPE, text=True,
                                  env={**os.environ, "DEV_LOOP_NOTES": str(self.notes), "DEVLOOP_TEST_SECURITY_AUDIT_DIR": str(self.skill)})
                 for r in (self.run_dir, other)]
        self.assertEqual([p.wait() for p in procs], [0, 0])
        text = self.note.read_text()
        self.assertEqual(len([l for l in text.splitlines() if RECORD_RE_LINE.match(l)]), 2)


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class RunFolderTests(unittest.TestCase):
    setUp = ReleaseRecordTests.setUp
    make_run = ReleaseRecordTests.make_run
    cli = ReleaseRecordTests.cli
    record = ReleaseRecordTests.record

    def commit(self) -> None:
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], check=True)

    def test_plan_creates_run_1(self) -> None:
        shutil.rmtree(self.notes / "Security Audit")
        self.commit()
        out, code = self.cli("release-plan")
        self.assertEqual((code, out["run"]), (0, "Run 1"))
        self.assertTrue((self.notes / "Security Audit" / "Run 1").is_dir())

    def test_record_writes_run_ids(self) -> None:
        out, code = self.record()
        self.assertEqual(code, 0)
        self.assertTrue(out["record"].startswith(f"- {self.today} Run 2 at {SHA}:"))
        self.assertEqual(sum("(security, confirmed, Run 2)" in l for l in out["deferred"]), 1)
        self.assertIn("Security Audit/Run 2/REPORT.md", out["gardener"])
        self.commit()
        checked, _ = self.cli("release-check", "--sha", "HEAD")
        reason = next(r for r in checked["reasons"] if r["reason"] == "open-security")
        self.assertEqual(len(reason["items"]), 3)

    def test_record_refuses_old_folder(self) -> None:
        old = self.root / "elsewhere" / "run-2"
        shutil.copytree(self.run_dir, old)
        out, code = self.cli("release-record", "--run", str(old))
        self.assertEqual((code, out["error"]), (1, "bad-run"))

    def test_release_commands_stop_on_old_audit_folder(self) -> None:
        self.commit()
        (self.notes / "security-audit").mkdir()
        before = sorted(p.name for p in self.notes.iterdir())
        for args in (("release-check", "--sha", "HEAD"), ("release-plan",), ("release-record", "--run", str(self.run_dir))):
            out, code = self.cli(*args)
            self.assertEqual((code, out["error"]), (1, "old-notes-layout"), args)
        self.assertEqual(sorted(p.name for p in self.notes.iterdir()), before)

    def test_item_with_feature_id_still_parses(self) -> None:
        text = "## Deferred\n- 2026-10-25 (security, needs-validation, release-security-audit): x\n"
        items, unparsed = release.security_items(text)
        self.assertEqual(([i["source"] for i in items], unparsed), (["release-security-audit"], []))


class WaveRepo(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "repo"
        self.notes = self.root / "Demo"
        self.repo.mkdir()
        self.notes.mkdir()
        self.git("init", "-q", "-b", "main")
        self.a = self.commit("init", {"src/app.py": "a", "package.json": '{"version": "1.0.0"}\n',
                                      "dev-loop.json": json.dumps({"version": "1.0.0", "version_files": ["package.json"]}),
                                      "CHANGELOG.md": "# Changelog\n\n## [1.0.0] - 2026-10-01\n"})
        self.note = self.notes / "Project Note.md"
        self.note.write_text("## Deferred\n\n## Gardener\n\n## Release audits\n"
                             f"- 2026-10-29 Run 2 at {self.a}: confirmed 0, needs validation 0, hardening 0, profile standard, scope whole\n")
        self.run_meta("Run 2", self.a)
        self.today = date.today().isoformat()

    def run_meta(self, run: str, sha: str) -> None:
        folder = self.notes / "Security Audit" / run
        folder.mkdir(parents=True)
        (folder / "run-metadata.json").write_text(json.dumps({"run_status": "complete", "source_ref": {"commit": sha}}))

    def git(self, *args: str) -> str:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True, env=env).stdout.strip()

    def commit(self, subject: str, files: dict | None = None) -> str:
        for name, body in (files or {"src/app.py": subject}).items():
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(body)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", subject)
        return self.git("rev-parse", "HEAD")

    def release_commit(self) -> str:
        return self.commit("🔖(release) demo 1.0.1", {
            "package.json": '{"version": "1.0.1"}\n',
            "dev-loop.json": json.dumps({"version": "1.0.1", "version_files": ["package.json"]}),
            "CHANGELOG.md": "# Changelog\n\n## [1.0.1] - 2026-10-30\n\n## [1.0.0] - 2026-10-01\n"})

    def cli(self, command: str, *args: str) -> tuple[dict, int]:
        done = subprocess.run([sys.executable, str(DEVLOOP), command, "--repo", str(self.repo), *args],
                              capture_output=True, text=True, env={**os.environ, "DEV_LOOP_NOTES": str(self.notes)})
        return json.loads(done.stdout or "{}"), done.returncode

    def wave(self, *issues: int) -> tuple[dict, int]:
        return self.cli("release-record", "--fix-wave", *(arg for i in issues for arg in ("--issue", str(i))))

    def check(self, *args: str) -> tuple[dict, int]:
        return self.cli("release-check", *args)


class FixWaveTests(WaveRepo):
    def test_wave_line(self) -> None:
        self.commit("#7: fix")
        c = self.commit("#8: fix")
        out, code = self.wave(8, 7)
        want = f"- {self.today} Run 2 fix wave at {c}: #7, #8"
        text = self.note.read_text()
        self.assertEqual((code, out["wave"]), (0, want))
        self.assertEqual([line for line in text.splitlines() if line][-1], want)
        self.assertEqual(release.current_record(text)["run"], "Run 2")

    def test_duplicate_issue_and_crlf_note(self) -> None:
        self.note.write_bytes(self.note.read_text().replace("\n", "\r\n").encode())
        self.commit("#7: fix")
        out, code = self.wave(7, 7)
        self.assertEqual(code, 0)
        self.assertTrue(out["wave"].endswith(": #7"))
        self.assertEqual(release.current_wave(self.note.read_text(), "Run 2")["issues"], [7])

    def test_refusals_write_nothing(self) -> None:
        before = self.note.read_bytes()
        out, code = self.wave(7)
        self.assertEqual((code, out["error"], self.note.read_bytes()), (1, "not-descendant", before))
        self.commit("#7: fix")
        self.assertEqual(self.wave(7)[1], 0)
        before = self.note.read_bytes()
        out, code = self.wave(7)
        self.assertEqual((code, out["error"], self.note.read_bytes()), (1, "wave-exists", before))
        self.note.write_text("## Deferred\n\n## Release audits\n")
        before = self.note.read_bytes()
        out, code = self.wave(7)
        self.assertEqual((code, out["error"], self.note.read_bytes()), (1, "no-audit", before))

    def test_foreign_commit(self) -> None:
        b = self.commit("#7: fix")
        c = self.commit("✨(x) #12: feature")
        out, code = self.wave(7)
        self.assertEqual((code, out["error"], out["commits"]), (1, "foreign-commit", [{"sha": c, "subject": "✨(x) #12: feature"}]))
        out, _ = self.wave(30)
        self.assertEqual([found["sha"] for found in out["commits"]], [c, b])

    def test_unfixed(self) -> None:
        self.commit("#7: fix")
        self.commit("✨(x) #12: feature")
        out, code = self.wave(7, 12, 30)
        self.assertEqual((code, out["error"], out["issues"]), (1, "unfixed", [30]))

    def test_test_doc_and_release_commits_are_not_foreign(self) -> None:
        self.commit("#7: fix")
        self.commit("chore: tests", {"tests/unit/test_x.py": "x", "docs/x.md": "x"})
        self.release_commit()
        self.assertEqual(self.wave(7)[1], 0)

    def test_bad_version_files_is_foreign(self) -> None:
        self.commit("#7: fix")
        d = self.commit("chore: bump", {"dev-loop.json": json.dumps({"version": "1.0.0", "version_files": "package.json"})})
        out, _ = self.wave(7)
        self.assertEqual((out["error"], [found["sha"] for found in out["commits"]]), ("foreign-commit", [d]))

    def test_names_issue(self) -> None:
        for subject, named in (("#7: x", True), ("fix #7", True), ("#7a", True), ("#70: x", False), ("#17", False), ("7", False)):
            self.assertEqual(release.names_issue(subject, 7), named, subject)
        self.commit("#70: x", {"docs/x.md": "x"})
        out, _ = self.wave(7)
        self.assertEqual((out["error"], out["issues"]), ("unfixed", [7]))

    def test_merge_commit_bringing_code_is_foreign(self) -> None:
        self.git("checkout", "-q", "-b", "side")
        s = self.commit("wip", {"src/other.py": "x"})
        self.git("checkout", "-q", "main")
        self.commit("#7: fix")
        self.git("merge", "-q", "--no-ff", "side", "-m", "Merge side")
        merge = self.git("rev-parse", "HEAD")
        out, _ = self.wave(7)
        self.assertEqual((out["error"], {found["sha"] for found in out["commits"]}), ("foreign-commit", {s, merge}))

    def test_usage(self) -> None:
        self.commit("#7: fix")
        before = self.note.read_bytes()
        for args in (("--fix-wave", "--run", "X", "--issue", "7"), ("--fix-wave", "--base", self.a, "--issue", "7"),
                     ("--fix-wave", "--harness", "claude", "--issue", "7"), ("--fix-wave",), ("--fix-wave", "--issue", "0"),
                     ("--issue", "7"), ()):
            with self.subTest(args=args):
                self.assertEqual(self.cli("release-record", *args)[1], 2)
                self.assertEqual(self.note.read_bytes(), before)


class WaveCheckTests(WaveRepo):
    def setUp(self) -> None:
        super().setUp()
        self.b = self.commit("#7: fix")
        self.c = self.commit("#8: fix")
        self.line = self.wave(7, 8)[0]["wave"]

    def edit(self, old: str, new: str) -> None:
        self.note.write_text(self.note.read_text().replace(old, new))

    def test_wave_is_used(self) -> None:
        out, code = self.check("--sha", self.c)
        self.assertEqual((out["ok"], out["reasons"], out["wave"], out["wave_ignored"], code), (True, [], self.line, None, 0))

    def test_code_after_wave_is_stale(self) -> None:
        d = self.commit("#9: other")
        out, _ = self.check("--sha", d)
        self.assertEqual((out["reasons"], out["wave"]), ([{"reason": "stale-audit", "paths": ["src/app.py"]}], self.line))

    def test_release_commit_after_wave_is_current(self) -> None:
        out, _ = self.check("--sha", self.release_commit())
        self.assertTrue(out["ok"], out)

    def test_tag_on_wave_then_release_commit(self) -> None:
        self.git("tag", "-a", "v1.0.0-rc.1", "-m", "x", self.c)
        for args in (("--sha", self.c), ("--tag", "v1.0.0-rc.1")):
            out, _ = self.check(*args)
            self.assertEqual((out["ok"], out["wave"]), (True, self.line), args)
        out, _ = self.check("--sha", self.release_commit())
        reason = out["reasons"][0]
        self.assertEqual((reason["reason"], out["wave"], out["wave_ignored"]), ("stale-audit", None, "released: v1.0.0-rc.1"))
        self.assertIn("src/app.py", reason["paths"])

    def test_other_tags(self) -> None:
        self.git("tag", "backup", self.c)
        d = self.release_commit()
        self.assertTrue(self.check("--sha", d)[0]["ok"])
        self.git("tag", "v2.0.0", self.c)
        self.git("tag", "v1.0.0", self.c)
        self.assertEqual(self.check("--sha", d)[0]["wave_ignored"], "released: v1.0.0")
        self.git("tag", "-d", "v1.0.0", "v2.0.0")
        self.git("tag", "v1.0.1", d)
        self.assertTrue(self.check("--sha", d)[0]["ok"])

    def test_tag_order_ignores_tag_sort_config(self) -> None:
        self.git("config", "tag.sort", "-refname")
        self.git("tag", "v1.0.0", self.c)
        self.git("tag", "v1.0.1", self.c)
        self.assertEqual(self.check("--sha", self.release_commit())[0]["wave_ignored"], "released: v1.0.0")

    def test_non_release_tag_has_wave_keys(self) -> None:
        self.git("tag", "foo", self.c)
        out, code = self.check("--tag", "foo")
        self.assertEqual((code, out["skipped"], out["wave"], out["wave_ignored"]), (0, "not-a-release-tag", None, None))

    def test_edited_wave_issues(self) -> None:
        self.edit(": #7, #8", ": #7")
        out, _ = self.check("--sha", self.c)
        self.assertEqual((out["reasons"][0]["reason"], out["wave"], out["wave_ignored"]), ("stale-audit", None, "foreign-commit"))
        self.edit(": #7\n", ": #7, #8, #30\n")
        self.assertEqual(self.check("--sha", self.c)[0]["wave_ignored"], "unfixed")

    def test_wave_not_ancestor(self) -> None:
        self.git("checkout", "-q", "-b", "side", self.a)
        side = self.commit("#7: side")
        self.git("checkout", "-q", "main")
        for sha in (side, self.a):
            with self.subTest(sha=sha):
                self.note.write_text(self.note.read_text().replace(self.line, self.line.replace(self.c, sha)))
                self.line = self.line.replace(self.c, sha)
                self.assertEqual(self.check("--sha", self.c)[0]["wave_ignored"], "not-ancestor")
                self.note.write_text(self.note.read_text().replace(self.line, self.line.replace(sha, self.c)))
                self.line = self.line.replace(sha, self.c)
        self.assertEqual(self.check("--sha", self.b)[0]["wave_ignored"], "not-ancestor")

    def test_newer_record_ignores_old_wave(self) -> None:
        run3 = f"- 2026-10-30 Run 3 at {self.c}: confirmed 0, needs validation 0, hardening 0, profile standard, scope {self.a}..{self.c}"
        self.note.write_text(self.note.read_text().rstrip("\n") + f"\n{run3}\n")
        self.run_meta("Run 3", self.c)
        out, _ = self.check("--sha", self.c)
        self.assertEqual((out["wave"], out["wave_ignored"], out["record"]), (None, None, run3))

    def test_plan_base_stays_the_record(self) -> None:
        out, _ = self.cli("release-plan")
        self.assertEqual((out["base"], out["scope"]), (self.a, f"{self.a}..{self.c}"))
