#!/usr/bin/env python3
"""Tests for the built-in StrykerJS adapter `skill/scripts/mutation/stryker.py`."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT, write  # noqa: E402

FOLDER = ROOT / "skill" / "scripts" / "mutation"
SCRIPT = FOLDER / "stryker.py"
RECORDED = HERE / "fixtures" / "mutation" / "stryker" / "mutation.json"
PRICE = ROOT / "tests" / "fixtures" / "mutation" / "stryker" / "head" / "web" / "src" / "core" / "price.ts"
if str(FOLDER) not in sys.path:
    sys.path.append(str(FOLDER))
_spec = importlib.util.spec_from_file_location("mutation_stryker", SCRIPT)
assert _spec is not None and _spec.loader is not None
stryker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stryker)

CONFIG = json.dumps({"testRunner": "vitest"})
CORE = "node_modules/@stryker-mutator/core/package.json"
STUB = """#!{python}
import pathlib, shutil, sys
pathlib.Path({log!r}).open("a").write(" ".join(sys.argv[1:]) + "\\n")
report = pathlib.Path("reports/mutation/mutation.json")
if report.exists():
    sys.exit(5)
if {report}:
    report.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy({recorded!r}, report)
sys.exit({code})
"""
LOCATION = {"start": {"line": 2, "column": 7}, "end": {"line": 2, "column": 18}}
SUFFIX = ". Stage 0 owns it: see references/mutation.md, Setup."


class StrykerAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.log = self.root / "calls.log"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def adapter(self, files: dict[str, str], request: dict, code: int = 0,
                report: bool = True) -> subprocess.CompletedProcess[str]:
        write(self.root / "bin" / "npx", STUB.format(python=sys.executable, log=str(self.log),
                                                     recorded=str(RECORDED), code=code, report=report))
        (self.root / "bin" / "npx").chmod(0o755)
        entry = self.root / "entry"
        for name, text in files.items():
            write(entry / name, text)
        env = {**os.environ, "PATH": f"{self.root / 'bin'}{os.pathsep}{os.environ['PATH']}"}
        stdin = json.dumps({"protocol": 1, "base": "b", "head": "h", "only": None, **request})
        return subprocess.run([sys.executable, str(SCRIPT)], cwd=entry, input=stdin, capture_output=True,
                              text=True, env=env, timeout=60)

    def test_units_without_tool(self) -> None:
        files = {"src/b.ts": [[1, 1]], "src/a.ts": [[2, 3]]}
        done = self.adapter({"stryker.config.json": CONFIG, CORE: "{}"}, {"run": False, "files": files})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"units": ["src/a.ts", "src/b.ts"]})
        self.assertFalse(self.log.exists())
        self.assertEqual(stryker.mutate_arg({"b.ts": [[1, 1]], "a.ts": [[9, 9], [10, 12]]}),
                         "a.ts:9-9,a.ts:10-12,b.ts:1-1")

    def test_recorded_report(self) -> None:
        report = json.loads(RECORDED.read_text())
        price = report["files"]["src/core/price.ts"]
        price["mutants"] += [
            {"status": "Killed", "testsCompleted": 0, "coveredBy": ["0"], "location": LOCATION, "replacement": "a"},
            {"status": "Timeout", "coveredBy": ["0"], "location": LOCATION, "replacement": "b"},
            {"status": "RuntimeError", "location": LOCATION, "replacement": "c"},
            {"status": "CompileError", "location": LOCATION, "replacement": "d"},
            {"status": "Survived", "testsCompleted": 1, "location": LOCATION, "replacement": "e"},
            {"status": "Survived", "testsCompleted": 1, "coveredBy": [], "location": LOCATION, "replacement": "f"},
            {"status": "Ignored", "location": LOCATION, "replacement": "g"},
            {"status": "Pending", "location": LOCATION, "replacement": "h"},
        ]
        report["files"]["src/other.ts"] = copy.deepcopy(price)
        mutants = stryker.mutants_from(report, {"src/core/price.ts": [[1, 17]]})
        self.assertEqual({(m["unit"], m["file"]) for m in mutants}, {("src/core/price.ts", "src/core/price.ts")})
        self.assertEqual(sorted(m["line"] for m in mutants if m["status"] == "killed"),
                         [1, 2, 2, 2, 2, 8, 9, 9, 9, 9, 9])
        rows = [(m["line"], m["status"], m.get("detail"), m["diff"]) for m in mutants if m["status"] != "killed"]
        line2 = "-  if (total > 100) {\n+  if ("
        self.assertEqual(sorted(rows, key=lambda r: (r[0], r[3])), [
            (2, "inconclusive", "no test completed", line2 + "a) {"),
            (2, "inconclusive", "Timeout", line2 + "b) {"),
            (2, "inconclusive", "RuntimeError", line2 + "c) {"),
            (2, "inconclusive", "CompileError", line2 + "d) {"),
            (2, "no-tests", None, line2 + "e) {"),
            (2, "no-tests", None, line2 + "f) {"),
            (2, "inconclusive", "Ignored", line2 + "g) {"),
            (2, "inconclusive", "Pending", line2 + "h) {"),
            (2, "survived", None, line2 + "total >= 100) {"),
            (10, "survived", None, '-    throw new Error("negative quantity");\n+    throw new Error("");'),
            (15, "no-tests", None, "-export function unused(x: number): number {\n-  return x * 2;\n-}\n"
                                   "+export function unused(x: number): number {}"),
            (16, "no-tests", None, "-  return x * 2;\n+  return x / 2;"),
        ])

        sources = {"stryker.config.json": CONFIG, CORE: "{}", "src/core/price.ts": PRICE.read_text(),
                   "src/core/none.ts": "export {};\n", "reports/mutation/mutation.json": "{}"}
        files = {"src/core/price.ts": [[1, 17]], "src/core/none.ts": [[1, 1]]}
        odd = {"src/app/[id]/page.ts": [[3, 5]], "src/a,b.ts": [[1, 1]], "src/{x}.ts": [[2, 2]], "src/*.ts": [[1, 1]],
               "src/q?.ts": [[1, 1]], "src/!n.ts": [[1, 1]], "src/p@(q).ts": [[4, 4]], "src/t(u) v.ts": [[1, 1]]}
        done = self.adapter(sources, {"run": True, "files": {**files, **odd}}, code=1)
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertEqual(out["units"], sorted([*files, *odd]))
        self.assertEqual(len(out["mutants"]), 15)
        gaps = sorted((g["file"], g["start"], g["end"], g["reason"]) for g in out["not_covered"])
        self.assertEqual(gaps, [
            ("src/!n.ts", 1, 1, "path not expressible in --mutate"),
            ("src/*.ts", 1, 1, "path not expressible in --mutate"),
            ("src/a,b.ts", 1, 1, "path not expressible in --mutate"),
            ("src/app/[id]/page.ts", 3, 5, "path not expressible in --mutate"),
            ("src/core/none.ts", 1, 1, "no mutable code"),
            ("src/p@(q).ts", 4, 4, "path not expressible in --mutate"),
            ("src/q?.ts", 1, 1, "path not expressible in --mutate"),
            ("src/t(u) v.ts", 1, 1, "no mutable code"),
            ("src/{x}.ts", 2, 2, "path not expressible in --mutate"),
        ])
        self.assertEqual(self.log.read_text(), "stryker run --mutate src/core/none.ts:1-1,src/core/price.ts:1-17,"
                                               "src/t(u) v.ts:1-1 --reporters json\n")

        self.log.unlink()
        done = self.adapter({"reports/mutation/mutation.json": "{}"},
                            {"run": True, "files": files, "only": ["src/core/price.ts"]})
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertEqual(out["units"], ["src/core/none.ts", "src/core/price.ts"])
        self.assertEqual(len(out["mutants"]), 15)
        self.assertEqual(out["not_covered"], [{"file": "src/core/none.ts", "start": 1, "end": 1,
                                               "reason": "unit not in only"}])
        self.assertEqual(self.log.read_text(), "stryker run --mutate src/core/price.ts:1-17 --reporters json\n")

    def test_diff(self) -> None:
        source = "a\nfoo(1,\n  2);\nz\n"
        location = {"start": {"line": 2, "column": 5}, "end": {"line": 3, "column": 4}}
        self.assertEqual(stryker.diff_of(source, location, "()"), "-foo(1,\n-  2);\n+foo(());")
        one = {"start": {"line": 4, "column": 1}, "end": {"line": 4, "column": 2}}
        self.assertEqual(stryker.diff_of(source, one, "y\nw"), "-z\n+y\n+w")
        breaks = {"start": {"line": 4, "column": 1}, "end": {"line": 4, "column": 2}}
        self.assertEqual(stryker.diff_of("a\r\nb\u2028c\rd\n", breaks, "X"), "-d\n+X")
        emoji = {"start": {"line": 1, "column": 21}, "end": {"line": 1, "column": 26}}
        self.assertEqual(stryker.diff_of('const s = "\U0001F600"; if (a > b) {\n', emoji, "a >= b"),
                         '-const s = "\U0001F600"; if (a > b) {\n+const s = "\U0001F600"; if (a >= b) {')

    def test_setup_refusals(self) -> None:
        request = {"run": True, "files": {"src/a.ts": [[1, 1]]}}
        for files, words in (
                ({"stryker.config.json": CONFIG}, "run npm ci first"),
                ({"stryker.config.json": json.dumps({"testRunner": "vitest", "incremental": True}), CORE: "{}"},
                 '"incremental": true'),
                ({"stryker.config.json": json.dumps({"testRunner": "command"}), CORE: "{}"}, "testRunner"),
                ({"stryker.config.json": "{}", CORE: "{}"}, "testRunner"),
                ({"stryker.config.json": json.dumps({"testRunner": "vitest", "coverageAnalysis": "off"}), CORE: "{}"},
                 "coverageAnalysis"),
                ({"stryker.config.json": "{", CORE: "{}"}, "stryker.config.json"),
                ({CORE: "{}"}, "stryker.config.json")):
            with self.subTest(words=words, files=sorted(files)):
                shutil.rmtree(self.root / "entry", ignore_errors=True)
                done = self.adapter({"src/a.ts": "export const a = 1;\n", **files}, request)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertEqual(done.stdout, "")
                last = done.stderr.strip().splitlines()[-1]
                self.assertIn(words, last)
                self.assertTrue(last.endswith(SUFFIX), last)
                self.assertFalse(self.log.exists())
        for allowed in ("perTest", "all"):
            config = json.dumps({"testRunner": "vitest", "coverageAnalysis": allowed})
            self.log.unlink(missing_ok=True)
            done = self.adapter({"stryker.config.json": config, CORE: "{}"}, request)
            self.assertEqual(done.returncode, 0, done.stderr)
        done = self.adapter({"stryker.config.json": CONFIG, CORE: "{}"}, request, code=3, report=False)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(done.stdout, "")


if __name__ == "__main__":
    unittest.main()
