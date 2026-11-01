#!/usr/bin/env python3
"""Tests for `audit-run report` and `release-record` on a run the tools made."""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
sys.path.insert(1, str(HERE))
import release  # noqa: E402
from audit_fixtures import AuditRunCase  # noqa: E402

FILES = ("REPORT.md", "FINDINGS-DETAIL.md", "NEEDS-VALIDATION.md")


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class AuditReportTests(AuditRunCase):
    def summary(self, text: str) -> str:
        path = self.root / "summary.md"
        path.write_text(text)
        return str(path)

    def titled(self, title: str) -> None:
        base = self.record
        self.record = lambda fp, verdict, trace=("a.py",): {**base(fp, verdict, trace), "title": title}

    def reported(self, summary: str = "POSTURE-TEXT\n") -> str:
        out, code = self.step("findings")
        self.assertEqual(code, 0, out)
        out, code = self.step("report", "--summary", self.summary(summary))
        self.assertEqual((code, out), (0, {"files": [str(self.path(name).resolve()) for name in FILES]}))
        return self.path("REPORT.md").read_text()

    def test_not_closed(self) -> None:
        self.closed_flow()
        out, code = self.step("report", "--summary", self.summary("x"))
        self.assertEqual((code, out["error"]), (1, "not-closed"))
        self.assertFalse(self.path("REPORT.md").exists())

    def test_no_summary(self) -> None:
        self.closed_flow()
        self.step("findings")
        out, code = self.step("report", "--summary", str(self.root / "missing.md"))
        self.assertEqual((code, out["error"]), (1, "no-summary"))

    def test_three_files(self) -> None:
        self.titled("Line one\nLine two")
        self.closed_flow(hardening=(["note a", "note b"], ["note a"]))
        report = self.reported()
        self.assertTrue(report.startswith(f"# {self.path('.').name} security audit report\n"))
        self.assertIn("POSTURE-TEXT", report.split("## Security posture", 1)[1].split("\n## ", 1)[0])
        hard = report.split("## Hardening notes", 1)[1].split("\n## ", 1)[0]
        self.assertEqual([line for line in hard.splitlines() if line.strip()], ["- note a", "- note b"])
        for name in FILES:
            self.assertNotIn("Line one\nLine two", self.path(name).read_text())
        self.assertIn("### Finding 1: Line one Line two", report)
        self.assertIn("### Finding 1: Line one Line two", self.path("FINDINGS-DETAIL.md").read_text())
        self.assertNotIn("f-1", self.path("NEEDS-VALIDATION.md").read_text())

    def test_needs_validation_file(self) -> None:
        self.closed_flow(verdict="needs_validation")
        report = self.reported()
        self.assertIn("f-1", self.path("NEEDS-VALIDATION.md").read_text())
        self.assertNotIn("f-1", self.path("FINDINGS-DETAIL.md").read_text())
        self.assertIn("## Confirmed findings\n\nNone.", report)

    def test_release_record_accepts_a_tool_run(self) -> None:
        self.closed_flow(hardening=(["note a", "note b"],))
        self.reported()
        out, code = self.devloop("release-record", "--run", str(self.path(".")), "--repo", str(self.repo))
        self.assertEqual(code, 0, out)
        self.assertIn("hardening 2", (self.notes / "Project Note.md").read_text())

    def test_hardening_note_with_odd_line_breaks_is_one_bullet(self) -> None:
        self.closed_flow(hardening=(["a\x0b- b\x0b- c"],))
        self.reported()
        self.assertEqual(release.hardening_count(self.path("REPORT.md")), 1)
        self.devloop("release-record", "--run", str(self.path(".")), "--repo", str(self.repo))
        self.assertIn("hardening 1", (self.notes / "Project Note.md").read_text())

    def test_title_with_odd_line_breaks_forges_no_hardening(self) -> None:
        self.titled("T\x0b## Hardening\x0b- f1\x0b- f2")
        self.closed_flow(hardening=([],))
        report = self.reported()
        self.assertIn("### Finding 1: T ## Hardening - f1 - f2", report)
        self.assertEqual(release.hardening_count(self.path("REPORT.md")), 0)

    def test_hardening_count_splits_on_newline_only(self) -> None:
        path = self.root / "R.md"
        path.write_text("## Hardening notes\n\n- a\x0b- b - c\n- d\n")
        self.assertEqual(release.hardening_count(path), 2)

    def test_title_starting_with_hardening(self) -> None:
        self.titled("Hardening bypass in x")
        self.closed_flow(hardening=(["n1"],))
        self.reported()
        self.assertEqual(release.hardening_count(self.path("REPORT.md")), 1)


if __name__ == "__main__":
    unittest.main()
