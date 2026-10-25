#!/usr/bin/env python3
"""Tests for release.py: parsing the project note (security items, rulings, audit records)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
import release  # noqa: E402

RECORD_RE_LINE = release.RECORD_RE
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

class ReleasePlanTests(unittest.TestCase):
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

    def test_first_plan_creates_run_folder(self) -> None:
        out, code = self.plan()
        folder = self.notes / "security-audit" / "run-1"
        self.assertEqual((code, out["run"], out["first"], out["profile"], out["budget"], out["scope"], out["base"], out["prior_runs"], out["head"]),
                         (0, "run-1", True, "quick", 100, "whole", None, [], self.b))
        self.assertEqual(out["output"], str(folder))
        self.assertTrue(folder.is_dir())

    def test_next_plan_scopes_from_record(self) -> None:
        for n in (1, 3):
            (self.notes / "security-audit" / f"run-{n}").mkdir(parents=True)
        self.record(self.a)
        out, code = self.plan()
        base = self.notes / "security-audit"
        self.assertEqual((code, out["run"], out["first"], out["profile"], out["budget"], out["scope"], out["base"]),
                         (0, "run-4", False, "standard", None, f"{self.a}..{self.b}", self.a))
        self.assertEqual(out["prior_runs"], [str(base / "run-1"), str(base / "run-3")])

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
        run = self.notes / "security-audit" / "run-1"
        run.mkdir(parents=True)
        (run / ".findings.json.icloud").write_text("")
        out, code = self.plan()
        self.assertEqual((code, out["error"], len(out["files"])), (1, "placeholder", 1))
        self.assertIn(".findings.json.icloud", out["files"][0])
        self.assertEqual([p.name for p in run.parent.iterdir()], ["run-1"])

    def test_dirty_tracked_file_blocks_untracked_does_not(self) -> None:
        (self.repo / "untracked.txt").write_text("u")
        self.assertEqual(self.plan()[1], 0)
        (self.notes / "security-audit" / "run-1").rmdir()
        (self.repo / "a.txt").write_text("changed")
        out, code = self.plan()
        self.assertEqual((code, out["error"], out["paths"]), (1, "dirty", ["a.txt"]))
        self.assertFalse((self.notes / "security-audit").exists() and any((self.notes / "security-audit").iterdir()))

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
        (self.notes / "security-audit" / "run-1").mkdir(parents=True)
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

    def make_run(self, findings: list | None = None, status: str = "complete", hardening: int = 3) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "run-metadata.json").write_text(json.dumps(
            {"run_status": status, "profile": "standard", "source_ref": {"commit": SHA}}))
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


STUBS = {
    "ok": 'print(json.dumps({"ok": True, "reasons": []}))',
    "fail": 'print(json.dumps({"ok": False, "reasons": [{"reason": "no-audit"}, {"reason": "open-security", "items": []}]})); sys.exit(1)',
    "slow": "time.sleep(10)",
    "quote": 'sys.stderr.write("bad \\"quote\\" and \\\\ here\\nsecond line\\n"); sys.exit(1)',
}


class ReleaseGuardTests(unittest.TestCase):
    """Both git-guard.sh copies, fed hook JSON on stdin, with a stub release-check CLI in the main worktree."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.a = self.commit("a")
        self.git("tag", "-a", "v1.0.0", "-m", "v1.0.0")
        self.git("tag", "release-1")
        self.b = self.commit("b")
        self.c = self.commit("c")
        self.git("update-ref", "refs/remotes/origin/main", self.c)
        self.git("checkout", "-q", "-b", "feat/x")
        self.cli = self.repo / ".agents" / "skills" / "dev-loop" / "scripts" / "devloop.py"
        self.calls = Path(tmp.name) / "calls.txt"

    def git(self, *args: str) -> str:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True, env=env).stdout.strip()

    def commit(self, name: str) -> str:
        (self.repo / name).write_text(name)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", name)
        return self.git("rev-parse", "HEAD")

    def stub(self, mode: str | None) -> None:
        if mode is None:
            self.cli.unlink(missing_ok=True)
            return
        self.cli.parent.mkdir(parents=True, exist_ok=True)
        self.cli.write_text("import json, sys, time\n"
                            f"open({str(self.calls)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n" + STUBS[mode] + "\n")

    def guard(self, harness: str, command: str, **extra: str) -> tuple[str, str, float]:
        """(decision, reason, seconds). Claude exit 2 is a deny with the stderr text."""
        payload = json.dumps({"cwd": str(self.repo), "tool_name": "Bash", "tool_input": {"command": command}, **extra})
        start = time.monotonic()
        done = subprocess.run([shutil.which({"claude": "bash", "codex": "sh"}[harness]), str(HERE.parents[2] / harness / "hooks" / "git-guard.sh")],
                              input=payload, capture_output=True, text=True, timeout=60)
        seconds = time.monotonic() - start
        if done.returncode == 2:
            return "deny", done.stderr, seconds
        if not done.stdout:
            return "allow", "", seconds
        out = json.loads(done.stdout)["hookSpecificOutput"]
        return out["permissionDecision"], out["permissionDecisionReason"], seconds

    def test_a_release_tag_push_carries_the_release_check_verdict(self) -> None:
        # Oracle 46, 47, 49, 50, 51, 52.
        for harness, mode, command, extra, decision, parts, absent in [
            ("claude", "fail", "git push origin v1.0.0", {}, "ask", ["a tag push", "release-check: no-audit, open-security"], None),
            ("claude", "ok", "git push origin v1.0.0", {}, "ask",
             ["Global rule: a tag push needs explicit permission each time; release-check: ok."], None),
            ("claude", "ok", "git push origin feat/x v1.0.0", {}, "ask", ["release-check: ok"], None),
            ("claude", "ok", "git push origin HEAD:refs/tags/v2.0.0", {}, "ask", ["release-check: ok"], None),
            ("claude", "ok", "git push origin v1.0.0 release-1", {}, "ask", ["a tag push"], "release-check"),
            ("claude", "ok", "git push origin refs/tags/v1.0.0", {}, "ask", ["release-check: ok"], None),
            ("claude", "ok", "git push origin tag v1.0.0", {}, "ask", ["release-check: ok"], None),
            ("codex", "fail", "git push origin v1.0.0", {}, "deny", ["a tag push", "release-check: no-audit"], None),
            ("claude", "fail", "git push origin v1.0.0", {"agent_id": "a1"}, "deny", ["subagent"], None),
            ("claude", None, "git push origin v1.0.0", {}, "ask", ["release-check failed: no CLI"], None),
            ("codex", None, "git push origin v1.0.0", {}, "deny", ["release-check failed"], None),
            ("claude", "ok", "git push --tags", {}, "ask", ["tag push"], "release-check"),
            ("claude", "ok", "git push --follow-tags origin feat/x", {}, "ask", ["tag push"], "release-check"),
            ("claude", "ok", "git push origin release-1", {}, "ask", ["a tag push"], "release-check"),
            ("claude", "ok", "git push origin 'refs/tags/*'", {}, "ask", [], "release-check"),
        ]:
            with self.subTest(harness=harness, mode=mode, command=command, extra=extra):
                self.stub(mode)
                got, reason, _ = self.guard(harness, command, **extra)
                self.assertEqual(got, decision, reason)
                for part in parts:
                    self.assertIn(part, reason)
                if absent:
                    self.assertNotIn(absent, reason)
        self.assertIn(f"release-check --repo {self.repo} --tag v1.0.0", self.calls.read_text())
        self.assertIn(f"release-check --repo {self.repo} --tag v2.0.0", self.calls.read_text())
        # A subagent is denied before the CLI runs.
        self.calls.unlink()
        self.stub("slow")
        got, reason, seconds = self.guard("claude", "git push origin v1.0.0", agent_id="a1")
        self.assertEqual((got, self.calls.exists()), ("deny", False), reason)
        self.assertLess(seconds, 3)

    def test_gh_release_create_checks_the_commit_it_tags(self) -> None:
        # Oracle 48: a local tag, else --target, else refs/remotes/origin/main; any tag name asks.
        self.stub("fail")
        for command, sha in [("gh release create v1.0.0 --notes x", self.a), (f"gh release create v2.0.0 --target {self.b}", self.b),
                             ("gh release create v2.0.0 --target=main --title 'v2 release'", self.c),
                             ("gh release create v2.0.0", self.c), ("gh release create release-1", self.a),
                             ("cd /tmp && command gh release create v2.0.0", None)]:
            with self.subTest(command=command):
                self.calls.unlink(missing_ok=True)
                got, reason, _ = self.guard("claude", command)
                self.assertEqual(got, "ask", reason)
                if sha:
                    self.assertIn(f"a release create at {sha} needs explicit permission each time; release-check: no-audit", reason)
                    self.assertEqual(self.calls.read_text(), f"release-check --repo {self.repo} --sha {sha}\n")
        got, reason, _ = self.guard("claude", "gh release create v2.0.0 --target nowhere")
        self.assertIn("release-check failed: target nowhere does not resolve", reason)
        got, reason, _ = self.guard("codex", "gh release create v1.0.0")
        self.assertEqual((got, "release-check: no-audit" in reason), ("deny", True), reason)
        got, reason, _ = self.guard("claude", "gh release create v1.0.0", agent_id="a1")
        self.assertEqual(got, "deny", reason)
        for command in ["gh pr create --title x", "gh release list", "gh -R o/r release view v1.0.0"]:
            self.assertEqual(self.guard("claude", command)[0], "allow", command)
        # gh flags around release, the new alias, and a word the parser cannot read.
        for harness, decision in [("claude", "ask"), ("codex", "deny")]:
            for command, part in [("gh release new v1.0.0", f"at {self.a}"), ("gh -R o/r release create v1.0.0", f"at {self.a}"),
                                  ("gh release -R o/r create v1.0.0", f"at {self.a}"),
                                  ("gh --repo=o/r release --repo o/r new v1.0.0", f"at {self.a}"),
                                  ("gh release $SUB v1.0.0", "release-check failed"),
                                  ("gh release -R o/r 'cr'$X v1.0.0", "release-check failed")]:
                with self.subTest(harness=harness, command=command):
                    got, reason, _ = self.guard(harness, command)
                    self.assertEqual(got, decision, reason)
                    self.assertIn(part, reason)

    def test_a_slow_or_broken_release_check_still_asks(self) -> None:
        # Oracle 48a and Review Focus 3: a timeout within the hook's 5 s, a stderr line with a quote stays valid JSON.
        self.stub("slow")
        got, reason, seconds = self.guard("claude", "git push origin v1.0.0")
        self.assertEqual(got, "ask", reason)
        self.assertIn("release-check failed: timeout", reason)
        self.assertLess(seconds, 5)
        self.stub("quote")
        for harness, decision in [("claude", "ask"), ("codex", "deny")]:
            got, reason, _ = self.guard(harness, "gh release create v1.0.0")
            self.assertEqual(got, decision, reason)
            self.assertIn('release-check failed: bad "quote" and \\ here', reason)
            self.assertNotIn("second line", reason)


if __name__ == "__main__":
    unittest.main()
