"""The Stage 0 pointer in SKILL.md, references/bootstrap.md and the Stage 4 contract line."""

from __future__ import annotations

import re
import unittest

from support import SKILL_ROOT

SKILL = (SKILL_ROOT / "SKILL.md").read_text()
BOOT = (SKILL_ROOT / "references" / "bootstrap.md").read_text()


def section(heading: str) -> str:
    start = SKILL.index(f"\n{heading}\n")
    end = SKILL.find("\n## ", start + 1)
    return SKILL[start:end if end != -1 else None]


class BootstrapTextTests(unittest.TestCase):
    def test_skill_stage_0_points_to_bootstrap(self) -> None:
        stage0 = section("## 0 Bootstrap")
        self.assertIn("references/bootstrap.md", stage0)
        self.assertIn("Done when `scripts/gate.sh` blocks a deliberately broken unit test.", stage0)
        for word in ("pyproject.toml", "go.mod", "uv init", "biome", "vitest"):
            self.assertNotIn(word, stage0)

    def test_bootstrap_names(self) -> None:
        for word in ("stack-detect", "presets.json", "notes-migrate", "--apply", "dry run", "confirm", '"capabilities"', "`ui`",
                     "`backend`", '"source_dirs"', '"stacks"', "devloop.py verify-init", "references/verify.md",
                     "references/coding-standards.md", "DEVLOOP_REPORT_DIR", "commands.stack_reset", "recreate", "`unknown`",
                     "`junit.package`", "`junit.setup`", "deepest unit", "core.fsmonitor", "Delivery never runs `notes-migrate`",
                     "`CODING_STANDARDS.md`", "nightly", "app CLI skeleton (acceptance run; `backend`: stack up, stack reset), mapped in `commands`"):
            with self.subTest(word=word):
                self.assertIn(word, BOOT)
        self.assertNotIn("empty `CODING_STANDARDS.md`", BOOT)
        self.assertNotIn("in stacks", BOOT)

    def test_agents_stacks_line_mapping(self) -> None:
        line = next(line for line in BOOT.splitlines() if "`stacks:` line" in line)
        for word in ("`python` family", "`go` family", "`ui`", "Other families add no name"):
            self.assertIn(word, line)

    def test_bootstrap_links_resolve(self) -> None:
        targets = re.findall(r"\]\(([^)]+\.md)\)", BOOT)
        self.assertTrue(targets)
        for target in targets:
            with self.subTest(target=target):
                self.assertTrue((SKILL_ROOT / "references" / target).is_file())

    def test_contract_line_in_stage_4(self) -> None:
        line = next(line for line in section("## 4 Plan").splitlines() if "contract" in line)
        for word in ("stacks", "two or more", "`ui` and `backend` both in `capabilities`"):
            self.assertIn(word, line)
        self.assertNotIn("plan task 1 is the contract", section("## 2 Spec"))


if __name__ == "__main__":
    unittest.main()
