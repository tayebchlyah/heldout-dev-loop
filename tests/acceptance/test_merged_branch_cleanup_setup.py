#!/usr/bin/env python3
"""Held-out acceptance test for the merged branch cleanup setup line (spec: 20261114 Merged Branch Cleanup, F8).

Seam 2: the `check_setup.py --harness claude --repo <repo>` process of the kit source, its stdout and exit code, with
a stub `gh` first on `PATH`.

Fixture choices the spec leaves open:
- The project is the Stack Model setup project (`stack_model_support.SetupProject`): installed, on `main`, where
  `check_setup.py --harness claude` passes, with no remote. The test adds `origin`
  `git@github.com:acme/kettle-works.git` (no `origin/main` ref, so the main ref is `main`).
- The stub `gh` appends its argv as one JSON line to `calls.jsonl`. It answers `--version`, and `api
  repos/acme/kettle-works` with the repo JSON (`delete_branch_on_merge` set by the case, `allow_rebase_merge` true),
  applying a plain `.field` `--jq` the way gh does (a string raw, any other value as JSON). In `fail` mode, and for
  any other call, it exits 1.
- Each `dev-loop.json` change is committed, so the checkout and the main ref agree, except in the branch-only case.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

from stack_model_support import SetupProject, commit_all, git, put, user_actions

SLUG = "acme/kettle-works"
SETUP_LINE = (f'turn on "Automatically delete head branches" in the GitHub settings of {SLUG} (Stage 7 leaves the '
              f"merged branch on GitHub): gh api -X PATCH repos/{SLUG} -F delete_branch_on_merge=true, or waive it "
              'with "delete_branch_on_merge": false in dev-loop.json')
LINE_MARK = "Automatically delete head branches"
CONFIG_FAILURE = "delete_branch_on_merge in dev-loop.json must be true or false"

STUB = r'''
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ARGV = sys.argv[1:]
with open(os.path.join(HERE, "calls.jsonl"), "a") as handle:
    handle.write(json.dumps(ARGV) + "\n")
if ARGV[:1] == ["--version"]:
    print("gh version 2.103.2 (2026-10-01)")
    sys.exit(0)
with open(os.path.join(HERE, "state.json")) as handle:
    state = json.load(handle)
if state["mode"] != "ok" or len(ARGV) < 2 or ARGV[0] != "api" or ARGV[1] != "repos/" + state["slug"]:
    sys.stderr.write("stub gh: no answer for " + " ".join(ARGV) + "\n")
    sys.exit(1)
value = state["repo"]
out = json.dumps(value) + "\n"
for flag in ("--jq", "-q"):
    if flag in ARGV[:-1]:
        expr = ARGV[ARGV.index(flag) + 1]
        if re.fullmatch(r"(\.[A-Za-z_][A-Za-z0-9_]*)+", expr):
            for part in expr.split(".")[1:]:
                value = value.get(part) if isinstance(value, dict) else None
            out = (value if isinstance(value, str) else json.dumps(value)) + "\n"
sys.stdout.write(out)
'''


class SetupLineTests(SetupProject):

    def setUp(self) -> None:
        super().setUp()
        self.stub = self.root / "stub-gh"
        put(self.stub / "gh", f"#!{sys.executable}\n{STUB}")
        (self.stub / "gh").chmod(0o755)
        self.env["PATH"] = f"{self.stub}{os.pathsep}{self.env['PATH']}"
        git(self.project, self.env, "remote", "add", "origin", f"git@github.com:{SLUG}.git")

    def answer(self, mode: str, setting: object = False) -> None:
        repo = {"full_name": SLUG, "allow_rebase_merge": True, "allow_merge_commit": True,
                "delete_branch_on_merge": setting}
        put(self.stub / "state.json", json.dumps({"mode": mode, "slug": SLUG, "repo": repo}))
        (self.stub / "calls.jsonl").unlink(missing_ok=True)

    def calls(self) -> list[list[str]]:
        log = self.stub / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def config(self, waiver: object, message: str) -> None:
        config = dict(self.base)
        if waiver is not None:
            config["delete_branch_on_merge"] = waiver
        self.write_config(config)
        commit_all(self.project, self.env, message)

    def line_given(self, output: str) -> bool:
        return any(SETUP_LINE in line for line in user_actions(output))

    def no_line(self, output: str) -> None:
        self.assertEqual([line for line in output.splitlines() if LINE_MARK in line], [], output)

    def test_f8_setup_line(self) -> None:
        """F8: setting off, setting on, gh failure, non-GitHub origin, waiver at the main ref, waiver on a branch
        only, bad waiver value. O21, O22, O23, O24, O25."""
        with self.subTest(oracle=21):
            self.answer("ok", False)
            code, output = self.check()
            self.assertTrue(self.line_given(output), output)
            self.assertTrue([argv for argv in self.calls() if argv[:2] == ["api", f"repos/{SLUG}"]], self.calls())

        with self.subTest(oracle=22, case="setting on"):
            self.answer("ok", True)
            code, output = self.check()
            self.no_line(output)

        with self.subTest(oracle=22, case="gh fails"):
            self.answer("fail")
            code, output = self.check()
            self.no_line(output)

        with self.subTest(oracle=22, case="not GitHub"):
            self.answer("ok", False)
            git(self.project, self.env, "remote", "set-url", "origin", "https://git.example.invalid/acme/kettle-works.git")
            try:
                code, output = self.check()
            finally:
                git(self.project, self.env, "remote", "set-url", "origin", f"git@github.com:{SLUG}.git")
            self.no_line(output)

        with self.subTest(oracle=24):
            self.answer("ok", False)
            git(self.project, self.env, "checkout", "-q", "-b", "feat/9-waive")
            try:
                self.config(False, "Waive the branch delete setting on a branch")
                code, output = self.check()
            finally:
                git(self.project, self.env, "checkout", "-q", "main")
            self.assertTrue(self.line_given(output), output)

        with self.subTest(oracle=23):
            self.answer("ok", False)
            self.config(False, "Waive the branch delete setting")
            code, output = self.check()
            self.no_line(output)
            self.assertNotIn(CONFIG_FAILURE, output)

        with self.subTest(oracle=25):
            self.answer("ok", False)
            self.config("no", "Set a bad waiver")
            code, output = self.check()
            self.assertEqual(code, 1, output)
            self.assertIn(CONFIG_FAILURE, output)
            self.assertIn("FAIL", output)


if __name__ == "__main__":
    unittest.main()
