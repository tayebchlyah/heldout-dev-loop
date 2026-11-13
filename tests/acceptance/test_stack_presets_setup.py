#!/usr/bin/env python3
"""Held-out oracle tests for stack presets (spec: 20261113 Stack Presets), the `check_setup.py` seam.

Seam 2: the `check_setup.py --harness claude` process of the kit source on a fixture repo, its stdout and exit code;
flow F1 also runs `devloop.py stack-detect` on it. One test per flow F1 and F2, plus the oracle lines no flow holds;
each docstring names the `## Acceptance oracle` lines (O<n>) it checks.

Fixture choices the spec leaves open:
- The fixture repo is the Stack Model setup project (`stack_model_support.SetupProject`): installed, on `main`,
  `check_setup.py --harness claude` passes with `"capabilities": []`, `commands.acceptance` and
  `"source_dirs": ["src/"]`. Each case writes `dev-loop.json` and its folders, then commits them on `main`, so the
  checkout and the main ref agree on `source_dirs`.
- A "stacks line" is one of the five new failure lines: a stripped stdout line that starts with `- stacks `.
  O21 "a line that names `stacks`" is any stdout line that holds `stacks`.
"""

from __future__ import annotations

import shutil
import sys
import unittest

from stack_model_support import DEVLOOP, SetupProject, commit_all, put, run_json, stripped

INVALID = "- stacks in dev-loop.json is invalid: "
REACT_PACKAGE = '{"dependencies": {"react": "1"}}\n'
COMPOSE = "services:\n  api:\n    image: kettle-api:1\n"


def unknown_line(name: str) -> str:
    return f"- stacks in dev-loop.json names an unknown preset: {name}"


def folder_line(path: str) -> str:
    return f"- stacks path {path} is not a folder in this checkout"


def cover_line(path: str) -> str:
    return f'- stacks path {path} is not in source_dirs: add "{path}"'


def stacks_lines(output: str) -> list[str]:
    return [line for line in stripped(output) if line.startswith("- stacks ")]


class StacksSetup(SetupProject):
    def apply(self, **changes: object) -> tuple[int, str]:
        """Write the base config with `changes`, commit on `main`, run `check_setup.py`."""
        self.write_config(self.config_with(**changes))
        commit_all(self.project, self.env, "Change the setup")
        return self.check()

    def folder(self, *paths: str) -> None:
        for path in paths:
            put(self.project / path / "keep.txt", "")

    def remove(self, path: str) -> None:
        shutil.rmtree(self.project / path)


class TwoUnitFlowTests(StacksSetup):
    def test_f1_two_unit_repo_written_as_proposed(self) -> None:
        """F1, O5; with the Stack Model (`capabilities`) and the Source Dirs Wall (`source_dirs`): `stack-detect` on
        an existing repo with `backend/` (uv), `frontend/` (npm, react) and `compose.yaml` proposes both units,
        `["ui", "backend"]` and `["backend/", "frontend/"]`; with `stacks`, `capabilities` and `source_dirs` written
        as proposed, `check_setup.py` prints no stacks line."""
        put(self.project / "backend" / "pyproject.toml", "")
        put(self.project / "backend" / "uv.lock", "")
        put(self.project / "frontend" / "package.json", REACT_PACKAGE)
        put(self.project / "compose.yaml", COMPOSE)
        commit_all(self.project, self.env, "Add the two units")

        code, proposal = run_json([sys.executable, str(DEVLOOP), "stack-detect", "--repo", str(self.project)],
                                  self.project, self.env)
        self.assertEqual(code, 0, proposal)
        stacks = proposal.get("stacks", [])
        self.assertEqual([(entry.get("path"), entry.get("preset")) for entry in stacks],
                         [("backend/", "uv"), ("frontend/", "npm")], proposal)
        self.assertEqual(proposal.get("capabilities"), ["ui", "backend"], proposal)
        self.assertEqual(proposal.get("source_dirs"), ["backend/", "frontend/"], proposal)

        record = [{"path": entry["path"], "preset": entry["preset"]} for entry in stacks]
        code, output = self.apply(stacks=record, capabilities=proposal["capabilities"],
                                  source_dirs=proposal["source_dirs"])
        self.assertEqual(stacks_lines(output), [], output)


class CoverageDriftFlowTests(StacksSetup):
    def test_f2_o18_o21_new_unit_without_source_dirs_entry(self) -> None:
        """F2, O18, O21: with no `stacks` key no line names `stacks`; a `stacks` entry `api/` for a new folder with
        `source_dirs` `["src/"]` fails with the `add "api/"` line; after the `source_dirs` entry, no stacks line."""
        with self.subTest(oracle=21):
            code, output = self.check()
            self.assertEqual([line for line in output.splitlines() if "stacks" in line], [], output)

        put(self.project / "api" / "go.mod", "module example.invalid/api\n")
        record = [{"path": "api/", "preset": "go"}]
        with self.subTest(oracle=18, source_dirs=["src/"]):
            code, output = self.apply(stacks=record)
            self.assertEqual(code, 1, output)
            self.assertIn(cover_line("api/"), stripped(output), output)
        with self.subTest(flow=2, source_dirs=["src/", "api/"]):
            code, output = self.apply(stacks=record, source_dirs=["src/", "api/"])
            self.assertEqual(stacks_lines(output), [], output)
            self.assertEqual(code, 0, output)
        with self.subTest(oracle=18, source_dirs=["api/"]):
            code, output = self.apply(stacks=record, source_dirs=["api/"])
            self.assertEqual(stacks_lines(output), [], output)


class RecordShapeTests(StacksSetup):
    def test_o15_invalid_record(self) -> None:
        """O15: `stacks` `{}`, a path out of the repo, an entry with no `preset`, or a duplicate path: exit 1, the
        `stacks in dev-loop.json is invalid` line."""
        self.folder("api")
        cases = (("object", {}),
                 ("outside path", [{"path": "../x/", "preset": "go"}]),
                 ("no preset", [{"path": "api/"}]),
                 ("duplicate path", [{"path": "api/", "preset": "go"}, {"path": "api/", "preset": "go"}]))
        for name, value in cases:
            with self.subTest(oracle=15, case=name):
                code, output = self.apply(stacks=value, source_dirs=["src/", "api/"])
                self.assertEqual(code, 1, output)
                self.assertTrue([line for line in stripped(output) if line.startswith(INVALID)], output)

    def test_o16_o17_unknown_preset_and_missing_folder(self) -> None:
        """O16: preset `gradel`: exit 1, the `unknown preset: gradel` line; preset `null`: no such line. O17: path
        `web/` with no `web/` folder: exit 1, the `not a folder in this checkout` line."""
        self.folder("api")
        with self.subTest(oracle=16, preset="gradel"):
            code, output = self.apply(stacks=[{"path": "api/", "preset": "gradel"}], source_dirs=["src/", "api/"])
            self.assertEqual(code, 1, output)
            self.assertIn(unknown_line("gradel"), stripped(output), output)
        with self.subTest(oracle=16, preset=None):
            code, output = self.apply(stacks=[{"path": "api/", "preset": None}], source_dirs=["src/", "api/"])
            self.assertEqual([line for line in output.splitlines() if "unknown preset" in line], [], output)
        with self.subTest(oracle=17):
            code, output = self.apply(stacks=[{"path": "web/", "preset": "go"}])
            self.assertEqual(code, 1, output)
            self.assertIn(folder_line("web/"), stripped(output), output)


class RootUnitCoverageTests(StacksSetup):
    def test_o19_o20_root_unit_preset_folders(self) -> None:
        """O19: `stacks` `.` go with `cmd/` and `internal/` and `source_dirs` `["cmd/"]`: exit 1, a stacks line
        that names `internal/`; with no `internal/` folder, no stacks line. O20: `.` with preset `dotnet` or `null`:
        no coverage line."""
        self.folder("cmd", "internal")
        root_go = [{"path": ".", "preset": "go"}]
        with self.subTest(oracle=19, internal="present"):
            code, output = self.apply(stacks=root_go, source_dirs=["cmd/"])
            self.assertEqual(code, 1, output)
            self.assertTrue([line for line in stacks_lines(output) if "internal/" in line], output)
        for preset in ("dotnet", None):
            with self.subTest(oracle=20, preset=preset):
                code, output = self.apply(stacks=[{"path": ".", "preset": preset}], source_dirs=["src/"])
                self.assertEqual([line for line in output.splitlines() if "not in source_dirs" in line], [], output)
                self.assertEqual(stacks_lines(output), [], output)
        self.remove("internal")
        with self.subTest(oracle=19, internal="absent"):
            code, output = self.apply(stacks=root_go, source_dirs=["cmd/"])
            self.assertEqual(stacks_lines(output), [], output)


if __name__ == "__main__":
    unittest.main()
