#!/usr/bin/env python3
"""Held-out tests for the board GitHub links (spec: 20261114 Board GitHub Links): the kit text.

One test per `## Acceptance oracle` line 22 and 23 (O<n>), on the kit source text of `skill/references/spec.md`
and `skill/SKILL.md`.

Fixture choices the spec leaves open:
- the template frontmatter of spec.md is the `---` block that holds the `review_status:` line;
- the Stage 2 section of SKILL.md runs from its `## 2 ` heading to the next `## ` heading;
- the messages name the missing words only, never the kit text.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_REFERENCE = REPO_ROOT / "skill" / "references" / "spec.md"
SKILL_MD = REPO_ROOT / "skill" / "SKILL.md"


class KitTextTests(unittest.TestCase):
    longMessage = False

    def test_o22_spec_template_frontmatter_has_issue(self) -> None:
        """O22, D8: the template frontmatter of spec.md has an `issue:` line."""
        lines = SPEC_REFERENCE.read_text().splitlines()
        marks = [n for n, line in enumerate(lines) if re.match(r"^\s*review_status:", line)]
        self.assertTrue(marks, "spec.md has no review_status: line")
        found = False
        for mark in marks:
            start = max((n for n in range(mark) if lines[n].strip() == "---"), default=None)
            end = next((n for n in range(mark + 1, len(lines)) if lines[n].strip() == "---"), None)
            if start is not None and end is not None:
                found = found or any(re.match(r"^\s*issue:", line) for line in lines[start + 1:end])
        self.assertTrue(found, "no issue: line in the template frontmatter of spec.md")

    def test_o23_skill_stage_2_issue_key_and_track_s_branch(self) -> None:
        """O23, D8: Stage 2 says to write the GitHub issue number in the spec `issue:` key; the Track S branch
        line names `<prefix>/<issue>-<slug>`."""
        skill = SKILL_MD.read_text()
        match = re.search(r"(?ms)^## 2 .*?(?=^## |\Z)", skill)
        self.assertIsNotNone(match, "SKILL.md has no '## 2 ' section")
        assert match
        stage2 = match.group(0)
        self.assertIn("issue:", stage2, "Stage 2 does not name the spec issue: key")
        self.assertTrue(re.search(r"GitHub issue", stage2, re.IGNORECASE) and re.search(r"\bnumber\b", stage2),
                        "Stage 2 does not name the GitHub issue number")
        self.assertIn("<prefix>/<issue>-<slug>", skill, "SKILL.md names no <prefix>/<issue>-<slug> branch")


if __name__ == "__main__":
    unittest.main()
