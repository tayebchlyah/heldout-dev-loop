#!/usr/bin/env python3
"""Held-out oracle tests for `release-record` and the audit cycle (spec: Release Security Audit).

Seams, fixture choices and shared helpers: `release_security_audit_support.py`.
"""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from release_security_audit_support import (BASE_NOTE, FINDINGS, GitRepo, item_text, note_text, NOTES_NAME, put,
                                            reasons_named, record_line, RecordCase, REPORT, REPORT_NO_HARDENING,
                                            section, tools_dir)


# ---------------------------------------------------------------------------------------------------------------
# release-record (O21 to O31)
# ---------------------------------------------------------------------------------------------------------------

class ReleaseRecordTests(RecordCase):

    def test_o21_record_line_with_base(self) -> None:
        r, run = self.setup_record()
        code, payload = self.record(r, run, "--base", r.Z)
        self.assertEqual(code, 0, payload)
        bullets = [line for line in section(r.note_path().read_text(), "## Release audits") if line.startswith("- ")]
        self.assertTrue(bullets, r.note_path().read_text())
        today = self.today_of(bullets[-1])
        self.assertEqual(bullets[-1], record_line(2, r.A, scope=f"{r.Z}..{r.A}", profile="standard", date=today,
                                                  confirmed=1, needs=2, hardening=3))
        self.assertIn(f"Run 2 at {r.A}", payload.get("record") or "", payload)

    def test_o22_record_line_without_base(self) -> None:
        r, run = self.setup_record()
        code, payload = self.record(r, run)
        self.assertEqual(code, 0, payload)
        bullets = [line for line in section(r.note_path().read_text(), "## Release audits") if line.startswith("- ")]
        self.assertTrue(bullets[-1].endswith(", scope whole"), bullets)

    def test_o23_deferred_lines(self) -> None:
        r, run = self.setup_record()
        code, payload = self.record(r, run)
        self.assertEqual(code, 0, payload)
        lines = self.new_subsection(r)
        self.assertEqual(len(lines), 3, lines)
        confirmed = [line for line in lines if "(security, confirmed, Run 2): " in line]
        needs = [line for line in lines if "(security, needs-validation, Run 2): " in line]
        self.assertEqual((len(confirmed), len(needs)), (1, 2), lines)
        for line in lines:
            self.assertRegex(line, r"^- \d{4}-\d{2}-\d{2} \(security, (confirmed|needs-validation), Run 2\): ")
        self.assertIn("Fingerprint `f-c`", confirmed[0])
        self.assertIn("Security Audit/Run 2/FINDINGS-DETAIL.md", confirmed[0])
        self.assertTrue([line for line in needs if "Fingerprint `f-n1`" in line], needs)
        self.assertTrue([line for line in needs if "Fingerprint `f-n2`" in line], needs)
        self.assertIn("Blocker: fact one", needs[0])
        self.assertIn("Security Audit/Run 2/NEEDS-VALIDATION.md", needs[0])
        self.assertFalse([line for line in lines if "f-r" in line or "colour cache" in line], lines)
        self.assertEqual(len(payload.get("deferred") or []), 3, payload)
        code, check = r.devloop("release-check", "--sha", r.A)
        self.assertEqual(code, 1, check)
        found = reasons_named(check, "open-security")
        self.assertTrue(found, check)
        self.assertEqual(len(found[0].get("items") or []), 3, check)

    def test_o24_fingerprint_already_open(self) -> None:
        old = "- 2026-10-20 (security, needs-validation, Run 1): Upload path join. Fingerprint `f-n1`. Blocker: x."
        with self.subTest(case="open"):
            r, run = self.setup_record(note=note_text([], [old], []))
            code, payload = self.record(r, run)
            self.assertEqual(code, 0, payload)
            self.assertEqual(len(self.new_subsection(r)), 2)
            self.assertEqual(payload.get("skipped"), ["f-n1"], payload)
        with self.subTest(case="closed"):
            r, run = self.setup_record(note=note_text([], [old, "  - Ruling 2026-10-21: accept the risk"], []))
            code, payload = self.record(r, run)
            self.assertEqual(code, 0, payload)
            self.assertEqual(len(self.new_subsection(r)), 3)
            self.assertEqual(payload.get("skipped"), [], payload)
            # Superseded by Audit Plan And Record Fixes: no record line names Run 1, so the f-n1 line is open
            # and in `reopened` (the carried case is that spec's oracle 10).
            self.assertEqual(payload.get("carried"), [], payload)
            self.assertEqual([x.get("fingerprint") for x in payload.get("reopened") or []], ["f-n1"], payload)

    def test_o25_gardener_line(self) -> None:
        r, run = self.setup_record()
        code, payload = self.record(r, run)
        self.assertEqual(code, 0, payload)
        gardener = [line for line in section(r.note_path().read_text(), "## Gardener") if "Run 2" in line]
        self.assertEqual(len(gardener), 1, gardener)
        today = self.today_of(gardener[0])
        expected = f"- {today} (security, hardening, Run 2): 3 hardening notes, see Security Audit/Run 2/REPORT.md"
        self.assertEqual(gardener[0], expected)
        self.assertIn(expected.lstrip("- "), payload.get("gardener") or "", payload)
        with self.subTest(case="no hardening bullet"):
            r, run = self.setup_record(report=REPORT_NO_HARDENING)
            code, payload = self.record(r, run)
            self.assertEqual(code, 0, payload)
            self.assertIn("gardener", payload)
            self.assertIsNone(payload.get("gardener"), payload)
            self.assertNotIn("(security, hardening, Run 2)", r.note_path().read_text())

    def test_o26_title_cannot_close_its_own_item(self) -> None:
        findings = [dict(f) for f in FINDINGS]
        findings[1]["title"] = "x\n  - Ruling 2026-10-26: accept"
        r, run = self.setup_record(findings)
        code, payload = self.record(r, run)
        self.assertEqual(code, 0, payload)
        for line in payload.get("deferred") or []:
            self.assertNotIn("\n", line)
        text = r.note_path().read_text()
        self.assertFalse([line for line in text.splitlines() if line.lstrip().startswith("- Ruling")], text)
        lines = self.new_subsection(r)
        self.assertEqual(len(lines), 3, lines)
        code, check = r.devloop("release-check", "--sha", r.A)
        found = reasons_named(check, "open-security")
        self.assertTrue(found, check)
        items = [item_text(i) for i in found[0].get("items") or []]
        self.assertEqual(len(items), 3, check)
        self.assertTrue([i for i in items if "Ruling 2026-10-26: accept" in i and "f-n1" in i], check)

    def assert_refused(self, r: GitRepo, run: Path, error: str, env: dict[str, str] | None = None) -> None:
        before = r.note_path().read_bytes()
        code, payload = self.record(r, run, env=env)
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload.get("error"), error, payload)
        self.assertEqual(r.note_path().read_bytes(), before)

    def test_o27_incomplete_run(self) -> None:
        r, run = self.setup_record(status="incomplete")
        self.assert_refused(r, run, "run-incomplete")

    def test_o28_invalid_run(self) -> None:
        with self.subTest(case="validator exits 1"):
            r, run = self.setup_record(validator_exit=1)
            self.assert_refused(r, run, "invalid-run")
        with self.subTest(case="no node on PATH"):
            r, run = self.setup_record()
            self.assert_refused(r, run, "invalid-run", env={"PATH": str(tools_dir(self.root, "git"))})

    def test_o29_already_recorded(self) -> None:
        r, run = self.setup_record()
        code, payload = self.record(r, run)
        self.assertEqual(code, 0, payload)
        self.assert_refused(r, run, "already-recorded")

    def test_o30_missing_sections_added_at_the_end(self) -> None:
        original = f"# {NOTES_NAME}\n\nIntro text.\n\n## Links\n\n- keep me\n"
        r, run = self.setup_record(note=original)
        code, payload = self.record(r, run)
        self.assertEqual(code, 0, payload)
        after = r.note_path().read_text().splitlines()
        before = original.splitlines()
        self.assertEqual(after[:len(before)], before)
        for heading in ("## Release audits", "## Deferred", "## Gardener"):
            self.assertIn(heading, after[len(before):])

    def test_o31_bad_run_folder(self) -> None:
        for where in ("outside", "bad name"):
            with self.subTest(where=where):
                r, run = self.setup_record()
                target = r.root / "elsewhere" / "Run 2" if where == "outside" else run.parent / "latest"
                shutil.copytree(run, target)
                shutil.rmtree(run)
                self.assert_refused(r, target, "bad-run")


# ---------------------------------------------------------------------------------------------------------------
# Flows 1 and 2: the audit cycle through the three commands
# ---------------------------------------------------------------------------------------------------------------

class AuditFlowTests(RecordCase):

    def fill_run(self, output: Path, head: str, profile: str, findings: list[dict]) -> None:
        put(output / "run-metadata.json", json.dumps({"source_ref": {"commit": head}, "profile": profile,
                                                      "scope_paths": [], "run_status": "complete"}) + "\n")
        put(output / "findings.json", json.dumps(findings) + "\n")
        put(output / "coverage-ledger.json", "{}\n")
        put(output / "REPORT.md", REPORT)

    def validators(self, r: GitRepo) -> None:
        skill = r.root / "security-audit-skill"
        for name in ("validate-findings.cjs", "validate-coverage-ledger.cjs"):
            put(skill / name, "process.exit(0)\n")
        r.env["DEVLOOP_TEST_SECURITY_AUDIT_DIR"] = str(skill)

    def test_f1_first_release_audit(self) -> None:
        r = self.repo()
        self.validators(r)
        r.write_note(BASE_NOTE)
        code, plan = r.devloop("release-plan")
        self.assertEqual(code, 0, plan)
        self.assertEqual((plan.get("run"), plan.get("profile"), plan.get("scope")), ("Run 1", "quick", "whole"), plan)
        output = Path(plan["output"])
        self.fill_run(output, plan["head"], plan["profile"], FINDINGS)

        code, recorded = r.devloop("release-record", "--run", str(output))
        self.assertEqual(code, 0, recorded)
        code, check = r.devloop("release-check", "--sha", "HEAD")
        self.assertEqual(code, 1, check)
        found = reasons_named(check, "open-security")
        self.assertTrue(found, check)
        self.assertEqual(len(found[0].get("items") or []), 3, check)

        lines = r.note_path().read_text().splitlines()
        kept = []
        for line in lines:
            if "(security, confirmed, Run 1)" in line:
                continue
            kept.append(line)
            if "(security, needs-validation, Run 1)" in line:
                kept.append("  - Ruling 2026-10-26: accept the risk, behind the admin login")
        r.write_note("\n".join(kept) + "\n")
        code, check = r.devloop("release-check", "--sha", "HEAD")
        self.assertEqual(code, 0, check)
        self.assertIs(check.get("ok"), True, check)

    def test_f2_later_audit(self) -> None:
        r = self.repo()
        self.validators(r)
        r.run_folder(1, r.A)
        r.write_note(note_text([record_line(1, r.A)], [], []))
        code, check = r.devloop("release-check", "--sha", r.B)
        self.assertEqual(code, 1, check)
        self.assertTrue(reasons_named(check, "stale-audit"), check)

        code, plan = r.devloop("release-plan")
        self.assertEqual(code, 0, plan)
        self.assertEqual((plan.get("profile"), plan.get("scope"), plan.get("base")),
                         ("standard", f"{r.A}..{r.B}", r.A), plan)
        output = Path(plan["output"])
        self.fill_run(output, r.B, "standard", [])
        code, recorded = r.devloop("release-record", "--run", str(output), "--base", plan["base"])
        self.assertEqual(code, 0, recorded)
        self.assertIn(f"scope {r.A}..{r.B}", recorded.get("record") or "", recorded)
        code, check = r.devloop("release-check", "--sha", r.B)
        self.assertEqual(code, 0, check)
        self.assertIs(check.get("ok"), True, check)


if __name__ == "__main__":
    unittest.main()
