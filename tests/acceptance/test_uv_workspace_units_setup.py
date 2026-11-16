#!/usr/bin/env python3
"""Held-out flow tests for uv workspace units (spec: 20261116 Uv Workspace Units), flows F1 and F2.

Seams: the `devloop.py stack-detect --repo <path>` process of the kit source, its stdout JSON and exit code (seam 1);
the `check_setup.py --harness claude` process on the same fixture repo, its stdout and exit code (seam 2). Each
docstring names the `## Acceptance oracle` lines (O<n>) it checks. The other oracle lines are in
`test_uv_workspace_units.py`.

Fixture choices the spec leaves open:
- The fixture repo is the Stack Model setup project (`stack_model_support.SetupProject`): installed, on `main`,
  `check_setup.py --harness claude` passes with `"capabilities": []`, `commands.acceptance` and
  `"source_dirs": ["src/"]`. Each case removes the folders it does not name (`src/`, and for F2 `scripts/`), adds
  its tree, writes `dev-loop.json` and commits on `main`, so the checkout and the main ref agree.
- `ws(["backend"])`: the root `pyproject.toml` text `[tool.uv.workspace]` with `members = ["backend"]`, plus an
  empty root `uv.lock` (the spec's notation).
- A "stacks line" is a stripped stdout line that starts with `- stacks ` (as in the Stack Presets tests).
- F2 "its proposal holds only folders that exist": a new repo with a root `package.json` (preset `npm`, no `src/` or
  `tests/unit/` folder), a Go unit `api/` (`api/go.mod`) and `docs/guide.md`; each `source_dirs` entry of the
  proposal is a folder of the repo, and the proposal is `["api/"]`.
"""

from __future__ import annotations

import shutil
import sys
import unittest

from stack_model_support import DEVLOOP, SetupProject, commit_all, put, run_json, stripped

MISSING = "source_dirs missing in dev-loop.json"
POINTER = ". devloop.py stack-detect proposes the value"
WORKSPACE = '[tool.uv.workspace]\nmembers = ["backend"]\n'


def stacks_lines(output: str) -> list[str]:
    return [line for line in stripped(output) if line.startswith("- stacks ")]


class WorkspaceSetup(SetupProject):
    def drop(self, *folders: str) -> None:
        for folder in folders:
            shutil.rmtree(self.project / folder, ignore_errors=True)

    def propose(self) -> dict:
        code, proposal = run_json([sys.executable, str(DEVLOOP), "stack-detect", "--repo", str(self.project)],
                                  self.project, self.env)
        self.assertEqual(code, 0, proposal)
        return proposal

    def assert_existing(self, proposal: dict) -> None:
        for folder in proposal.get("source_dirs", []):
            path = self.project / folder
            self.assertTrue(path.is_dir() and not path.is_symlink(), f"{folder} is not a folder: {proposal}")


class WorkspaceFlowTests(WorkspaceSetup):
    def test_f1_o1_workspace_written_as_proposed(self) -> None:
        """F1, O1; with Stack Presets (the `check_setup.py` stacks lines): `ws(["backend"])`,
        `backend/pyproject.toml`, `backend/tests/unit/test_a.py` and `frontend/package.json` give the entries
        `backend/` (uv, unit `backend`) and `frontend/` (npm), no `.` entry, `source_dirs` `["backend/",
        "frontend/"]`, each an existing folder; with `stacks`, `capabilities` and `source_dirs` written as proposed,
        `check_setup.py` prints no stacks line."""
        self.drop("src")
        put(self.project / "pyproject.toml", WORKSPACE)
        put(self.project / "uv.lock", "")
        put(self.project / "backend" / "pyproject.toml", "")
        put(self.project / "backend" / "tests" / "unit" / "test_a.py", "")
        put(self.project / "frontend" / "package.json", "")
        commit_all(self.project, self.env, "Add the workspace")

        proposal = self.propose()
        stacks = proposal.get("stacks", [])
        self.assertEqual([(entry.get("path"), entry.get("preset")) for entry in stacks],
                         [("backend/", "uv"), ("frontend/", "npm")], proposal)
        self.assertEqual(stacks[0].get("unit"), "backend", proposal)
        self.assertEqual(proposal.get("source_dirs"), ["backend/", "frontend/"], proposal)
        self.assert_existing(proposal)

        record = [{"path": entry["path"], "preset": entry["preset"]} for entry in stacks]
        self.write_config(self.config_with(stacks=record, capabilities=proposal.get("capabilities", []),
                                           source_dirs=proposal["source_dirs"]))
        commit_all(self.project, self.env, "Write the proposal")
        code, output = self.check()
        self.assertEqual(stacks_lines(output), [], output)

    def test_f2_o17_missing_key_points_to_stack_detect(self) -> None:
        """F2, O17: a new repo with no `source_dirs` key and the folders `api/` and `docs/`: the
        `source_dirs missing in dev-loop.json` line ends with `. devloop.py stack-detect proposes the value` and
        lists `Top-level folders: api/ docs/`; the `stack-detect` proposal holds only folders that exist."""
        self.drop("src", "scripts")
        put(self.project / "package.json", "")
        put(self.project / "api" / "go.mod", "module example.invalid/api\n")
        put(self.project / "docs" / "guide.md", "# Guide\n")
        self.write_config(self.config_with(drop=("source_dirs",)))
        commit_all(self.project, self.env, "A new repo with no source_dirs")

        code, output = self.check()
        lines = [line for line in stripped(output) if MISSING in line]
        self.assertEqual(len(lines), 1, output[-3000:])
        self.assertTrue(lines[0].endswith(POINTER), lines[0])
        self.assertIn("Top-level folders: api/ docs/", lines[0])

        proposal = self.propose()
        self.assert_existing(proposal)
        self.assertEqual(proposal.get("source_dirs"), ["api/"], proposal)


if __name__ == "__main__":
    unittest.main()
