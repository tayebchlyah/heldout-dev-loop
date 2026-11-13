#!/usr/bin/env python3
"""Tests for the mutation canary: `scripts/mutation_canary.sh` and `.github/workflows/mutation-canary.yml`."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT, write  # noqa: E402

SCRIPT = ROOT / "scripts" / "mutation_canary.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "mutation-canary.yml"
CONFIG = {"version": "0.0.0", "mutation": [{"path": ".", "command": "./adapt", "critical": ["core.py"]}]}
MUTANT = {"unit": "u", "file": "core.py", "line": 2, "status": "killed", "diff": None}
OUTPUT = json.dumps({"units": ["u"], "mutants": [MUTANT], "not_covered": []})
ADAPT = f"#!/bin/sh\ncat >/dev/null\nprintf '%s\\n' '{OUTPUT}'\n"
EXPECTED = """base: <base>
head: <head>
entry .: ./adapt pass
. mutants run: 1
. units that need one unit test: 0
. message-only survivors, to Deferred: 0
. inconclusive: 0
. not covered by the selection: 0
outside the critical modules: 0
"""


class MutationCanary(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def canary(self, expected: str) -> subprocess.CompletedProcess[str]:
        fixture = self.root / "fixture"
        write(fixture / "base" / "dev-loop.json", json.dumps(CONFIG))
        write(fixture / "base" / "core.py", "a = 1\nb = 2\n")
        write(fixture / "base" / "adapt", ADAPT)
        (fixture / "base" / "adapt").chmod(0o755)
        write(fixture / "head" / "core.py", "a = 1\nb = 3\n")
        write(fixture / "expected.txt", expected)
        scratch = self.root / "tmp"
        scratch.mkdir(exist_ok=True)
        return subprocess.run([str(SCRIPT), str(fixture)], capture_output=True, text=True, timeout=120,
                              env={**os.environ, "TMPDIR": str(scratch)})

    def test_canary_passes_and_fails(self) -> None:
        done = self.canary(EXPECTED)
        self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)
        self.assertEqual(list((self.root / "tmp").iterdir()), [])
        done = self.canary(EXPECTED.replace("pass", "failed"))
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("+entry .: ./adapt pass", done.stdout)
        self.assertEqual(list((self.root / "tmp").iterdir()), [])

    def test_workflow(self) -> None:
        text = WORKFLOW.read_text()
        for needle in ("schedule:", "cron:", "workflow_dispatch:", "pull_request:", "- skill/scripts/mutation/**",
                       "- tests/fixtures/mutation/**", "adapter: [mutmut, stryker, mutago]",
                       "scripts/mutation_canary.sh tests/fixtures/mutation/${{ matrix.adapter }}"):
            self.assertIn(needle, text)
        uses = re.findall(r"uses: (\S+)", text)
        self.assertTrue(uses)
        for action in uses:
            self.assertRegex(action, r"^[\w./-]+@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
