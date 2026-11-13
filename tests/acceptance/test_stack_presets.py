#!/usr/bin/env python3
"""Held-out oracle tests for stack presets (spec: 20261113 Stack Presets), the `stack-detect` seam.

Seam 1: the `devloop.py stack-detect --repo <path>` process of the kit source on a fixture tree, its stdout JSON
and exit code. Each docstring names the `## Acceptance oracle` lines (O<n>) it checks. The `check_setup.py` seam and
flows F1 and F2 are in `test_stack_presets_setup.py`, the kit text and flow F3 in `test_stack_presets_kit_text.py`.

Fixture choices the spec leaves open:
- Each fixture tree is a fresh git repo (`git init`, nothing committed): `stack-detect` lists the filesystem,
  tracked or not, and skips `.git/` (its name starts with `.`). Files are empty unless a content is named.
- O13: `commands.junit` holds at least `command` and `package` (the Interfaces example); each key it holds equals
  the same key of the preset `junit` in `skill/presets.json`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEVLOOP = REPO_ROOT / "skill" / "scripts" / "devloop.py"
PRESETS = REPO_ROOT / "skill" / "presets.json"

REACT_PACKAGE = '{"dependencies": {"react": "1"}}'


def presets() -> dict[str, dict]:
    return {preset["name"]: preset for preset in json.loads(PRESETS.read_text())["presets"]}


def clean_env(home: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("GIT_", "DEV_LOOP", "DEVLOOP", "CLAUDE", "CODEX"))}
    env.update({"HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
                "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def pairs(payload: dict) -> list[tuple[str, str]]:
    return [(entry.get("path"), entry.get("preset")) for entry in payload.get("stacks", [])]


class DetectCase(unittest.TestCase):
    """A scratch folder (removed after the test); `detect` builds one fixture tree and runs `stack-detect` on it."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="stack-presets-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "home").mkdir()
        self.env = clean_env(self.root / "home")
        self.trees = 0

    def detect(self, *paths: str, contents: dict[str, str] | None = None) -> dict:
        self.trees += 1
        repo = self.root / f"tree-{self.trees}"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, env=self.env, check=True, capture_output=True)
        for name in (*paths, *(contents or {})):
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text((contents or {}).get(name, ""))
        result = subprocess.run([sys.executable, str(DEVLOOP), "stack-detect", "--repo", str(repo)], cwd=repo,
                                env=self.env, capture_output=True, text=True, timeout=120)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"stdout is not one JSON object (exit {result.returncode}):\n"
                                 f"{result.stdout[-1500:]}\n{result.stderr[-1500:]}") from exc
        self.assertIsInstance(payload, dict, result.stdout)
        self.assertEqual(result.returncode, 0, payload)
        return payload

    def assert_commands(self, payload: dict) -> None:
        """O13: each entry's `commands` equal its preset in `presets.json`."""
        known = presets()
        for entry in payload.get("stacks", []):
            with self.subTest(oracle=13, path=entry.get("path")):
                preset = known[entry["preset"]]
                commands = entry.get("commands")
                self.assertIsInstance(commands, dict, entry)
                for key in ("lint", "typecheck", "test"):
                    self.assertIn(key, commands, entry)
                    self.assertEqual(commands[key], preset[key], key)
                junit = commands.get("junit")
                self.assertIsInstance(junit, dict, entry)
                self.assertLessEqual({"command", "package"}, set(junit), entry)
                for key, value in junit.items():
                    self.assertIn(key, preset["junit"], entry)
                    self.assertEqual(value, preset["junit"][key], key)


class RootPresetTests(DetectCase):
    def test_o1_to_o4_o13_root_presets(self) -> None:
        """O1: `pyproject.toml` and `uv.lock` give one entry `.`, uv, unit `root`, and `source_dirs` holds `src/` and
        `tests/unit/`. O2: poetry, pip from `pyproject.toml` alone or `requirements.txt` alone. O3: pnpm, yarn, npm.
        O4: `go.mod` gives go and `source_dirs` `["cmd/", "internal/"]`. O13 on each entry."""
        cases = ((1, ("pyproject.toml", "uv.lock"), "uv"),
                 (2, ("pyproject.toml", "poetry.lock"), "poetry"),
                 (2, ("pyproject.toml",), "pip"),
                 (2, ("requirements.txt",), "pip"),
                 (3, ("package.json", "pnpm-lock.yaml"), "pnpm"),
                 (3, ("package.json", "yarn.lock"), "yarn"),
                 (3, ("package.json",), "npm"),
                 (4, ("go.mod",), "go"))
        for oracle, files, preset in cases:
            with self.subTest(oracle=oracle, files=files):
                payload = self.detect(*files)
                self.assertEqual(pairs(payload), [(".", preset)], payload)
                self.assertEqual(payload["stacks"][0].get("unit"), "root", payload)
                if preset == "uv":
                    self.assertIn("src/", payload.get("source_dirs", []), payload)
                    self.assertIn("tests/unit/", payload.get("source_dirs", []), payload)
                if preset == "go":
                    self.assertEqual(payload.get("source_dirs"), ["cmd/", "internal/"], payload)
                self.assert_commands(payload)


class UnitFolderTests(DetectCase):
    def test_o5_o13_backend_and_frontend_units(self) -> None:
        """O5: `backend/` uv, `frontend/` npm with react, root `compose.yaml`: both entries, units, capabilities
        `["ui", "backend"]`, `source_dirs` `["backend/", "frontend/"]`. O13 on each entry."""
        payload = self.detect("backend/pyproject.toml", "backend/uv.lock", "compose.yaml",
                              contents={"frontend/package.json": REACT_PACKAGE})
        self.assertEqual(pairs(payload), [("backend/", "uv"), ("frontend/", "npm")], payload)
        self.assertEqual([entry.get("unit") for entry in payload["stacks"]], ["backend", "frontend"], payload)
        self.assertEqual(payload.get("capabilities"), ["ui", "backend"], payload)
        self.assertEqual(payload.get("source_dirs"), ["backend/", "frontend/"], payload)
        self.assert_commands(payload)

    def test_o6_o13_children_of_backend(self) -> None:
        """O6: `backend/api/go.mod` and `backend/worker/Cargo.toml` give `backend/api/` (go, unit `backend-api`) and
        `backend/worker/` (cargo, unit `backend-worker` by the unit rule). O13 on each entry."""
        payload = self.detect("backend/api/go.mod", "backend/worker/Cargo.toml")
        self.assertEqual(pairs(payload), [("backend/api/", "go"), ("backend/worker/", "cargo")], payload)
        self.assertEqual([entry.get("unit") for entry in payload["stacks"]], ["backend-api", "backend-worker"],
                         payload)
        self.assert_commands(payload)


class SkipRuleTests(DetectCase):
    def test_o7_o8_skipped_folders_and_empty_repo(self) -> None:
        """O7: build files only under `node_modules/`, `.venv/`, `tests/acceptance/` and `a/b/`: `stacks` is `[]`.
        O8: an empty repo gives the four empty lists, exit 0."""
        with self.subTest(oracle=7):
            payload = self.detect("node_modules/x/package.json", ".venv/pyproject.toml",
                                  "tests/acceptance/package.json", "a/b/go.mod")
            self.assertEqual(payload.get("stacks"), [], payload)
        with self.subTest(oracle=8):
            payload = self.detect()
            self.assertEqual(payload, {"stacks": [], "capabilities": [], "source_dirs": [], "unknown": []})

    def test_o9_unknown_folders(self) -> None:
        """O9: a root uv unit with `tests/unit/test_x.py`, `docs/a.md`, `scripts/b.sh`: `unknown` is
        `["docs/", "scripts/"]`."""
        payload = self.detect("pyproject.toml", "uv.lock", "tests/unit/test_x.py", "docs/a.md", "scripts/b.sh")
        self.assertEqual(payload.get("unknown"), ["docs/", "scripts/"], payload)


class OnePresetPerFolderTests(DetectCase):
    def test_o10_o11_o12_first_preset_and_workspace_members(self) -> None:
        """O10: root `pyproject.toml`, `uv.lock` and `package.json`: one entry, uv. O11: a python folder under the
        root uv unit is a member (no entry, in `source_dirs`), `frontend/` npm stays a unit. O12: root `package.json`
        alone: `source_dirs` `["src/", "tests/unit/"]`."""
        with self.subTest(oracle=10):
            payload = self.detect("pyproject.toml", "uv.lock", "package.json")
            self.assertEqual(pairs(payload), [(".", "uv")], payload)
        with self.subTest(oracle=11):
            payload = self.detect("pyproject.toml", "uv.lock", "backend/pyproject.toml", "frontend/package.json")
            self.assertEqual(pairs(payload), [(".", "uv"), ("frontend/", "npm")], payload)
            for folder in ("backend/", "frontend/", "src/", "tests/unit/"):
                self.assertIn(folder, payload.get("source_dirs", []), payload)
        with self.subTest(oracle=12):
            payload = self.detect("package.json")
            self.assertEqual(payload.get("source_dirs"), ["src/", "tests/unit/"], payload)


if __name__ == "__main__":
    unittest.main()
