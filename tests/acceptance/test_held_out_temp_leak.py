#!/usr/bin/env python3
"""Held-out acceptance tests for the held-out temp leak guard (spec: Held-Out Temp Leak).

One test per `## Acceptance flows` item (F1 to F3); each docstring names the `## Acceptance oracle` lines (O<n>)
it proves. Seam: `scripts/acceptance.sh <fixture folder>` of this repo, run as a process with a chosen caller
`TMPDIR` and `DEVLOOP_REPORT_DIR`, the seam of Stage 6 Mechanics O22.

Fixture choices the spec leaves open:
- Each run gets its own temporary root: the caller `TMPDIR`, the fixture folder, the reports folder and a record
  file are separate folders under it, so the caller `TMPDIR` starts empty and the root removal takes all of them.
- The fixture Python test writes its `TMPDIR`, the entries of that folder when it starts and the names it leaves
  to the record file, outside every `TMPDIR`, so the test knows the scratch folder and the leaked names.
- "Empty when the tests start" allows a `node-compile-cache` entry, in case the Node part runs first.
- F3 also sets `DEVLOOP_REPORT_DIR`, so no run writes its reports into the repo; by O4 that run writes the
  temp-leak case too, with no `failure` child, since the allowed entries are no leftover.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_SH = REPO_ROOT / "scripts" / "acceptance.sh"
LEAK_LINE = "temp leak, the tests left in TMPDIR:"
LEAK_XML = "acceptance-temp-leak.xml"

PYTHON_LEAK = """import json
import os
import tempfile
import unittest

RECORD = {record!r}


class FixtureLeak(unittest.TestCase):
    def test_leaves_a_dir(self):
        scratch = os.environ.get("TMPDIR", "")
        entries = sorted(os.listdir(scratch)) if scratch else None
        left = tempfile.mkdtemp()
        with open(RECORD, "w") as handle:
            json.dump({{"tmpdir": scratch, "entries": entries, "left": os.path.basename(left)}}, handle)
"""

PYTHON_CLEAN = """import json
import os
import shutil
import tempfile
import unittest

RECORD = {record!r}


class FixtureClean(unittest.TestCase):
    def test_removes_its_dir(self):
        scratch = os.environ.get("TMPDIR", "")
        entries = sorted(os.listdir(scratch)) if scratch else None
        made = tempfile.mkdtemp()
        shutil.rmtree(made)
        with open(RECORD, "w") as handle:
            json.dump({{"tmpdir": scratch, "entries": entries}}, handle)
"""

PYTHON_PROBE = """import os
import tempfile
import unittest


class FixtureProbe(unittest.TestCase):
    def test_leaves_a_probe(self):
        with open(os.path.join(tempfile.gettempdir(), "abcd_123"), "w") as handle:
            handle.write("blat")
"""

NODE_PASS = "import test from 'node:test';\n\ntest('fixture passes', () => {});\n"

NODE_CLEAN = """import test from 'node:test';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

test('fixture removes its dir', () => {
  const made = mkdtempSync(join(tmpdir(), 'fixture-clean-'));
  writeFileSync(join(made, 'note.txt'), 'x');
  rmSync(made, { recursive: true, force: true });
});
"""

NODE_CACHE = """import test from 'node:test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

test('fixture leaves a compile cache', () => {
  const cache = join(tmpdir(), 'node-compile-cache');
  mkdirSync(cache, { recursive: true });
  writeFileSync(join(cache, 'entry'), 'cached');
});
"""


class TempLeakGuardTests(unittest.TestCase):

    def setUp(self) -> None:
        self.assertTrue(ACCEPTANCE_SH.is_file(), f"missing {ACCEPTANCE_SH.relative_to(REPO_ROOT)}")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name))
        self.caller_tmp = self.root / "caller-tmp"
        self.folder = self.root / "suite"
        self.reports = self.root / "reports"
        self.record = self.root / "record.json"
        for path in (self.caller_tmp, self.folder, self.reports):
            path.mkdir()

    def fixture(self, python: str, node: str) -> None:
        (self.folder / "test_fixture_widgets.py").write_text(python.format(record=str(self.record)))
        (self.folder / "widgets.test.mjs").write_text(node)

    def run_script(self) -> tuple[int, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DEVLOOP_", "GATE_"))}
        env.update(TMPDIR=str(self.caller_tmp), DEVLOOP_REPORT_DIR=str(self.reports), PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(["bash", str(ACCEPTANCE_SH), str(self.folder)], cwd=REPO_ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600)
        return result.returncode, result.stdout

    def leak_case(self, tail: str) -> ET.Element:
        path = self.reports / LEAK_XML
        self.assertTrue(path.is_file(), f"no {LEAK_XML}: {sorted(p.name for p in self.reports.iterdir())} {tail}")
        cases = list(ET.parse(path).getroot().iter("testcase"))
        self.assertEqual(len(cases), 1, path.read_text()[:800])
        self.assertEqual((cases[0].get("classname"), cases[0].get("name")), ("temp-leak", "TMPDIR"))
        return cases[0]

    def assert_scratch(self, tail: str) -> dict:
        """O1: the tests saw a `TMPDIR` that is not the caller's, empty at their start; the caller's is unchanged."""
        self.assertTrue(self.record.is_file(), f"the fixture Python test did not run: {tail}")
        record = json.loads(self.record.read_text())
        scratch = record["tmpdir"]
        self.assertTrue(scratch, f"the tests ran with no TMPDIR: {record}")
        self.assertNotEqual(os.path.realpath(scratch), str(self.caller_tmp), "the tests ran in the caller's TMPDIR")
        self.assertEqual([e for e in record["entries"] if e != "node-compile-cache"], [], record)
        self.assertEqual(sorted(os.listdir(self.caller_tmp)), [], "the run changed the caller's TMPDIR")
        return record

    def test_f1_leak(self) -> None:
        """F1, O1, O2, O4: a passing test that leaves a dir fails the run, the output and the JUnit leak case."""
        self.fixture(PYTHON_LEAK, NODE_PASS)
        code, output = self.run_script()
        tail = output[-1600:]
        record = self.assert_scratch(tail)
        left = record["left"]
        self.assertEqual(code, 1, tail)
        lines = [line.strip() for line in output.splitlines()]
        self.assertIn(LEAK_LINE, lines, tail)
        self.assertIn(left, lines[lines.index(LEAK_LINE) + 1:], tail)
        failure = self.leak_case(tail).find("failure")
        self.assertIsNotNone(failure, "a leak has no failure child in the temp-leak case")
        self.assertIn(left, failure.text or "", ET.tostring(failure, encoding="unicode"))

    def test_f2_clean(self) -> None:
        """F2, O1, O4, O5: tests that remove their dirs pass the run, the leak case passes, both reports stay."""
        self.fixture(PYTHON_CLEAN, NODE_CLEAN)
        code, output = self.run_script()
        tail = output[-1600:]
        self.assert_scratch(tail)
        self.assertEqual(code, 0, tail)
        self.assertNotIn("temp leak", output, tail)
        case = self.leak_case(tail)
        self.assertIsNone(case.find("failure"), ET.tostring(case, encoding="unicode"))
        self.assertIsNone(case.find("skipped"), ET.tostring(case, encoding="unicode"))
        python_xml = self.reports / "acceptance-python.xml"
        node_xml = self.reports / "acceptance-node.xml"
        self.assertTrue(python_xml.is_file(), f"no acceptance-python.xml {tail}")
        self.assertTrue(node_xml.is_file(), f"no acceptance-node.xml {tail}")
        python_names = [c.get("name", "") for c in ET.parse(python_xml).getroot().iter("testcase")]
        self.assertTrue(any("test_removes_its_dir" in name for name in python_names), python_names)
        node_names = [c.get("name", "") for c in ET.parse(node_xml).getroot().iter("testcase")]
        self.assertIn("fixture removes its dir", node_names)

    def test_f3_allowed_entries(self) -> None:
        """F3, O3 (O4): a `node-compile-cache` dir and a Python tempfile probe left behind are not a leak."""
        self.fixture(PYTHON_PROBE, NODE_CACHE)
        code, output = self.run_script()
        tail = output[-1600:]
        self.assertEqual(code, 0, tail)
        self.assertNotIn("temp leak", output, tail)
        case = self.leak_case(tail)
        self.assertIsNone(case.find("failure"), ET.tostring(case, encoding="unicode"))


if __name__ == "__main__":
    unittest.main()
