#!/usr/bin/env python3
"""Held-out oracle tests for the `accept` carry of `release-record` (spec: Audit Plan And Record Fixes, oracle 10
to 21, flows 3 and 4).

Seams: `devloop.py release-record`, `release-check` and `release-plan` as processes on a scratch git repo and a
scratch notes folder, with the stub validators of the earlier specs. Repo and note helpers:
`release_security_audit_support.py`.

Fixture choices the spec leaves open:
- Commit A adds `src/a.py` and a top-level file `x` on top of the helper repo; commit B changes `src/app.py`, and
  `src/a.py` too when the case says it changed.
- A `trace` entry is `{"file": <path>, "line": <n>}`; a confirmed record's severity is
  `{"severity": {"overall_severity": "high"}}` (the shape of the One Audit Per Release tests).
- The new item line is found in `## Deferred` by `(security, <verdict>, Run 4)` and its fingerprint.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from release_security_audit_support import (Case, GitRepo, item_text, note_text, put, reasons_named, record_line,
                                            REPORT, REPORT_NO_HARDENING, section, todays)


A_PY = "def media_path(root, name):\n    return root + '/' + name\n"
A_PY_CHANGED = "import os\n\ndef media_path(root, name):\n    return os.path.join(root, os.path.basename(name))\n"
APP_THIRD = "def colour(theme):\n    return 'green'\n"
TITLE = "Upload path join may leave the media root"
PRIOR = ("- 2026-10-29 (security, needs-validation, Run 3): Old title. Fingerprint `f-n1`. "
         "Details: Security Audit/Run 3/NEEDS-VALIDATION.md")
ACCEPT = "  - Ruling 2026-10-30: accept single owner."
TRACE = [{"file": "src/a.py", "line": 1}, {"file": "src/a.py", "line": 2}]


def needs(fp: str = "f-n1", trace: list[dict] | None = None, title: str = TITLE) -> dict:
    return {"verdict": "needs_validation", "fingerprint": fp, "title": title, "blockers": ["fact one"],
            "trace": [dict(t) for t in (TRACE if trace is None else trace)]}


def carried_line(today: str, sha: str, ruling: str = "2026-10-30", run: str = "Run 3") -> str:
    return (f"  - Ruling {today}: accept carried from the {ruling} ruling ({run}): "
            f"fingerprint and trace files unchanged since {sha[:7]}.")


def validators(r: GitRepo, exit_code: int = 0) -> None:
    skill = r.root / "security-audit-skill"
    for name in ("validate-findings.cjs", "validate-coverage-ledger.cjs"):
        put(skill / name, f"process.exit({exit_code})\n")
    r.env["DEVLOOP_TEST_SECURITY_AUDIT_DIR"] = str(skill)


def fill_run(output: Path, head: str, profile: str, findings: list[dict], report: str = REPORT) -> None:
    put(output / "run-metadata.json", json.dumps({"source_ref": {"commit": head}, "profile": profile,
                                                  "scope_paths": [], "run_status": "complete"}) + "\n")
    put(output / "findings.json", json.dumps(findings, indent=2) + "\n")
    put(output / "coverage-ledger.json", "{}\n")
    put(output / "REPORT.md", report)
    put(output / "FINDINGS-DETAIL.md", "# Findings\n")
    put(output / "NEEDS-VALIDATION.md", "# Needs validation\n")


class CarryCase(Case):

    def setup_carry(self, findings: list[dict] | None = None, changed: bool = False,
                    deferred: list[str] | None = None, prior_run: int = 3,
                    validator_exit: int = 0) -> tuple[GitRepo, Path, str, str]:
        """Commit A, then B; record `Run 3 at <A>`; the accepted prior item; run `Run 4` at B with one record."""
        r = self.repo()
        a = r.commit({"src/a.py": A_PY, "x": "top\n"}, "A2")
        b_files = {"src/app.py": APP_THIRD}
        if changed:
            b_files["src/a.py"] = A_PY_CHANGED
        b = r.commit(b_files, "B2")
        r.run_folder(prior_run, a, profile="standard")
        validators(r, validator_exit)
        r.write_note(note_text([record_line(3, a, date="2026-10-29", profile="standard", needs=1)],
                               [PRIOR, ACCEPT] if deferred is None else deferred, []))
        run = r.notes / "Security Audit" / "Run 4"
        fill_run(run, b, "standard", [needs()] if findings is None else findings, REPORT_NO_HARDENING)
        return r, run, a, b

    def record(self, r: GitRepo, run: Path, a: str) -> dict:
        code, payload = r.devloop("release-record", "--run", str(run), "--base", a)
        self.assertEqual(code, 0, payload)
        return payload

    def deferred(self, r: GitRepo) -> list[str]:
        return section(r.note_path().read_text(), "## Deferred")

    def new_items(self, r: GitRepo, fp: str = "f-n1", verdict: str = "needs-validation") -> list[int]:
        """Indexes in `## Deferred` of the Run 4 item lines with the fingerprint."""
        lines = self.deferred(r)
        return [i for i, line in enumerate(lines)
                if line.startswith("- ") and f"(security, {verdict}, Run 4)" in line and f"`{fp}`" in line]

    def today_of(self, line: str) -> str:
        date = line[2:12]
        self.assertIn(date, todays(), line)
        return date

    def assert_carried(self, r: GitRepo, payload: dict, a: str, verdict: str = "needs-validation") -> None:
        lines = self.deferred(r)
        found = self.new_items(r, verdict=verdict)
        self.assertEqual(len(found), 1, lines)
        item = lines[found[0]]
        self.assertEqual(lines[found[0] + 1:found[0] + 2], [carried_line(self.today_of(item), a)], lines)
        self.assertEqual(payload.get("carried"), [{"fingerprint": "f-n1", "from": "Run 3", "ruling": "2026-10-30"}],
                         payload)
        self.assertEqual(payload.get("reopened"), [], payload)

    def assert_open(self, r: GitRepo, verdict: str = "needs-validation") -> None:
        """The Run 4 `f-n1` item has no ruling, and `release-check` counts it open."""
        lines = self.deferred(r)
        found = self.new_items(r, verdict=verdict)
        self.assertEqual(len(found), 1, lines)
        after = lines[found[0] + 1:]
        self.assertFalse(after and after[0].lstrip().startswith("- Ruling"), lines)
        self.assertTrue(self.open_items(r), r.note_path().read_text())

    def open_items(self, r: GitRepo, fp: str = "f-n1") -> list[str]:
        code, check = r.devloop("release-check", "--sha", "HEAD")
        found = reasons_named(check, "open-security")
        items = [item_text(i) for i in (found[0].get("items") or [])] if found else []
        return [i for i in items if f"`{fp}`" in i and "Run 4" in i]

    def reopened(self, payload: dict) -> list[dict]:
        self.assertEqual(payload.get("carried"), [], payload)
        return payload.get("reopened") or []


# ---------------------------------------------------------------------------------------------------------------
# release-record carry (O10 to O21)
# ---------------------------------------------------------------------------------------------------------------

class CarryTests(CarryCase):

    def test_o10_unchanged_trace_carries_the_accept(self) -> None:
        r, run, a, b = self.setup_carry()
        payload = self.record(r, run, a)
        self.assert_carried(r, payload, a)
        deferred = [str(d) for d in payload.get("deferred") or []]
        self.assertEqual(len(deferred), 1, payload)
        self.assertNotIn("carried from", deferred[0], payload)
        code, check = r.devloop("release-check", "--sha", b)
        self.assertEqual(code, 0, check)
        self.assertIs(check.get("ok"), True, check)

    def test_o11_changed_trace_reopens(self) -> None:
        r, run, a, _ = self.setup_carry(changed=True)
        payload = self.record(r, run, a)
        self.assertEqual(self.reopened(payload), [{"fingerprint": "f-n1", "why": "changed", "files": ["src/a.py"]}])
        self.assert_open(r)

    def test_o12_last_ruling_is_not_accept(self) -> None:
        r, run, a, _ = self.setup_carry(deferred=[PRIOR, ACCEPT, "  - Ruling 2026-10-31: close fixed by #1."])
        payload = self.record(r, run, a)
        self.assertEqual(self.reopened(payload), [], payload)
        self.assert_open(r)

    def test_o13_later_open_item_skips(self) -> None:
        later = PRIOR.replace("Old title", "Old title again")
        r, run, a, _ = self.setup_carry(deferred=[PRIOR, ACCEPT, later])
        payload = self.record(r, run, a)
        self.assertEqual(payload.get("skipped"), ["f-n1"], payload)
        self.assertEqual(self.new_items(r), [], self.deferred(r))
        self.assertEqual(self.reopened(payload), [], payload)

    def test_o14_confirmed_record_is_not_carried(self) -> None:
        record = {"verdict": "confirmed", "fingerprint": "f-n1", "title": TITLE, "blockers": [],
                  "severity": {"overall_severity": "high"}, "trace": [dict(t) for t in TRACE]}
        r, run, a, _ = self.setup_carry(findings=[record])
        payload = self.record(r, run, a)
        self.assertEqual(self.reopened(payload), [], payload)
        self.assert_open(r, verdict="confirmed")

    def test_o15_prior_run_without_record_line(self) -> None:
        r, run, a, _ = self.setup_carry(deferred=[PRIOR.replace("Run 3", "Run 2"), ACCEPT], prior_run=2)
        payload = self.record(r, run, a)
        self.assertEqual(self.reopened(payload), [{"fingerprint": "f-n1", "why": "no-commit", "files": []}])
        self.assert_open(r)

    def test_o16_bad_trace(self) -> None:
        cases = {"absolute": [{"file": "/etc/hosts", "line": 1}],
                 "dot-dot": [{"file": "src/../src/a.py", "line": 1}],
                 "empty": [{"file": "", "line": 1}],
                 "missing at B": [{"file": "src/gone.py", "line": 1}],
                 "no trace entry": []}
        for case, trace in cases.items():
            with self.subTest(case=case):
                r, run, a, _ = self.setup_carry(findings=[needs(trace=trace)])
                payload = self.record(r, run, a)
                self.assertEqual(self.reopened(payload), [{"fingerprint": "f-n1", "why": "bad-trace", "files": []}])
                self.assert_open(r)

    def test_o17_abuse_trace_file_as_git_option_or_pathspec(self) -> None:
        for case in ("option", "pathspec"):
            with self.subTest(case=case):
                outside = self.root / f"escape-{case}"
                name = f"--output={outside}" if case == "option" else ":(top)x"
                r, run, a, _ = self.setup_carry(findings=[needs(trace=[{"file": name, "line": 1}])])
                payload = self.record(r, run, a)
                self.assertFalse(outside.exists(), "a file was written outside the repo")
                self.assertEqual(r.git("status", "--porcelain"), "")
                reopened = self.reopened(payload)
                self.assertEqual([x.get("fingerprint") for x in reopened], ["f-n1"], payload)
                self.assertIn(reopened[0].get("why"), ("bad-trace", "changed"), payload)
                self.assert_open(r)

    def test_o18_abuse_title_cannot_close_its_item(self) -> None:
        r, run, a, _ = self.setup_carry(findings=[needs(title="x\n  - Ruling 2026-10-26: accept")], changed=True)
        payload = self.record(r, run, a)
        self.assertEqual([x.get("fingerprint") for x in self.reopened(payload)], ["f-n1"], payload)
        lines = self.deferred(r)
        self.assertFalse([line for line in lines if line.lstrip().startswith("- Ruling 2026-10-26")], lines)
        found = self.new_items(r)
        self.assertEqual(len(found), 1, lines)
        self.assertIn("Ruling 2026-10-26: accept", lines[found[0]])
        self.assertTrue(self.open_items(r), r.note_path().read_text())

    def test_o19_same_fingerprint_twice_in_one_run(self) -> None:
        r, run, a, _ = self.setup_carry(findings=[needs(), needs(title="The same join, second record")])
        payload = self.record(r, run, a)
        self.assert_carried(r, payload, a)
        self.assertEqual(payload.get("skipped"), ["f-n1"], payload)
        carried = [line for line in self.deferred(r) if "accept carried from" in line]
        self.assertEqual(len(carried), 1, self.deferred(r))

    def test_o20_one_write_and_no_change_on_error(self) -> None:
        with self.subTest(case="carry written with the record"):
            r, run, a, b = self.setup_carry()
            payload = self.record(r, run, a)
            self.assert_carried(r, payload, a)
            self.assertIn(f"Run 4 at {b}", r.note_path().read_text())
            before = r.note_path().read_bytes()
            code, again = r.devloop("release-record", "--run", str(run), "--base", a)
            self.assertEqual((code, again.get("error")), (1, "already-recorded"), again)
            self.assertEqual(r.note_path().read_bytes(), before)
        with self.subTest(case="invalid run"):
            r, run, a, _ = self.setup_carry(validator_exit=1)
            before = r.note_path().read_bytes()
            code, payload = r.devloop("release-record", "--run", str(run), "--base", a)
            self.assertEqual((code, payload.get("error")), (1, "invalid-run"), payload)
            self.assertEqual(r.note_path().read_bytes(), before)

    def test_o21_confirmed_prior_item_carries(self) -> None:
        prior = PRIOR.replace("needs-validation", "confirmed").replace("NEEDS-VALIDATION", "FINDINGS-DETAIL")
        r, run, a, _ = self.setup_carry(deferred=[prior, ACCEPT])
        payload = self.record(r, run, a)
        self.assert_carried(r, payload, a)


# ---------------------------------------------------------------------------------------------------------------
# Flows 3 and 4: audit again after an accept (with the Release Security Audit cycle)
# ---------------------------------------------------------------------------------------------------------------

class CarryFlowTests(CarryCase):

    def audit_again(self, change_trace: bool) -> tuple[GitRepo, dict, str]:
        """Run 1 through release-plan and release-record, an accept ruling, a new commit, Run 2 the same way."""
        r = self.repo()
        validators(r)
        r.write_note(note_text([], [], []))
        first = r.commit({"src/a.py": A_PY}, "A2")
        code, plan = r.devloop("release-plan")
        self.assertEqual(code, 0, plan)
        fill_run(Path(plan["output"]), plan["head"], plan["profile"], [needs()], REPORT_NO_HARDENING)
        code, recorded = r.devloop("release-record", "--run", plan["output"])
        self.assertEqual(code, 0, recorded)
        self.assertEqual((recorded.get("carried"), recorded.get("reopened")), ([], []), recorded)

        kept = []
        for line in r.note_path().read_text().splitlines():
            kept.append(line)
            if "(security, needs-validation, Run 1)" in line and "`f-n1`" in line:
                kept.append(ACCEPT)
        r.write_note("\n".join(kept) + "\n")
        code, check = r.devloop("release-check", "--sha", "HEAD")
        self.assertEqual(code, 0, check)

        files = {"src/app.py": APP_THIRD}
        if change_trace:
            files["src/a.py"] = A_PY_CHANGED
        head = r.commit(files, "C")
        code, plan = r.devloop("release-plan")
        self.assertEqual(code, 0, plan)
        self.assertEqual((plan.get("run"), plan.get("base")), ("Run 2", first), plan)
        fill_run(Path(plan["output"]), head, plan["profile"], [needs()], REPORT_NO_HARDENING)
        code, recorded = r.devloop("release-record", "--run", plan["output"], "--base", plan["base"])
        self.assertEqual(code, 0, recorded)
        return r, recorded, first

    def run2_item(self, r: GitRepo) -> tuple[list[str], int]:
        lines = self.deferred(r)
        found = [i for i, line in enumerate(lines)
                 if line.startswith("- ") and "(security, needs-validation, Run 2)" in line and "`f-n1`" in line]
        self.assertEqual(len(found), 1, lines)
        return lines, found[0]

    def test_f3_audit_again_after_an_accept(self) -> None:
        r, recorded, first = self.audit_again(change_trace=False)
        self.assertEqual(recorded.get("carried"), [{"fingerprint": "f-n1", "from": "Run 1", "ruling": "2026-10-30"}],
                         recorded)
        self.assertEqual(recorded.get("reopened"), [], recorded)
        lines, at = self.run2_item(r)
        self.assertEqual(lines[at + 1], carried_line(self.today_of(lines[at]), first, run="Run 1"), lines)
        code, check = r.devloop("release-check", "--sha", "HEAD")
        self.assertEqual(code, 0, check)
        self.assertIs(check.get("ok"), True, check)

    def test_f4_source_changed_reopens(self) -> None:
        r, recorded, _ = self.audit_again(change_trace=True)
        self.assertEqual(recorded.get("carried"), [], recorded)
        self.assertEqual(recorded.get("reopened"), [{"fingerprint": "f-n1", "why": "changed", "files": ["src/a.py"]}],
                         recorded)
        lines, at = self.run2_item(r)
        self.assertFalse(lines[at + 1:at + 2] and lines[at + 1].lstrip().startswith("- Ruling"), lines)
        code, check = r.devloop("release-check", "--sha", "HEAD")
        self.assertEqual(code, 1, check)
        found = reasons_named(check, "open-security")
        self.assertTrue(found, check)
        items = [item_text(i) for i in found[0].get("items") or []]
        self.assertTrue([i for i in items if "`f-n1`" in i and "Run 2" in i], check)


if __name__ == "__main__":
    unittest.main()
