#!/usr/bin/env python3
"""Tests for the built-in mutago adapter `skill/scripts/mutation/mutago.py`."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT, write  # noqa: E402

FOLDER = ROOT / "skill" / "scripts" / "mutation"
SCRIPT = FOLDER / "mutago.py"
RECORDED = HERE / "fixtures" / "mutation" / "mutago" / "report.json"
PRICE = ROOT / "tests" / "fixtures" / "mutation" / "mutago" / "head" / "api" / "core" / "price.go"
if str(FOLDER) not in sys.path:
    sys.path.append(str(FOLDER))
_spec = importlib.util.spec_from_file_location("mutation_mutago", SCRIPT)
assert _spec is not None and _spec.loader is not None
mutago = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mutago)

SOURCE = """package a

import "fmt"

func Func(x int) int {
	return x + 1
}

type Recv struct{ n int }

func (r *Recv) Method() error {
	return fmt.Errorf("n %d", r.n)
}

type Box[T any] struct{ v T }

func (Box[T]) Get() int { return 1 }

func Map[T any](
	v T,
) T {
	return v
}
"""
LOGGER = """#!{python}
import json, os, pathlib, sys
pathlib.Path({log!r}).open("a").write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd(), "env": dict(os.environ)}}) + "\\n")
"""


class MutagoAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.log = self.root / "calls.log"
        self.cache = self.root / "cache"
        self.entry = self.root / "entry"
        self.bins = self.root / "bin"
        self.tmp = self.root / "tmp"
        self.tmp.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def stub(self, path: Path, body: str = "") -> None:
        write(path, LOGGER.format(python=sys.executable, log=str(self.log)) + body)
        path.chmod(0o755)

    def calls(self) -> list[dict]:
        return [json.loads(row) for row in self.log.read_text().splitlines()] if self.log.exists() else []

    def env(self, **extra: str) -> dict[str, str]:
        return {**os.environ, "PATH": f"{self.bins}{os.pathsep}{os.environ['PATH']}", "XDG_CACHE_HOME": str(self.cache),
                "TMPDIR": str(self.tmp), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, **extra}

    def adapter(self, request: dict, **extra: str) -> subprocess.CompletedProcess[str]:
        self.entry.mkdir(exist_ok=True)
        stdin = json.dumps({"protocol": 1, "base": "b0", "head": "h0", "only": None, **request})
        return subprocess.run([sys.executable, str(SCRIPT)], cwd=self.entry, input=stdin, capture_output=True,
                              text=True, env=self.env(**extra), timeout=60)

    def test_units_without_tool(self) -> None:
        self.stub(self.bins / "go")
        write(self.entry / "a.go", SOURCE)
        write(self.entry / "notes.txt", "x\n")
        files = {"a.go": [[3, 6], [9, 9], [11, 13], [17, 17], [19, 23]], "notes.txt": [[1, 1]]}
        done = self.adapter({"run": False, "files": files})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout),
                         {"units": ["a.go:Box.Get", "a.go:Func", "a.go:Map", "a.go:Recv.Method"]})
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.cache.exists())
        self.assertEqual(mutago.go_units(SOURCE), [("Func", 5, 7), ("Recv.Method", 11, 13), ("Box.Get", 17, 17),
                                                   ("Map", 19, 23)])

    def test_recorded_report(self) -> None:
        write(self.entry / "core" / "price.go", PRICE.read_text())
        report = json.loads(RECORDED.read_text())

        def extra(name: str, line: int, diff: str = "") -> dict:
            return {"mutator": {"mutatorName": "x", "location": {"physical": {"filename": name, "line": line}}},
                    "diff": diff}

        report["errored"] = [extra("core/price.go", 11)]
        report["skipped"] = [extra("core/price.go", 12)]
        report["escaped"] += [extra(str(self.entry / "core" / "price.go"), 14, "@@ -14 +14 @@\n-\treturn 0\n+\treturn 1\n"),
                              extra("core/price.go", 25)]
        mutants = mutago.mutants_from(report, {"core/price.go": [[3, 24], [27, 27]]}, self.entry)
        self.assertEqual({m["file"] for m in mutants}, {"core/price.go"})
        check, discount, unused = "core/price.go:Cart.Check", "core/price.go:Discount", "core/price.go:Unused"
        self.assertEqual(sorted((m["unit"], m["line"], m["status"], m.get("detail")) for m in mutants), [
            (check, 20, "killed", None), (check, 20, "killed", None),
            (check, 20, "survived", None), (check, 20, "survived", None),
            (check, 21, "killed", None), (check, 21, "survived", None), (check, 21, "survived", None),
            (check, 21, "survived", None),
            (check, 23, "killed", None),
            (discount, 11, "inconclusive", "errored"), (discount, 11, "killed", None),
            (discount, 11, "survived", None), (discount, 11, "survived", None), (discount, 11, "survived", None),
            (discount, 12, "inconclusive", "skipped"), (discount, 12, "killed", None),
            (discount, 12, "killed", None), (discount, 12, "killed", None), (discount, 12, "killed", None),
            (discount, 14, "survived", None),
            (unused, 27, "no-tests", None), (unused, 27, "no-tests", None), (unused, 27, "no-tests", None),
            (unused, 27, "no-tests", None),
        ])
        diffs = {m["diff"] for m in mutants if m["unit"] == discount and m["status"] == "survived"}
        self.assertIn(' var ErrNegative = errors.New("negative")\n \n func Discount(total int) int {\n'
                      '-\tif total > 100 {\n+\tif total >= 100 {\n \t\treturn 10\n \t}\n \treturn 0', diffs)
        self.assertIn("-\treturn 0\n+\treturn 1", diffs)
        self.assertEqual({m["diff"] for m in mutants if m["status"] == "no-tests"}, {None})

    def test_install_command(self) -> None:
        self.stub(self.bins / "go", "out = pathlib.Path(os.environ['GOBIN']) / 'mutago'\n"
                                    "out.parent.mkdir(parents=True)\nout.write_text('#!/bin/sh\\n')\nout.chmod(0o755)\n")
        folder = self.cache / "dev-loop" / "mutago" / "v2.10.42"
        hostile = {"GOSUMDB": "off", "GONOSUMDB": "*", "GONOSUMCHECK": "1", "GOPRIVATE": "*", "GOINSECURE": "*"}
        with mock.patch.dict(os.environ, self.env(**hostile), clear=True):
            self.assertEqual(mutago.binary(), folder / "mutago")
            self.assertEqual(mutago.binary(), folder / "mutago")
        [call] = self.calls()
        self.assertEqual(call["argv"], ["install", "github.com/quality-gates/mutago/v2/cmd/mutago@v2.10.42"])
        self.assertEqual((call["env"]["GOBIN"], call["env"]["GOSUMDB"]), (str(folder), "sum.golang.org"))
        self.assertEqual([k for k in ("GONOSUMDB", "GONOSUMCHECK", "GOPRIVATE", "GOINSECURE") if k in call["env"]], [])

    def test_run_command_and_cleanup(self) -> None:
        tool = self.cache / "dev-loop" / "mutago" / "v2.10.42" / "mutago"
        self.stub(tool, f"import shutil\nconfig = pathlib.Path(sys.argv[sys.argv.index('--config') + 1])\n"
                        f"pathlib.Path({str(self.log)!r} + '.yml').write_text(config.read_text())\n"
                        f"shutil.copy({str(RECORDED)!r}, 'report.json')\n"
                        "pathlib.Path('mutago-summary.json').write_text('{}')\n"
                        "pathlib.Path('mutago-agentic.json').write_text('{}')\n"
                        "sys.exit(int(os.environ.get('STUB_EXIT', '0')))\n")
        write(self.entry / "core" / "price.go", PRICE.read_text())
        files = {"core/price.go": [[3, 24], [27, 27]]}
        caller = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.pager", "GIT_CONFIG_VALUE_0": "cat"}
        done = self.adapter({"run": True, "files": files}, **caller)
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertEqual(out["units"], ["core/price.go:Cart.Check", "core/price.go:Discount", "core/price.go:Unused"])
        self.assertEqual(len(out["mutants"]), 21)
        self.assertEqual(sorted((g["start"], g["end"], g["reason"]) for g in out["not_covered"]),
                         [(3, 9, "outside any function"), (16, 18, "outside any function")])
        [call] = self.calls()
        config = call["argv"][call["argv"].index("--config") + 1]
        self.assertEqual(call["argv"], ["--git-diff-lines", "--git-diff-base", "b0", "--coverage", "--config", config,
                                        "./..."])
        self.assertEqual(Path(config).name, "mutago.yml")
        self.assertEqual(Path(config).parent.parent, self.tmp)
        self.assertEqual(Path(str(self.log) + ".yml").read_text().split(), ["json_output:", "true",
                                                                            "skip_without_test:", "false"])
        self.assertEqual(Path(call["cwd"]).resolve(), self.entry)
        settings = [(call["env"][f"GIT_CONFIG_KEY_{i}"], call["env"][f"GIT_CONFIG_VALUE_{i}"]) for i in range(6)]
        self.assertEqual((call["env"]["GIT_CONFIG_COUNT"], settings), ("6", [
            ("core.pager", "cat"), ("color.diff", "never"), ("diff.noprefix", "false"),
            ("diff.mnemonicPrefix", "false"), ("diff.relative", "false"), ("diff.dstPrefix", "b/")]))
        self.assertEqual(sorted(p.name for p in self.entry.iterdir()), ["core"])
        self.assertEqual(list(self.tmp.iterdir()), [])

        self.log.unlink()
        only = ["core/price.go:Cart.Check", "core/price.go:Discount"]
        done = self.adapter({"run": True, "files": files, "only": only})
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertEqual(len(out["units"]), 3)
        self.assertEqual({m["unit"] for m in out["mutants"]}, set(only))
        self.assertIn({"file": "core/price.go", "start": 27, "end": 27, "reason": "unit not in only"},
                      out["not_covered"])
        [call] = self.calls()
        self.assertEqual(call["argv"][-3:-1], ["--match", "^(Check|Discount)$"])

        for extra, files_before in (({"STUB_EXIT": "3"}, {}), ({}, {"report.json": "{}"}),
                                    ({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "diff.external",
                                      "GIT_CONFIG_VALUE_0": "x"}, {})):
            with self.subTest(extra=extra, files=files_before):
                self.log.unlink(missing_ok=True)
                self.stub(tool, "sys.exit(int(os.environ.get('STUB_EXIT', '0')))\n")
                for name, text in files_before.items():
                    write(self.entry / name, text)
                done = self.adapter({"run": True, "files": files}, **extra)
                self.assertEqual((done.returncode, done.stdout), (2, ""), done.stderr)
                self.assertTrue(done.stderr.strip().endswith(". Stage 0 owns it: see references/mutation.md, Setup."))
                self.assertEqual(len(self.calls()), 1 if extra.get("STUB_EXIT") else 0)
                self.assertEqual(list(self.tmp.iterdir()), [])
                for name in files_before:
                    self.assertEqual((self.entry / name).read_text(), "{}")
                    (self.entry / name).unlink()


if __name__ == "__main__":
    unittest.main()
