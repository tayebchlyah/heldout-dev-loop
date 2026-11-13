"""The mutation kit text and the removal of select_mutants.py."""

from __future__ import annotations

import unittest

import test_devloop
from support import CHECKER, SKILL_ROOT, all_instructions

REFERENCES = SKILL_ROOT / "references"
MUTATION_MD = REFERENCES / "mutation.md"
KIT_FOLDERS = [SKILL_ROOT, SKILL_ROOT.parent / "skills", SKILL_ROOT.parent / "claude", SKILL_ROOT.parent / "codex"]
RUN_LINE = (
    "BASE=$(git rev-list -1 --before='24 hours ago' main); "
    "python3 .agents/skills/dev-loop/scripts/devloop.py mutation "
    '--base "${BASE:-$(git rev-list --max-parents=0 main | tail -n 1)}" --head main --run'
)
SETUP_CHECK = SKILL_ROOT / "scripts" / "mutation" / "setup.py"


class MutationKitTextTests(unittest.TestCase):
    def test_script_removed(self) -> None:
        self.assertFalse((SKILL_ROOT / "scripts" / "select_mutants.py").exists())
        for folder in KIT_FOLDERS:
            for path in folder.rglob("*"):
                if path.is_file() and path != SETUP_CHECK and "select_mutants" in path.read_text(errors="ignore"):
                    self.fail(f"{path} names select_mutants")

    def test_run_line(self) -> None:
        for heading in ["## Run", "## Nightly CI"]:
            with self.subTest(section=heading):
                self.assertIn(RUN_LINE, test_devloop.section(MUTATION_MD, heading))

    def test_mutation_md_terms(self) -> None:
        text = MUTATION_MD.read_text()
        for value in ["devloop.py mutation", "protocol", "--entry", "mutago", "v2.10.42",
                      "timeout counts as killed", "opt in", "Node 22", "Go 1.26.6", "first word of the `command`",
                      "lies fully inside a changed range", "`%w` to `%v`"]:
            with self.subTest(value=value):
                self.assertIn(value, text)
        tests = (REFERENCES / "tests.md").read_text()
        self.assertIn("devloop.py mutation", tests)
        self.assertNotIn("select_mutants", tests)

    def test_fix_rule(self) -> None:
        fix = test_devloop.section(MUTATION_MD, "## Fix")
        for value in ["devloop.py mutation --base <report base> --head HEAD --run --entry <path> --only",
                      "fewer mutants under its unit and no more inconclusive mutants"]:
            with self.subTest(value=value):
                self.assertIn(value, fix)
        self.assertIn("a mutation fix test proves the kill", (REFERENCES / "tests.md").read_text().lower())

    def test_bootstrap_opt_in(self) -> None:
        text = (REFERENCES / "bootstrap.md").read_text()
        self.assertIn("Stage 0 offers mutation", text)
        self.assertIn("writes the `mutation` key only on a yes", text)

    def test_no_route_runs_mutation_in_the_gate(self) -> None:
        for route in ["claude.md", "codex.md"]:
            with self.subTest(route=route):
                self.assertNotIn("mutation.md", (REFERENCES / route).read_text())

    def test_no_route_uses_a_stryker_flag_that_does_not_exist(self) -> None:
        for line in all_instructions().splitlines():
            if "--since" in line:
                self.assertNotIn("stryker", line.lower())
                self.assertTrue("drive" in line or "devloop.py sessions" in line, line)

    def test_no_em_dash_in_the_mutation_payload(self) -> None:
        paths = [MUTATION_MD, CHECKER, *sorted((SKILL_ROOT / "scripts" / "mutation").glob("*"))]
        for path in paths:
            if path.is_file():
                with self.subTest(path=path.name):
                    self.assertNotIn("\u2014", path.read_text())
