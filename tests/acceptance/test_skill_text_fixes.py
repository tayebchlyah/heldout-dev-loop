#!/usr/bin/env python3
"""Held-out acceptance tests for `gate --summary --changed` (spec: Skill Text Fixes).

F1 (oracle 1, 4, 5, 6), F2 (oracle 2, 5), F3 (oracle 3): `devloop.py gate --summary [--changed]` (the kit source,
`skill/scripts/devloop.py`) run as a process in the clean main checkout of a `RatchetRepo` scratch repo
(`time_ratchet_support.py`): a bare `origin`, `main` pushed, `commands.gate` a shell script outside the repo.

Fixture choices the spec leaves open:
- The gate script also writes `$F/scope.log`: `set:<value>` when `GATE_SCOPE` is in its environment, else `unset`.
- The caller environment never holds `GATE_SCOPE` unless a case sets it.
- The failing project gate exits 3, so its exit is not the exit of the process.
- `ratchet.record` null on the `--changed` run is backed by no record file for the HEAD tree.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from merge_check_binding_support import DEVLOOP
from time_ratchet_support import BASE, RatchetRepo, same_path

SCOPE_RUNNER = """#!/bin/sh
F='{fix}'
echo ran >> "$F/ran.log"
if [ -n "${{GATE_SCOPE+x}}" ]; then printf 'set:%s\\n' "$GATE_SCOPE" > "$F/scope.log"; else echo unset > "$F/scope.log"; fi
mkdir -p "$DEVLOOP_REPORT_DIR"
if [ -f "$F/report.xml" ]; then cp "$F/report.xml" "$DEVLOOP_REPORT_DIR/fixture.xml"; fi
exit "$(cat "$F/exit")"
"""


class ScopeRepo(RatchetRepo):
    """A `RatchetRepo` whose gate script logs what it sees of `GATE_SCOPE`."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.env.pop("GATE_SCOPE", None)
        (self.fix / "run.sh").write_text(SCOPE_RUNNER.format(fix=self.fix))

    def scope_seen(self) -> str:
        return (self.fix / "scope.log").read_text().strip()

    def run_gate(self, *flags: str, env: dict[str, str] | None = None, exit_code: int = 0) -> tuple[int, dict]:
        self.fixture(BASE, exit_code)
        (self.fix / "scope.log").unlink(missing_ok=True)
        return self.run_cli(self.repo, "gate", "--summary", *flags, env=env)

    def record_path(self) -> Path:
        return self.records("gate") / f"{self.tree(self.repo)}.json"


class GateScopeTests(unittest.TestCase):

    def repo(self) -> ScopeRepo:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return ScopeRepo(Path(tmp.name).resolve())

    def ratchet(self, payload: dict) -> dict:
        self.assertIsInstance(payload.get("ratchet"), dict, payload)
        return payload["ratchet"]

    def test_f1_fix_wave_runs_the_changed_scope(self) -> None:
        """F1, O1, O4, O5, O6."""
        repo = self.repo()

        code, out = repo.run_gate("--changed")
        with self.subTest(oracle=1):
            self.assertEqual(repo.scope_seen(), "set:changed", out)
            self.assertEqual(out.get("scope"), "changed", out)
        with self.subTest(oracle=5):
            self.assertEqual(code, 0, out)
            self.assertEqual(out.get("exit"), 0, out)
            self.assertIsNone(self.ratchet(out).get("record"), out)
            self.assertFalse(repo.record_path().exists(), out)

        with self.subTest(oracle=4):
            code, out = repo.run_gate("--changed", env={"GATE_SCOPE": "other"})
            self.assertEqual(repo.scope_seen(), "set:changed", out)
            self.assertEqual(out.get("scope"), "changed", out)

        with self.subTest(oracle=6, case="failing project gate"):
            code, out = repo.run_gate("--changed", exit_code=3)
            self.assertEqual(code, 1, out)
            self.assertEqual(out.get("exit"), 3, out)
            self.assertEqual(out.get("scope"), "changed", out)

        with self.subTest(oracle=6, case="no --summary"):
            result = subprocess.run([sys.executable, str(DEVLOOP), "gate", "--changed"], cwd=repo.repo,
                                    env=repo.env, capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 2, f"{result.stdout[-600:]!r} {result.stderr[-600:]!r}")

    def test_f2_stage_end_gate_runs_the_full_scope(self) -> None:
        """F2, O2, O5."""
        repo = self.repo()
        code, out = repo.run_gate()
        with self.subTest(oracle=2):
            self.assertEqual(repo.scope_seen(), "unset", out)
            self.assertEqual(out.get("scope"), "full", out)
        with self.subTest(oracle=5):
            self.assertEqual(code, 0, out)
            record = repo.record_path()
            self.assertTrue(same_path(self.ratchet(out).get("record"), record), out)
            self.assertTrue(record.is_file(), out)

    def test_f3_stop_hook_scope_passes_through(self) -> None:
        """F3, O3."""
        repo = self.repo()
        code, out = repo.run_gate(env={"GATE_SCOPE": "changed"})
        self.assertEqual(repo.scope_seen(), "set:changed", out)
        self.assertEqual(out.get("scope"), "changed", out)


if __name__ == "__main__":
    unittest.main()
