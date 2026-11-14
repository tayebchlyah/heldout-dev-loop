#!/usr/bin/env python3
"""Held-out acceptance tests for the test time ratchet, kit text (spec: Test Time Ratchet).

F4 (oracle 21): the kit source text, `skill/references/tests.md` rule 6, `skill/SKILL.md` Stage 6 and both
`spec-tester` agent files.

Fixture choices the spec leaves open:
- Rule 6 is the text from the line `6. ` to the next numbered rule or heading; the three gate limits are the
  numbers 2, 10 and 30.
- A Stage 6 statement is one line of the `## 6` section of `SKILL.md`.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = REPO_ROOT / "skill" / "SKILL.md"
TESTS_MD = REPO_ROOT / "skill" / "references" / "tests.md"
AGENTS = (REPO_ROOT / "claude" / "agents" / "spec-tester.md", REPO_ROOT / "codex" / "agents" / "spec-tester.toml")


def section(text: str, number: int) -> str:
    match = re.search(rf"^## {number} .*?(?=^## |\Z)", text, re.M | re.S)
    assert match, f"no `## {number}` section"
    return match.group(0)


class KitTextTests(unittest.TestCase):

    def test_f4_kit_text(self) -> None:
        """F4, O21."""
        with self.subTest(oracle=21, file="tests.md"):
            match = re.search(r"^6\. .*?(?=^\d+\. |^#|\Z)", TESTS_MD.read_text(), re.M | re.S)
            self.assertIsNotNone(match, "no rule 6 in tests.md")
            rule = match.group(0)
            self.assertRegex(rule, r"\bslow\b")
            self.assertRegex(rule, r"(?i)\bid\b")
            for limit in ("2", "10", "30"):
                self.assertRegex(rule, rf"(?<![\d.]){limit}(?!\d|\.\d)", f"limit {limit} s")

        stage6 = section(SKILL.read_text(), 6).splitlines()
        with self.subTest(oracle=21, file="SKILL.md", case="grown"):
            self.assertTrue([line for line in stage6 if re.search(r"(?i)\bgrown\b", line)
                             and re.search(r"(?i)fix round", line)],
                            "no Stage 6 line names a grown held-out test as a fix round")
        with self.subTest(oracle=21, file="SKILL.md", case="slow"):
            self.assertTrue([line for line in stage6 if re.search(r"(?i)\bslow\b", line)
                             and re.search(r"Attention", line)],
                            "no Stage 6 line names a new slow held-out test as an Attention item")

        for agent in AGENTS:
            with self.subTest(oracle=21, file=agent.name):
                self.assertRegex(agent.read_text(), r"\bslow\b")


if __name__ == "__main__":
    unittest.main()
