#!/usr/bin/env python3
"""Held-out flow tests (F1, F2) and text oracle tests (O20 to O22) of the spec One Audit Per Release.

Seams, fixture choices and shared helpers: `one_audit_per_release_support.py`. F3 and oracle 1 to 5:
`test_one_audit_per_release_record.py`; oracle 6 to 19: `test_one_audit_per_release_wave.py`.
"""

from __future__ import annotations

import json
import re
import unittest

from one_audit_per_release_support import bare, WaveCase
from release_security_audit_support import put, reasons_named, REPO_ROOT


RELEASE_REF = REPO_ROOT / "reference" / "release.md"
RELEASE_SKILL = REPO_ROOT / "skills" / "release" / "SKILL.md"
AUDIT_SKILL = REPO_ROOT / "skills" / "release-audit" / "SKILL.md"
CHECK_REF = REPO_ROOT / "reference" / "cli" / "release-check.md"
RECORD_REF = REPO_ROOT / "reference" / "cli" / "release-record.md"


def heading_body(text: str, heading: str) -> str:
    match = re.search(rf"(?ms)^{re.escape(heading)}[ \t]*\n(.*?)(?=^## |\Z)", text)
    return match.group(1) if match else ""


def step(text: str, n: int) -> str:
    match = re.search(rf"(?ms)^{n}\. .*?(?=^{n + 1}\. |^#|\Z)", text)
    return match.group(0) if match else ""


class FlowTests(WaveCase):

    def test_f1_one_audit_one_wave(self) -> None:
        """F1 (oracle 6, 8): record, two fix commits, `--fix-wave`, release commit, `release-check` ok."""
        r = self.wave_repo(recorded=False)
        r.validators()
        run = r.notes / "Security Audit" / "Run 2"
        put(run / "run-metadata.json", json.dumps({"source_ref": {"commit": r.A}, "profile": "quick",
                                                   "scope_paths": [], "run_status": "complete"}) + "\n")
        put(run / "findings.json", "[]\n")
        put(run / "coverage-ledger.json", "{}\n")
        put(run / "REPORT.md", "# Security audit report\n\n### Hardening (not findings)\n\nNone.\n")
        put(run / "FINDINGS-DETAIL.md", "# Findings\n")
        put(run / "NEEDS-VALIDATION.md", "# Needs validation\n")
        code, recorded = r.devloop("release-record", "--run", str(run))
        self.assertEqual(code, 0, recorded)
        self.assertIn(f"Run 2 at {r.A}", r.audits()[-1])

        r.code("#7: fix")
        c = r.code("#8: fix")
        code, check = r.check("--sha", c)
        self.assertEqual(code, 1, check)
        self.assertTrue(reasons_named(check, "stale-audit"), check)

        code, waved = r.fix_wave(7, 8)
        self.assertEqual(code, 0, waved)
        line = r.audits()[-1]
        self.assert_today_wave(line, c, [7, 8])
        self.assertEqual(bare(waved.get("wave")), bare(line), waved)

        e = r.release("1.1.0")
        code, check = r.check("--sha", e)
        self.assertEqual(code, 0, check)
        self.assertIs(check.get("ok"), True, check)
        self.assertEqual(check.get("reasons"), [], check)
        self.assert_wave(check, line)

    def test_f2_next_release_needs_a_new_audit(self) -> None:
        """F2 (oracle 9, 18): a tag on the wave, then `release-check` is stale again and `release-plan` covers
        the fix code."""
        r, line = self.waved()
        r.tag("v1.0.0-rc.1", r.C)
        code, check = r.check("--tag", "v1.0.0-rc.1")
        self.assertEqual(code, 0, check)
        self.assert_wave(check, line)

        e = r.release("1.0.0-rc.2")
        code, check = r.check("--sha", e)
        self.assertEqual(code, 1, check)
        stale = reasons_named(check, "stale-audit")
        self.assertTrue(stale, check)
        self.assertIn("src/app.py", stale[0].get("paths") or [], check)
        self.assertEqual(check.get("wave_ignored"), "released: v1.0.0-rc.1", check)

        r.validators()
        code, plan = r.devloop("release-plan")
        self.assertEqual(code, 0, plan)
        self.assertEqual(plan.get("base"), r.A, plan)
        self.assertEqual(plan.get("scope"), f"{r.A}..{e}", plan)


class TextTests(WaveCase):

    def assert_has(self, text: str, patterns: tuple[str, ...], where: str) -> None:
        for pattern in patterns:
            with self.subTest(where=where, pattern=pattern):
                self.assertRegex(text, re.compile(pattern, re.I | re.S))

    def test_o20_release_reference_section(self) -> None:
        body = heading_body(RELEASE_REF.read_text(), "## One audit, one fix wave")
        self.assertTrue(body.strip(), "reference/release.md has no `## One audit, one fix wave` section")
        self.assert_has(body, (r"\btag\b", r"\blow\b", r"informational", r"--fix-wave",
                               r"next[^.\n]{0,30}\btag", r"fix code", r"\bguards?\b", r"mistake", r"hardening"),
                        "reference/release.md")

    def test_o21_release_and_audit_skill_steps(self) -> None:
        third = step(RELEASE_SKILL.read_text(), 3)
        self.assertTrue(third, "skills/release/SKILL.md has no step 3")
        self.assert_has(third, (r"release-record --fix-wave", r"fix #", r"\baccept\b", r"\bclose\b"),
                        "skills/release/SKILL.md step 3")
        self.assert_has(AUDIT_SKILL.read_text(), (r"\bguards?\b", r"mistake", r"`low`"),
                        "skills/release-audit/SKILL.md")

    def test_o22_cli_references(self) -> None:
        check = CHECK_REF.read_text()
        self.assert_has(check, (r"`wave`", r"`wave_ignored`"), "reference/cli/release-check.md")
        record = RECORD_REF.read_text()
        self.assert_has(record, (r"--fix-wave", r"--issue", r"no-audit", r"wave-exists", r"not-descendant",
                                 r"foreign-commit", r"unfixed", r"`low`"), "reference/cli/release-record.md")


if __name__ == "__main__":
    unittest.main()
