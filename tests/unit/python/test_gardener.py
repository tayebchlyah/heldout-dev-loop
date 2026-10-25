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


GUARD = "`codebase-design`: reference only. Never invoke it alone. Never run its design-it-twice step unless the user asks for it."


def comments_section(text: str) -> str:
    body = text.split("\n## Comments\n", 1)[1]
    return body.split("\n## ", 1)[0]


class KitTextTests(unittest.TestCase):
    def read(self, rel: str) -> str:
        return (ROOT / rel).read_text()

    def test_template_names_six_kinds(self) -> None:
        section = comments_section(self.read("skill/references/coding-standards.md"))
        for value in [
            "license header", "outside dependency", "public API", "issue or an RFC", "tool directive",
            "shortcut: <the limit>, <when to upgrade>", "new and changed lines",
        ]:
            with self.subTest(value=value):
                self.assertIn(value, section)

    def test_repo_standards_match_template(self) -> None:
        self.assertEqual(
            comments_section(self.read("skill/references/coding-standards.md")),
            comments_section(self.read("CODING_STANDARDS.md")),
        )

    def test_stage_lines(self) -> None:
        text = self.read("skill/SKILL.md")
        self.assertIn("references/coding-standards.md", text)
        self.assertNotIn("empty `CODING_STANDARDS.md`", text)
        self.assertIn("triaged weekly with `/garden` (Codex `$garden`)", text)

    def test_building_pointer(self) -> None:
        text = self.read("skill/references/building.md")
        self.assertIn("coding-standards.md", text)
        self.assertIn("shortcut: <the limit>, <when to upgrade>", text)
        self.assertNotIn("Comment only the why the code cannot show", text)

    def test_codebase_design_guard(self) -> None:
        for rel in ["skill/references/claude.md", "skill/references/codex.md"]:
            with self.subTest(rel=rel):
                self.assertIn(GUARD, self.read(rel))
