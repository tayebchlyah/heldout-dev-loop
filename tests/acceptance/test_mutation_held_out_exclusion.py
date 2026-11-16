#!/usr/bin/env python3
"""Held-out oracle tests for mutation held-out exclusion (spec: 20261116 Mutation Held-Out Exclusion), Stryker runs.

Seam 2: the `devloop.py mutation --run` process of the kit source on a light fixture repo with a stub
`node_modules/.bin/stryker` (`mutation_held_out_exclusion_support`). One test per flow F1 to F3; each docstring
names the `## Acceptance oracle` lines (O<n>) it checks.

Fixture choices the spec leaves open:
- The Stryker config of an entry is `{"testRunner": "vitest"}`, plus `ignorePatterns` when a line names one.
- F2 has two entries, `.` and `web/`: the `.` entry is the contrast that shows the stub records the option.
- In F3 the report keys name files that exist in the repo (`tests/acceptance/a.test.ts`, `tests/unit/b.test.ts`).
"""

from __future__ import annotations

import unittest

from mutation_held_out_exclusion_support import (HeldOutCase, PRICE_HEAD, RunRepo, entry_of, option_values,
                                                 stryker_folder, stryker_report)

BACKSTOP = "held-out test files ran: 1 under tests/acceptance/"
RUNNER = {"testRunner": "vitest"}


def stryker_entry(path: str) -> dict:
    return {"path": path, "adapter": "stryker", "critical": ["src/core/*"]}


class StrykerRunTests(HeldOutCase):
    def build(self, name: str, entries: list[str], config: dict, reports: dict[str, dict | None]) -> RunRepo:
        """One Stryker entry per path of `entries` (`.` or `<folder>/`), each with `config`; the stub planted."""
        files: dict[str, str] = {}
        changes: dict[str, str] = {}
        for path in entries:
            prefix = "" if path == "." else path
            files.update(stryker_folder(prefix, config))
            changes[f"{prefix}src/core/price.ts"] = PRICE_HEAD
        repo = RunRepo(self, name).build([stryker_entry(path) for path in entries], files, changes)
        repo.stryker(reports)
        return repo

    def calls_in(self, repo: RunRepo, cwd: str, output: str) -> list[list[str]]:
        argvs = [call["argv"] for call in repo.stryker_calls() if call["cwd"] == cwd]
        self.assertEqual(len(argvs), 1, f"stryker calls in {cwd!r}: {repo.stryker_calls()}\n{output}")
        return argvs

    def test_f1_o1_o2_root_entry_keeps_the_held_out_suite_out(self) -> None:
        """F1, O1: entry `.`, adapter `stryker`, no `ignorePatterns`: the `stryker run` command holds
        `--ignorePatterns /tests/acceptance`. O2: `ignorePatterns` `["dist", "/big"]`: the value is
        `dist,/big,/tests/acceptance`. The mutant only a held-out test would kill is a survivor."""
        unit_only = stryker_report(["tests/unit/b.test.ts"])
        for oracle, name, config, value in ((1, "plain", RUNNER, "/tests/acceptance"),
                                            (2, "patterns", {**RUNNER, "ignorePatterns": ["dist", "/big"]},
                                             "dist,/big,/tests/acceptance")):
            repo = self.build(name, ["."], config, {".": unit_only})
            code, payload, output = repo.run()
            with self.subTest(oracle=oracle, part="--ignorePatterns"):
                (argv,) = self.calls_in(repo, ".", output)
                self.assertIn("run", argv, argv)
                self.assertEqual(option_values(argv), [value], argv)
            with self.subTest(oracle=oracle, part="survivor"):
                self.assertEqual(entry_of(payload, ".").get("status"), "survivors", output)

    def test_f2_o3_nested_entry_runs_an_unchanged_command(self) -> None:
        """F2, O3: entry `web/` (the repo `tests/acceptance/` is not under `web/`): the command holds no
        `--ignorePatterns`. Contrast: the `.` entry of the same repo holds `--ignorePatterns /tests/acceptance`."""
        unit_only = stryker_report(["tests/unit/b.test.ts"])
        repo = self.build("nested", [".", "web/"], RUNNER, {".": unit_only, "web": stryker_report(["test/p.test.ts"])})
        code, payload, output = repo.run()
        with self.subTest(oracle=3):
            (argv,) = self.calls_in(repo, "web", output)
            self.assertIn("run", argv, argv)
            self.assertEqual([item for item in argv if item.startswith("--ignorePatterns")], [], argv)
        with self.subTest(oracle=3, part="contrast: entry ."):
            (argv,) = self.calls_in(repo, ".", output)
            self.assertEqual(option_values(argv), ["/tests/acceptance"], argv)

    def test_f3_o4_backstop_when_the_held_out_suite_ran(self) -> None:
        """F3, O4: entry `.`, a report whose `testFiles` holds `tests/acceptance/a.test.ts` and
        `tests/unit/b.test.ts`: entry status `setup`, its cause holds `held-out test files ran: 1 under
        tests/acceptance/`, exit `1`. Entry `web/`, a report key `../tests/acceptance/a.test.ts`: the same status
        and cause. A report with no `testFiles`: no such cause."""
        cases = (("root", ".", ".", stryker_report(["tests/acceptance/a.test.ts", "tests/unit/b.test.ts"])),
                 ("web", "web/", "web", stryker_report(["../tests/acceptance/a.test.ts", "test/p.test.ts"])))
        for name, path, cwd, report in cases:
            repo = self.build(name, [path], RUNNER, {cwd: report})
            code, payload, output = repo.run()
            with self.subTest(oracle=4, entry=path):
                self.calls_in(repo, cwd, output)
                found = entry_of(payload, path)
                self.assertEqual(found.get("status"), "setup", output)
                self.assertIn(BACKSTOP, str(found.get("cause")), output)
                self.assertEqual(code, 1, output)

        repo = self.build("bare", ["."], RUNNER, {".": stryker_report()})
        code, payload, output = repo.run()
        with self.subTest(oracle=4, report="no testFiles"):
            self.calls_in(repo, ".", output)
            self.assertNotIn("held-out test files ran", str(entry_of(payload, ".").get("cause")), output)


if __name__ == "__main__":
    unittest.main()
