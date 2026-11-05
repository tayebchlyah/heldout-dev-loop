"""`devloop.py release-record`: the `accept` carry (release_carry.py)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import release
from test_release import DEVLOOP
from test_release_record import VALIDATOR

ITEM = "- 2026-10-29 (security, needs-validation, Run 3): Old title. Fingerprint `f-n1`. Details: x."
ACCEPT = "  - Ruling 2026-10-30: accept single owner."
TRACE = [{"file": "src/a.py", "line": 1}, {"file": "src/a.py", "line": 2}]


def record(**kw) -> dict:
    return {"verdict": "needs_validation", "fingerprint": "f-n1", "title": "New title", "blockers": ["b"], "trace": TRACE, **kw}


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class ReleaseCarryTests(unittest.TestCase):
    def case(self, change: bool = False, deferred: list[str] | None = None, record_run: str = "Run 3", record_sha: str | None = None,
             findings: list | None = None, repo_arg: str | None = None, base: str | None = "A", check: bool = False,
             records: list[str] | None = None, run_name: str = "Run 4") -> tuple[dict, int, str, list[str]]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo, notes, skill = root / "repo", root / "Demo", root / "skill"
        for folder in (repo, notes, skill, repo / "src"):
            folder.mkdir()
        for name in ("validate-findings.cjs", "validate-coverage-ledger.cjs"):
            (skill / name).write_text(VALIDATOR)

        def git(*args: str) -> str:
            return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                                  capture_output=True, text=True, check=True).stdout.strip()

        git("init", "-q", "-b", "main")
        (repo / "src" / "a.py").write_text("x = 1\n")
        (repo / "b.py").write_text("b = 1\n")
        (repo / "src" / "l.py").symlink_to("a.py")
        git("add", "-A")
        git("commit", "-q", "-m", "A")
        a = git("rev-parse", "HEAD")
        (repo / "b.txt").write_text("b\n")
        (repo / "b.py").write_text("b = 2\n")
        if change:
            (repo / "src" / "a.py").write_text("x = 2\n")
        git("add", "-A")
        git("commit", "-q", "-m", "B")
        b = git("rev-parse", "HEAD")
        lines = [ITEM, ACCEPT] if deferred is None else deferred
        note = notes / "Project Note.md"
        note.write_text("## Deferred\n\n" + "".join(f"{line}\n" for line in lines) + "\n## Gardener\n\n## Release audits\n"
                        f"- 2026-10-29 {record_run} at {record_sha or a}: confirmed 0, needs validation 1, hardening 0, profile quick, scope whole\n"
                        + "".join(f"{r.replace('{A}', a).replace('{B}', b)}\n" for r in records or []))
        run = notes / "Security Audit" / run_name
        run.mkdir(parents=True)
        (run / "run-metadata.json").write_text(json.dumps({"run_status": "complete", "profile": "standard", "source_ref": {"commit": b}}))
        (run / "coverage-ledger.json").write_text("[]")
        (run / "REPORT.md").write_text("# Report\n")
        (run / "findings.json").write_text(json.dumps([record()] if findings is None else findings))
        env = {**os.environ, "DEV_LOOP_NOTES": str(notes), "DEVLOOP_TEST_SECURITY_AUDIT_DIR": str(skill)}
        args = ["--base", {"A": a, "B": b}[base]] if base else []
        self.a, self.b, self.before = a, b, note.read_bytes()
        done = subprocess.run([sys.executable, str(DEVLOOP), "release-record", "--repo", str(repo / repo_arg) if repo_arg else str(repo), "--run", str(run), *args],
                              capture_output=True, text=True, env=env)
        if check:
            checked = subprocess.run([sys.executable, str(DEVLOOP), "release-check", "--repo", str(repo), "--sha", b],
                                     capture_output=True, text=True, env=env)
            self.checked = json.loads(checked.stdout)
        text = note.read_text()
        prints = [i["fingerprint"] for i in release.security_items(text)[0]]
        return json.loads(done.stdout or "{}"), done.returncode, text, prints

    def test_unchanged_trace_carries_the_accept(self) -> None:
        today = date.today().isoformat()
        rows = [("default", {}), ("confirmed prior", {"deferred": [ITEM.replace("needs-validation", "confirmed"), ACCEPT]}),
                ("subfolder repo", {"repo_arg": "src"})]
        for name, kw in rows:
            with self.subTest(name):
                out, code, text, prints = self.case(check=name == "default", **kw)
                lines = text.splitlines()
                at = lines.index(out["deferred"][0])
                want = f"  - Ruling {today}: accept carried from the 2026-10-30 ruling (Run 3): fingerprint and trace files unchanged since {self.a[:7]}."
                self.assertEqual((code, lines[at + 1]), (0, want))
                self.assertEqual((out["carried"], out["reopened"]), ([{"fingerprint": "f-n1", "from": "Run 3", "ruling": "2026-10-30"}], []))
                self.assertEqual(len(out["deferred"]), 1)
                self.assertNotIn("Ruling", out["deferred"][0])
                self.assertNotIn("f-n1", prints)
                if name == "default":
                    self.assertNotIn("open-security", [r.get("reason") for r in self.checked.get("reasons", [])])

    def test_not_carried(self) -> None:
        close = "  - Ruling 2026-10-31: close fixed by #1."
        reopen = lambda why, files=(): [{"fingerprint": "f-n1", "why": why, "files": list(files)}]  # noqa: E731
        rows = [
            ("changed", {"change": True}, reopen("changed", ["src/a.py"])),
            ("close after accept", {"deferred": [ITEM, ACCEPT, close]}, []),
            ("confirmed record", {"findings": [record(verdict="confirmed", severity={"overall_severity": "high"})]}, []),
            ("no record line", {"deferred": [ITEM.replace("Run 3", "Run 2"), ACCEPT]}, reopen("no-commit")),
            ("unknown prior sha", {"record_sha": "c" * 40, "base": None}, reopen("no-commit")),
        ]
        bad = [[{"file": "/abs/a.py"}], [{"file": "src/../src/a.py"}], [{"file": ""}], [{"file": "src/gone.py"}], [{"file": "src"}],
               None, [], "src/a.py", ["src/a.py"], [{"file": 3}], [{"file": "\ud800"}]]
        for trace in bad:
            finding = record(trace=trace)
            if trace is None:
                del finding["trace"]
            rows.append((f"bad-trace {trace!r}", {"findings": [finding]}, reopen("bad-trace")))
        rows.append(("symlink", {"change": True, "findings": [record(trace=[{"file": "src/l.py"}])]}, reopen("bad-trace")))
        for name, kw, reopened in rows:
            with self.subTest(name):
                out, code, _, prints = self.case(**kw)
                self.assertEqual((code, out["carried"], out["reopened"], "f-n1" in prints), (0, [], reopened, True))

    def test_waived_and_rc_scoped_items_are_skipped_never_carried(self) -> None:
        for sub in ("  - Ruling 2026-10-30: waive v3.0.0-rc.5 needs a live check", "  - Ruling 2026-10-30: accept for rc.4 only. Validate later."):
            with self.subTest(sub=sub):
                out, code, text, prints = self.case(deferred=[ITEM, sub])
                self.assertEqual((code, out["skipped"], out["carried"], out["deferred"], "f-n1" in prints, text.count("accept carried")),
                                 (0, ["f-n1"], [], [], True, 0))

    def test_carry_chain_compares_against_the_human_ruling(self) -> None:
        run4 = "- 2026-10-31 (security, needs-validation, Run 4): Again. Fingerprint `f-n1`."
        carried = "  - Ruling 2026-10-31: accept carried from the 2026-10-30 ruling (Run 3): fingerprint and trace files unchanged since aaaaaaa."
        record4 = "- 2026-10-31 Run 4 at {B}: confirmed 0, needs validation 1, hardening 0, profile quick, scope whole"
        both = [{"file": "src/a.py"}, {"file": "b.py"}]
        deferred = [ITEM, ACCEPT, run4, carried]
        out, code, _, _ = self.case(deferred=deferred, records=[record4], run_name="Run 5", base="B", findings=[record(trace=both)])
        self.assertEqual((code, out["carried"], out["reopened"]), (0, [], [{"fingerprint": "f-n1", "why": "changed", "files": ["b.py"]}]))
        out, code, text, _ = self.case(deferred=deferred, records=[record4], run_name="Run 5", base="B")
        want = f"  - Ruling {date.today().isoformat()}: accept carried from the 2026-10-30 ruling (Run 3): fingerprint and trace files unchanged since {self.a[:7]}."
        self.assertEqual((code, out["carried"], want in text.splitlines()), (0, [{"fingerprint": "f-n1", "from": "Run 3", "ruling": "2026-10-30"}], True))
        only_carried = ACCEPT.replace("accept single owner", "accept carried from the x")
        out, code, _, _ = self.case(deferred=[ITEM, only_carried, run4, carried], records=[record4], run_name="Run 5", base="B")
        self.assertEqual((code, out["carried"], out["reopened"]), (0, [], [{"fingerprint": "f-n1", "why": "no-commit", "files": []}]))

    def test_abuse_trace_and_title(self) -> None:
        for file in ("--output=LEAK", ":(top)src/a.py"):
            with self.subTest(file):
                out, code, _, prints = self.case(findings=[record(trace=[{"file": file.replace("LEAK", str(Path(tempfile.gettempdir()) / "carry-leak"))}])])
                self.assertEqual((code, out["carried"], "f-n1" in prints), (0, [], True))
                self.assertIn(out["reopened"][0]["why"], ("bad-trace", "changed"))
                self.assertFalse((Path(tempfile.gettempdir()) / "carry-leak").exists())
        out, code, text, prints = self.case(change=True, findings=[record(title="New\n  - Ruling 2026-10-26: accept")])
        self.assertEqual((code, len(out["deferred"]), "f-n1" in prints), (0, 1, True))
        self.assertEqual(text.count("Ruling 2026-10-26"), 1)

    def test_open_item_and_duplicate_are_skipped(self) -> None:
        again = "- 2026-10-29 (security, needs-validation, Run 3): Again. Fingerprint `f-n1`."
        out, _, _, _ = self.case(deferred=[ITEM, ACCEPT, again])
        self.assertEqual((out["skipped"], out["deferred"], out["carried"]), (["f-n1"], [], []))
        out, _, text, _ = self.case(findings=[record(), record(title="Twice")])
        self.assertEqual((out["skipped"], len(out["carried"]), text.count("accept carried")), (["f-n1"], 1, 1))

    def test_error_leaves_the_note_unchanged(self) -> None:
        out, code, text, _ = self.case(base="B")
        self.assertEqual((code, out["error"]), (1, "bad-base"))
        self.assertEqual(text.encode(), self.before)


if __name__ == "__main__":
    unittest.main()
