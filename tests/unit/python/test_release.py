#!/usr/bin/env python3
"""Tests for release.py: parsing the project note (security items, rulings, audit records)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
import release  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
