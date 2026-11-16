#!/usr/bin/env python3
"""Held-out oracle tests for mutation held-out exclusion (spec: 20261116 Mutation Held-Out Exclusion), refusals.

Seam 1: the `check_setup.py --harness claude` process of the kit source on the installed setup project, its
`mutation` failure lines. Seam 2: the `devloop.py mutation --run` process of the kit source on a light fixture repo
with the stub tools (`mutation_held_out_exclusion_support`). One test per flow F4 to F6; each docstring names the
`## Acceptance oracle` lines (O<n>) it checks.

Fixture choices the spec leaves open:
- The setup project holds the root `tests/acceptance/` (with `test_x.py`) and `tests/unit/`; each mutmut entry
  folder holds `pyproject.toml` (an exact Poetry pin, `[tool.mutmut]` with `source_paths`), `src/kettle.py` and
  `tests/unit/`; the setup project adds a `poetry.lock`, the run repo has no lock file (the adapter runs `mutmut` from
  `PATH`). Each Stryker entry folder holds `stryker.config.json`, a `package.json` with the core and vitest runner at
  10.1.1, and `src/core/price.ts`. `.gitignore` ignores `node_modules/`, `.stryker-tmp/`, `reports/`, `mutants/`.
- Each run case is a new run repo, with the same entry and tool config as its check case, and the stub tools
  planted, so the run reaches the tool unless the adapter refuses the setup.
- F6 also checks the selection `["tests"]` at entry `.`: the contrast that shows the accepted `app/` case is the rule.
"""

from __future__ import annotations

import unittest

from mutation_held_out_exclusion_support import (CLI_ARGS, COMMA, IN_PLACE, KETTLE_HEAD, NO_PATH, NOT_LIST, PRICE_HEAD,
                                                 REACHES, SetupRepo, declared, entry_of, mutation_failures,
                                                 python_folder, stryker_folder, stryker_report)

RUNNER = {"testRunner": "vitest"}
STRYKER = [{"path": ".", "adapter": "stryker", "critical": ["src/core/*"]}]
MUTMUT = [{"path": ".", "adapter": "mutmut", "critical": ["src/*"]}]
LOCK = {"poetry.lock": "# lock\n"}


class StrykerRefusalTests(SetupRepo):
    def test_f4_o5_o6_in_place_and_bad_ignore_patterns(self) -> None:
        """F4, O5: `stryker.config.json` with `"inPlace": true`: a `check_setup.py` line that names `inPlace`, and
        `devloop.py mutation --run` gives the entry status `setup` without a call of the tool. O6: `ignorePatterns`
        `"dist"` (a string): a line that names `not a list of strings`; `ignorePatterns` `["a,b"]`: a line that names
        `has a comma: a,b`. Both are `setup` at run time."""
        cases = ((5, "inPlace", {**RUNNER, "inPlace": True}, IN_PLACE),
                 (6, "string", {**RUNNER, "ignorePatterns": "dist"}, NOT_LIST),
                 (6, "comma", {**RUNNER, "ignorePatterns": ["a,b"]}, COMMA + "a,b"))
        for oracle, name, config, line in cases:
            code, output = self.apply(stryker_folder("", config), STRYKER)
            with self.subTest(oracle=oracle, case=name, seam="check_setup"):
                self.assertTrue(declared(output, line), output[-3000:])

            repo = self.run_repo().build(STRYKER, stryker_folder("", config), {"src/core/price.ts": PRICE_HEAD})
            repo.stryker({".": stryker_report(["tests/unit/b.test.ts"])})
            code, payload, run_output = repo.run()
            with self.subTest(oracle=oracle, case=name, seam="mutation --run"):
                self.assertEqual(entry_of(payload, ".").get("status"), "setup", run_output)
                if oracle == 5:
                    self.assertEqual(repo.stryker_calls(), [], run_output)


class MutmutRefusalTests(SetupRepo):
    def test_f5_o7_o8_o11_o12_selection_that_reaches_the_suite_or_names_no_path(self) -> None:
        """F5, O7: mutmut entry `.`, selection `["tests"]`, `["."]`, `["tests/acceptance/test_x.py::test_a"]`: the
        line `test selection reaches tests/acceptance`. O8: selection `["-x"]`, `["-k", "unit"]`,
        `["-x", "--tb", "short"]`: the line `test selection names no test path`. O11: selection `["tests/unit"]`,
        `pytest_add_cli_args` `["tests"]`: the line `pytest_add_cli_args reaches tests/acceptance`. O12: the setup of
        O7 or O8, `devloop.py mutation --run`: the entry status is `setup`, mutmut does not run."""
        cases = ((7, ["tests"], None, REACHES), (7, ["."], None, REACHES),
                 (7, ["tests/acceptance/test_x.py::test_a"], None, REACHES),
                 (8, ["-x"], None, NO_PATH), (8, ["-k", "unit"], None, NO_PATH),
                 (8, ["-x", "--tb", "short"], None, NO_PATH),
                 (11, ["tests/unit"], ["tests"], CLI_ARGS))
        for oracle, selection, cli_args, line in cases:
            code, output = self.apply({**python_folder("", selection, cli_args), **LOCK}, MUTMUT)
            with self.subTest(oracle=oracle, selection=selection, cli_args=cli_args):
                self.assertTrue(declared(output, line), output[-3000:])

        for source, selection in ((7, ["tests"]), (8, ["-x"])):
            repo = self.run_repo().build(MUTMUT, python_folder("", selection), {"src/kettle.py": KETTLE_HEAD})
            repo.mutmut()
            code, payload, run_output = repo.run()
            with self.subTest(oracle=12, setup_of=source, selection=selection):
                self.assertEqual(entry_of(payload, ".").get("status"), "setup", run_output)
                self.assertEqual(repo.mutmut_calls(), [], run_output)


class MutmutAcceptedTests(SetupRepo):
    def test_f6_o9_o10_unit_test_paths_only(self) -> None:
        """F6, O9: mutmut entry `.`, selection `["tests/unit"]` or
        `["tests/unit", "--deselect", "tests/unit/test_slow.py::test_t"]`: no `mutation` failure line. O10: mutmut
        entry `app/` (the repo `tests/acceptance/` is not under `app/`), selection `["tests"]`: no `mutation` failure
        line. Contrast: the same selection `["tests"]` at entry `.` gives the `reaches tests/acceptance` line."""
        for selection in (["tests/unit"], ["tests/unit", "--deselect", "tests/unit/test_slow.py::test_t"]):
            code, output = self.apply({**python_folder("", selection), **LOCK}, MUTMUT)
            with self.subTest(oracle=9, selection=selection):
                self.assertEqual(mutation_failures(output), [], output[-3000:])

        nested = [{"path": "app/", "adapter": "mutmut", "critical": ["src/*"]}]
        code, output = self.apply({**python_folder("app/", ["tests"]), "app/poetry.lock": "# lock\n"}, nested)
        with self.subTest(oracle=10):
            self.assertEqual(mutation_failures(output), [], output[-3000:])

        code, output = self.apply({**python_folder("", ["tests"]), **LOCK}, MUTMUT)
        with self.subTest(oracle=10, part="contrast: entry ."):
            self.assertTrue(declared(output, REACHES), output[-3000:])


if __name__ == "__main__":
    unittest.main()
