#!/usr/bin/env python3
"""Tests for `devloop.py cleanup`: worktree removal, its safety skips, branch delete, main checkout rebase."""

from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_devloop import Sandbox, git, put, run_cli  # noqa: E402


class CleanupTests(Sandbox):
    def cleanup(self, branch: str, *args: str, cwd: Path | None = None, env: dict[str, str] | None = None
                ) -> tuple[int, dict]:
        return run_cli("cleanup", "--branch", branch, *args, cwd=cwd or self.repo, env=env or self.env)

    def worktree(self, branch: str, name: str | None = None) -> Path:
        path = self.root / "wt" / (name or branch.rsplit("/", 1)[-1])
        git(self.repo, "worktree", "add", "-q", "-b", branch, str(path), "main")
        return path

    def merged(self, branch: str, name: str | None = None) -> Path:
        path = self.worktree(branch, name)
        self.commit(path, f"{branch.replace('/', '-')}.txt", "done\n")
        git(path, "push", "-q", "origin", "HEAD:main")
        return path

    def advance_origin(self, path: str = "upstream.txt") -> None:
        scratch = self.root / f"scratch-{path.replace('/', '-')}"
        git(self.root, "clone", "-q", str(self.origin), str(scratch))
        self.commit(scratch, path, "upstream\n")
        git(scratch, "push", "-q", "origin", "HEAD:main")
        shutil.rmtree(scratch)

    def head(self, cwd: Path | None = None) -> str:
        return git(cwd or self.repo, "rev-parse", "HEAD").strip()

    def has_ref(self, ref: str) -> bool:
        return bool(git(self.repo, "for-each-ref", ref).strip())

    def test_merged_feature_is_cleaned_and_a_rerun_is_a_no_op(self) -> None:
        self.commit(self.repo, ".gitignore", "*.log\n")
        git(self.repo, "push", "-q", "origin", "main")
        path = self.merged("feat/widget")
        put(path / "x.log", "ignored\n")
        git(path, "push", "-q", "origin", "HEAD:feat/widget")
        git(self.repo, "fetch", "-q", "origin")
        git(self.origin, "branch", "-D", "feat/widget")

        code, payload = self.cleanup("feat/widget")
        self.assertEqual((code, payload), (0, {
            "target": "feat/widget", "path": str(path), "worktree": "removed", "branch": "deleted",
            "main": "rebased", "reasons": [],
        }))
        self.assertFalse(path.exists())
        self.assertFalse(self.has_ref("refs/heads/feat/widget"))
        self.assertFalse(self.has_ref("refs/remotes/origin/feat/widget"))
        self.assertEqual(self.head(), git(self.repo, "rev-parse", "origin/main").strip())

        code, payload = self.cleanup("feat/widget")
        self.assertEqual((code, payload), (0, {
            "target": "feat/widget", "path": None, "worktree": "absent", "branch": "absent",
            "main": "current", "reasons": [],
        }))

    def test_repo_may_name_any_checkout_folder(self) -> None:
        keep = self.worktree("feat/keep", "keeper")
        self.commit(self.repo, "sub/a.txt", "a\n")
        git(self.repo, "push", "-q", "origin", "main")
        for name, repo in (("one", keep), ("two", self.repo / "sub")):
            with self.subTest(repo=name):
                path = self.merged(f"feat/{name}")
                code, payload = self.cleanup(f"feat/{name}", "--repo", str(repo), cwd=self.root)
                self.assertEqual((code, payload["worktree"], payload["branch"]), (0, "removed", "deleted"), payload)
                self.assertFalse(path.exists())
        self.assertTrue(keep.exists())

    def test_kept_worktree_states(self) -> None:
        def unmerged(path: Path) -> None:
            self.commit(path, "local.txt", "local\n")

        def modified(path: Path) -> None:
            put(path / "GLOSSARY.md", "# Changed\n")

        def untracked(path: Path) -> None:
            put(path / "new.txt", "new\n")

        def staged(path: Path) -> None:
            put(path / "staged.txt", "staged\n")
            git(path, "add", "staged.txt")

        def pause(path: Path) -> None:
            put(Path(git(path, "rev-parse", "--absolute-git-dir").strip()) / "dev-loop" / "pause.json", "{}\n")

        def deleted(path: Path) -> None:
            shutil.rmtree(path)

        def link(path: Path) -> Path:
            target = self.root / "link"
            target.symlink_to(path)
            return target

        rows = (
            ("unmerged", unmerged, None, "worktree: not-merged", "local.txt"),
            ("modified", modified, None, "worktree: dirty", "GLOSSARY.md"),
            ("untracked", untracked, None, "worktree: dirty", "new.txt"),
            ("staged", staged, None, "worktree: dirty", "staged.txt"),
            ("paused", pause, None, "worktree: paused", None),
            ("subfolder", None, lambda path: path / "sub", "worktree: in-use", None),
            ("symlink", None, link, "worktree: in-use", None),
            ("deleted", deleted, None, "worktree: dirty", None),
        )
        for name, setup, cwd, reason, kept in rows:
            with self.subTest(state=name):
                branch = f"feat/{name}"
                path = self.merged(branch) if name != "unmerged" else self.worktree(branch)
                if name == "subfolder":
                    self.commit(path, "sub/a.txt", "a\n")
                    git(path, "push", "-q", "origin", "HEAD:main")
                if setup:
                    setup(path)
                self.advance_origin(f"{name}-upstream.txt")
                code, payload = self.cleanup(branch, cwd=cwd(path) if cwd else None)
                self.assertEqual(code, 1, payload)
                self.assertEqual((payload["worktree"], payload["branch"], payload["main"]),
                                 ("skipped", "skipped", "rebased"), payload)
                self.assertEqual(payload["reasons"], [reason, "branch: worktree-kept"])
                self.assertTrue(self.has_ref(f"refs/heads/{branch}"))
                if name != "deleted":
                    self.assertTrue(path.exists())
                if kept:
                    self.assertTrue((path / kept).exists())

        with self.subTest(state="main-checkout"):
            git(self.repo, "checkout", "-q", "-b", "feat/x")
            code, payload = self.cleanup("feat/x")
            self.assertEqual((code, payload["worktree"], payload["branch"], payload["main"]),
                             (1, "skipped", "skipped", "skipped"), payload)
            self.assertEqual(payload["reasons"],
                             ["worktree: main-checkout", "branch: worktree-kept", "main: not-on-main"])
            self.assertTrue(self.has_ref("refs/heads/feat/x"))

    def test_branch_without_worktree(self) -> None:
        git(self.repo, "branch", "feat/merged")
        code, payload = self.cleanup("feat/merged")
        self.assertEqual((code, payload["worktree"], payload["branch"], payload["reasons"]),
                         (0, "absent", "deleted", []), payload)
        self.assertFalse(self.has_ref("refs/heads/feat/merged"))

        path = self.worktree("feat/open")
        self.commit(path, "open.txt", "open\n")
        git(self.repo, "worktree", "remove", str(path))
        code, payload = self.cleanup("feat/open")
        self.assertEqual((code, payload["worktree"], payload["branch"], payload["reasons"]),
                         (1, "absent", "skipped", ["branch: not-merged"]), payload)
        self.assertTrue(self.has_ref("refs/heads/feat/open"))

        code, payload = self.cleanup("feat/none")
        self.assertEqual((code, payload["worktree"], payload["branch"], payload["reasons"]),
                         (0, "absent", "absent", []), payload)

    def test_main_checkout_states(self) -> None:
        start = self.head()

        def reset() -> None:
            if (self.repo / ".git" / "MERGE_HEAD").exists():
                git(self.repo, "merge", "--abort")
            git(self.repo, "checkout", "-q", "-f", "main")
            git(self.repo, "reset", "-q", "--hard", start)
            git(self.repo, "clean", "-qfd")

        def nothing() -> None:
            pass

        def local_commit() -> None:
            self.commit(self.repo, "local.txt", "local\n")

        def unstaged() -> None:
            put(self.repo / "GLOSSARY.md", "# Changed\n")

        def staged() -> None:
            put(self.repo / "staged.txt", "s\n")
            git(self.repo, "add", "staged.txt")

        def other_branch() -> None:
            git(self.repo, "checkout", "-q", "-b", "side")

        def detached() -> None:
            git(self.repo, "checkout", "-q", "--detach")

        def merging() -> None:
            git(self.repo, "checkout", "-q", "-b", "merge-side")
            self.commit(self.repo, "side.txt", "side\n")
            git(self.repo, "checkout", "-q", "main")
            git(self.repo, "merge", "-q", "--no-commit", "--no-ff", "merge-side")

        def untracked_elsewhere() -> None:
            put(self.repo / "mine.txt", "mine\n")

        rows = (
            ("current", nothing, False, "current", None),
            ("behind", nothing, True, "rebased", None),
            ("untracked", untracked_elsewhere, True, "rebased", None),
            ("local-commits", local_commit, True, "skipped", "main: local-commits"),
            ("unstaged", unstaged, True, "skipped", "main: dirty"),
            ("staged", staged, True, "skipped", "main: dirty"),
            ("other-branch", other_branch, True, "skipped", "main: not-on-main"),
            ("detached", detached, True, "skipped", "main: not-on-main"),
            ("merging", merging, True, "skipped", "main: operation-in-progress"),
        )
        for name, setup, behind, want, reason in rows:
            with self.subTest(state=name):
                reset()
                git(self.origin, "update-ref", "refs/heads/main", start)
                git(self.repo, "fetch", "-q", "origin")
                if behind:
                    self.advance_origin(f"{name}.txt")
                setup()
                before = self.head()
                code, payload = self.cleanup("feat/none")
                self.assertEqual((code, payload["main"], payload["reasons"]),
                                 (1 if reason else 0, want, [reason] if reason else []), payload)
                if want == "rebased":
                    self.assertEqual(self.head(), git(self.repo, "rev-parse", "origin/main").strip())
                else:
                    self.assertEqual(self.head(), before)
                if name == "local-commits":
                    self.assertTrue((self.repo / "local.txt").exists())
                if name == "unstaged":
                    self.assertEqual((self.repo / "GLOSSARY.md").read_text(), "# Changed\n")
                if name == "untracked":
                    self.assertEqual((self.repo / "mine.txt").read_text(), "mine\n")

        with self.subTest(state="rebase-failed"):
            reset()
            self.advance_origin("clash.txt")
            put(self.repo / "clash.txt", "mine\n")
            before = self.head()
            code, payload = self.cleanup("feat/none")
            self.assertEqual((code, payload["main"]), (1, "skipped"), payload)
            self.assertEqual(len(payload["reasons"]), 1, payload)
            self.assertTrue(payload["reasons"][0].startswith("main: rebase-failed: "), payload)
            self.assertEqual(self.head(), before)
            self.assertFalse((self.repo / ".git" / "rebase-merge").exists())
            self.assertFalse((self.repo / ".git" / "rebase-apply").exists())
            self.assertEqual((self.repo / "clash.txt").read_text(), "mine\n")

    def test_fetch_failure_changes_nothing(self) -> None:
        path = self.merged("feat/widget")
        before = self.head()
        self.origin.rename(self.root / "gone.git")
        code, payload = self.cleanup("feat/widget")
        self.assertEqual(code, 1, payload)
        self.assertEqual((payload["worktree"], payload["branch"], payload["main"]),
                         ("skipped", "skipped", "skipped"), payload)
        self.assertEqual(len(payload["reasons"]), 1, payload)
        self.assertTrue(payload["reasons"][0].startswith("fetch: "), payload)
        self.assertTrue(path.exists())
        self.assertTrue(self.has_ref("refs/heads/feat/widget"))
        self.assertEqual(self.head(), before)

    def test_usage_errors(self) -> None:
        path = self.merged("feat/widget")
        before = self.head()
        outside = self.root / "outside"
        outside.mkdir()
        for args in ((), ("--branch", "main"), ("--branch", "master"),
                     ("--branch", "feat/widget", "--repo", str(outside))):
            with self.subTest(args=args):
                code, payload = run_cli("cleanup", *args, cwd=self.repo, env=self.env)
                self.assertEqual(code, 2, payload)
                self.assertIn("error", payload)
        self.assertTrue(path.exists())
        self.assertTrue(self.has_ref("refs/heads/feat/widget"))
        self.assertEqual(self.head(), before)

    def test_never_pushes_stashes_resets_or_forces(self) -> None:
        real = shutil.which("git")
        bin_dir = self.root / "bin"
        log = self.root / "git.log"
        gh_log = self.root / "gh.log"
        put(bin_dir / "git", f"#!{sys.executable}\nimport json, os, sys\n"
            f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
            f"os.execv({real!r}, [{real!r}, *sys.argv[1:]])\n")
        put(bin_dir / "gh", f"#!/bin/sh\necho \"$@\" >> {gh_log}\nexit 1\n")
        for tool in ("git", "gh"):
            (bin_dir / tool).chmod(0o755)
        env = {**self.env, "PATH": f"{bin_dir}{os.pathsep}{self.env['PATH']}"}

        self.merged("feat/widget")
        self.advance_origin("one.txt")
        self.assertEqual(self.cleanup("feat/widget", env=env)[0], 0)
        self.advance_origin("two.txt")
        put(self.repo / "GLOSSARY.md", "# Changed\n")
        self.assertEqual(self.cleanup("feat/none", env=env)[1]["reasons"], ["main: dirty"])
        git(self.repo, "checkout", "-q", "GLOSSARY.md")
        self.advance_origin("clash.txt")
        put(self.repo / "clash.txt", "mine\n")
        self.assertEqual(self.cleanup("feat/none", env=env)[0], 1)

        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertTrue(calls)
        for argv in calls:
            self.assertFalse({"push", "stash", "reset", "--autostash"} & set(argv), argv)
            if "worktree" in argv and "remove" in argv:
                self.assertFalse({"--force", "-f"} & set(argv), argv)
        self.assertFalse(gh_log.exists())


if __name__ == "__main__":
    unittest.main()
