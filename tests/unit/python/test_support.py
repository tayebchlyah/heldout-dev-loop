#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent

PROBE = f"""
import json, sys
sys.path.insert(0, {str(HERE)!r})
import support
eager = "check_setup" in vars(support)
loaded = support.check_setup.__file__
from support import check_setup as c
try:
    getattr(support, "nope")
    other = False
except AttributeError:
    other = True
print(json.dumps([eager, loaded, str(support.CHECKER), c is support.check_setup, other]))
"""


class LazyCheckSetupTests(unittest.TestCase):
    def test_check_setup_loads_on_first_use(self) -> None:
        run = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        eager, loaded, checker, same, other = json.loads(run.stdout)
        self.assertFalse(eager)
        self.assertEqual(loaded, checker)
        self.assertTrue(same)
        self.assertTrue(other)


if __name__ == "__main__":
    unittest.main()
