#!/usr/bin/env python3
"""Held-out oracle tests for one session and effort per stage (spec: One Session and Effort per Stage), part 2:
the worktree wall `claude/hooks/worktree-wall.sh` and the `deny-ask.sh` reason, acceptance flow 2.

Each docstring names the `## Acceptance oracle` lines (O<n>) or flows (F<n>) it proves. Seam: the hook script run
with `bash`, hook JSON on stdin (`cwd`, `tool_name`, `tool_input`), in temporary repos with linked worktrees.
The process cwd is the hook `cwd` when that folder exists; `CLAUDE_PROJECT_DIR` is the main checkout, where the
session started. Every name in the temp repos is invented.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "unit" / "python"))
from board_fixtures import CLAUDE_HOOKS, Board, git, put

WALL = CLAUDE_HOOKS / "worktree-wall.sh"
MARKER = "pwned-marker"


def decision(result: subprocess.CompletedProcess[str]) -> tuple[str | None, str]:
    """The PreToolUse decision on stdout and its reason; (None, "") when stdout is not a JSON object."""
    try:
        out = json.loads(result.stdout) if result.stdout.strip() else None
    except ValueError:
        return None, ""
    if not isinstance(out, dict):
        return None, ""
    specific = out.get("hookSpecificOutput") or {}
    found = specific.get("permissionDecision") or out.get("decision")
    reason = specific.get("permissionDecisionReason") or out.get("reason") or ""
    return found, reason


class WallCase(Board):
    """The board sandbox: a main checkout `acme-widgets` with `dev-loop.json` and a bare origin, plus a feature
    worktree on `feat/demo` with a committed `src/app.py`."""

    def setUp(self) -> None:
        super().setUp()
        for key in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CLAUDE_EFFORT"):
            self.env.pop(key, None)
        self.env["CLAUDE_PROJECT_DIR"] = str(self.repo)
        self.wt = self.worktree("demo")
        put(self.wt / "src" / "app.py", "print('demo')\n")
        put(self.wt / "notes.ipynb", "{}\n")

    def hook(self, payload: object, script: Path = WALL, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        self.assertTrue(script.is_file(), f"no hook script {script}")
        if cwd is None:
            hook_cwd = payload.get("cwd") if isinstance(payload, dict) else None
            cwd = Path(hook_cwd) if hook_cwd and Path(hook_cwd).is_dir() else self.repo
        stdin = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run(["bash", str(script)], cwd=cwd, input=stdin, env=self.env, capture_output=True,
                              text=True, timeout=60)

    @staticmethod
    def edit(cwd: Path | str, path: Path | str, tool: str = "Edit") -> dict:
        key = "notebook_path" if tool == "NotebookEdit" else "file_path"
        tool_input: dict = {key: str(path)}
        if tool == "Edit":
            tool_input.update(old_string="demo", new_string="demo 2")
        elif tool == "Write":
            tool_input["content"] = "x = 1\n"
        else:
            tool_input.update(new_source="x = 1", edit_mode="replace")
        return {"session_id": "s-1", "transcript_path": "/t/s-1.jsonl", "cwd": str(cwd),
                "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}

    def assert_denied(self, result: subprocess.CompletedProcess[str], worktree: Path | None = None) -> str:
        found, reason = decision(result)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(found, "deny", f"no deny JSON: {result.stdout!r} {result.stderr!r}")
        if worktree is not None:
            self.assertIn("EnterWorktree", reason)
            self.assertIn(str(worktree), reason)
        return reason

    def assert_silent(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "", result.stdout)

    def linked(self, repo: Path, branch: str, name: str) -> Path:
        path = self.root / "linked" / name
        git(repo, "worktree", "add", "-q", "-b", branch, str(path))
        put(path / "README.md", "# linked\n")
        return path.resolve()


class WorktreeEntryFlowTests(WallCase):
    def test_f2_worktree_entry(self) -> None:
        """F2, O15, O16: an `Edit` into the feature worktree from the main checkout `cwd` is denied with the
        `EnterWorktree` reason; the same `Edit` with `cwd` the worktree, or a folder in it, passes."""
        target = self.wt / "src" / "app.py"
        reason = self.assert_denied(self.hook(self.edit(self.repo, target)), worktree=self.wt)
        self.assertEqual(reason, f"EnterWorktree {self.wt} first: Stages 3 to 7 run with the session cwd in the "
                                 "feature worktree.")
        for name, cwd in (("worktree", self.wt), ("folder in it", self.wt / "src")):
            with self.subTest(cwd=name):
                self.assert_silent(self.hook(self.edit(cwd, target)))


class DenyTargetTests(WallCase):
    def test_o17_to_o20_deny_each_feature_worktree_target(self) -> None:
        """O17, O18, O19, O20: a second feature worktree as `cwd`; a `Write` into a folder that does not exist yet;
        a `NotebookEdit`; a relative `file_path` that resolves into the worktree: each is denied."""
        other = self.worktree("second")
        with self.subTest(oracle=17):
            self.assert_denied(self.hook(self.edit(other, self.wt / "src" / "app.py")), worktree=self.wt)
        with self.subTest(oracle=18):
            new = self.wt / "pkg" / "deep" / "new_module.py"
            self.assertFalse(new.parent.exists())
            self.assert_denied(self.hook(self.edit(self.repo, new, tool="Write")), worktree=self.wt)
        with self.subTest(oracle=19):
            self.assert_denied(self.hook(self.edit(self.repo, self.wt / "notes.ipynb", tool="NotebookEdit")),
                               worktree=self.wt)
        with self.subTest(oracle=20):
            relative = os.path.relpath(self.wt / "src" / "app.py", self.repo)
            self.assertTrue(relative.startswith(".."), relative)
            self.assert_denied(self.hook(self.edit(self.repo, relative)), worktree=self.wt)

    def test_o21_no_deny_outside_a_feature_worktree(self) -> None:
        """O21: with `cwd` the main checkout, no deny for a file of the main checkout, a file outside any git repo,
        a linked worktree on a branch without `/`, a linked worktree on `feat/x` of a repo without
        `dev-loop.json`, a linked worktree on `release/v1.2.0` of a repo with `dev-loop.json`."""
        outside = self.root / "loose" / "scratch.txt"
        put(outside, "loose\n")
        plain = self.linked(self.repo, "scratch", "plain-branch")
        bare = self.root / "other-repo"
        bare.mkdir()
        git(bare, "init", "-q")
        put(bare / "README.md", "# Other\n")
        git(bare, "add", "-A")
        git(bare, "commit", "-q", "-m", "base")
        no_kit = self.linked(bare, "feat/x", "no-kit")
        release = self.linked(self.repo, "release/v1.2.0", "release-wt")
        self.assertTrue((release / "dev-loop.json").is_file())
        put(self.repo / "src" / "main.py", "print('main')\n")
        for name, target in (("main checkout", self.repo / "src" / "main.py"), ("outside git", outside),
                             ("branch without /", plain / "README.md"), ("no dev-loop.json", no_kit / "README.md"),
                             ("release/ branch", release / "README.md")):
            with self.subTest(target=name):
                self.assert_silent(self.hook(self.edit(self.repo, target)))


class AbuseTests(WallCase):
    def test_o22_symlink_and_dotdot_into_the_worktree(self) -> None:
        """O22: a path in the main checkout through a symlink into the feature worktree, or a path with `..` that
        lands in it: a deny."""
        link = self.repo / "shortcut"
        link.symlink_to(self.wt / "src", target_is_directory=True)
        with self.subTest(case="symlink"):
            self.assert_denied(self.hook(self.edit(self.repo, link / "app.py")), worktree=self.wt)
        with self.subTest(case="dotdot"):
            dotdot = f"{self.repo}/.dev-loop/../../worktrees/{self.wt.name}/src/app.py"
            self.assert_denied(self.hook(self.edit(self.repo, dotdot)), worktree=self.wt)

    def test_o23_shell_syntax_in_the_worktree_path(self) -> None:
        """O23: a feature worktree path with a space, `$(touch <marker>)`, a backtick and a quote: a deny, and the
        marker file does not exist after the run."""
        name = f"odd dir $(touch {MARKER}) `touch {MARKER}2` it's \"q\""
        path = self.root / "worktrees" / name
        git(self.repo, "worktree", "add", "-q", "-b", "feat/odd", str(path), "origin/main")
        path = path.resolve()
        put(path / "src" / "app.py", "print('odd')\n")
        self.assert_denied(self.hook(self.edit(self.repo, path / "src" / "app.py")), worktree=path)
        made = [str(p) for p in self.root.rglob(f"{MARKER}*")]
        made += [str(p) for p in Path.cwd().glob(f"{MARKER}*")]
        self.assertEqual(made, [], "the hook ran the shell syntax in the path")

    def test_o24_bad_input_fails_open(self) -> None:
        """O24: input that is not JSON, input with no `cwd`, input with no target path: no output, exit 0."""
        no_cwd = self.edit(self.repo, self.wt / "src" / "app.py")
        del no_cwd["cwd"]
        no_target = self.edit(self.repo, self.wt / "src" / "app.py")
        no_target["tool_input"] = {"old_string": "demo", "new_string": "demo 2"}
        for name, payload in (("not JSON", "{not json at all"), ("no cwd", no_cwd), ("no target", no_target)):
            with self.subTest(case=name):
                self.assert_silent(self.hook(payload, cwd=self.repo))


class DenyAskReasonTests(WallCase):
    def test_o34_deny_ask_reason_names_delivery(self) -> None:
        """O34: the `deny-ask.sh` reason starts with "Delivery autonomy grant:" and names `devloop.py decide`."""
        payload = {"session_id": "s-1", "cwd": str(self.wt), "hook_event_name": "PreToolUse",
                   "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "JSON or YAML?"}]}}
        result = self.hook(payload, script=CLAUDE_HOOKS / "deny-ask.sh", cwd=self.wt)
        reason = self.assert_denied(result)
        self.assertTrue(reason.startswith("Delivery autonomy grant:"), reason)
        self.assertIn("devloop.py decide", reason)


if __name__ == "__main__":
    unittest.main()
