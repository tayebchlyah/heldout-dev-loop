#!/usr/bin/env python3
"""Held-out oracle tests for the kit test rules (spec: 20261115 Kit Test Rules), the kit text seam.

The kit text files of the source tree, read as text. One test per `## Acceptance oracle` line (O<n>), grouped by
acceptance flow; each docstring names its oracle line.

Fixture choices the spec leaves open:
- A numbered rule of `skill/references/tests.md` is the text from a line `<n>. ` to the next numbered line, heading
  or end of file, outside the `## Oracle copy step` section; rule 3 is the first `3. ` item.
- A section runs from its `## ` heading to the next `## ` heading. A paragraph is a block between blank lines; in
  `## 6 Feature gate` of `SKILL.md`, which writes each paragraph as one line, it is a non-empty line. A paragraph
  "starts with" a phrase after leading markup (`*`, `_`, `-`, spaces).
- Phrase checks ignore line wraps (whitespace runs read as one space) and, where the oracle gives no exact casing,
  case.
- A route phase 6 is an item that starts with `6.` or `Phase 6`, up to the next such item or heading.
- O10 reads the size of `SKILL.md` once it holds the `Oracle copies` paragraph of Stage 6 (O5): the size bound is
  about the text with the step in it.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_MD = REPO_ROOT / "skill" / "SKILL.md"
TESTS_MD = REPO_ROOT / "skill" / "references" / "tests.md"
ROUTES = (REPO_ROOT / "skill" / "references" / "claude.md", REPO_ROOT / "skill" / "references" / "codex.md")
KIT_MD = REPO_ROOT / "reference" / "kit.md"
GLOSSARY = REPO_ROOT / "GLOSSARY.md"


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def section(text: str, heading: str) -> str:
    found = re.search(rf"(?ms)^{re.escape(heading)}\b.*?(?=^## |\Z)", text)
    if found is None:
        raise AssertionError(f"no `{heading}` section")
    return found.group(0)


def paragraphs(text: str) -> list[str]:
    return [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]


def starts_with(paragraph: str, phrase: str) -> bool:
    return paragraph.lstrip("*_- \t").startswith(phrase)


def numbered_rules() -> list[tuple[str, str]]:
    text = TESTS_MD.read_text()
    step = re.search(r"(?ms)^## Oracle copy step\b.*?(?=^## |\Z)", text)
    if step is not None:
        text = text[:step.start()] + text[step.end():]
    return [(found.group(1), flat(found.group(0)))
            for found in re.finditer(r"(?ms)^(\d+)\. .*?(?=^\d+\. |^#|\Z)", text)]


def stage6() -> str:
    return section(SKILL_MD.read_text(), "## 6 Feature gate")


def stage6_paragraph(phrase: str) -> tuple[int, str]:
    lines = [line.strip() for line in stage6().splitlines() if line.strip()]
    found = [(index, paragraph) for index, paragraph in enumerate(lines) if starts_with(paragraph, phrase)]
    if not found:
        raise AssertionError(f"no Stage 6 paragraph starts with `{phrase}`")
    return found[0]


class TestRulesFlow(unittest.TestCase):
    """Flow 1: an agent reads `tests.md` before it writes a test."""

    def test_o1_rule_3_in_process_rows_and_wiring(self) -> None:
        """O1: rule 3 says table rows run in-process at the function seam, names `at most 3 end-to-end rows` per
        behaviour, and defines a wiring as one entry point run by one harness and one caller kind."""
        rules = [rule for number, rule in numbered_rules() if number == "3"]
        self.assertTrue(rules, "no rule 3 in tests.md")
        rule = rules[0]
        for pattern in (r"(?i)table rows?", r"(?i)in-process", r"(?i)\bseam\b", r"(?i)at most 3 end-to-end rows",
                        r"(?i)\bbehaviou?r\b", r"(?i)\bwiring\b", r"(?i)\bone entry point\b", r"(?i)\bone harness\b",
                        r"(?i)\bone caller kind\b"):
            with self.subTest(oracle=1, pattern=pattern):
                self.assertRegex(rule, pattern)

    def test_o2_oracle_copy_rule(self) -> None:
        """O2: a numbered rule names `oracle copy` and says a unit test does not repeat a held-out test."""
        rules = [rule for _, rule in numbered_rules()
                 if re.search(r"(?i)oracle copy", rule) and re.search(r"(?i)unit test", rule)
                 and re.search(r"(?i)\brepeats?\b", rule) and re.search(r"(?i)held-out", rule)]
        self.assertTrue(rules, "no numbered rule of tests.md names oracle copy and says a unit test does not "
                               "repeat a held-out test")

    def test_o3_stub_rule_and_developer_tools_line(self) -> None:
        """O3: a numbered rule says a stub is written `once per process`; a line names the macOS `Developer Tools`
        exemption."""
        with self.subTest(oracle=3, part="stub rule"):
            rules = [rule for _, rule in numbered_rules()
                     if re.search(r"(?i)\bstubs?\b", rule) and re.search(r"(?i)once per process", rule)]
            self.assertTrue(rules, "no numbered rule of tests.md writes a stub once per process")
        with self.subTest(oracle=3, part="Developer Tools"):
            text = TESTS_MD.read_text()
            lines = [line for line in text.splitlines() if "Developer Tools" in line]
            self.assertTrue(lines, "no tests.md line names Developer Tools")
            blocks = [flat(block) for block in paragraphs(text) if "Developer Tools" in block]
            self.assertTrue([block for block in blocks if "macOS" in block
                             and re.search(r"(?i)\bexemption\b", block)],
                            "the Developer Tools line names no macOS exemption")


class OracleCopyStepFlow(unittest.TestCase):
    """Flow 2: the controller reaches the end of the acceptance loop and the closing drive on each harness."""

    def test_o4_oracle_copy_step_section(self) -> None:
        """O4: `## Oracle copy step` names the branch scope, the copy rule, the decision row, the implementer brief,
        the gate, the revert, the report lines and the `acceptance: none` skip."""
        step = flat(section(TESTS_MD.read_text(), "## Oracle copy step"))
        checks = {
            "branch scope": r"origin/main\.\.\.HEAD",
            "merge-base": r"(?i)merge[- ]base",
            "copy rule": r"(?i)at least as strict",
            "decision row": r"decide --kind decision",
            "held-out evidence": r"(?i)evidence",
            "held-out": r"(?i)held-out",
            "implementer": r"\bimplementer\b",
            "brief with unit test ids": r"(?i)brief\b.*?unit test ids?\b|unit test ids?\b.*?\bbrief",
            "gate": r"gate --summary",
            "revert": r"git revert",
            "removed line": r"oracle copies: <n> removed",
            "skipped line": r"oracle copies: skipped:",
            "acceptance none skip": r"acceptance:? none",
        }
        for part, pattern in checks.items():
            with self.subTest(oracle=4, part=part):
                self.assertRegex(step, pattern)

    def test_o5_stage6_oracle_copies_paragraph(self) -> None:
        """O5: Stage 6 has a paragraph that starts with `Oracle copies`, after `Closing drive` and before `End:`;
        it names `tests.md`, Tracks M and L, and `oracle copies:`."""
        closing, _ = stage6_paragraph("Closing drive")
        copies, paragraph = stage6_paragraph("Oracle copies")
        end, _ = stage6_paragraph("End:")
        with self.subTest(oracle=5, part="order"):
            self.assertLess(closing, copies, "Oracle copies comes before Closing drive")
            self.assertLess(copies, end, "Oracle copies comes after End:")
        text = flat(paragraph)
        for part, pattern in (("tests.md", r"tests\.md"), ("Tracks M and L", r"\bM and L\b"),
                              ("report line", r"oracle copies:")):
            with self.subTest(oracle=5, part=part):
                self.assertRegex(text, pattern)

    def test_o6_stage6_end_paragraph(self) -> None:
        """O6: the `End:` paragraph says `<sha>` = HEAD after the oracle copy step and `Report seven lines`, with
        `oracle copies:` after `drive:`."""
        _, paragraph = stage6_paragraph("End:")
        text = flat(paragraph)
        with self.subTest(oracle=6, part="sha"):
            self.assertIn("<sha>", text)
            self.assertRegex(text, r"(?i)HEAD after the oracle copy step")
        with self.subTest(oracle=6, part="seven lines"):
            self.assertIn("Report seven lines", text)
        with self.subTest(oracle=6, part="line order"):
            self.assertIn("drive:", text)
            self.assertIn("oracle copies:", text)
            self.assertLess(text.index("drive:"), text.index("oracle copies:"),
                            "oracle copies: is not named after drive:")

    def test_o7_route_phase_6(self) -> None:
        """O7: `claude.md` and `codex.md`, section `## Feature gate`, say `Six phases, in order` and have a phase 6
        that names the oracle copy step and `implementer`."""
        for path in ROUTES:
            gate = section(path.read_text(), "## Feature gate")
            with self.subTest(oracle=7, file=path.name, part="six phases"):
                self.assertIn("Six phases, in order", flat(gate))
            with self.subTest(oracle=7, file=path.name, part="phase 6"):
                found = re.search(r"(?ms)^[\s>*_-]*(?:6\.|Phase 6\b).*?(?=^[\s>*_-]*(?:\d+\.|Phase \d)|^#|\Z)", gate)
                self.assertIsNotNone(found, f"no phase 6 in {path.name} ## Feature gate")
                phase = flat(found.group(0))
                self.assertRegex(phase, r"(?i)oracle cop(?:y|ies)")
                self.assertRegex(phase, r"\bimplementer\b")

    def test_o10_skill_md_size(self) -> None:
        """O10: `skill/SKILL.md`, with the Stage 6 `Oracle copies` paragraph in it, is 40,000 bytes or less."""
        stage6_paragraph("Oracle copies")
        self.assertLessEqual(SKILL_MD.stat().st_size, 40_000)


class UpgradeFlow(unittest.TestCase):
    """Flow 3: a consumer project reads the upgrade note and the new term."""

    def test_o8_upgrade_note(self) -> None:
        """O8: `reference/kit.md` `## Upgrade` has a paragraph that names `#329`, the oracle copy step and
        `tests.md`."""
        notes = [flat(block) for block in paragraphs(section(KIT_MD.read_text(), "## Upgrade"))
                 if "#329" in block]
        self.assertTrue([note for note in notes if re.search(r"(?i)oracle copy", note) and "tests.md" in note],
                        "no ## Upgrade paragraph names #329, the oracle copy step and tests.md")

    def test_o9_glossary_term(self) -> None:
        """O9: `GLOSSARY.md` has the term `**Oracle copy**:`."""
        self.assertIn("**Oracle copy**:", GLOSSARY.read_text())


if __name__ == "__main__":
    unittest.main()
