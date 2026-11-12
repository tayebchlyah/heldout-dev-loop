#!/usr/bin/env python3
"""Held-out oracle tests for the stack model (spec: 20261112 Stack Model), the kit text seam.

The kit text files of the source tree and this repo's `dev-loop.json`, read as text (seam 3); each docstring names
the `## Acceptance oracle` lines (O<n>) it checks. A stage section of `skill/SKILL.md` runs from its `## <n> `
heading to the next stage heading.
"""

from __future__ import annotations

import json
import re
import unittest

from stack_model_support import REPO_ROOT, SKILL

SKILL_MD = SKILL / "SKILL.md"
REFERENCES = SKILL / "references"
ROUTES = (REFERENCES / "claude.md", REFERENCES / "codex.md")
FALLBACK_RUNNERS = ("pytest tests/acceptance", "go test ./tests/acceptance", "npx playwright test tests/acceptance")
STACK_RULE = re.compile(r"\bin (?:stacks\b|`stacks:`)", re.IGNORECASE)
RUNNER_FORM = re.compile(r"(?:pytest|go test|playwright test)\b[^\n]*tests/acceptance")


def stage(number: int) -> str:
    text = SKILL_MD.read_text()
    found = re.search(rf"(?ms)^## {number} .*?(?=^## {number + 1} )", text)
    if found is None:
        raise AssertionError(f"no Stage {number} section in skill/SKILL.md")
    return found.group(0)


class KitTextTests(unittest.TestCase):
    def test_o14_no_stack_name_rule(self) -> None:
        """O14: no file of `skill/SKILL.md` and `skill/references/*.md` holds "in stacks" or "in `stacks:`"."""
        files = [SKILL_MD, *sorted(REFERENCES.glob("*.md"))]
        self.assertGreater(len(files), 2, files)
        for path in files:
            with self.subTest(oracle=14, file=str(path.relative_to(REPO_ROOT))):
                found = [line.strip() for line in path.read_text().splitlines() if STACK_RULE.search(line)]
                self.assertEqual(found, [])

    def test_o15_o18_acceptance_runs_only_through_the_mapped_command(self) -> None:
        """O15: the harness routes hold no fallback runner line. O18: Stage 6 makes a missing
        `commands.acceptance` a stop item that names the `check_setup.py` line, and no route names a runner for
        that case."""
        for path in ROUTES:
            text = path.read_text()
            for runner in FALLBACK_RUNNERS:
                with self.subTest(oracle=15, file=path.name, runner=runner):
                    self.assertNotIn(runner, text)
            with self.subTest(oracle=18, file=path.name):
                self.assertEqual([line.strip() for line in text.splitlines() if RUNNER_FORM.search(line)], [])
        with self.subTest(oracle=18, part="Stage 6 stop item"):
            lines = [line.strip() for line in stage(6).splitlines()
                     if "commands.acceptance" in line and re.search(r"\bstop\b", line, re.IGNORECASE)
                     and "check_setup.py" in line]
            self.assertTrue(lines, "no Stage 6 line makes a missing commands.acceptance a stop item that names "
                                   "check_setup.py")

    def test_o16_o17_stage_0_writes_capabilities_and_this_repo_sets_it(self) -> None:
        """O16: Stage 0 names `capabilities`, `ui` and `backend`. O17: this repo's `dev-loop.json` has
        `"capabilities": []`."""
        text = stage(0)
        with self.subTest(oracle=16):
            self.assertIn("capabilities", text)
            self.assertRegex(text, r"(?<![\w-])ui(?![\w-])")
            self.assertRegex(text, r"(?<![\w-])backend(?![\w-])")
        with self.subTest(oracle=17):
            config = json.loads((REPO_ROOT / "dev-loop.json").read_text())
            self.assertIn("capabilities", config)
            self.assertEqual(config["capabilities"], [])


if __name__ == "__main__":
    unittest.main()
