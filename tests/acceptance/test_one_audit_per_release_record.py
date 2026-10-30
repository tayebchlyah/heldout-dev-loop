#!/usr/bin/env python3
"""Held-out oracle tests for the severity bar of `release-record` and the confirmed rulings of `release-check`
(spec: One Audit Per Release, oracle 1 to 5, flow 3).

Seams, fixture choices and shared helpers: `one_audit_per_release_support.py`.
"""

from __future__ import annotations

import unittest

from one_audit_per_release_support import bare, WaveCase
from release_security_audit_support import (BASE_NOTE, item_text, note_text, reasons_named, record_line, RecordCase,
                                            section, todays)


LOW_TITLE = "Verbose error page names the framework version"
MEDIUM_TITLE = "Session cookie lacks the secure flag"


def finding(fp: str, title: str, severity: str | None) -> dict:
    item = {"verdict": "confirmed", "fingerprint": fp, "title": title, "blockers": []}
    if severity is not None:
        item["severity"] = {"overall_severity": severity}
    return item


class SeverityBarTests(RecordCase):

    def low_lines(self, text: str) -> list[str]:
        return [line for line in section(text, "## Gardener") if "(security, low, Run 2)" in line]

    def assert_low_routed(self, severity: str) -> None:
        r, run = self.setup_record([finding("f-low", LOW_TITLE, severity)])
        code, payload = self.record(r, run)
        self.assertEqual(code, 0, payload)
        text = r.note_path().read_text()
        self.assertFalse([line for line in section(text, "## Deferred") if "f-low" in line], text)
        lines = self.low_lines(text)
        self.assertEqual(len(lines), 1, text)
        date = lines[0][2:12]
        self.assertIn(date, todays(), lines)
        self.assertEqual(lines[0], f"- {date} (security, low, Run 2): {LOW_TITLE}. Fingerprint `f-low`. "
                                   "Details: Security Audit/Run 2/FINDINGS-DETAIL.md.")
        self.assertEqual([bare(item) for item in payload.get("low") or []], [bare(lines[0])], payload)
        bullets = [line for line in section(text, "## Release audits") if line.startswith("- ")]
        self.assertIn("confirmed 1,", bullets[-1], bullets)

    def test_o01_low_confirmed_goes_to_gardener(self) -> None:
        self.assert_low_routed("low")

    def test_o02_informational_confirmed_goes_to_gardener(self) -> None:
        self.assert_low_routed("informational")

    def test_o03_other_severity_stays_in_deferred(self) -> None:
        for severity in ("medium", "high", "critical", None, "bogus"):
            with self.subTest(severity=severity):
                r, run = self.setup_record([finding("f-x", MEDIUM_TITLE, severity)])
                code, payload = self.record(r, run)
                self.assertEqual(code, 0, payload)
                text = r.note_path().read_text()
                deferred = [line for line in section(text, "## Deferred")
                            if "(security, confirmed, Run 2)" in line and "f-x" in line]
                self.assertEqual(len(deferred), 1, text)
                self.assertFalse(self.low_lines(text), text)
                self.assertIn("low", payload)
                self.assertFalse(payload["low"], payload)

    def test_o04_low_fingerprint_already_in_gardener(self) -> None:
        old = ("- 2026-10-20 (security, low, Run 1): Verbose error page. Fingerprint `f-low`. "
               "Details: Security Audit/Run 1/FINDINGS-DETAIL.md.")
        r, run = self.setup_record([finding("f-low", LOW_TITLE, "low")], note=note_text([], [], [old]))
        code, payload = self.record(r, run)
        self.assertEqual(code, 0, payload)
        text = r.note_path().read_text()
        self.assertEqual([line for line in text.splitlines() if "f-low" in line], [old], text)
        self.assertIn("f-low", payload.get("skipped") or [], payload)

    def test_f3_low_finding_does_not_block_the_release(self) -> None:
        """F3 (oracle 1, 3): one low and one medium confirmed record, then `release-check` reports only the
        medium item."""
        r, run = self.setup_record([finding("f-low", LOW_TITLE, "low"), finding("f-med", MEDIUM_TITLE, "medium")],
                                   note=BASE_NOTE)
        code, payload = self.record(r, run)
        self.assertEqual(code, 0, payload)
        self.assertEqual(len(payload.get("low") or []), 1, payload)
        code, check = r.devloop("release-check", "--sha", r.A)
        self.assertEqual(code, 1, check)
        found = reasons_named(check, "open-security")
        self.assertTrue(found, check)
        items = [item_text(i) for i in found[0].get("items") or []]
        self.assertEqual(len(items), 1, check)
        self.assertIn("f-med", items[0], check)
        self.assertFalse(reasons_named(check, "stale-audit"), check)


class ConfirmedRulingTests(WaveCase):

    def test_o05_confirmed_item_closes_with_a_ruling(self) -> None:
        item = "- 2026-10-25 (security, confirmed, Run 2): x"
        for ruling, still_open in (("  - Ruling 2026-10-30: accept low risk", False),
                                   ("  - Ruling 2026-10-30: close fixed by #7", False),
                                   ("  - Ruling 2026-10-30: fix #7 in a slice", True)):
            with self.subTest(ruling=ruling):
                r = self.wave_repo()
                r.write_note(note_text([record_line(2, r.A)], [item, ruling], []))
                code, check = r.check("--sha", r.A)
                found = reasons_named(check, "open-security")
                if still_open:
                    self.assertEqual(code, 1, check)
                    self.assertTrue(found, check)
                    self.assertTrue([i for i in found[0].get("items") or [] if bare(item) in item_text(i)], check)
                else:
                    self.assertEqual(code, 0, check)
                    self.assertEqual(check.get("reasons"), [], check)


if __name__ == "__main__":
    unittest.main()
