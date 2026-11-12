"""capabilities: the validator, the proposal and the check_setup.py capabilities and commands.acceptance lines."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import CHECKER, checker_env, write

import capabilities


class CapabilitiesTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def repo(self, name: str, config: object = None, stacks: str | None = None, compose: str | None = None) -> Path:
        repo = self.root / name
        repo.mkdir()
        if config is not None:
            write(repo / "dev-loop.json", json.dumps(config) + "\n")
        if stacks is not None:
            write(repo / "AGENTS.md", f"# AGENTS.md\n\nstacks: {stacks}\n")
        if compose is not None:
            write(repo / compose, "services: {}\n")
        return repo

    def test_valid_table(self) -> None:
        for value in ([], ["ui"], ["backend", "ui"]):
            self.assertTrue(capabilities.valid(value), value)
        for value in ("ui", ["ui", "ui"], ["mobile"], {}, [1], [["ui"]], None):
            self.assertFalse(capabilities.valid(value), value)

    def test_proposal(self) -> None:
        self.assertEqual(capabilities.proposal(self.repo("a")), [])
        self.assertEqual(capabilities.proposal(self.repo("b", stacks="python, ui", compose="compose.yaml")), ["ui", "backend"])
        self.assertEqual(capabilities.proposal(self.repo("c", stacks="go", compose="docker-compose.yml")), ["backend"])
        self.assertEqual(capabilities.proposal(self.repo("d", stacks="python,ui")), ["ui"])
        for n, name in enumerate(capabilities.COMPOSE_FILES):
            self.assertEqual(capabilities.proposal(self.repo(f"e{n}", compose=name)), ["backend"], name)

    def test_setup_lines(self) -> None:
        missing = capabilities.MISSING.format("[]")
        self.assertEqual(capabilities.setup(self.repo("a", {})), ([], [missing, capabilities.ACCEPTANCE_MISSING]))
        mapped = {"commands": {"acceptance": "run"}}
        self.assertEqual(capabilities.setup(self.repo("b", {"capabilities": ["ui"], **mapped})), ([], []))
        for n, value in enumerate(("ui", ["ui", "ui"], ["mobile"], {})):
            repo = self.repo(f"c{n}", {"capabilities": value, **mapped})
            self.assertEqual(capabilities.setup(repo), ([capabilities.INVALID], []), value)

    def test_setup_skips_unreadable_config(self) -> None:
        self.assertEqual(capabilities.setup(self.repo("a", [])), ([], []))
        self.assertEqual(capabilities.setup(self.repo("b")), ([], []))
        self.assertEqual(capabilities.setup(self.repo("c", {"capabilities": [], "commands": "x"})), ([], []))

    def check(self, repo: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(CHECKER), "--harness", "claude", "--home", str(self.root / "home"),
                               "--repo", str(repo)], capture_output=True, text=True, check=False,
                              env=checker_env(self.root / "bin"))

    def test_check_setup_prints_the_lines(self) -> None:
        result = self.check(self.repo("a", {"version": "3.0.0"}, stacks="python, ui", compose="compose.yaml"))
        self.assertEqual(result.returncode, 1)
        lines = result.stdout.splitlines()
        self.assertIn("- " + capabilities.MISSING.format('["ui", "backend"]'), lines)
        self.assertIn("- " + capabilities.ACCEPTANCE_MISSING, lines)
        quiet = self.check(self.repo("b", {"version": "3.0.0", "capabilities": ["ui"], "commands": {"acceptance": "run"},
                                           "verify": False}))
        self.assertEqual([line for line in quiet.stdout.splitlines()
                          if "capabilities" in line or "commands.acceptance missing" in line], [])
        invalid = self.check(self.repo("c", {"version": "3.0.0", "capabilities": "ui", "commands": {"acceptance": "run"}}))
        self.assertIn("- " + capabilities.INVALID, invalid.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
