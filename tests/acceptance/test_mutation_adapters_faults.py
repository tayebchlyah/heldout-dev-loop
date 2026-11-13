#!/usr/bin/env python3
"""Held-out oracle tests for mutation adapters (spec: 20261113 Mutation Adapters), broken adapters (flow F5).

Seam 1: the `devloop.py mutation --run` process of the kit source on a fixture repo whose `command` adapters are
fixture scripts (`mutation_adapters_support`). One test for flow F5; its docstring names the `## Acceptance oracle`
lines (O<n>) it checks.

Fixture choices the spec leaves open:
- The head changes two critical files, `app/core/a.py` and `app/core/b.py`, so a valid output names both (a mutant
  of `a.py`, a `not_covered` entry for `b.py`), and the O14 "names only one of them" output drops `b.py`.
- "`cause` names the fault": the cause is one non-empty line that holds one of the words of that fault (the broken
  key, value or file), compared case-insensitively.
"""

from __future__ import annotations

import json
import unittest

from mutation_adapters_support import (COMPARE_BASE, COMPARE_HEAD, MutationCase, Shop, entry, gap, mutant, output,
                                       report_lines)

DECISION = ["-    return a > b", "+    return a >= b"]


def valid(**extra: object) -> dict:
    return output(["u1"], [mutant("u1", "core/a.py", 3, diff=DECISION)], [gap("core/b.py", 1, 1)], **extra)


def with_mutant(**changes: object) -> dict:
    """The valid output with its mutant changed (a None value removes the key)."""
    data = valid()
    item = {**data["mutants"][0], **changes}
    data["mutants"] = [{key: value for key, value in item.items() if value is not None}]
    return data


class BrokenAdapterFlowTests(MutationCase):
    def test_f5_o13_o14_o17_broken_adapter(self) -> None:
        """F5, O13: adapter exit 2 with stderr `x`: entry status `setup`, `cause` holds `x`, exit 1; exit 127:
        `setup`; exit 3: `failed`, exit 1; an output with an extra key `x`: accepted. O14: exit 0 with each broken
        output (not JSON, a mutant without `unit`, status `lived`, `line` 0, `line` true, `file` `../x.py`, a unit
        not in `units`, a `not_covered` with `start` 5 and `end` 4, an output that names one of the two files):
        status `setup`, `cause` names the fault, exit 1. O17: two entries, one adapter exits 3, the other returns a
        survivor: both run, the report shows both, status `failed`."""
        shop = Shop(self)
        shop.build({"mutation": [entry("app/", ["core/**"])]},
                   {"app/core/a.py": COMPARE_BASE, "app/core/b.py": "B = 1\n"})
        shop.change({"app/core/a.py": COMPARE_HEAD, "app/core/b.py": "B = 2\n"})

        shop.control("app/", valid(), exit=2, stderr="x\n")
        code, payload = self.run_json(shop, "--head", "feat", "--run")
        with self.subTest(oracle=13, exit=2):
            app = self.entry_of(payload, "app/")
            self.assertEqual(app.get("status"), "setup", app)
            self.assertIn("x", app.get("cause") or "", app)
            self.assertEqual(payload.get("status"), "setup", payload)
            self.assertEqual(code, 1, payload)
        for exit_code, status in ((127, "setup"), (3, "failed")):
            shop.control("app/", valid(), exit=exit_code, stderr="adapter broke\n")
            code, payload = self.run_json(shop, "--head", "feat", "--run")
            with self.subTest(oracle=13, exit=exit_code):
                self.assertEqual(self.entry_of(payload, "app/").get("status"), status, payload)
                self.assertEqual(payload.get("status"), status, payload)
                self.assertEqual(code, 1, payload)
        shop.control("app/", valid(x=1))
        code, payload = self.run_json(shop, "--head", "feat", "--run")
        with self.subTest(oracle=13, extra_key="x"):
            self.assertEqual(payload.get("status"), "survivors", payload)
            self.assertEqual(code, 0, payload)

        broken = (("not JSON", "this is not json\n", ("json",)),
                  ("no unit", with_mutant(unit=None), ("unit",)),
                  ("status lived", with_mutant(status="lived"), ("lived", "status")),
                  ("line 0", with_mutant(line=0), ("line",)),
                  ("line true", with_mutant(line=True), ("line",)),
                  ("file ../x.py", with_mutant(file="../x.py"), ("x.py", "file")),
                  ("unit not in units", with_mutant(unit="u9"), ("u9", "unit")),
                  ("start after end", output(["u1"], [mutant("u1", "core/a.py", 3, diff=DECISION)],
                                             [gap("core/b.py", 5, 4)]), ("start", "end", "not_covered")),
                  ("one file of two", output(["u1"], [mutant("u1", "core/a.py", 3, diff=DECISION)]),
                   ("b.py", "files", "file")))
        for name, data, words in broken:
            shop.control("app/", data, units=["u1"])
            code, payload = self.run_json(shop, "--head", "feat", "--run")
            with self.subTest(oracle=14, output=name):
                app = self.entry_of(payload, "app/")
                self.assertEqual(app.get("status"), "setup", app)
                self.assertEqual(payload.get("status"), "setup", payload)
                self.assertEqual(code, 1, payload)
                cause = app.get("cause")
                self.assertIsInstance(cause, str, app)
                self.assertTrue(cause.strip() and "\n" not in cause, app)
                self.assertTrue(any(word in cause.lower() for word in words), f"{name}: {json.dumps(app)}")

        pair = Shop(self, "pair")
        pair.build({"mutation": [entry("app/", ["core/**"]), entry("lib/", ["core/**"])]},
                   {"app/core/a.py": COMPARE_BASE, "lib/core/a.py": COMPARE_BASE}, adapters=("app/", "lib/"))
        pair.change({"app/core/a.py": COMPARE_HEAD, "lib/core/a.py": COMPARE_HEAD})
        pair.control("app/", output(["u1"], [mutant("u1", "core/a.py", 3, diff=DECISION)]), exit=3,
                     stderr="tool crashed\n")
        pair.control("lib/", output(["u1"], [mutant("u1", "core/a.py", 3, diff=DECISION)]))
        code, payload = self.run_json(pair, "--head", "feat", "--run")
        with self.subTest(oracle=17):
            self.assertEqual(len(pair.run_calls("app/")), 1, pair.calls("app/"))
            self.assertEqual(len(pair.run_calls("lib/")), 1, pair.calls("lib/"))
            self.assertEqual(self.entry_of(payload, "app/").get("status"), "failed", payload)
            self.assertEqual(self.entry_of(payload, "lib/").get("status"), "survivors", payload)
            lines = report_lines(payload)
            self.assertTrue([line for line in lines if line.startswith("entry app/: ")], payload.get("report"))
            self.assertTrue([line for line in lines if line.startswith("entry lib/: ")], payload.get("report"))
            self.assertIn("lib/ units that need one unit test: 1", lines, payload.get("report"))
            self.assertEqual(payload.get("status"), "failed", payload)
            self.assertEqual(code, 1, payload)


if __name__ == "__main__":
    unittest.main()
