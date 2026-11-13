#!/usr/bin/env python3
"""Held-out oracle tests for mutation adapters (spec: 20261113 Mutation Adapters), the `devloop.py mutation` seam.

Seam 1: the `devloop.py mutation` process of the kit source on a fixture repo with a `command` entry whose adapter
is a fixture script (`mutation_adapters_support`). One test per flow F1 and F2, plus the selection and report oracle
lines no flow holds; each docstring names the `## Acceptance oracle` lines (O<n>) it checks.

Fixture choices the spec leaves open:
- The stdin `base` and `head` are checked by the commit they name (`git rev-parse`), not by their spelling.
- A unit of a report section is a line indented by exactly 2 spaces under the section line.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from mutation_adapters_support import (COMPARE_BASE, COMPARE_HEAD, MutationCase, Shop, entry, gap, mutant, output,
                                       report_lines, section, top_items)

STDIN_KEYS = {"protocol", "run", "base", "head", "files", "only"}
CRITICAL = ["core/**", "!core/gen/**"]
DECISION = ["-    return a > b", "+    return a >= b"]


class NightlyRunFlowTests(MutationCase):
    def test_f1_o1_o4_o7_o12_nightly_run_with_a_project_adapter(self) -> None:
        """F1, O1, O4, O7, O12: entry `app/` with critical `core/**`, `!core/gen/**`; the head changes lines 3 to 4
        of `app/core/a.py`, `app/core/gen/g.py` and `app/web/b.py`. The adapter gets the protocol 1 stdin with
        `files` `{"core/a.py": [[3, 4]]}` and `only` null, runs in `app/`; `outside` holds the other two files. A
        survived and a no-tests mutant of `u1`: status `survivors`, exit 0, `need_tests` 1, the unit block of the
        report. Two inconclusive mutants (`timeout`, no detail) alone: `timeout: 1`, `inconclusive: 1`, `pass`."""
        shop = Shop(self)
        shop.build({"mutation": [entry("app/", CRITICAL)]},
                   {"app/core/a.py": COMPARE_BASE, "app/core/gen/g.py": "GEN = 1\n", "app/web/b.py": "PAGE = 1\n"})
        head = shop.change({"app/core/a.py": COMPARE_HEAD, "app/core/gen/g.py": "GEN = 2\n",
                            "app/web/b.py": "PAGE = 2\n"})
        no_tests = ["-    right = b", "+    pass"]
        timeout = mutant("u2", "core/a.py", 3, "inconclusive", ["-    left = a * 2", "+    left = a * 3"], "timeout")
        silent = mutant("u2", "core/a.py", 4, "inconclusive", ["-    return a > b", "+    return a < b"])
        shop.control("app/", output(["u1", "u2"], [mutant("u1", "core/a.py", 3, diff=DECISION),
                                                   mutant("u1", "core/a.py", 4, "no-tests", no_tests),
                                                   timeout, silent]))

        code, payload = self.run_json(shop, "--head", "feat", "--run")
        calls = shop.run_calls("app/")
        with self.subTest(oracle=1, part="stdin"):
            self.assertEqual(len(calls), 1, shop.calls("app/"))
            stdin = calls[0]
            self.assertEqual(set(stdin), STDIN_KEYS, stdin)
            self.assertEqual(stdin["protocol"], 1, stdin)
            self.assertEqual(stdin["files"], {"core/a.py": [[3, 4]]}, stdin)
            self.assertIsNone(stdin["only"], stdin)
            self.assertEqual(shop.sha(stdin["base"]), shop.base, stdin)
            self.assertEqual(shop.sha(stdin["head"]), head, stdin)
        with self.subTest(oracle=1, part="outside"):
            self.assertIn("app/core/gen/g.py", payload.get("outside") or [], payload)
            self.assertIn("app/web/b.py", payload.get("outside") or [], payload)
        with self.subTest(oracle=4):
            cwds = [call["cwd"] for call in shop.calls("app/")]
            self.assertTrue(cwds, "the adapter never ran")
            self.assertEqual({str(Path(cwd).resolve()) for cwd in cwds},
                             {str(shop.path / "app")}, cwds)
        with self.subTest(oracle=7):
            self.assertEqual(code, 0, payload)
            self.assertEqual(payload.get("status"), "survivors", payload)
            app = self.entry_of(payload, "app/")
            self.assertEqual(app.get("status"), "survivors", app)
            self.assertEqual(app.get("need_tests"), 1, app)
            self.assertBlock(report_lines(payload), ["app/ units that need one unit test: 1", "  u1",
                                                     "    app/core/a.py:3 survived", "      -    return a > b",
                                                     "      +    return a >= b"], payload)
        with self.subTest(oracle=12, part="no tests"):
            self.assertBlock(report_lines(payload), ["    app/core/a.py:4 no tests", "      -    right = b",
                                                     "      +    pass"], payload)
        with self.subTest(oracle=12, part="inconclusive with a survivor"):
            self.assertBlock(report_lines(payload), ["app/ inconclusive: 2"], payload)

        shop.control("app/", output(["u1", "u2"], [timeout, silent]))
        code, payload = self.run_json(shop, "--head", "feat", "--run")
        with self.subTest(oracle=12, part="inconclusive only"):
            self.assertEqual(code, 0, payload)
            self.assertEqual(payload.get("status"), "pass", payload)
            self.assertEqual(sorted(section(payload, "app/ inconclusive: 2")), ["  inconclusive: 1", "  timeout: 1"],
                             payload.get("report"))
            self.assertEqual(self.entry_of(payload, "app/").get("need_tests"), 0, payload)


class ClassificationFlowTests(MutationCase):
    def test_f2_o8_o9_o10_o11_survivor_classification_per_language(self) -> None:
        """F2, O8 to O11: a `.py` survivor that only changes the string of a `raise` is message-only and the run
        passes. Then `.go` `fmt.Errorf("bad")` to `("")` and `.ts` `throw new Error("bad")` to `("")` are message
        survivors; `.go` and `.ts` condition changes, a `.rb` message change and a null `diff` are decision
        survivors, listed under the units that need one unit test."""
        shop = Shop(self)
        base = {"app/core/a.py": "def check(v):\n    if not v:\n        raise ValueError(\"bad\")\n",
                "app/core/m.go": "package m\n\nfunc F(s string) error {\n\treturn fmt.Errorf(\"bad\")\n}\n",
                "app/core/m.ts": "export function f(k: string) {\n  throw new Error(\"bad\")\n}\n",
                "app/core/m.rb": "def f\n  raise ArgumentError, \"bad\"\nend\n"}
        shop.build({"mutation": [entry("app/", ["core/**"])]}, base)
        shop.change({"app/core/a.py": "def check(v):\n    if not v:\n        raise ValueError(\"worse\")\n",
                     "app/core/m.go": "package m\n\nfunc F(s string) error {\n\tif s == \"x\" {\n\t\treturn nil\n"
                                      "\t}\n\treturn fmt.Errorf(\"bad\")\n}\n",
                     "app/core/m.ts": "export function f(k: string) {\n  if (k === \"id\") {\n    return\n  }\n"
                                      "  throw new Error(\"bad\")\n}\n",
                     "app/core/m.rb": "def f\n  raise ArgumentError, \"worse\"\nend\n"})
        py_message = mutant("py_msg", "core/a.py", 3,
                            diff=['-        raise ValueError("bad")', '+        raise ValueError("XXbadXX")'])
        gaps = [gap("core/m.go", 4, 7), gap("core/m.ts", 2, 5), gap("core/m.rb", 2, 2)]
        shop.control("app/", output(["py_msg"], [py_message], gaps))

        code, payload = self.run_json(shop, "--head", "feat", "--run")
        with self.subTest(oracle=8):
            self.assertEqual(code, 0, payload)
            self.assertEqual(payload.get("status"), "pass", payload)
            self.assertEqual(top_items(section(payload, "app/ message-only survivors, to Deferred: 1")),
                             ["py_msg app/core/a.py:3"], payload.get("report"))
            self.assertEqual(self.entry_of(payload, "app/").get("need_tests"), 0, payload)

        mutants = [py_message,
                   mutant("go_msg", "core/m.go", 7, diff=['-\treturn fmt.Errorf("bad")', '+\treturn fmt.Errorf("")']),
                   mutant("go_dec", "core/m.go", 4, diff=['-\tif s == "x" {', '+\tif s == "" {']),
                   mutant("ts_msg", "core/m.ts", 5, diff=['-  throw new Error("bad")', '+  throw new Error("")']),
                   mutant("ts_dec", "core/m.ts", 2, diff=['-  if (k === "id") {', '+  if (k === "") {']),
                   mutant("rb_msg", "core/m.rb", 2,
                          diff=['-  raise ArgumentError, "worse"', '+  raise ArgumentError, ""']),
                   mutant("py_null", "core/a.py", 2, diff=None)]
        shop.control("app/", output([item["unit"] for item in mutants], mutants))
        code, payload = self.run_json(shop, "--head", "feat", "--run")
        message = [item.split(" ")[0] for item in top_items(section(payload, "app/ message-only survivors, to "
                                                                             "Deferred: 3"))]
        need = top_items(section(payload, "app/ units that need one unit test: 4"))
        with self.subTest(oracle=9):
            self.assertIn("go_msg", message, payload.get("report"))
            self.assertIn("go_dec", need, payload.get("report"))
        with self.subTest(oracle=10):
            self.assertIn("ts_msg", message, payload.get("report"))
            self.assertIn("ts_dec", need, payload.get("report"))
        with self.subTest(oracle=11):
            self.assertIn("rb_msg", need, payload.get("report"))
            self.assertIn("py_null", need, payload.get("report"))
        with self.subTest(flow=2, part="totals"):
            self.assertEqual(sorted(message), ["go_msg", "py_msg", "ts_msg"], payload.get("report"))
            self.assertEqual(sorted(need), ["go_dec", "py_null", "rb_msg", "ts_dec"], payload.get("report"))
            self.assertEqual(code, 0, payload)
            self.assertEqual(payload.get("status"), "survivors", payload)
            self.assertEqual(self.entry_of(payload, "app/").get("need_tests"), 4, payload)


class SelectionTests(MutationCase):
    def test_o2_head_side_ranges(self) -> None:
        """O2: the head deletes line 5 of `app/core/a.py`: stdin `files` holds `[[4, 4]]`; it changes lines 3, 4
        and 6: `[[3, 4], [6, 6]]`."""
        shop = Shop(self)
        shop.build({"mutation": [entry("app/", ["core/**"])]}, {"app/core/a.py": COMPARE_BASE})
        lines = COMPARE_BASE.splitlines(keepends=True)
        shop.change({"app/core/a.py": "".join(lines[:4] + lines[5:])}, branch="cut")
        shop.change({"app/core/a.py": "".join(lines[:2] + ["    left = b\n", "    right = a\n", lines[4],
                                                           "    return -total\n"])}, branch="edit")
        shop.control("app/", output(["u1"], [mutant("u1", "core/a.py", 4, "killed", DECISION)]))
        for branch, ranges in (("cut", [[4, 4]]), ("edit", [[3, 4], [6, 6]])):
            with self.subTest(oracle=2, branch=branch):
                shop.checkout(branch)
                shop.forget_calls()
                code, payload = self.run_json(shop, "--head", branch, "--run")
                calls = shop.run_calls("app/")
                self.assertEqual(len(calls), 1, payload)
                self.assertEqual(calls[0].get("files"), {"core/a.py": ranges}, calls[0])

    def test_o3_o20_selection_json_and_one_segment_star(self) -> None:
        """O3: critical `core/*`: a change to `app/core/x/a.py` is `outside`. O20: no `--run`: the selection JSON,
        exit 0; no adapter runs."""
        shop = Shop(self)
        shop.build({"mutation": [entry("app/", ["core/*"])]},
                   {"app/core/a.py": COMPARE_BASE, "app/core/x/a.py": COMPARE_BASE})
        shop.change({"app/core/a.py": COMPARE_HEAD, "app/core/x/a.py": COMPARE_HEAD})
        code, payload = self.run_json(shop, "--head", "feat")
        with self.subTest(oracle=3):
            self.assertIn("app/core/x/a.py", payload.get("outside") or [], payload)
        with self.subTest(oracle=20):
            self.assertEqual(code, 0, payload)
            self.assertEqual(shop.calls("app/"), [])
            self.assertTrue({"base", "head", "entries", "outside", "none"} <= set(payload), payload)
            app = self.entry_of(payload, "app/")
            self.assertEqual(app.get("command"), "./adapt", app)
            self.assertTrue(app.get("files"), app)
            self.assertNotIn("app/core/a.py", payload.get("outside") or [], payload)

    def test_o5_o6_unchanged_entry_and_deepest_entry(self) -> None:
        """O5: no critical file changed: the adapter does not run; entry `mutants` 0, status `pass`. O6: entries
        `.` and `app/`, a change to `app/core/a.py`: only the `app/` adapter gets it."""
        shop = Shop(self)
        shop.build({"mutation": [entry(".", ["**"]), entry("app/", ["core/**"])]},
                   {"app/core/a.py": COMPARE_BASE, "app/web/b.py": "PAGE = 1\n", "lib.py": "X = 1\n"},
                   adapters=(".", "app/"))
        shop.change({"app/web/b.py": "PAGE = 2\n"}, branch="web")
        shop.change({"app/core/a.py": COMPARE_HEAD}, branch="core")
        with self.subTest(oracle=5):
            shop.checkout("web")
            code, payload = self.run_json(shop, "--head", "web", "--run")
            self.assertEqual(shop.calls("app/"), [])
            app = self.entry_of(payload, "app/")
            self.assertEqual(app.get("mutants"), 0, app)
            self.assertEqual(app.get("status"), "pass", app)
            self.assertEqual(code, 0, payload)
        with self.subTest(oracle=6):
            shop.checkout("core")
            shop.forget_calls()
            shop.control("app/", output(["u1"], [mutant("u1", "core/a.py", 3, "killed", DECISION)]))
            code, payload = self.run_json(shop, "--head", "core", "--run")
            self.assertEqual(shop.calls("."), [])
            calls = shop.run_calls("app/")
            self.assertEqual([call.get("files") for call in calls], [{"core/a.py": [[3, 4]]}], payload)


class OffAndInvalidTests(MutationCase):
    def test_o18_o19_mutation_off_invalid_and_unit_without_entry(self) -> None:
        """O18: `"mutation": false`: status `pass`, report `mutation: off`, exit 0 (no `mutation` key: flow F4,
        `test_mutation_adapters_setup`); `"mutation": {}`: status `setup`, exit 1, with and without `--run`. O19:
        `stacks` `[{"path": "api/", "preset": "go"}]` and no `api/` entry: `none` holds `api/`, the report holds
        `mutation: none for api/`."""
        files = {"app/core/a.py": COMPARE_BASE, "api/go.mod": "module example.invalid/api\n"}
        cases = (("off", {"mutation": False}), ("object", {"mutation": {}}),
                 ("none", {"mutation": [entry("app/", ["core/**"])], "stacks": [{"path": "api/", "preset": "go"}]}))
        shops = {}
        for name, config in cases:
            shops[name] = Shop(self, name)
            shops[name].build(config, files)
            shops[name].change({"app/core/a.py": COMPARE_HEAD})
        with self.subTest(oracle=18, mutation=False):
            code, payload = self.run_json(shops["off"], "--head", "feat", "--run")
            self.assertEqual(code, 0, payload)
            self.assertEqual(payload.get("status"), "pass", payload)
            self.assertIn("mutation: off", report_lines(payload), payload)
        for args in (("--run",), ()):
            with self.subTest(oracle=18, mutation={}, args=args):
                code, payload = self.run_json(shops["object"], "--head", "feat", *args)
                self.assertEqual(payload.get("status"), "setup", payload)
                self.assertEqual(code, 1, payload)
        with self.subTest(oracle=19):
            shops["none"].control("app/", output(["u1"], [mutant("u1", "core/a.py", 3, "killed", DECISION)]))
            code, payload = self.run_json(shops["none"], "--head", "feat", "--run")
            self.assertIn("api/", payload.get("none") or [], payload)
            self.assertIn("mutation: none for api/", report_lines(payload), payload)


if __name__ == "__main__":
    unittest.main()
