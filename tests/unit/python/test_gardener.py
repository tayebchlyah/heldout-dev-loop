#!/usr/bin/env python3
"""Tests for the garden kit skill, the comment rule template and the harness-name check."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / "skills" / "garden"


class GardenSkillTests(unittest.TestCase):
    def test_frontmatter_locks(self) -> None:
        head = (SKILL / "SKILL.md").read_text().split("---")[1]
        self.assertIn("name: garden\n", head)
        self.assertIn("disable-model-invocation: true", head)
        self.assertIn("allow_implicit_invocation: false", (SKILL / "agents" / "openai.yaml").read_text())

    def test_procedure_text(self) -> None:
        text = (SKILL / "SKILL.md").read_text()
        for value in [
            "## Gardener", "Gardener Log.md", "`encode`", "`fix`", "`keep`", "`drop`", "`run`",
            "| Date | Item | Verdict | Check | Proof |",
            "structure or type", "lint rule", "guard test", "`CODING_STANDARDS.md` line",
            "Proof: red at", "stacks:", "at most 3", "Track S",
            "One commit holds the check", "never builds an item that has an open PR",
        ]:
            with self.subTest(value=value):
                self.assertIn(value, text)
