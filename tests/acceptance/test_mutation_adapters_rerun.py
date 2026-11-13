#!/usr/bin/env python3
"""Held-out oracle tests for mutation adapters (spec: 20261113 Mutation Adapters), rerun and integrity.

Seam 1: the `devloop.py mutation` process of the kit source on a fixture repo whose `command` adapters are fixture
scripts (`mutation_adapters_support`). One test for flow F3, plus O21; each docstring names the
`## Acceptance oracle` lines (O<n>) it checks.

Fixture choices the spec leaves open:
- O22 gives the repo an `origin` remote (a bare repo in the scratch folder) whose `main` is the base commit, so
  `origin/main` is the main ref. A changed adapter is `app/adapt` with one more comment line; a changed `mutation` is
  one more `!` glob. The runs pass `--base <base commit>`, so the diff selects `app/core/a.py` even when local
  `main` equals the branch.
- "`cause` names it": the cause holds `adapt` for the adapter, and `mutation` or `dev-loop.json` for the key.
- A new test file is an untracked `app/tests/test_new.py`.
"""

from __future__ import annotations

import json
import unittest

from mutation_adapters_support import (COMPARE_BASE, COMPARE_HEAD, MutationCase, Shop, entry, mutant, output)
from stack_model_support import git, put

DECISION = ["-    return a > b", "+    return a >= b"]


def survivor(unit: str) -> dict:
    return output(["u1", "u2"], [mutant(unit, "core/a.py", 3, diff=DECISION)])


class RerunFlowTests(MutationCase):
    def test_f3_o15_o16_o22_rerun_of_listed_units(self) -> None:
        """F3, O15: two entries, `--only u1` without `--entry`: exit 2; `--entry app/ --only u1`: only `app/` runs,
        with `only` `["u1"]`; `units` without `u1`: exit 2 and one adapter call, `run` false; a mutant of `u2`
        under `only` `["u1"]`: `setup`. O16: `--entry app/ --only u1` with no critical change: exit 2. O22: a branch
        whose `./adapt` or `mutation` differs from `origin/main` (also with local `main` equal to it): `failed`,
        `cause` names it, no adapter call; a selected file edited in the working tree: `failed`; a new test file:
        no effect."""
        shop = Shop(self)
        shop.build({"mutation": [entry("app/", ["core/**"]), entry("lib/", ["core/**"])]},
                   {"app/core/a.py": COMPARE_BASE, "lib/core/a.py": COMPARE_BASE, "app/web/b.py": "PAGE = 1\n"},
                   adapters=("app/", "lib/"))
        shop.change({"app/core/a.py": COMPARE_HEAD, "lib/core/a.py": COMPARE_HEAD})
        shop.control("app/", survivor("u1"))
        shop.control("lib/", survivor("u1"))
        rerun = ("--head", "feat", "--run", "--entry", "app/", "--only", "u1")

        with self.subTest(oracle=15, part="--only without --entry"):
            code, _, text = shop.mutation("--head", "feat", "--run", "--only", "u1")
            self.assertEqual(code, 2, text)
        shop.forget_calls()
        with self.subTest(oracle=15, part="--entry app/ --only u1"):
            code, payload = self.run_json(shop, *rerun)
            self.assertEqual(code, 0, payload)
            self.assertEqual(shop.calls("lib/"), [])
            runs = shop.run_calls("app/")
            self.assertEqual(len(runs), 1, shop.calls("app/"))
            self.assertEqual(runs[0].get("only"), ["u1"], runs[0])
            self.assertEqual([item.get("path") for item in payload.get("entries") or []], ["app/"], payload)
        shop.forget_calls()
        shop.control("app/", survivor("u2"), units=["u2"])
        with self.subTest(oracle=15, part="unknown unit"):
            code, _, text = shop.mutation(*rerun)
            self.assertEqual(code, 2, text)
            calls = shop.calls("app/")
            self.assertEqual(len(calls), 1, calls)
            self.assertIs(calls[0]["stdin"].get("run"), False, calls)
        shop.control("app/", survivor("u2"))
        with self.subTest(oracle=15, part="mutant outside only"):
            code, payload = self.run_json(shop, *rerun)
            self.assertEqual(self.entry_of(payload, "app/").get("status"), "setup", payload)
            self.assertEqual(code, 1, payload)

        shop.change({"app/web/b.py": "PAGE = 2\n"}, branch="docs")
        shop.forget_calls()
        with self.subTest(oracle=16):
            code, _, text = shop.mutation("--head", "docs", "--run", "--entry", "app/", "--only", "u1")
            self.assertEqual(code, 2, text)
            self.assertEqual(shop.run_calls("app/"), [])

        self.integrity(shop)

    def integrity(self, shop: Shop) -> None:
        bare = self.root / "origin.git"
        git(self.root, self.env, "init", "-q", "--bare", str(bare))
        git(shop.path, self.env, "remote", "add", "origin", str(bare))
        git(shop.path, self.env, "push", "-q", "origin", f"{shop.base}:refs/heads/main")
        git(shop.path, self.env, "fetch", "-q", "origin")
        shop.control("app/", survivor("u1"))
        adapt = (shop.path / "app" / "adapt").read_text() + "# quieter\n"
        config = json.loads((shop.path / "dev-loop.json").read_text())
        config["mutation"][0]["critical"] = ["core/**", "!core/zz/**"]
        branches = (("adapter", {"app/core/a.py": COMPARE_HEAD, "app/adapt": adapt}, ("adapt",)),
                    ("config", {"app/core/a.py": COMPARE_HEAD, "dev-loop.json": json.dumps(config, indent=2) + "\n"},
                     ("mutation", "dev-loop.json")))
        for name, files, words in branches:
            sha = shop.change(files, branch=name)
            for local_main in ("base", "branch"):
                git(shop.path, self.env, "branch", "-f", "main", shop.base if local_main == "base" else sha)
                shop.forget_calls()
                code, payload = self.run_json(shop, "--head", name, "--base", shop.base, "--run")
                with self.subTest(oracle=22, changed=name, local_main=local_main):
                    self.assertEqual(payload.get("status"), "failed", payload)
                    self.assertEqual(code, 1, payload)
                    causes = " ".join(str(item.get("cause")) for item in payload.get("entries") or [])
                    causes += " " + str(payload.get("cause") or "")
                    self.assertTrue(any(word in causes for word in words), payload)
                    self.assertEqual(shop.run_calls("app/"), [])
                    self.assertEqual(shop.run_calls("lib/"), [])
        git(shop.path, self.env, "branch", "-f", "main", shop.base)
        shop.checkout("feat")
        put(shop.path / "app" / "core" / "a.py", COMPARE_HEAD + "# local edit\n")
        shop.forget_calls()
        code, payload = self.run_json(shop, "--head", "feat", "--run")
        with self.subTest(oracle=22, part="selected file edited"):
            self.assertEqual(payload.get("status"), "failed", payload)
            self.assertEqual(code, 1, payload)
            self.assertEqual(shop.run_calls("app/"), [])
        git(shop.path, self.env, "checkout", "--", "app/core/a.py")
        put(shop.path / "app" / "tests" / "test_new.py", "def test_new():\n    assert True\n")
        shop.forget_calls()
        code, payload = self.run_json(shop, "--head", "feat", "--run")
        with self.subTest(oracle=22, part="new test file"):
            self.assertEqual(payload.get("status"), "survivors", payload)
            self.assertEqual(code, 0, payload)
            self.assertEqual(len(shop.run_calls("app/")), 1, shop.calls("app/"))


class WorkingTreeConfigTests(MutationCase):
    def test_o21_working_tree_mutation_differs_from_head(self) -> None:
        """O21: `--head` has entry `a/`, the working tree `dev-loop.json` has `b/`: status `failed`; no adapter
        runs."""
        shop = Shop(self)
        shop.build({"mutation": [entry("a/", ["core/**"])]},
                   {"a/core/a.py": COMPARE_BASE, "b/core/a.py": COMPARE_BASE}, adapters=("a/", "b/"))
        shop.change({"a/core/a.py": COMPARE_HEAD, "b/core/a.py": COMPARE_HEAD})
        shop.control("a/", survivor("u1"))
        shop.control("b/", survivor("u1"))
        shop.write_config({"mutation": [entry("b/", ["core/**"])]})
        code, payload = self.run_json(shop, "--head", "feat", "--run")
        self.assertEqual(payload.get("status"), "failed", payload)
        self.assertEqual(code, 1, payload)
        self.assertEqual(shop.calls("a/"), [])
        self.assertEqual(shop.calls("b/"), [])


if __name__ == "__main__":
    unittest.main()
