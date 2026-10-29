"""`devloop.py merge-check` binding: its own worktree, one slug branch."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from test_devloop import GIT, frontmatter, git, put, run_cli
from test_merge_check import MergeCheckCase


class MergeBindingTests(MergeCheckCase):
    """`merge-check` checks the worktree it runs in, or the one branch `--feature` names, and reports it."""

    def tree(self, name: str, branch: str, path: str, reviewed: bool) -> tuple[Path, str]:
        wt = self.root / name
        git(self.repo, "worktree", "add", "-q", "-b", branch, str(wt), "origin/main")
        self.commit(wt, path, f"{name}\n")
        sha = self.head(wt)
        if reviewed:
            put(Path(git(wt, "rev-parse", "--absolute-git-dir").strip()) / "dev-loop" / "review-done", sha + "\n")
        return wt.resolve(), sha

    def fresh(self) -> None:
        self.tearDown()
        self.setUp()

    def test_no_feature_checks_this_worktree(self) -> None:
        wt, sha = self.tree("b-feat", "feat/demo", "src/app.py", True)
        out = self.ok(wt)
        self.assertEqual((out["merge"], out["head"], out["branch"], out["feature"]), ("auto", sha, "feat/demo", "demo"))
        self.fresh()
        self.tree("a-spike", "spike/demo", "src/spike.py", True)
        wt, sha = self.tree("b-feat", "feat/demo", "src/app.py", False)
        out = self.ok(wt)
        self.assertEqual((out["merge"], out["branch"], out["head"]), ("human", "feat/demo", sha))
        self.assertIn({"reason": "no-review"}, out["reasons"])
        git(wt, "switch", "-q", "--detach")
        code, out = self.merge_check(cwd=wt)
        self.assertEqual((code, "error" in out, "merge" in out), (1, True, False))

    def test_feature_lookup_needs_one_branch(self) -> None:
        for row in ("worktrees", "branches"):
            with self.subTest(row):
                self.fresh()
                if row == "worktrees":
                    self.tree("a-spike", "spike/demo", "src/spike.py", True)
                    self.tree("b-feat", "feat/demo", "src/app.py", False)
                    names = ("feat/demo", "spike/demo")
                else:
                    git(self.repo, "branch", "feat/Demo", "main")
                    git(self.repo, "branch", "fix/demo", "main")
                    names = ("feat/Demo", "fix/demo")
                code, out = self.merge_check("--feature", "demo", cwd=self.repo)
                self.assertEqual((code, "merge" in out), (1, False), out)
                for name in names:
                    self.assertIn(name, out["error"])

    def test_feature_lookup_finds_the_head(self) -> None:
        for row in ("worktree", "no worktree", "mid-rebase"):
            with self.subTest(row):
                self.fresh()
                wt, sha = self.tree("b-feat", "feat/demo", "src/app.py", True)
                if row == "no worktree":
                    git(self.repo, "worktree", "remove", "--force", str(wt))
                    sha = git(self.repo, "rev-parse", "refs/heads/feat/demo").strip()
                elif row == "mid-rebase":
                    self.commit(self.repo, "src/app.py", "main\n")
                    git(self.repo, "push", "-q", "origin", "main")
                    git(self.repo, "fetch", "-q", "origin")
                    subprocess.run([*GIT, "rebase", "origin/main"], cwd=wt, capture_output=True)
                    sha = self.head(wt)
                out = self.ok(self.repo, "--feature", "demo")
                self.assertEqual((out["branch"], out["head"]), ("feat/demo", sha))

    def no_env(self) -> None:
        self.env = {k: v for k, v in self.env.items() if k != "DEV_LOOP_NOTES"}

    def test_notes_come_from_the_main_worktree(self) -> None:
        for row in ("worktree local file", "branch dev-loop.json"):
            with self.subTest(row):
                self.fresh()
                self.no_env()
                fake = self.root / "fake"
                put(self.notes / "specs" / "20261001 Demo.md", frontmatter(feature="demo", track="L"))
                put(fake / "specs" / "20261001 Demo.md", frontmatter(feature="demo", track="M"))
                if row == "worktree local file":
                    put(self.repo / "dev-loop.local.json", json.dumps({"notes": str(self.notes)}))
                    wt, _ = self.tree("b-feat", "feat/demo", "src/app.py", True)
                    put(wt / "dev-loop.local.json", json.dumps({"notes": str(fake)}))
                else:
                    self.main_config({"version": "2.0.0", "notes": str(self.notes)})
                    wt, _ = self.tree("b-feat", "feat/demo", "dev-loop.json", True)
                    self.commit(wt, "dev-loop.json", json.dumps({"version": "2.0.0", "notes": str(fake)}))
                out = self.ok(wt)
                self.assertEqual(out["track"], "L")
                self.assertIn({"reason": "track-l"}, out["reasons"])

    def test_tracked_local_file_stops_merge_check_and_status(self) -> None:
        notes_env = dict(self.env)
        self.no_env()
        wt, _ = self.tree("b-feat", "feat/demo", "src/app.py", True)
        put(self.repo / "dev-loop.local.json", json.dumps({"notes": str(self.notes)}))
        git(self.repo, "add", "-f", "dev-loop.local.json")
        git(self.repo, "commit", "-q", "-m", "local")
        for command in ("merge-check", "status"):
            code, out = run_cli(command, cwd=wt, env=self.env)
            self.assertEqual((code, "error" in out), (1, True), out)
        code, out = run_cli("merge-check", cwd=wt, env=notes_env)
        self.assertEqual(code, 0, out)
