#!/usr/bin/env python3
"""Held-out oracle tests for uv workspace units (spec: 20261116 Uv Workspace Units), the `stack-detect` seam.

Seam 1: the `devloop.py stack-detect --repo <path>` process of the kit source on a fixture tree, its stdout JSON
and exit code. Each docstring names the `## Acceptance oracle` lines (O<n>) it checks. Flows F1 and F2, with O1 and
O17, are in `test_uv_workspace_units_setup.py`.

Fixture choices the spec leaves open:
- Each fixture tree is a fresh git repo (`git init`, nothing committed), as in the Stack Presets tests. Files are
  empty unless a content is named. A folder the oracle names with no file in it holds one empty file whose name
  matches no preset (`src/app.py`, `src/index.js`, `tests/unit/test_app.py`,
  `cmd/main.go`, `backend/README.md`, `packages/c/README.md`).
- `ws(m)`: the root `pyproject.toml` text `[tool.uv.workspace]` with `members = m` (a TOML array), plus an empty
  root `uv.lock`. O3 adds `exclude = [...]` to the same table; O4 puts `[project]` with `name = "x"` before it.
- O6: `../outside` is the folder `outside/` beside the fixture repo folder, with its own `pyproject.toml`.
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

REACT_PACKAGE = '{"dependencies": {"react": "1"}}'
BAD_TOML = '[tool.uv.workspace\nmembers = ["backend"\n'


def ws(members: list[str], before: str = "", extra: str = "") -> dict[str, str]:
    """The root files of `ws(members)`: `pyproject.toml` with the workspace table, and `uv.lock`."""
    text = f"{before}[tool.uv.workspace]\nmembers = {json.dumps(members)}\n{extra}"
    return {"pyproject.toml": text, "uv.lock": ""}


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
        self.root = Path(tempfile.mkdtemp(prefix="uv-workspace-units-")).resolve()
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


class WorkspaceMemberTests(DetectCase):
    def test_o2_o3_member_globs_and_exclude(self) -> None:
        """O2: `members = ["packages/*"]` gives `packages/a/` (unit `packages-a`) and `packages/b/` (unit
        `packages-b`), both uv, no `packages/c/` (no `pyproject.toml`), both in `source_dirs`. O3: with
        `exclude = ["packages/b"]`, the one member entry is `packages/a/`."""
        with self.subTest(oracle=2):
            payload = self.detect("packages/a/pyproject.toml", "packages/b/pyproject.toml", "packages/c/README.md",
                                  contents=ws(["packages/*"]))
            self.assertEqual(pairs(payload), [("packages/a/", "uv"), ("packages/b/", "uv")], payload)
            self.assertEqual([entry.get("unit") for entry in payload["stacks"]], ["packages-a", "packages-b"],
                             payload)
            for folder in ("packages/a/", "packages/b/"):
                self.assertIn(folder, payload.get("source_dirs", []), payload)
        with self.subTest(oracle=3):
            payload = self.detect("packages/a/pyproject.toml", "packages/b/pyproject.toml",
                                  contents=ws(["packages/*"], extra='exclude = ["packages/b"]\n'))
            self.assertEqual(pairs(payload), [("packages/a/", "uv")], payload)

    def test_o6_o7_members_that_are_not_kept(self) -> None:
        """O6: members `backend`, `../outside`, `.hidden`, `node_modules/x`, `tests/acceptance`, each with a
        `pyproject.toml`: the only entry is `backend/`, and no `source_dirs` entry names the other four. O7: a
        `backend` member folder with no `pyproject.toml` gives no `backend/` uv entry."""
        with self.subTest(oracle=6):
            (self.root / "outside").mkdir()
            (self.root / "outside" / "pyproject.toml").write_text("")
            members = ["backend", "../outside", ".hidden", "node_modules/x", "tests/acceptance"]
            payload = self.detect("backend/pyproject.toml", ".hidden/pyproject.toml",
                                  "node_modules/x/pyproject.toml", "tests/acceptance/pyproject.toml",
                                  contents=ws(members))
            self.assertEqual(pairs(payload), [("backend/", "uv")], payload)
            for name in ("outside", ".hidden", "node_modules", "tests/acceptance"):
                self.assertEqual([entry for entry in payload.get("source_dirs", []) if name in entry], [], payload)
        with self.subTest(oracle=7):
            payload = self.detect("backend/README.md", contents=ws(["backend"]))
            self.assertNotIn(("backend/", "uv"), pairs(payload), payload)


class WorkspaceRootTests(DetectCase):
    def test_o4_o5_o15_workspace_root_unit(self) -> None:
        """O4: a workspace root with `[project]` and `src/x/__init__.py`: entries `.` (uv) and `backend/` (uv),
        `source_dirs` `["backend/", "src/"]`. O5: the same with no `src/`: `backend/` only. O15: a
        `[tool.uv.workspace]` table with no `members` key, `uv.lock` and `src/`: one entry `.` uv."""
        package = '[project]\nname = "x"\n\n'
        with self.subTest(oracle=4):
            payload = self.detect("src/x/__init__.py", "backend/pyproject.toml",
                                  contents=ws(["backend"], before=package))
            self.assertEqual(pairs(payload), [(".", "uv"), ("backend/", "uv")], payload)
            self.assertEqual(payload.get("source_dirs"), ["backend/", "src/"], payload)
        with self.subTest(oracle=5):
            payload = self.detect("backend/pyproject.toml", contents=ws(["backend"], before=package))
            self.assertEqual(pairs(payload), [("backend/", "uv")], payload)
        with self.subTest(oracle=15):
            payload = self.detect("uv.lock", "src/app.py", contents={"pyproject.toml": "[tool.uv.workspace]\n"})
            self.assertEqual(pairs(payload), [(".", "uv")], payload)

    def test_o13_o14_o16_root_without_unit_and_other_family(self) -> None:
        """O13: a workspace root with no unit and a root `package.json` with react: `capabilities` holds `ui`.
        O14: `tools/pyproject.toml`, not a member, beside member `packages/a/`: entries `packages/a/` (uv) and
        `tools/` (pip), no `.`. O16: root `go.mod` and `cmd/` with a workspace of member `tools`: entries `.` (go)
        and `tools/` (uv)."""
        with self.subTest(oracle=13):
            payload = self.detect("backend/pyproject.toml", "backend/uv.lock",
                                  contents={**ws(["backend"]), "package.json": REACT_PACKAGE})
            self.assertIn("ui", payload.get("capabilities", []), payload)
        with self.subTest(oracle=14):
            payload = self.detect("packages/a/pyproject.toml", "tools/pyproject.toml", contents=ws(["packages/a"]))
            self.assertEqual(pairs(payload), [("packages/a/", "uv"), ("tools/", "pip")], payload)
        with self.subTest(oracle=16):
            text = '[tool.uv.workspace]\nmembers = ["tools"]\n'
            payload = self.detect("go.mod", "cmd/main.go", "tools/pyproject.toml",
                                  contents={"pyproject.toml": text})
            self.assertEqual(pairs(payload), [(".", "go"), ("tools/", "uv")], payload)


class ExistingFolderTests(DetectCase):
    def test_o8_to_o12_root_unit_proposes_existing_folders(self) -> None:
        """O8: a root `pyproject.toml` that does not parse, `uv.lock`, `src/`: one entry `.` uv, `source_dirs`
        `["src/"]`. O9: `pyproject.toml` and `uv.lock` alone: one entry `.` uv, `[]`. O10: with `src/` and
        `tests/unit/`: `["src/", "tests/unit/"]`. O11: `package.json` alone `[]`, with `src/` `["src/"]`. O12:
        `go.mod` and `cmd/`, no `internal/`: `["cmd/"]`."""
        with self.subTest(oracle=8):
            payload = self.detect("uv.lock", "src/app.py", contents={"pyproject.toml": BAD_TOML})
            self.assertEqual(pairs(payload), [(".", "uv")], payload)
            self.assertEqual(payload.get("source_dirs"), ["src/"], payload)
        with self.subTest(oracle=9):
            payload = self.detect("pyproject.toml", "uv.lock")
            self.assertEqual(pairs(payload), [(".", "uv")], payload)
            self.assertEqual(payload.get("source_dirs"), [], payload)
        with self.subTest(oracle=10):
            payload = self.detect("pyproject.toml", "uv.lock", "src/app.py", "tests/unit/test_app.py")
            self.assertEqual(payload.get("source_dirs"), ["src/", "tests/unit/"], payload)
        with self.subTest(oracle=11, src="absent"):
            payload = self.detect("package.json")
            self.assertEqual(payload.get("source_dirs"), [], payload)
        with self.subTest(oracle=11, src="present"):
            payload = self.detect("package.json", "src/index.js")
            self.assertEqual(payload.get("source_dirs"), ["src/"], payload)
        with self.subTest(oracle=12):
            payload = self.detect("go.mod", "cmd/main.go")
            self.assertEqual(payload.get("source_dirs"), ["cmd/"], payload)


if __name__ == "__main__":
    unittest.main()
