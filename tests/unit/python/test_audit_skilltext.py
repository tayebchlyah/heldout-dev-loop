#!/usr/bin/env python3
"""Tests for audit/skilltext.py: skill text by heading, fence, block, schema branch."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from support import ROOT

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
sys.path.insert(1, str(HERE))
from audit import skilltext  # noqa: E402
from audit_fixtures import stub_skill  # noqa: E402


class AuditSkillTextTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.skill = Path(tmp.name)
        stub_skill(self.skill)

    def test_fence_skips_inner_headings(self) -> None:
        (text,) = skilltext.fences(self.skill, "HUNTING.md", "#### Core hunting method", 1)
        for marker in ("CORE-HUNTING-TEXT", "## Inner fenced heading", "INNER-AFTER-TEXT"):
            self.assertIn(marker, text)
        self.assertTrue(text.startswith("```") and text.rstrip().endswith("```"))

    def test_section_runs_to_the_next_same_level_heading(self) -> None:
        text = skilltext.section(self.skill, "HUNTING.md", "## Structured hunter result")
        self.assertIn("STRUCTURED-RESULT-TEXT", text)
        self.assertNotIn("CONSOLIDATION-TEXT", text)

    def test_verifier_fences_and_phase_5_span(self) -> None:
        a, b = skilltext.fences(self.skill, "VALIDATION-AND-REPORTING.md", "#### Candidate-verifier prompt", 2)
        self.assertIn("VERIFIER-PROMPT-TEXT", a)
        self.assertIn("VERIFIER-PROMOTION-TEXT", b)
        span = skilltext.span(self.skill, "VALIDATION-AND-REPORTING.md", "### Phase 5", "### Phase 6")
        self.assertIn("PHASE-5-TEXT", span)
        self.assertNotIn("PHASE-6-TEXT", span)
        self.assertNotIn("PHASE-4-TEXT", span)

    def test_blocks(self) -> None:
        access = skilltext.block(self.skill, "ATTACK-CLASSES.md#Access control")
        self.assertIn("ACCESS-CONTROL-TEXT", access)
        self.assertNotIn("INJECTION-TEXT", access)
        core = skilltext.block(self.skill, "AI-AND-LLM.md#Core discipline")
        self.assertIn("AI-CORE-TEXT", core)
        self.assertNotIn("AI-MOVES-TEXT", core)
        self.assertIn("AI-VALIDATION-TEXT", skilltext.block(self.skill, "AI-AND-LLM.md#Validation rules"))

    def test_schema_branch(self) -> None:
        self.assertIn("BRANCH-needs_validation", skilltext.schema_branch(self.skill, "needs_validation"))

    def test_misses_name_file_and_section(self) -> None:
        cases = [(lambda: skilltext.block(self.skill, "ATTACK-CLASSES.md#Nope"), ("ATTACK-CLASSES.md", "Nope")),
                 (lambda: skilltext.block(self.skill, "GONE.md#X"), ("GONE.md", "X")),
                 (lambda: skilltext.block(self.skill, "../outside.md#H"), ("../outside.md", "H")),
                 (lambda: skilltext.block(self.skill, "/abs/outside.md#H"), ("/abs/outside.md", "H")),
                 (lambda: skilltext.section(self.skill, "HUNTING.md", "## Gone"), ("HUNTING.md", "## Gone")),
                 (lambda: skilltext.fences(self.skill, "HUNTING.md", "## Structured hunter result", 1),
                  ("HUNTING.md", "## Structured hunter result")),
                 (lambda: skilltext.span(self.skill, "VALIDATION-AND-REPORTING.md", "### Phase 5", "### Phase 9"),
                  ("VALIDATION-AND-REPORTING.md", "### Phase 5")),
                 (lambda: skilltext.schema_branch(self.skill, "rejected-x"), ("report-schema.json", "rejected-x"))]
        for call, want in cases:
            with self.assertRaises(skilltext.SkillChanged) as caught:
                call()
            self.assertEqual((caught.exception.file, caught.exception.section), want)


if __name__ == "__main__":
    unittest.main()
