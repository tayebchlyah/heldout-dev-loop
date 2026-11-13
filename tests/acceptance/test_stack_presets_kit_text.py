#!/usr/bin/env python3
"""Held-out oracle tests for stack presets (spec: 20261113 Stack Presets), the kit text seam.

Seam 3: the kit files of the source tree read as text and JSON, and this repo's `dev-loop.json`; O25 also runs
`check_setup.py --harness claude --repo <this repo>`. One test for flow F3, plus the oracle lines no flow holds;
each docstring names the `## Acceptance oracle` lines (O<n>) it checks. A stage section of `skill/SKILL.md` runs
from its `## <n> ` heading to the next stage heading.

Fixture choices the spec leaves open:
- A folder entry of `presets.json` is a non-empty relative path that ends in `/`.
- A "stacks line" of `check_setup.py` is a stripped stdout line that starts with `- stacks `.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = REPO_ROOT / "skill"
SKILL_MD = SKILL / "SKILL.md"
BOOTSTRAP = SKILL / "references" / "bootstrap.md"
PRESETS = SKILL / "presets.json"
CHECKER = SKILL / "scripts" / "check_setup.py"

NAMES = ["maven", "gradle", "go", "uv", "poetry", "pip", "pnpm", "yarn", "npm", "cargo", "dotnet", "bundler"]
FAMILIES = {"python", "js", "jvm", "go", "rust", "dotnet", "ruby"}
FIELDS = {"name", "family", "detect", "lint", "typecheck", "test", "junit", "source_dirs", "test_dir", "layout",
          "source"}
JUNIT_FIELDS = {"command", "files", "package", "setup"}
BOOTSTRAP_NAMES = ("stack-detect", "presets.json", "notes-migrate", "--apply", "capabilities", "devloop.py verify-init",
                   "references/verify.md", "references/coding-standards.md", "DEVLOOP_REPORT_DIR")


def stage(number: int) -> str:
    found = re.search(rf"(?ms)^## {number} .*?(?=^## {number + 1} )", SKILL_MD.read_text())
    if found is None:
        raise AssertionError(f"no Stage {number} section in skill/SKILL.md")
    return found.group(0)


def is_folder(value: object) -> bool:
    return (isinstance(value, str) and value.endswith("/") and len(value) > 1 and not value.startswith("/")
            and ".." not in value.split("/"))


class PresetsFlowTests(unittest.TestCase):
    def test_f3_o14_presets_file_meets_the_schema(self) -> None:
        """F3, O14: `skill/presets.json` names the 12 presets in order, each with every field (`junit` with its four
        keys, `family` from the P10 list), each `source` a non-empty list of `https://` URLs, and `ui_frameworks`."""
        data = json.loads(PRESETS.read_text())
        self.assertIsInstance(data, dict)
        frameworks = data.get("ui_frameworks")
        self.assertIsInstance(frameworks, list, "ui_frameworks")
        self.assertTrue(frameworks and all(isinstance(name, str) and name for name in frameworks), frameworks)
        entries = data.get("presets")
        self.assertIsInstance(entries, list)
        self.assertEqual([entry.get("name") for entry in entries], NAMES)
        for entry in entries:
            with self.subTest(oracle=14, preset=entry.get("name")):
                self.assertLessEqual(FIELDS, set(entry), sorted(FIELDS - set(entry)))
                self.assertIn(entry["family"], FAMILIES)
                detect = entry["detect"]
                self.assertTrue(isinstance(detect, list) and detect, detect)
                for alternative in detect:
                    self.assertTrue(isinstance(alternative, list) and alternative, detect)
                    self.assertTrue(all(isinstance(name, str) and name for name in alternative), detect)
                for key in ("lint", "typecheck", "test"):
                    self.assertTrue(entry[key] is None or isinstance(entry[key], str), key)
                junit = entry["junit"]
                self.assertIsInstance(junit, dict)
                self.assertLessEqual(JUNIT_FIELDS, set(junit), sorted(JUNIT_FIELDS - set(junit)))
                self.assertTrue(isinstance(entry["source_dirs"], list)
                                and all(is_folder(folder) for folder in entry["source_dirs"]), entry["source_dirs"])
                self.assertTrue(entry["test_dir"] is None or is_folder(entry["test_dir"]), entry["test_dir"])
                layout = entry["layout"]
                self.assertIsInstance(layout, dict)
                self.assertTrue(layout.get("init") is None or isinstance(layout.get("init"), str), layout)
                self.assertIsInstance(layout.get("dirs"), list, layout)
                source = entry["source"]
                self.assertTrue(isinstance(source, list) and source, source)
                self.assertTrue(all(isinstance(url, str) and url.startswith("https://") for url in source), source)


class Stage0TextTests(unittest.TestCase):
    def test_o22_o23_stage_0_moves_to_bootstrap(self) -> None:
        """O22: `skill/SKILL.md` `## 0 Bootstrap` names `references/bootstrap.md` and holds none of
        `pyproject.toml`, `go.mod`, `uv init`. O23: `skill/references/bootstrap.md` names the Stage 0 names, `ui`
        and `backend`, and does not hold "empty `CODING_STANDARDS.md`"."""
        text = stage(0)
        with self.subTest(oracle=22):
            self.assertRegex(text, r"(?m)^## 0 Bootstrap")
            self.assertIn("references/bootstrap.md", text)
            for name in ("pyproject.toml", "go.mod", "uv init"):
                self.assertNotIn(name, text)
        bootstrap = BOOTSTRAP.read_text()
        with self.subTest(oracle=23):
            for name in BOOTSTRAP_NAMES:
                self.assertIn(name, bootstrap)
            self.assertRegex(bootstrap, r"(?<![\w-])ui(?![\w-])")
            self.assertRegex(bootstrap, r"(?<![\w-])backend(?![\w-])")
            self.assertNotIn("empty `CODING_STANDARDS.md`", bootstrap)


class ContractAndRepoTests(unittest.TestCase):
    def test_o24_o25_contract_task_and_this_repo_record(self) -> None:
        """O24: `skill/SKILL.md` Stage 4 makes two or more `stacks` entries a reason for the contract task. O25:
        this repo's `dev-loop.json` has no `stacks` key and `check_setup.py --harness claude --repo <this repo>`
        prints no stacks line."""
        with self.subTest(oracle=24):
            lines = [line.strip() for line in stage(4).splitlines()
                     if "contract" in line.lower() and "stacks" in line
                     and re.search(r"\b(?:two|2) or more\b", line, re.IGNORECASE)]
            self.assertTrue(lines, "no Stage 4 line makes two or more stacks entries a reason for the contract task")
        with self.subTest(oracle=25, part="dev-loop.json"):
            self.assertNotIn("stacks", json.loads((REPO_ROOT / "dev-loop.json").read_text()))
        with self.subTest(oracle=25, part="check_setup"):
            result = subprocess.run([sys.executable, str(CHECKER), "--harness", "claude", "--repo", str(REPO_ROOT)],
                                    cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
            output = result.stdout + result.stderr
            self.assertEqual([line for line in output.splitlines() if line.strip().startswith("- stacks ")], [],
                             output[-3000:])


if __name__ == "__main__":
    unittest.main()
