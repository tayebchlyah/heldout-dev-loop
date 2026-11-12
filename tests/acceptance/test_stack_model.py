#!/usr/bin/env python3
"""Held-out oracle tests for the stack model (spec: 20261112 Stack Model), the `check_setup.py` seam.

One test per `## Acceptance flows` item (F<n>) that `check_setup.py` decides, plus the oracle lines no flow
holds; each docstring names the `## Acceptance oracle` lines (O<n>) it checks. Seams and fixture:
`stack_model_support.py`. The `verify-init` and `drive` flow is in `test_stack_model_drive.py`, the kit text in
`test_stack_model_kit_text.py`.
"""

from __future__ import annotations

import json
import os
import unittest

from stack_model_support import (INVALID_LINE, PLAYWRIGHT_LINE, RUNNER_LINE, SetupProject, lines_with, missing_line,
                                 put, stripped, user_actions, verify_lines)

COMPOSE = "services:\n  api:\n    image: kettle-api:1\n"


class UpgradeFlowTests(SetupProject):
    def test_f1_upgrade_of_a_ui_and_service_project(self) -> None:
        """F1, O2, O4, O6, O7; with the Verify Skill (`verify-init`, the filled skill, its O5 as replaced by O7):
        an earlier project with `stacks: python, ui` and `compose.yaml` gets the proposal `["ui", "backend"]` and
        the runner line, adds both keys, gets the `verify-init` line, adds its verify skill, and gets no line about
        `capabilities`, `commands.acceptance` or `verify` (the `stacks:` line still drives the mutation setup lines,
        a non-goal, so the verdict is not part of this flow)."""
        self.write_config(self.config_with(drop=("capabilities",), no_runner=True))
        self.stacks("stacks: python, ui")
        put(self.project / "compose.yaml", COMPOSE)

        code, output = self.check()
        actions = stripped("\n".join(user_actions(output)))
        with self.subTest(oracle=2):
            self.assertEqual(code, 1, output)
            self.assertIn(missing_line('["ui", "backend"]'), actions, output)
        with self.subTest(oracle=6, runner="missing"):
            self.assertIn(RUNNER_LINE, actions, output)

        self.write_config(self.config_with(capabilities=["ui", "backend"]))
        code, output = self.check()
        with self.subTest(oracle=7):
            self.assertEqual(code, 1, output)
            self.assertTrue(lines_with(output, "verify-init"), output)
        with self.subTest(oracle=6, runner="mapped"):
            self.assertEqual(lines_with(output, "commands.acceptance"), [], output)
            self.assertEqual(lines_with(output, "capabilities"), [], output)

        self.add_verify_skill()
        config = json.loads((self.project / "dev-loop.json").read_text())
        self.assertEqual(config.get("capabilities"), ["ui", "backend"], config)
        code, output = self.check()
        with self.subTest(flow=1, step="keys and verify skill in place"):
            self.assertEqual(lines_with(output, "capabilities", "commands.acceptance"), [], output)
            self.assertEqual(verify_lines(output), [], output)

        self.write_config({**config, "capabilities": ["ui"]})
        code, output = self.check()
        with self.subTest(oracle=4):
            self.assertEqual(lines_with(output, "capabilities"), [], output)


class CliFlowTests(SetupProject):
    def test_f2_cli_project(self) -> None:
        """F2, O1, O6, O8; with the Verify Skill (its O7 as replaced by O8): a project with no `stacks:` line and no
        compose file gets the proposal `[]`; with `[]` and then the runner no line about `verify` asks for anything,
        whatever the `AGENTS.md` `stacks:` line holds."""
        self.write_config(self.config_with(drop=("capabilities",), no_runner=True))
        code, output = self.check()
        with self.subTest(oracle=1):
            self.assertEqual(code, 1, output)
            self.assertIn(missing_line("[]"), stripped("\n".join(user_actions(output))), output)

        self.write_config(self.config_with(capabilities=[], no_runner=True))
        code, output = self.check()
        with self.subTest(oracle=6, runner="missing"):
            self.assertEqual(code, 1, output)
            self.assertIn(RUNNER_LINE, stripped("\n".join(user_actions(output))), output)
            self.assertEqual(lines_with(output, "capabilities"), [], output)
        with self.subTest(flow=2, step="[] without the runner"):
            self.assertEqual(verify_lines(output), [], output)

        self.write_config(self.config_with(capabilities=[]))
        code, output = self.check()
        with self.subTest(oracle=6, runner="mapped"):
            self.assertEqual(lines_with(output, "commands.acceptance"), [], output)
            self.assertEqual(code, 0, output)
        with self.subTest(flow=2, step="[] with the runner"):
            self.assertEqual(verify_lines(output), [], output)

        self.stacks("stacks: python, ui")
        for value in ([], ["backend"]):
            self.write_config(self.config_with(capabilities=value))
            code, output = self.check()
            with self.subTest(oracle=8, capabilities=value):
                self.assertEqual(verify_lines(output), [], output)


class KeyShapeTests(SetupProject):
    def test_o3_o5_backend_proposal_and_invalid_values(self) -> None:
        """O3: `stacks: go` and `docker-compose.yml` propose `["backend"]`. O5: a value that is not a list of
        distinct values from `ui`, `backend` fails with the invalid line."""
        self.write_config(self.config_with(drop=("capabilities",)))
        self.stacks("stacks: go")
        put(self.project / "docker-compose.yml", COMPOSE)
        code, output = self.check()
        with self.subTest(oracle=3):
            self.assertEqual(code, 1, output)
            self.assertIn(missing_line('["backend"]'), stripped("\n".join(user_actions(output))), output)

        for value in ("ui", ["ui", "ui"], ["mobile"], {}):
            self.write_config(self.config_with(capabilities=value))
            code, output = self.check()
            with self.subTest(oracle=5, capabilities=value):
                self.assertEqual(code, 1, output)
                self.assertIn(INVALID_LINE, stripped(output), output)


class PlaywrightKeyTests(SetupProject):
    def test_o9_playwright_folder_must_be_in_the_repo(self) -> None:
        """O9: on a `ui` project with a valid verify skill, a `verify.playwright` that is absolute, has a `..`
        part, is empty, or is a link to a folder outside the repo fails; a folder in the repo does not."""
        self.write_config(self.config_with(capabilities=["ui"]))
        self.add_verify_skill()
        config = json.loads((self.project / "dev-loop.json").read_text())
        put(self.project / "web" / "package.json", '{"name": "kettle-web", "private": true}\n')
        outside = self.root / "elsewhere"
        put(outside / "package.json", '{"name": "elsewhere", "private": true}\n')
        os.symlink(outside, self.project / "linked-web", target_is_directory=True)

        for value in ("/tmp", "../web", "", "linked-web"):
            self.write_config({**config, "verify": {**config["verify"], "playwright": value}})
            code, output = self.check()
            with self.subTest(oracle=9, playwright=value):
                self.assertEqual(code, 1, output)
                self.assertIn(PLAYWRIGHT_LINE, stripped(output), output)

        self.write_config({**config, "verify": {**config["verify"], "playwright": "web"}})
        code, output = self.check()
        with self.subTest(oracle=9, playwright="web"):
            self.assertNotIn(PLAYWRIGHT_LINE, stripped(output), output)
            self.assertEqual(lines_with(output, "verify.playwright"), [], output)


if __name__ == "__main__":
    unittest.main()
