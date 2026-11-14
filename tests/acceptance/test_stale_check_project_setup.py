#!/usr/bin/env python3
"""Held-out oracle tests for the stale check project key (spec: Stale Check Project): the oracle lines no flow holds.

O18: the second seam, the `check_setup.py --harness claude` process of the kit source on a fixture repo, its stdout
and exit code. O19: the contract text files of this repo, read as text.

Fixture choices the spec leaves open:
- The fixture repo is the Stack Model setup project (`stack_model_support.SetupProject`): installed, on `main`, where
  `check_setup.py --harness claude` passes. Each case writes `dev-loop.json` with `commands.compose_project` added to
  its commands, then commits it on `main`, so the checkout and the main ref agree.
- "It fails": exit 1 and a stdout line that names `compose_project`.
"""

from __future__ import annotations

import unittest

from stack_model_support import REPO_ROOT, SetupProject, commit_all

UNKNOWN = "unknown commands key"


class ComposeProjectSetupTests(SetupProject):

    def apply(self, value: object) -> tuple[int, str]:
        commands = {**self.base["commands"], "compose_project": value}
        self.write_config(self.config_with(commands=commands))
        commit_all(self.project, self.env, "Map the compose project")
        return self.check()

    def test_o18_check_setup_accepts_the_key(self) -> None:
        """O18: `commands.compose_project` a non-empty string: no `unknown commands key` failure; `""` or a
        number: `check_setup.py` fails."""
        with self.subTest(oracle=18, value="echo acme-wt"):
            code, output = self.apply("echo acme-wt")
            self.assertEqual([line for line in output.splitlines() if UNKNOWN in line], [], output)
            self.assertEqual(code, 0, output)
        for value in ("", 5):
            with self.subTest(oracle=18, value=value):
                code, output = self.apply(value)
                self.assertEqual(code, 1, output)
                self.assertTrue([line for line in output.splitlines() if "compose_project" in line], output)


class ContractTextTests(unittest.TestCase):

    def test_o19_contract_text_names_the_key(self) -> None:
        """O19: `reference/cli/acceptance.md` names `commands.compose_project`, `com.docker.compose.project` and
        `project`; `reference/kit.md` and `docs/configuration.md` name `compose_project`."""
        contract = (REPO_ROOT / "reference" / "cli" / "acceptance.md").read_text()
        for needle in ("commands.compose_project", "com.docker.compose.project", "`project`"):
            with self.subTest(oracle=19, file="reference/cli/acceptance.md", needle=needle):
                self.assertIn(needle, contract)
        for path in ("reference/kit.md", "docs/configuration.md"):
            with self.subTest(oracle=19, file=path):
                self.assertIn("compose_project", (REPO_ROOT / path).read_text())


if __name__ == "__main__":
    unittest.main()
