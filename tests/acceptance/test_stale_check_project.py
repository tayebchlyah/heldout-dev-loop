#!/usr/bin/env python3
"""Held-out acceptance tests for the stale check project key (spec: Stale Check Project), flows F1 to F5.

One test per `## Acceptance flows` item (F<n>), one subtest per `## Acceptance oracle` line (O<n>) it proves.
Seam and fixture choices: `stale_check_project_support.py`. F4 also proves the Stage 6 Mechanics oracle line 15 as
this spec replaces it (no compose file at the top, and no key or `COMPOSE_PROJECT_NAME` set: `skipped`).

Fixture choices the spec leaves open:
- O2 runs with a `compose.yaml` at the worktree top that the key path does not use; O3 removes it.
- O7: the second `api` container keeps the `config_files` label of the setup.
- O11: the fake holds one container of another project, `acme-other`, created before.
- O13: the fake holds an `api` container created before.
- O10: "prints nothing" is the key `true`; "two lines" is `printf 'acme-a\\nacme-b\\n'`.
- O17: each case commits `dev-loop.json` on `main` (the main ref) or in the worktree, then resets it; before the
  cases, one run writes the record. The key at `main` is the marker key, so "nothing run" covers the key command.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from merge_check_binding_support import junit
from stale_check_project_support import (AFTER, BEFORE, IMAGE_ONLY, KEY, PROJECT_FILTER, TOP_COMPOSE, Stack,
                                         marker_key, option_values)


class Case(unittest.TestCase):

    def tmp(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name).resolve()

    def stack(self, key: object = KEY, top_compose: bool = False, marker: bool = False) -> Stack:
        root = self.tmp()
        return Stack(root, marker_key(root) if marker else key, top_compose)

    def accept(self, stack: Stack, containers: list[dict], env: dict[str, str] | None = None,
               **state: object) -> tuple[int, dict, dict]:
        """Write the docker state, run `acceptance` once (passing JUnit, exit 0); the run and its `stale`."""
        stack.state(containers, **state)
        stack.fixture(junit(passing=1), 0)
        before = stack.ran()
        code, run = stack.run(env)
        self.assertNotIn("error", run)
        self.assertEqual(stack.ran(), before + 1, f"the acceptance command did not run once: {run}")
        stale = run.get("stale")
        self.assertIsInstance(stale, dict, run)
        return code, run, stale

    def assert_project(self, stale: dict, project: str | None) -> None:
        self.assertIn("project", stale, stale)
        self.assertEqual(stale["project"], project, stale)

    def assert_error(self, run: dict, stale: dict) -> str:
        """`stale` `error` with a message, `project` null, the run not counted; the message."""
        self.assertEqual(stale.get("state"), "error", run)
        self.assertTrue(stale.get("message"), run)
        self.assert_project(stale, None)
        self.assertIs(run.get("counted"), False, run)
        return str(stale["message"])

    def compose_calls(self, stack: Stack, word: str) -> list[list[str]]:
        return [call for call in stack.calls() if call[:1] == ["compose"] and word in call]

    def top_ps_calls(self, stack: Stack) -> list[list[str]]:
        return [call for call in stack.calls() if call[:1] == ["ps"]]

    def assert_lookup_of_today(self, stack: Stack) -> None:
        """The fake logged `docker compose --profile test ps -a -q api` and no project label filter."""
        found = [call for call in self.compose_calls(stack, "ps")
                 if call[-1] == "api" and "test" in option_values(call, "--profile")]
        self.assertTrue(found, stack.calls())
        self.assertEqual([call for call in stack.calls() if any(PROJECT_FILTER in arg for arg in call)], [],
                         stack.calls())


class KeyedProjectTests(Case):

    def test_f1_key_names_the_worktree_compose_project(self) -> None:
        """F1, O1, O2, O3, O4, O5, O6, O11, O13, O15: the key path checks the build-service containers of the
        named project, with the compose files of their labels, and names the project."""
        stack = self.stack()
        api_before = stack.container("a1", "api", BEFORE)
        api_after = stack.container("a1", "api", AFTER)

        code, run, stale = self.accept(stack, [api_before])
        with self.subTest(oracle=1):
            self.assertEqual(stale.get("state"), "stale", run)
            self.assertEqual(stale.get("services"), ["api"], run)
            self.assert_project(stale, "acme-wt")
            self.assertIs(run.get("counted"), False, run)
            self.assertIn("guard", run)
            self.assertIsNone(run["guard"], run)
            self.assertEqual(code, 1, run)
            ps = [call for call in self.top_ps_calls(stack)
                  if any(arg.endswith(PROJECT_FILTER + "acme-wt") for arg in call)]
            self.assertTrue(ps, stack.calls())
            config = self.compose_calls(stack, "config")
            self.assertTrue(config, stack.calls())
            call = config[0]
            self.assertIn("acme-wt", option_values(call, "-p", "--project-name"), call)
            self.assertIn(stack.stack, option_values(call, "--project-directory"), call)
            self.assertEqual(option_values(call, "-f", "--file"), [stack.compose_file], call)
            self.assertIn("test", option_values(call, "--profile"), call)

        (stack.wt / "compose.yaml").write_text(TOP_COMPOSE)
        _, run, stale = self.accept(stack, [api_after])
        with self.subTest(oracle=2):
            self.assertEqual(stale.get("state"), "fresh", run)
            self.assertEqual(stale.get("checked"), ["api"], run)
            self.assert_project(stale, "acme-wt")
            self.assertIs(run.get("counted"), True, run)

        (stack.wt / "compose.yaml").unlink()
        _, run, stale = self.accept(stack, [api_after])
        with self.subTest(oracle=3):
            self.assertNotIn("COMPOSE_FILE", stack.env)
            self.assertFalse(any((stack.wt / name).exists() for name in (
                "compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")))
            self.assertEqual(stale.get("state"), "fresh", run)
            self.assertNotEqual(stale.get("reason"), "no-compose", run)

        e2e = str(stack.wt / "e2e.yml")
        _, run, stale = self.accept(stack, [stack.container("a1", "api", AFTER,
                                                            config_files=f"{stack.compose_file},{e2e}")])
        with self.subTest(oracle=4):
            self.assertEqual(stale.get("state"), "fresh", run)
            config = self.compose_calls(stack, "config")
            self.assertTrue(config, stack.calls())
            self.assertEqual(option_values(config[0], "-f", "--file"), [stack.compose_file, e2e], config[0])

        _, run, stale = self.accept(stack, [api_after, stack.container("d2", "db", BEFORE)])
        with self.subTest(oracle=5):
            self.assertEqual(stale.get("state"), "fresh", run)

        _, run, stale = self.accept(stack, [api_after, stack.container("b3", "api", BEFORE, oneoff="True")])
        with self.subTest(oracle=6):
            self.assertEqual(stale.get("state"), "fresh", run)

        other = stack.container("c4", "api", BEFORE, project="acme-other", working_dir=stack.other)
        _, run, stale = self.accept(stack, [other])
        with self.subTest(oracle=11):
            self.assertEqual((stale.get("state"), stale.get("reason")), ("skipped", "no-container"), run)
            self.assert_project(stale, "acme-wt")
            self.assertIs(run.get("counted"), True, run)

        _, run, stale = self.accept(stack, [api_before], services=IMAGE_ONLY)
        with self.subTest(oracle=13):
            self.assertEqual((stale.get("state"), stale.get("reason")), ("skipped", "no-build-service"), run)
            self.assert_project(stale, "acme-wt")

        _, run, stale = self.accept(stack, [api_after], env={"COMPOSE_PROJECT_NAME": ""})
        with self.subTest(oracle=15):
            self.assertEqual(stale.get("state"), "fresh", run)
            self.assertEqual(stale.get("checked"), ["api"], run)
            self.assert_project(stale, "acme-wt")
            self.assertIs(run.get("counted"), True, run)


class SharedNameTests(Case):

    def test_f2_name_shared_by_two_worktrees(self) -> None:
        """F2, O7, O8: containers of the named project from two folders, or from two file sets: `error`, not
        counted; the message names both folders, or says to reset the stack."""
        stack = self.stack()
        api_after = stack.container("a1", "api", AFTER)

        _, run, stale = self.accept(stack, [api_after, stack.container("e5", "api", AFTER, working_dir=stack.other)])
        with self.subTest(oracle=7):
            message = self.assert_error(run, stale)
            self.assertIn(stack.other, message, run)
            self.assertIn(stack.stack, message, run)
            self.assertIn("guard", run)
            self.assertIsNone(run["guard"], run)

        other_file = str(stack.wt / "other.yml")
        _, run, stale = self.accept(stack, [api_after, stack.container("e5", "api", AFTER, config_files=other_file)])
        with self.subTest(oracle=8):
            message = self.assert_error(run, stale)
            self.assertIn("reset", message.lower(), run)


class KeyFailureTests(Case):

    def test_f3_key_command_fails_or_prints_a_bad_name(self) -> None:
        """F3, O9, O10, O12: the key exits 3, prints nothing or two lines, or the compose config call fails:
        `error`, `project` null, not counted."""
        broken = self.stack(key="echo 'acme key broke' >&2; exit 3")
        _, run, stale = self.accept(broken, [broken.container("a1", "api", AFTER)])
        with self.subTest(oracle=9):
            message = self.assert_error(run, stale)
            self.assertRegex(message, r"(?<!\d)3(?!\d)", run)
            self.assertEqual(self.top_ps_calls(broken), [], broken.calls())

        for name, key in (("nothing", "true"), ("two lines", "printf 'acme-a\\nacme-b\\n'")):
            stack = self.stack(key=key)
            _, run, stale = self.accept(stack, [stack.container("a1", "api", AFTER)])
            with self.subTest(oracle=10, prints=name):
                self.assert_error(run, stale)

        stack = self.stack()
        _, run, stale = self.accept(stack, [stack.container("a1", "api", AFTER)], config_fail=True)
        with self.subTest(oracle=12):
            message = self.assert_error(run, stale)
            self.assertIn("reset", message.lower(), run)


class CheckOfTodayTests(Case):

    def test_f4_variable_set_or_no_key_gives_the_check_of_today(self) -> None:
        """F4, O14, O16, and Stage 6 Mechanics O15 as replaced: `COMPOSE_PROJECT_NAME` set, or no key, runs the
        lookup of today (Stage 6 Mechanics D8); `project` is the variable, or null."""
        env = {"COMPOSE_PROJECT_NAME": "acme-env"}
        keyed = self.stack(top_compose=True, marker=True)
        _, run, stale = self.accept(keyed, [keyed.container("a1", "api", BEFORE, project="acme-env")], env=env)
        with self.subTest(oracle=14):
            self.assert_lookup_of_today(keyed)
            self.assertEqual(keyed.key_runs(), 0, "the key command ran with COMPOSE_PROJECT_NAME set")
            self.assert_project(stale, "acme-env")

        bare = self.stack(key=None, top_compose=True)
        _, run, stale = self.accept(bare, [bare.container("a1", "api", BEFORE, project="acme-widgets-demo")])
        with self.subTest(oracle=16):
            self.assert_lookup_of_today(bare)
            self.assertEqual(stale.get("state"), "stale", run)
            self.assert_project(stale, None)

        (bare.wt / "compose.yaml").unlink()
        _, run, stale = self.accept(bare, [bare.container("a1", "api", BEFORE)])
        with self.subTest(stage_6_mechanics=15, case="no key"):
            self.assertEqual((stale.get("state"), stale.get("reason")), ("skipped", "no-compose"), run)
            self.assert_project(stale, None)

        (keyed.wt / "compose.yaml").unlink()
        _, run, stale = self.accept(keyed, [keyed.container("a1", "api", BEFORE)], env=env)
        with self.subTest(stage_6_mechanics=15, case="COMPOSE_PROJECT_NAME set"):
            self.assertEqual((stale.get("state"), stale.get("reason")), ("skipped", "no-compose"), run)
            self.assert_project(stale, "acme-env")
            self.assertEqual(keyed.key_runs(), 0)


class TrustTests(Case):

    def assert_refused(self, stack: Stack, env: dict[str, str]) -> None:
        stack.state([stack.container("a1", "api", BEFORE)])
        stack.fixture(junit(passing=1), 0)
        before, ran, keys = stack.snapshot(), stack.ran(), stack.key_runs()
        code, out = stack.run(env)
        self.assertEqual(code, 1, out)
        self.assertTrue(out.get("error"), out)
        self.assertEqual(stack.ran(), ran, "the acceptance command ran")
        self.assertEqual(stack.key_runs(), keys, "the key command ran")
        self.assertEqual(stack.calls(), [], "docker was called")
        self.assertEqual(stack.snapshot(), before, "a log, a reports folder or a record change was made")

    def test_f5_key_changed_on_the_branch_only_is_refused(self) -> None:
        """F5, O17: the key only in the worktree, only at the main ref, a different value, or a number at the main
        ref: exit 1, `error`, nothing run, no log, the record unchanged; the same with `COMPOSE_PROJECT_NAME`."""
        stack = self.stack(marker=True)
        self.accept(stack, [stack.container("a1", "api", AFTER)])
        self.assertTrue((Path(stack.git(stack.wt, "rev-parse", "--absolute-git-dir")) / "dev-loop"
                         / "acceptance.json").is_file(), "the first run wrote no record")
        no_key, number = stack.key_config(None), stack.key_config(5)
        cases = (("worktree only", no_key, None), ("main only", None, no_key),
                 ("different value", None, stack.key_config("echo acme-zz")), ("number at main", number, number))
        for name, on_main, on_branch in cases:
            if on_main is not None:
                stack.commit(stack.repo, {"dev-loop.json": on_main}, f"Main: {name}")
            if on_branch is not None:
                stack.commit(stack.wt, {"dev-loop.json": on_branch}, f"Branch: {name}")
            for env in ({}, {"COMPOSE_PROJECT_NAME": "acme-env"}):
                with self.subTest(oracle=17, case=name, variable=bool(env)):
                    self.assert_refused(stack, env)
            if on_main is not None:
                stack.git(stack.repo, "reset", "-q", "--hard", "HEAD~1")
            if on_branch is not None:
                stack.git(stack.wt, "reset", "-q", "--hard", "HEAD~1")


if __name__ == "__main__":
    unittest.main()
