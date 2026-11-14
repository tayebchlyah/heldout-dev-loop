#!/usr/bin/env python3
"""Held-out acceptance tests for the merged branch cleanup (spec: 20261114 Merged Branch Cleanup, issue #320).

One test per `## Acceptance flows` item (F1 to F7 here, F8 in `test_merged_branch_cleanup_setup.py`). Each docstring
names the `## Acceptance oracle` lines (O<n>) it proves; each case is a subtest. Seam 1 only: the `devloop.py cleanup`
process of the kit source on a `Shop` (see `merged_branch_cleanup_support.py`); F7 also feeds the Claude
`git-guard.sh` hook JSON on stdin, then runs the guarded line.

Fixture choices the spec leaves open:
- Each case runs on its own `Shop`.
- F5 and F7 end with a control run of a valid `cleanup` on the same fixture: the guarded or refused state was one the
  command does change.
- F6: `git` and `gh` wrappers first on `PATH` append their argv as one JSON line to a log; the `git` wrapper then
  runs the real `git`, the `gh` wrapper exits 1. The git subcommand is the first argument after the global options.
- Oracle lines O26 and O28 (kit text of `skill/SKILL.md`) are in no flow and outside both seams: left to the unit
  seams.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from merged_branch_cleanup_support import GIT_GUARD, SKILL, ScratchCase, put

GUARDED_LINE = "python3 .agents/skills/dev-loop/scripts/devloop.py cleanup --branch feat/x"

WRAPPER = """#!{python}
import json
import os
import sys
with open({log!r}, "a") as handle:
    handle.write(json.dumps([{name!r}] + sys.argv[1:]) + "\\n")
if {real!r}:
    os.execv({real!r}, [{real!r}] + sys.argv[1:])
sys.exit(1)
"""


def subcommand(argv: list[str]) -> list[str]:
    """The git argv from its subcommand on, past the global options."""
    i = 0
    while i < len(argv):
        if argv[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
            i += 2
        elif argv[i].startswith("-"):
            i += 1
        else:
            return argv[i:]
    return []


class ShipAndCleanTests(ScratchCase):

    def test_f1_ship_and_clean(self) -> None:
        """F1: one run on a merged feature with a clean worktree and a behind main checkout removes the worktree,
        deletes the branch, rebases main; a rerun is a no-op with exit 0. O1, O2, O5, O8, O10, O11, O18."""
        shop = self.shop("ship")
        branch = "feat/7-gift-wrap"
        wt = shop.feature(branch)
        other = shop.feature("feat/8-gift-card")
        put(wt / "build" / "out.txt", "ignored build output\n")
        merged = shop.merge(branch)
        before = shop.head()
        self.assertNotEqual(before, merged)
        self.assertTrue(shop.has_ref(f"refs/remotes/origin/{branch}"))

        code, payload = shop.cleanup("--branch", branch)
        with self.subTest(oracle=1):
            self.assertEqual(code, 0, payload)
            self.assertEqual(payload.get("target"), branch, payload)
            self.assertEqual(os.path.realpath(str(payload.get("path"))), str(wt), payload)
            self.assertEqual((payload.get("worktree"), payload.get("branch")), ("removed", "deleted"), payload)
            self.assertFalse(wt.exists(), payload)
            self.assertFalse(shop.has_branch(branch), payload)
        with self.subTest(oracle=2):
            self.assertFalse(shop.has_ref(f"refs/remotes/origin/{branch}"), payload)
        with self.subTest(oracle=5):
            self.assertEqual(payload.get("reasons"), [], payload)
        with self.subTest(oracle=10):
            self.assertEqual(payload.get("main"), "rebased", payload)
            self.assertEqual(shop.head(), merged)
            self.assertEqual(shop.git(shop.repo, "branch", "--show-current"), "main")

        code, payload = shop.cleanup("--branch", branch, "--repo", str(other), cwd=other)
        with self.subTest(oracle=8, run="rerun"):
            self.assertEqual(code, 0, payload)
            self.assertEqual((payload.get("worktree"), payload.get("branch")), ("absent", "absent"), payload)
            self.assertIsNone(payload.get("path", "missing"), payload)
            self.assertEqual(payload.get("reasons"), [], payload)
        with self.subTest(oracle=11):
            self.assertEqual(payload.get("main"), "current", payload)
        with self.subTest(oracle=18):
            self.assertEqual(shop.head(), merged)
            self.assertTrue(other.exists())
            self.assertTrue(shop.has_branch("feat/8-gift-card"))


class UnsafeStatesTests(ScratchCase):

    def test_f2_unsafe_states_kept(self) -> None:
        """F2: unmerged branch, dirty worktree, paused worktree, branch in the main checkout, run from inside the
        worktree: nothing deleted, each reason given. O3, O4, O6, O9, O15, O27."""
        with self.subTest(oracle=3):
            shop = self.shop("unmerged")
            branch = "feat/11-coupon"
            wt = shop.feature(branch)
            code, payload = shop.cleanup("--branch", branch)
            self.assertEqual(code, 1, payload)
            self.assert_parts(payload, "skipped", "skipped", "current")
            self.assertEqual(payload.get("reasons"), ["worktree: not-merged", "branch: worktree-kept"], payload)
            self.assertTrue(wt.exists())
            self.assertTrue(shop.has_branch(branch))

        with self.subTest(oracle=4, change="tracked", also=15):
            shop = self.shop("dirty-tracked")
            branch = "feat/12-receipt"
            wt = shop.feature(branch)
            merged = shop.merge(branch)
            put(wt / "README.md", "# Acme shop\n\nEdited in the worktree.\n")
            code, payload = shop.cleanup("--branch", branch)
            self.assertEqual(code, 1, payload)
            self.assert_parts(payload, "skipped", "skipped", "rebased")
            self.assertIn("worktree: dirty", payload.get("reasons") or [], payload)
            self.assertEqual((wt / "README.md").read_text(), "# Acme shop\n\nEdited in the worktree.\n")
            self.assertTrue(shop.has_branch(branch))
            self.assertEqual(shop.head(), merged)

        with self.subTest(oracle=4, change="untracked"):
            shop = self.shop("dirty-untracked")
            branch = "feat/13-refund"
            wt = shop.feature(branch)
            shop.merge(branch)
            put(wt / "notes.txt", "scratch notes\n")
            code, payload = shop.cleanup("--branch", branch)
            self.assertEqual(code, 1, payload)
            self.assertEqual((payload.get("worktree"), payload.get("branch")), ("skipped", "skipped"), payload)
            self.assertIn("worktree: dirty", payload.get("reasons") or [], payload)
            self.assertEqual((wt / "notes.txt").read_text(), "scratch notes\n")
            self.assertTrue(shop.has_branch(branch))

        with self.subTest(oracle=6):
            shop = self.shop("paused")
            branch = "feat/14-invoice"
            wt = shop.feature(branch)
            shop.merge(branch)
            pause = shop.git_dir(wt) / "dev-loop" / "pause.json"
            put(pause, json.dumps({"intent": "Finish the invoice layout"}) + "\n")
            code, payload = shop.cleanup("--branch", branch)
            self.assertEqual(code, 1, payload)
            self.assertEqual((payload.get("worktree"), payload.get("branch")), ("skipped", "skipped"), payload)
            self.assertIn("worktree: paused", payload.get("reasons") or [], payload)
            self.assertTrue(wt.exists())
            self.assertTrue(pause.exists())
            self.assertTrue(shop.has_branch(branch))

        with self.subTest(oracle=9):
            shop = self.shop("main-checkout")
            branch = "feat/15-basket"
            shop.feature(branch, keep_worktree=False)
            shop.merge(branch)
            shop.git(shop.repo, "checkout", "-q", branch)
            code, payload = shop.cleanup("--branch", branch)
            self.assertEqual(code, 1, payload)
            self.assertEqual((payload.get("worktree"), payload.get("branch")), ("skipped", "skipped"), payload)
            self.assertIn("worktree: main-checkout", payload.get("reasons") or [], payload)
            self.assertTrue(shop.has_branch(branch))
            self.assertEqual(shop.git(shop.repo, "branch", "--show-current"), branch)
            self.assertTrue((shop.repo / "README.md").exists())

        with self.subTest(oracle=27):
            shop = self.shop("in-use")
            branch = "feat/16-wishlist"
            wt = shop.feature(branch)
            merged = shop.merge(branch)
            code, payload = shop.cleanup("--branch", branch, cwd=wt / "src")
            self.assertEqual(code, 1, payload)
            self.assert_parts(payload, "skipped", "skipped", "rebased")
            self.assertIn("worktree: in-use", payload.get("reasons") or [], payload)
            self.assertTrue(wt.exists())
            self.assertTrue(shop.has_branch(branch))
            self.assertEqual(shop.head(), merged)


class MainGuardedTests(ScratchCase):

    def test_f3_main_checkout_guarded(self) -> None:
        """F3: local commit, tracked change, other branch or detached: no rebase, each reason given, the feature
        cleanup still done. O12, O13, O14, O7."""
        with self.subTest(oracle=12):
            shop = self.shop("local-commit")
            branch = "feat/21-label"
            wt = shop.feature(branch)
            shop.merge(branch)
            local = shop.commit(shop.repo, {"LOCAL.md": "kept\n"}, "Local note")
            code, payload = shop.cleanup("--branch", branch)
            self.assertEqual(code, 1, payload)
            self.assert_parts(payload, "removed", "deleted", "skipped")
            self.assertEqual(payload.get("reasons"), ["main: local-commits"], payload)
            self.assertEqual(shop.head(), local)
            self.assertFalse(wt.exists())
            self.assertFalse(shop.has_branch(branch))

        with self.subTest(oracle=13, also=7):
            shop = self.shop("tracked-change")
            branch = "feat/22-tag"
            shop.feature(branch, keep_worktree=False)
            shop.merge(branch)
            before = shop.head()
            put(shop.repo / "README.md", "# Acme shop\n\nLocal edit.\n")
            code, payload = shop.cleanup("--branch", branch)
            self.assertEqual(code, 1, payload)
            self.assert_parts(payload, "absent", "deleted", "skipped")
            self.assertEqual(payload.get("reasons"), ["main: dirty"], payload)
            self.assertEqual(shop.head(), before)
            self.assertEqual((shop.repo / "README.md").read_text(), "# Acme shop\n\nLocal edit.\n")
            self.assertFalse(shop.has_branch(branch))

        for where in ("branch", "detached"):
            with self.subTest(oracle=14, main=where):
                shop = self.shop(f"not-on-main-{where}")
                branch = "feat/23-stock"
                wt = shop.feature(branch)
                shop.merge(branch)
                if where == "branch":
                    shop.git(shop.repo, "checkout", "-q", "-b", "chore/tidy")
                else:
                    shop.git(shop.repo, "checkout", "-q", "--detach")
                before = shop.head()
                code, payload = shop.cleanup("--branch", branch)
                self.assertEqual(code, 1, payload)
                self.assert_parts(payload, "removed", "deleted", "skipped")
                self.assertEqual(payload.get("reasons"), ["main: not-on-main"], payload)
                self.assertEqual(shop.head(), before)
                self.assertFalse(wt.exists())


class FetchFailureTests(ScratchCase):

    def test_f4_fetch_failure_changes_nothing(self) -> None:
        """F4: no reachable `origin`: all three parts `skipped`, one `fetch:` reason, exit 1, nothing changes. O16."""
        shop = self.shop("fetch-failure")
        branch = "feat/31-review"
        wt = shop.feature(branch)
        shop.merge(branch)
        shop.git(shop.repo, "fetch", "-q", "origin")
        shop.origin.rename(shop.root / "origin-gone.git")
        before = shop.snapshot()
        code, payload = shop.cleanup("--branch", branch)
        with self.subTest(oracle=16):
            self.assertEqual(code, 1, payload)
            self.assert_parts(payload, "skipped", "skipped", "skipped")
            reasons = payload.get("reasons") or []
            self.assertEqual(len(reasons), 1, payload)
            self.assertTrue(str(reasons[0]).startswith("fetch: "), payload)
            self.assertEqual(shop.snapshot(), before)
            self.assertTrue(wt.exists())


class UsageErrorTests(ScratchCase):

    def test_f5_usage_errors_change_nothing(self) -> None:
        """F5: `--branch main`, `--branch master`, no `--branch`, `--repo` outside a repository: exit 2, an `error`,
        nothing changes; then a valid run on the same fixture cleans it. O17."""
        shop = self.shop("usage")
        branch = "feat/41-search"
        wt = shop.feature(branch)
        shop.merge(branch)
        plain = shop.root / "plain-folder"
        plain.mkdir()
        before = shop.snapshot()
        for label, args in (("main", ("--branch", "main")), ("master", ("--branch", "master")), ("no-branch", ()),
                            ("repo", ("--branch", branch, "--repo", str(plain)))):
            with self.subTest(oracle=17, case=label):
                code, payload = shop.cleanup(*args)
                self.assertEqual(code, 2, payload)
                self.assertIsInstance(payload.get("error"), str, payload)
                self.assertEqual(shop.snapshot(), before)
                self.assertTrue(wt.exists())
        with self.subTest(oracle=17, case="control"):
            code, payload = shop.cleanup("--branch", branch)
            self.assertEqual(code, 0, payload)
            self.assert_parts(payload, "removed", "deleted", "rebased")


class NoForceTests(ScratchCase):

    def test_f6_no_push_no_gh_no_force(self) -> None:
        """F6: an argv log of `git` and `gh` wrappers shows no `git push`, no `gh`, no `git worktree remove --force`,
        no `git stash`, no `git reset`, on a skip run and a full cleanup run. O19, O2."""
        shop = self.shop("argv-log")
        dirty, clean = "feat/51-filter", "feat/52-sort"
        dirty_wt = shop.feature(dirty)
        clean_wt = shop.feature(clean)
        shop.merge(dirty)
        shop.merge(clean)
        put(dirty_wt / "README.md", "# Acme shop\n\nWork in progress.\n")
        bins = shop.root / "wrap-bin"
        log = shop.root / "argv.jsonl"
        real_git = shutil.which("git", path=shop.env.get("PATH"))
        self.assertTrue(real_git)
        for name, real in (("git", real_git), ("gh", "")):
            put(bins / name, WRAPPER.format(python=sys.executable, log=str(log), name=name, real=real))
            (bins / name).chmod(0o755)
        env = {"PATH": f"{bins}{os.pathsep}{shop.env.get('PATH', '')}"}
        code_dirty, payload_dirty = shop.cleanup("--branch", dirty, env=env)
        code_clean, payload_clean = shop.cleanup("--branch", clean, env=env)
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        git_calls = [subcommand(argv[1:]) for argv in calls if argv[0] == "git"]
        with self.subTest(oracle=19, check="runs"):
            self.assertEqual(code_dirty, 1, payload_dirty)
            self.assertIn("worktree: dirty", payload_dirty.get("reasons") or [], payload_dirty)
            self.assertEqual(code_clean, 0, payload_clean)
            self.assertEqual(payload_clean.get("worktree"), "removed", payload_clean)
            self.assertFalse(clean_wt.exists())
            self.assertTrue(dirty_wt.exists())
        with self.subTest(oracle=2):
            fetches = [argv for argv in git_calls if argv[:1] == ["fetch"]]
            self.assertTrue(fetches, calls)
            self.assertTrue(all("origin" in argv and "--prune" in argv for argv in fetches), fetches)
        with self.subTest(oracle=19, check="argv"):
            self.assertEqual([argv for argv in calls if argv[0] == "gh"], [], calls)
            for argv in git_calls:
                self.assertNotIn(argv[:1], (["push"], ["stash"], ["reset"]), argv)
                if argv[:2] == ["worktree", "remove"]:
                    self.assertFalse([a for a in argv[2:] if a == "--force" or a.startswith("-f")], argv)


class GuardSilenceTests(ScratchCase):

    def test_f7_guard_lets_the_cleanup_line_run(self) -> None:
        """F7: the Claude `git-guard.sh`, main session, gives no ask and no deny for the Stage 7 cleanup line; the line
        then runs with the main checkout kit and cleans the merged feature. O20."""
        shop = self.shop("guard")
        branch = "feat/x"
        wt = shop.feature(branch)
        shop.merge(branch)
        shutil.copytree(SKILL, shop.repo / ".agents" / "skills" / "dev-loop",
                        ignore=shutil.ignore_patterns("__pycache__"))
        hook = {"session_id": "session-1", "cwd": str(shop.repo), "hook_event_name": "PreToolUse",
                "tool_name": "Bash", "tool_input": {"command": GUARDED_LINE}}
        result = subprocess.run(["bash", str(GIT_GUARD)], cwd=shop.repo, input=json.dumps(hook), env=shop.env,
                                capture_output=True, text=True, timeout=60)
        try:
            out = json.loads(result.stdout) if result.stdout.strip() else {}
        except ValueError:
            out = {}
        decision = ((out.get("hookSpecificOutput") or {}).get("permissionDecision") if isinstance(out, dict)
                    else None)
        with self.subTest(oracle=20, check="guard"):
            self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
            self.assertNotIn(decision, ("ask", "deny"), result.stdout)
        run = subprocess.run(["bash", "-c", GUARDED_LINE], cwd=shop.repo, env=shop.env, capture_output=True,
                             text=True, timeout=180)
        with self.subTest(oracle=20, check="line runs"):
            self.assertEqual(run.returncode, 0, (run.stdout[-800:], run.stderr[-800:]))
            try:
                payload = json.loads(run.stdout)
            except ValueError:
                payload = {}
            self.assertEqual((payload.get("worktree"), payload.get("branch")), ("removed", "deleted"), run.stdout)
            self.assertFalse(wt.exists())


if __name__ == "__main__":
    unittest.main()
