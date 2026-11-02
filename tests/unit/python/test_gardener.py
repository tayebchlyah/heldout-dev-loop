#!/usr/bin/env python3
from __future__ import annotations

import unittest

from support import ROOT

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
            "One commit holds the check", "Proof: seen at", "never builds an item that has an open PR",
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
        self.assertIn(" A `CODING_STANDARDS.md` with no `## Comments` section counts as absent.", text)

    def test_routes_name_garden(self) -> None:
        self.assertIn("/garden", self.read("skill/references/claude.md"))
        self.assertIn("$garden", self.read("skill/references/codex.md"))

    def test_codebase_design_guard(self) -> None:
        for rel in ["skill/references/claude.md", "skill/references/codex.md"]:
            with self.subTest(rel=rel):
                self.assertIn(GUARD, self.read(rel))


CLAUDE_ONLY = [
    "TaskCreate", "TaskUpdate", "subagent_type", "SendMessage", "TaskStop", "EnterWorktree",
    "CLAUDE_CODE_SESSION_ID", "mattpocock-skills:",
]
CODEX_ONLY = [
    "spawn_agent", "send_input", "followup_task", "resume_agent", "interrupt_agent", "close_agent",
    "apply_patch", "CODEX_THREAD_ID",
]


def name_hits(route: str, folder: str, names: list[str]) -> list[str]:
    files = [ROOT / route, *sorted(p for p in (ROOT / folder).rglob("*") if p.is_file())]
    hits = []
    for path in files:
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            hits += [f"{path.relative_to(ROOT)}:{n} {name}" for name in names if name in line]
    return hits


class HarnessNameTests(unittest.TestCase):
    def test_codex_sources_have_no_claude_name(self) -> None:
        self.assertEqual([], name_hits("skill/references/codex.md", "codex", CLAUDE_ONLY))

    def test_claude_sources_have_no_codex_name(self) -> None:
        self.assertEqual([], name_hits("skill/references/claude.md", "claude", CODEX_ONLY))
