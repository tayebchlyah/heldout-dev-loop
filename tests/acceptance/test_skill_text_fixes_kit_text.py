#!/usr/bin/env python3
"""Held-out acceptance tests for the kit text of Skill Text Fixes (spec: Skill Text Fixes).

F4 (oracle 7 to 16): the kit source text, read as files: `reference/cli/gate.md`, `docs/commands.md`,
`skill/SKILL.md`, `skill/references/claude.md`, `codex.md`, `building.md`, `tests.md`, `claude/agents/implementer.md`,
`claude/agents/spec-tester.md` and `codex/agents/spec-tester.toml`, one subtest per oracle line.

Fixture choices the spec leaves open:
- A SKILL.md stage is its `## <n> ` section; the autonomy grant runs from the line `Autonomy grant` to the line
  `Monitor rule`. A `claude.md` section is its `## <title>` section.
- A rule is found on one line of its section (the kit writes one rule per line), a phrase case-insensitive.
- The Stage 7 post-merge list is the numbered list of `## 7` that names `cleanup --branch`; an item is a numbered
  line and the lines after it up to the next numbered line or blank line.
- "Names `cd` before a command": `cd` then the word `before` on the line; "an environment prefix": the words
  `environment prefix` or `env prefix`.
- An agent file tells it to run a script file when it says `write a script`, `script to a file`, `a script file`
  or `run the file`.
- "Does not search for it": a desk line that names a search negates it (`no`, `not`, `never` or `without` before it).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = REPO_ROOT / "skill" / "SKILL.md"
REFS = REPO_ROOT / "skill" / "references"
CLAUDE_MD = REFS / "claude.md"
CODEX_MD = REFS / "codex.md"
BUILDING = REFS / "building.md"
TESTS_MD = REFS / "tests.md"
GATE_MD = REPO_ROOT / "reference" / "cli" / "gate.md"
COMMANDS_MD = REPO_ROOT / "docs" / "commands.md"
IMPLEMENTER = REPO_ROOT / "claude" / "agents" / "implementer.md"
SPEC_TESTERS = (REPO_ROOT / "claude" / "agents" / "spec-tester.md", REPO_ROOT / "codex" / "agents" / "spec-tester.toml")

LADDER = "Prove it as far down this ladder"
PSTACK_SOURCE = "Source: pstack 0.15.36 `pstack/skills/blast-radius/SKILL.md`"
FIRST_LINE = ("End every stage that changed the repo with a direct gate: `commands.gate` from `dev-loop.json` when "
              "mapped, else `scripts/gate.sh`. The Stop hook runs a selective gate (`GATE_SCOPE=changed`); only a "
              "direct, full gate writes a green record.")
SCRIPT_FILE = re.compile(r"(?i)write a script|script to a file|\ba script file|run the file")


def stage(text: str, number: int) -> str:
    match = re.search(rf"^## {number} .*?(?=^## |\Z)", text, re.M | re.S)
    assert match, f"no `## {number}` section"
    return match.group(0)


def heading(text: str, title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\s*$.*?(?=^## |\Z)", text, re.M | re.S)
    assert match, f"no `## {title}` section"
    return match.group(0)


def lines_with(text: str, *patterns: str) -> list[str]:
    return [line for line in text.splitlines() if all(re.search(p, line) for p in patterns)]


def numbered_lists(text: str) -> list[list[str]]:
    lists: list[list[str]] = []
    for match in re.finditer(r"^[ \t]*(\d+)\. .*?(?=^[ \t]*\d+\. |^[ \t]*$|^#|\Z)", text, re.M | re.S):
        if match.group(1) == "1" or not lists:
            lists.append([])
        lists[-1].append(match.group(0))
    return lists


def first(items: list[str], *patterns: str) -> int:
    for index, item in enumerate(items):
        if all(re.search(p, item) for p in patterns):
            return index
    raise AssertionError(f"no list item matches {patterns}: {items}")


class KitTextTests(unittest.TestCase):

    def assert_line(self, text: str, *patterns: str, where: str) -> list[str]:
        found = lines_with(text, *patterns)
        self.assertTrue(found, f"{where}: no line matches {patterns}")
        return found

    def test_f4_kit_text(self) -> None:
        """F4, O7 to O16."""
        skill = SKILL.read_text()
        claude = CLAUDE_MD.read_text()

        with self.subTest(oracle=7, file="gate.md"):
            gate = GATE_MD.read_text()
            self.assertIn("--changed", gate)
            self.assertIn("`scope`", gate)
        with self.subTest(oracle=7, file="commands.md"):
            self.assert_line(COMMANDS_MD.read_text(), r"^- `gate --summary", r"--changed", where="commands.md")

        stage7 = stage(skill, 7)
        with self.subTest(oracle=8, file="SKILL.md"):
            self.assertLessEqual(len(SKILL.read_bytes()), 40_000)
            self.assertNotIn(LADDER, stage7)
            self.assertIn("references/building.md", stage7)
        with self.subTest(oracle=8, file="building.md"):
            building = BUILDING.read_text()
            self.assertIn(LADDER, building)
            self.assertIn(PSTACK_SOURCE, building)

        grant = re.search(r"^Autonomy grant.*?(?=^Monitor rule)", skill, re.M | re.S)
        with self.subTest(oracle=9):
            self.assertIsNotNone(grant, "no autonomy grant")
            grant_text = grant.group(0)
            self.assert_line(grant_text, r"Stop and report only for", r"(?i)checkpoint", where="stop list")
            self.assert_line(grant_text, r"(?i)checkpoint", r"\bdecide\b", where="autonomy grant")
            self.assert_line(grant_text, r"(?i)checkpoint", r"(?i)decision log", r"(?i)\bpush", r"(?i)\bmerge",
                             where="autonomy grant")
            self.assertIn("user checkpoint met", grant_text)

        with self.subTest(oracle=10):
            lists = [items for items in numbered_lists(stage7) if any("cleanup --branch" in i for i in items)]
            self.assertEqual(len(lists), 1, "no single Stage 7 numbered list names `cleanup --branch`")
            items = lists[0]
            order = [first(items, r"shipped", r"\bpr\b"), first(items, r"git fetch origin"),
                     first(items, r"metrics --write"), first(items, r"ExitWorktree"), first(items, r"cleanup --branch")]
            self.assertEqual(order, sorted(set(order)), f"post-merge steps out of order: {order}")
            self.assertGreaterEqual(first(items, r"`metrics:"), order[2])
            self.assertGreaterEqual(first(items, r"`cleanup:"), order[4])
            self.assertRegex(items[-1], r"`(?:metrics|cleanup):")

        stage6 = stage(skill, 6)
        with self.subTest(oracle=11, stage=6):
            self.assert_line(stage6, r"dropped:user-choice", r"(?i)quot", where="Stage 6")
            self.assertIn("stays a defect", stage6)
        with self.subTest(oracle=11, stage=1):
            self.assert_line(stage(skill, 1), r"(?i)prototype", r"(?i)variant", r"(?i)\bvalues?\b", r"Framing",
                             r"(?i)issue body", where="Stage 1")

        for name, text, pattern in (("SKILL.md", stage6, r"^Fix wave"),
                                    ("claude.md", heading(claude, "Feature gate"), r"\*\*Fix wave\.\*\*"),
                                    ("codex.md", heading(CODEX_MD.read_text(), "Feature gate"), r"\*\*Fix wave\.\*\*")):
            with self.subTest(oracle=12, file=name):
                self.assert_line(text, pattern, r"gate --summary --changed", r"(?i)\bbefore\b.*\bcommit",
                                 where=f"{name} fix wave")
        with self.subTest(oracle=12, case="first line"):
            self.assertIn(FIRST_LINE, skill)

        with self.subTest(oracle=13, file="claude.md"):
            worktree = re.search(r"^- Worktree session.*?(?=^- |^## |\Z)", heading(claude, "Delivery autonomy"),
                                 re.M | re.S)
            self.assertIsNotNone(worktree, "no Worktree session line")
            rule = worktree.group(0)
            self.assertRegex(rule, r"(?i)heredoc")
            self.assertIn("$(", rule)
            self.assertIn("&&", rule)
            self.assertRegex(rule, r"\bcd\b[^\n]*\bbefore\b")
            self.assertRegex(rule, r"(?i)\benv(?:ironment)? prefix")
            self.assertRegex(rule, r"(?i)main session[^\n]*script[^\n]*\$TMPDIR|main session[^\n]*\$TMPDIR")
            self.assertRegex(rule, r"(?i)script[^\n]*\bfile\b")
        for agent in (IMPLEMENTER, SPEC_TESTERS[0]):
            with self.subTest(oracle=13, file=agent.name):
                text = agent.read_text()
                self.assertRegex(text, r"(?i)one plain command per Bash call")
                self.assertNotRegex(text, SCRIPT_FILE)

        gate_section = heading(claude, "Feature gate")
        for desk in ("Pocock spec desk", "Pocock standards desk"):
            with self.subTest(oracle=14, desk=desk):
                lines = self.assert_line(gate_section, re.escape(desk), where="claude.md Feature gate")
                line = lines[0]
                self.assertRegex(line, r"(?i)controller")
                self.assertRegex(line, r"(?i)\bpath\b")
                self.assertIn("SKILL.md", line)
                if re.search(r"(?i)\bsearch", line):
                    self.assertRegex(line, r"(?i)\b(?:no|not|never|without)\b[^.]*\bsearch")
        with self.subTest(oracle=14, case="review wave brief"):
            wave = re.search(r"^1\. \*\*Review wave.*?(?=^2\. )", gate_section, re.M | re.S)
            self.assertIsNotNone(wave, "no review wave item")
            self.assertIn("--exclude-dir=acceptance", wave.group(0))
            self.assertRegex(wave.group(0), r"(?i)worktree session|shell rule|one plain command per Bash call")

        with self.subTest(oracle=15):
            names = heading(claude, "Skill and agent names")
            self.assert_line(names, r"frontmatter", r"/effort", where="Skill and agent names")
            self.assertIn("CLAUDE_CODE_EFFORT_LEVEL", names)
            self.assertRegex(names, r"https?://\S*sub-agents")

        for path in (TESTS_MD, *SPEC_TESTERS):
            with self.subTest(oracle=16, file=path.name):
                self.assert_line(path.read_text(), r"(?i)\bURL\b", r"(?i)\bhost\b", r"(?i)prefix", r"/",
                                 r"(?i)regex|regular expression", r"(?i)\bpars", where=path.name)


if __name__ == "__main__":
    unittest.main()
