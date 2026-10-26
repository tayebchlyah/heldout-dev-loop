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
        self.run_dir = self.notes / "security-audit" / "run-2"
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
        want = (f"- {self.today} run-2 at {SHA}: confirmed 1, needs validation 2, hardening 3, "
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
        self.assertIn(f"### Release audit run-2, {self.today}", text)
        self.assertEqual(sum("(security, confirmed, run-2)" in l for l in out["deferred"]), 1)
        self.assertEqual(sum("(security, needs-validation, run-2)" in l for l in out["deferred"]), 2)
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
        self.note.write_text(f"## Deferred\n\n- 2026-01-01 (security, needs-validation, run-1): Old. Fingerprint `f-n1`.\n\n## Release audits\n")
        out, _ = self.record()
        self.assertEqual((len(out["deferred"]), out["skipped"]), (2, ["f-n1"]))
        self.note.write_text("## Deferred\n\n- 2026-01-01 (security, needs-validation, run-1): Old. Fingerprint `f-n1`.\n  - Ruling 2026-01-02: accept fine\n")
        out, _ = self.record()
        self.assertEqual((len(out["deferred"]), out["skipped"]), (3, []))

    def test_gardener_line(self) -> None:
        out, _ = self.record()
        want = f"- {self.today} (security, hardening, run-2): 3 hardening notes, see security-audit/run-2/REPORT.md"
        self.assertEqual(out["gardener"], want)
        self.assertEqual(self.lines()[self.lines().index("## Gardener") + 1:][:2], [want, ""])
        self.make_run(hardening=0)
        self.note.write_text("## Deferred\n\n## Gardener\n\n## Release audits\n")
        out, _ = self.record()
        self.assertIsNone(out["gardener"])
        self.assertNotIn("hardening, run-2", self.note.read_text())

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

    def with_record(self, sha: str, run: str = "run-1") -> None:
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
        self.assertEqual(release.current_record(text)["run"], "run-2")
        self.assertEqual(len(release.security_items(text)[0]), 3)

    def test_existing_lines_stay(self) -> None:
        self.note.write_text("## Deferred\n- keep me\n\n## Gardener\n- gardener keep\n\n## Release audits\n- old line\n\n## Tail\ntext\n")
        self.record()
        text = self.note.read_text()
        for kept in ("- keep me", "- gardener keep", "- old line", "## Tail\ntext\n"):
            self.assertIn(kept, text)

    def test_bad_run_folder(self) -> None:
        for folder in (self.root / "elsewhere" / "run-2", self.notes / "security-audit" / "other", self.notes / "security-audit" / "run-2" / "sub"):
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
        other = self.notes / "security-audit" / "run-3"
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
