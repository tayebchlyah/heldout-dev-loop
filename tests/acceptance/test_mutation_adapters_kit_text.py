#!/usr/bin/env python3
"""Held-out oracle tests for mutation adapters (spec: 20261113 Mutation Adapters), the kit text seam.

Seam 3: the kit files of the source tree read as text, and this repo's `dev-loop.json`; O31 also runs
`check_setup.py --harness claude --repo <this repo>`. Each docstring names the `## Acceptance oracle` lines (O<n>) it
checks.

Fixture choices the spec leaves open:
- A "mutation line" of `check_setup.py` is a stripped stdout line that starts with `- mutation ` or names
  `select_mutants.py`.
- "Names the timeout gap": one line of `skill/references/mutation.md` names both `mutago` and `timeout`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = REPO_ROOT / "skill"
CHECKER = SKILL / "scripts" / "check_setup.py"
MUTATION_MD = SKILL / "references" / "mutation.md"
TESTS_MD = SKILL / "references" / "tests.md"


class KitTextTests(unittest.TestCase):
    def test_o32_run_line_and_removed_script(self) -> None:
        """O32: `skill/scripts/select_mutants.py` does not exist; `mutation.md` and `tests.md` name
        `devloop.py mutation` and not `select_mutants`; `mutation.md` names `protocol`, `--entry`, `mutago`,
        `v2.10.42` and the timeout gap."""
        with self.subTest(oracle=32, part="script removed"):
            self.assertFalse((SKILL / "scripts" / "select_mutants.py").exists())
        for path in (MUTATION_MD, TESTS_MD):
            with self.subTest(oracle=32, file=path.name):
                text = path.read_text()
                self.assertTrue("devloop.py mutation" in text, f"{path.name} lacks devloop.py mutation")
                self.assertFalse("select_mutants" in text, f"{path.name} names select_mutants")
        text = MUTATION_MD.read_text()
        for word in ("protocol", "--entry", "mutago", "v2.10.42"):
            with self.subTest(oracle=32, word=word):
                self.assertTrue(word in text, f"mutation.md lacks {word}")
        with self.subTest(oracle=32, part="timeout gap"):
            self.assertTrue([line for line in text.splitlines() if "mutago" in line and "timeout" in line.lower()],
                            "no mutation.md line names the mutago timeout gap")

    def test_o31_this_repo_turns_mutation_off(self) -> None:
        """O31: this repo's `dev-loop.json` has `"mutation": false`; `check_setup.py --harness claude --repo <this
        repo>` prints no mutation line."""
        with self.subTest(oracle=31, part="dev-loop.json"):
            config = json.loads((REPO_ROOT / "dev-loop.json").read_text())
            self.assertIn("mutation", config)
            self.assertIs(config["mutation"], False)
        with self.subTest(oracle=31, part="check_setup"):
            result = subprocess.run([sys.executable, str(CHECKER), "--harness", "claude", "--repo", str(REPO_ROOT)],
                                    cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
            output = result.stdout + result.stderr
            lines = [line.strip() for line in output.splitlines()
                     if line.strip().startswith("- mutation ") or "select_mutants" in line]
            self.assertEqual(lines, [], output[-3000:])


if __name__ == "__main__":
    unittest.main()
