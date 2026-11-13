#!/usr/bin/env python3
"""Tests for the built-in mutmut adapter `skill/scripts/mutation/mutmut.py` and its helper `adapter.py`."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import MODULE_BASE, ROOT, git_in, write  # noqa: E402

FOLDER = ROOT / "skill" / "scripts" / "mutation"
SCRIPT = FOLDER / "mutmut.py"
RECORDED = HERE / "fixtures" / "mutation" / "mutmut"
PRICE = (ROOT / "tests" / "fixtures" / "mutation" / "mutmut" / "head" / "app" / "src" / "shop" / "core" / "price.py")
if str(FOLDER) not in sys.path:
    sys.path.append(str(FOLDER))
_spec = importlib.util.spec_from_file_location("mutation_mutmut", SCRIPT)
assert _spec is not None and _spec.loader is not None
mutmut = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mutmut)
import adapter  # noqa: E402

PYPROJECT = """[tool.mutmut]
source_paths = ["src"]
only_mutate = ["src/pkg/*"]
do_not_mutate = ["src/pkg/skip.py"]
"""
HEAD = MODULE_BASE + "\n\nclass Raw:\n    def __new__(cls):\n        return 1\n"
STUB = """#!{python}
import pathlib, sys
pathlib.Path({log!r}).open("a").write(" ".join(sys.argv[1:]) + "\\n")
recorded = pathlib.Path({recorded!r})
if sys.argv[1] == "run":
    print({run_out!r})
    sys.exit({run_exit})
if sys.argv[1] == "results":
    print({results!r}, end="")
if sys.argv[1] == "show":
    print((recorded / "show" / (sys.argv[2] + ".txt")).read_text(), end="")
"""
EXTRA = """    shop.core.price.x_discount__mutmut_5: timeout
    shop.core.price.x_discount__mutmut_6: suspicious
    shop.core.price.x_check__mutmut_6: segfault
    shop.core.price.x_check__mutmut_7: not checked
    shop.core.price.x_check__mutmut_8: skipped
    shop.core.price.x_unused__mutmut_3: caught by type check
    shop.other.x_far__mutmut_1: not checked
"""


def show(name: str) -> str:
    return (RECORDED / "show" / f"shop.core.price.{name}.txt").read_text()


class MutmutAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.log = self.root / "calls.log"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def stub(self, run_out: str = "", run_exit: int = 0, results: str = "") -> None:
        text = STUB.format(python=sys.executable, log=str(self.log), recorded=str(RECORDED), run_out=run_out,
                           run_exit=run_exit, results=results)
        write(self.root / "bin" / "mutmut", text)
        (self.root / "bin" / "mutmut").chmod(0o755)

    def adapter(self, files: dict[str, str], request: dict) -> subprocess.CompletedProcess[str]:
        entry = self.root / "entry"
        for name, text in files.items():
            write(entry / name, text)
        env = {**os.environ, "PATH": f"{self.root / 'bin'}{os.pathsep}{os.environ['PATH']}"}
        stdin = json.dumps({"protocol": 1, "base": "b", "head": "h", "only": None, **request})
        return subprocess.run([sys.executable, str(SCRIPT)], cwd=entry, input=stdin, capture_output=True,
                              text=True, env=env, timeout=60)

    def test_units_without_tool(self) -> None:
        self.stub()
        files = {"src/pkg/mod.py": [[1, 1], [5, 5], [13, 13], [16, 16], [20, 20]]}
        done = self.adapter({"pyproject.toml": PYPROJECT, "src/pkg/mod.py": MODULE_BASE},
                            {"run": False, "files": files})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout),
                         {"units": ["pkg.mod.x_changed", "pkg.mod.xǁBoxǁgrow", "pkg.mod.xǁBoxǁmake"]})
        self.assertFalse(self.log.exists())

    def test_not_covered(self) -> None:
        self.stub(run_out="nothing matches", run_exit=1)
        sources = {"pyproject.toml": PYPROJECT, "src/pkg/mod.py": HEAD, "src/pkg/plain.py": "def f():\n    return 1\n",
                   "src/pkg/skip.py": "def g():\n    return 1\n", "src/other/x.py": "def h():\n    return 1\n",
                   "lib/y.py": "def k():\n    return 1\n", "src/pkg/data.txt": "a\n", "mutants/old": "x\n"}
        files = {"src/pkg/mod.py": [[1, 1], [5, 5], [13, 13], [24, 25], [30, 30]], "src/pkg/plain.py": [[2, 2]],
                 "src/pkg/skip.py": [[1, 2]], "src/other/x.py": [[1, 1]], "lib/y.py": [[2, 2]],
                 "src/pkg/data.txt": [[1, 1]]}
        done = self.adapter(sources, {"run": True, "files": files})
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertEqual(out["units"], ["pkg.mod.x_changed", "pkg.plain.x_f"])
        self.assertEqual(out["mutants"], [])
        gaps = sorted((g["file"], g["start"], g["end"], g["reason"]) for g in out["not_covered"])
        self.assertEqual(gaps, [
            ("lib/y.py", 2, 2, "outside source_paths"),
            ("src/other/x.py", 1, 1, "outside only_mutate"),
            ("src/pkg/data.txt", 1, 1, "not a Python file"),
            ("src/pkg/mod.py", 1, 1, "outside any function"),
            ("src/pkg/mod.py", 13, 13, "class body outside any method"),
            ("src/pkg/mod.py", 24, 25, "decorated function, mutmut skips it"),
            ("src/pkg/mod.py", 30, 30, "function mutmut never mutates"),
            ("src/pkg/plain.py", 2, 2, "no mutable code"),
            ("src/pkg/skip.py", 1, 2, "in do_not_mutate"),
        ])
        self.assertEqual(self.log.read_text(), "run pkg.mod.x_changed__mutmut_* pkg.plain.x_f__mutmut_*\n")
        self.assertFalse((self.root / "entry" / "mutants").exists())

        self.log.unlink()
        files = {"src/pkg/mod.py": [[5, 5]], "src/pkg/plain.py": [[2, 2]]}
        done = self.adapter(sources, {"run": True, "files": files, "only": ["pkg.mod.x_changed"]})
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertEqual(out["units"], ["pkg.mod.x_changed", "pkg.plain.x_f"])
        self.assertEqual(sorted((g["file"], g["start"], g["end"], g["reason"]) for g in out["not_covered"]), [
            ("src/pkg/mod.py", 5, 5, "no mutable code"), ("src/pkg/plain.py", 2, 2, "unit not in only")])
        self.assertEqual(self.log.read_text(), "run pkg.mod.x_changed__mutmut_*\n")

    def test_runner(self) -> None:
        for lock, expected in (("uv.lock", ["uv", "run", "mutmut"]), ("poetry.lock", ["poetry", "run", "mutmut"]),
                               (None, ["mutmut"])):
            folder = self.root / str(lock)
            folder.mkdir()
            if lock:
                write(folder / lock, "")
            self.assertEqual(mutmut.runner(folder), expected, lock)
        workspace = self.root / "ws"
        write(workspace / "uv.lock", "")
        git_in(workspace, "init", "-q")
        for entry, lock, expected in (("app", None, ["uv", "run", "mutmut"]),
                                      ("lib", "poetry.lock", ["poetry", "run", "mutmut"])):
            (workspace / entry).mkdir()
            if lock:
                write(workspace / entry / lock, "")
            self.assertEqual(mutmut.runner(workspace / entry), expected, entry)

    def test_recorded_results_and_show(self) -> None:
        results = (RECORDED / "results.txt").read_text()
        parsed = mutmut.parse_results(results)
        self.assertEqual(len(parsed), 11)
        self.assertEqual(parsed["shop.core.price.x_discount__mutmut_1"], "survived")
        self.assertEqual(parsed["shop.core.price.x_check__mutmut_1"], "killed")
        self.assertEqual(parsed["shop.core.price.x_unused__mutmut_2"], "no tests")
        self.assertEqual(adapter.diff_rows(show("x_discount__mutmut_1")),
                         " def discount(total):\n-    if total > 100:\n+    if total >= 100:\n"
                         "         return 10\n     return 0")
        self.assertEqual(adapter.diff_rows("# h\n--- a\n+++ b\n@@ -1 +1 @@\n x\n\n-y\n+z\n"), " x\n \n-y\n+z")
        self.assertIsNone(adapter.diff_rows("# h\n--- a\n+++ b\n"))
        self.assertEqual(adapter.diff_rows("--- a\n+++ b\n@@ -1 +1 @@\n-y\n\\ No newline at end of file\n+z\n"
                                           "\\ No newline at end of file\n"), "-y\n+z")
        self.assertEqual(adapter.diff_rows("# h\n--- a\n+++ b\n@@ -1,2 +1,2 @@\n--- x\n+++ y\n @@ z\n"),
                         "--- x\n+++ y\n @@ z")
        self.assertEqual(mutmut.mutant_line(show("x_discount__mutmut_1"), 1), 2)
        self.assertEqual(mutmut.mutant_line(show("x_check__mutmut_4"), 7), 9)
        self.assertEqual(mutmut.mutant_line("# h\n@@ -3,3 +3,3 @@\n a\n b\n-c\n+d\n", 10), 14)

        self.stub(results=results + EXTRA)
        pyproject = '[tool.mutmut]\nsource_paths = ["src"]\n'
        files = {"src/shop/core/price.py": [[2, 3], [9, 9], [14, 14]]}
        done = self.adapter({"pyproject.toml": pyproject, "src/shop/core/price.py": PRICE.read_text()},
                            {"run": True, "files": files})
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertEqual(out["not_covered"], [])
        self.assertEqual(len(out["mutants"]), 17)
        self.assertEqual({m["file"] for m in out["mutants"]}, {"src/shop/core/price.py"})
        rows = sorted((m["line"], m["status"], m.get("detail"), m["diff"]) for m in out["mutants"]
                      if m["status"] != "killed")
        self.assertEqual(rows, sorted([
            (1, "inconclusive", "suspicious", None),
            (1, "inconclusive", "timeout", None),
            (2, "survived", None, show("x_discount__mutmut_1").split("\n", 4)[4].rstrip("\n")),
            (2, "survived", None, show("x_discount__mutmut_2").split("\n", 4)[4].rstrip("\n")),
            (7, "inconclusive", "not checked", None),
            (7, "inconclusive", "segfault", None),
            (7, "inconclusive", "skipped", None),
            (9, "survived", None, show("x_check__mutmut_3").split("\n", 4)[4].rstrip("\n")),
            (9, "survived", None, show("x_check__mutmut_4").split("\n", 4)[4].rstrip("\n")),
            (9, "survived", None, show("x_check__mutmut_5").split("\n", 4)[4].rstrip("\n")),
            (14, "no-tests", None, show("x_unused__mutmut_1").split("\n", 4)[4].rstrip("\n")),
            (14, "no-tests", None, show("x_unused__mutmut_2").split("\n", 4)[4].rstrip("\n")),
        ]))
        self.assertEqual(sorted(m["unit"] for m in out["mutants"] if m["status"] == "killed"),
                         ["shop.core.price.x_check"] * 2 + ["shop.core.price.x_discount"] * 2
                         + ["shop.core.price.x_unused"])

    def test_unknown_protocol(self) -> None:
        done = self.adapter({"pyproject.toml": PYPROJECT}, {"protocol": 2, "run": False, "files": {}})
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stdout, "")
        self.assertIn("protocol", done.stderr)

    def test_setup_errors(self) -> None:
        sources = {"pyproject.toml": PYPROJECT, "src/pkg/plain.py": "def f():\n    return 1\n"}
        request = {"run": True, "files": {"src/pkg/plain.py": [[2, 2]]}}
        crashed = "".join(f"    pkg.plain.x_f__mutmut_{n}: segfault\n" for n in (1, 2)) + "    pkg.plain.x_f__mutmut_3: killed\n"
        untested = crashed.replace("segfault", "no tests")
        suffix = ". Stage 0 owns it: see references/mutation.md, Setup."
        for case, project, words in (
                ({"run_out": "failed to collect stats. runner returned 1", "run_exit": 1}, PYPROJECT, "unit tests fail"),
                ({"results": crashed}, PYPROJECT, "2 of 3 mutants crashed"),
                ({"results": untested}, PYPROJECT, "2 of 3 mutants reach no unit test"),
                ({}, "[project]\n", "mutation setup missing: no pyproject.toml holds [tool.mutmut]"),
                ({}, "[tool.mutmut]\n", "mutation setup missing: [tool.mutmut] has no source_paths")):
            self.stub(**case)
            done = self.adapter({**sources, "pyproject.toml": project}, request)
            self.assertEqual(done.returncode, 2, (case, done.stderr))
            self.assertEqual(done.stdout, "", case)
            last = done.stderr.strip().splitlines()[-1]
            self.assertIn(words, last)
            self.assertTrue(last.endswith(suffix), last)
        self.stub(run_out="boom", run_exit=3)
        self.assertEqual(self.adapter(sources, request).returncode, 1)


if __name__ == "__main__":
    unittest.main()
