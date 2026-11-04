"""`devloop.py merge-check`: the Stage 7 merge decision."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from test_devloop import PLAN, Sandbox, frontmatter, git, load_script, put, run_cli


class MergeCheckCase(Sandbox):
    """A main checkout with `dev-loop.json` on origin/main, and helpers for a feature worktree under review."""

    PLAN_NAME = "20261002 Widget Plan"

    def setUp(self) -> None:
        super().setUp()
        put(self.repo / ".git" / "info" / "exclude", ".superpowers/\ndev-loop.local.json\n")
        self.main_config({"version": "2.0.0"})

    def main_config(self, config: object, raw: str | None = None) -> None:
        """Commit `dev-loop.json` on main and push it to origin."""
        self.commit(self.repo, "dev-loop.json", raw if raw is not None else json.dumps(config), "config")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "origin")

    def feature(self, track: str = "M") -> Path:
        put(self.notes / "specs" / "20261001 Add Widget.md",
            frontmatter(feature="widget", track=track, review_status="approved"))
        put(self.notes / "plans" / f"{self.PLAN_NAME}.md", PLAN)
        wt = self.root / "wt-widget"
        git(self.repo, "worktree", "add", "-q", "-b", "feat/widget", str(wt), "origin/main")
        return wt.resolve()

    def head(self, cwd: Path) -> str:
        return git(cwd, "rev-parse", "HEAD").strip()

    def gate_green(self, wt: Path, *shas: str) -> None:
        put(wt / ".superpowers" / "sdd" / self.PLAN_NAME / "plan-path", f"{self.notes / 'plans' / (self.PLAN_NAME + '.md')}\n")
        put(wt / ".superpowers" / "sdd" / self.PLAN_NAME / "progress.md",
            "Task 1: complete\n" + "".join(f"Feature gate: green at {sha}\n" for sha in shas))

    def reviewed_feature(self, track: str = "M") -> tuple[Path, str]:
        wt = self.feature(track)
        self.commit(wt, "src/app.py", "print('a')\n")
        sha = self.head(wt)
        self.gate_green(wt, sha)
        return wt, sha

    def merge_check(self, *args: str, cwd: Path) -> tuple[int, dict]:
        return run_cli("merge-check", *args, cwd=cwd, env=self.env)

    def ok(self, cwd: Path, *args: str) -> dict:
        code, out = self.merge_check(*args, cwd=cwd)
        self.assertEqual(code, 0, out)
        return out

    def reasons(self, out: dict, name: str) -> list[dict]:
        return [r for r in out["reasons"] if r["reason"] == name]


class MergeCheckTests(MergeCheckCase):
    """`devloop.py merge-check` as a process: config from the main ref, human paths, patch-id review check."""

    def test_clean_track_m_is_auto(self) -> None:
        wt, sha = self.reviewed_feature()
        self.commit(self.repo, "other.txt", "x\n")
        git(self.repo, "push", "-q", "origin", "main")
        git(wt, "fetch", "-q", "origin")
        git(wt, "rebase", "-q", "origin/main")
        self.assertNotEqual(self.head(wt), sha)
        out = self.ok(wt)
        self.assertEqual(out, {"merge": "auto", "reasons": [], "reviewed": sha, "head": self.head(wt),
                               "branch": "feat/widget", "unreviewed_minor": [], "feature": "widget", "track": "M"})

    def test_track_s_reads_the_review_done_marker(self) -> None:
        git(self.repo, "switch", "-q", "-c", "fix/tiny")
        self.commit(self.repo, "src/app.py", "print('a')\n")
        out = self.ok(self.repo)
        self.assertEqual((out["track"], out["reasons"], out["reviewed"]), ("S", [{"reason": "no-review"}], None))
        sha = self.head(self.repo)
        put(self.repo / ".git" / "dev-loop" / "review-done", sha + "\n")
        out = self.ok(self.repo)
        self.assertEqual((out["merge"], out["reasons"], out["reviewed"], out["feature"]), ("auto", [], sha, "tiny"))

    def test_reasons_each_shape(self) -> None:
        cases = {
            "merge-human": ({"merge": "human"}, "M", None, {"reason"}),
            "track-l": ({}, "L", None, {"reason"}),
            "human-path": ({"merge_human_paths": ["src/auth/*"]}, "M", "auth", {"reason", "path", "pattern"}),
            "config-changed": ({"merge_human_paths": ["x/*"]}, "M", "config", {"reason"}),
            "no-review": ({}, "M", "no-ledger", {"reason"}),
            "unreviewed": ({}, "M", "late", {"reason", "commit", "paths"}),
            "config-unreadable": (None, "M", None, {"reason"}),
        }
        for name, (config, track, change, keys) in cases.items():
            with self.subTest(reason=name):
                self.tearDown()
                self.setUp()
                if config is None:
                    self.main_config(None, raw="{")
                else:
                    self.main_config(config)
                wt, sha = self.reviewed_feature(track)
                if change == "auth":
                    self.commit(wt, "src/auth/token.py", "t\n")
                    self.gate_green(wt, self.head(wt))
                elif change == "config":
                    self.commit(wt, "dev-loop.json", json.dumps({"merge_human_paths": []}))
                    self.gate_green(wt, self.head(wt))
                elif change == "no-ledger":
                    (wt / ".superpowers" / "sdd" / self.PLAN_NAME / "progress.md").unlink()
                elif change == "late":
                    self.commit(wt, "src/app.py", "print('b')\n")
                out = self.ok(wt)
                self.assertEqual(out["merge"], "human")
                self.assertEqual([r["reason"] for r in out["reasons"]], [name], out)
                self.assertEqual(set(out["reasons"][0]), keys)
                if name == "human-path":
                    self.assertEqual(out["reasons"][0], {"reason": name, "path": "src/auth/token.py",
                                                         "pattern": "src/auth/*"})
                if name == "unreviewed":
                    self.assertEqual(out["reasons"][0], {"reason": name, "commit": self.head(wt),
                                                         "paths": ["src/app.py"]})
                if name == "no-review":
                    self.assertIsNone(out["reviewed"])

    def test_harness_config_change_waits(self) -> None:
        """A change to a harness config file waits for the user, whatever `merge_human_paths` says."""
        def harness(path: str) -> dict:
            return {"reason": "harness-config", "path": path}
        rows = [
            (".claude/settings.json", {"merge_human_paths": ["x/*"]}, None, [harness(".claude/settings.json")]),
            (".claude/settings.local.json", {}, None, [harness(".claude/settings.local.json")]),
            (".codex/config.toml", {}, None, [harness(".codex/config.toml")]),
            (".codex/hooks.json", {"merge_human_paths": [".codex/*"]}, None,
             [{"reason": "human-path", "path": ".codex/hooks.json", "pattern": ".codex/*"},
              harness(".codex/hooks.json")]),
            (".mcp.json", {}, None, [harness(".mcp.json")]),
            (".Claude/Settings.JSON", {}, None, [harness(".Claude/Settings.JSON")]),
            (".codex", {}, "cfg", [harness(".codex")]),
            (".codex/hooks.json", None, None, [{"reason": "config-unreadable"}, harness(".codex/hooks.json")]),
            (".claude/agents/x.md", {"merge_human_paths": ["x/*"]}, None, [harness(".claude/agents/x.md")]),
            (".codex/rules/x", {}, None, [harness(".codex/rules/x")]),
            (".codex/agents/x.toml", {}, None, [harness(".codex/agents/x.toml")]),
            (".dev-loop/hooks/claude/gate.sh", {}, None, [harness(".dev-loop/hooks/claude/gate.sh")]),
            (".Dev-Loop/MANIFEST.json", {}, None, [harness(".Dev-Loop/MANIFEST.json")]),
            (".agents/skills/dev-loop/scripts/devloop.py", {}, None,
             [harness(".agents/skills/dev-loop/scripts/devloop.py")]),
            (".dev-loop", {}, "cfg", [harness(".dev-loop")]),
            (".agents/skills/dev-loop", {}, "cfg", [harness(".agents/skills/dev-loop")]),
            (".agents/skills/pause/SKILL.md", {}, None, []),
            (".agents/skills/dev-loopx/a", {}, None, []),
            (".mcp.j\u017fon", {}, None, [harness(".mcp.j\u017fon")]),
            (".agents/\u017fkills/dev-loop/x.py", {}, None, [harness(".agents/\u017fkills/dev-loop/x.py")]),
            (".\uff43laude/x", {}, None, []),
            (".claudex/a", {}, None, []),
            ("claude/hooks/gate.sh", {}, None, []),
        ]
        for path, config, link, expected in rows:
            with self.subTest(path=path, config=config):
                self.tearDown()
                self.setUp()
                self.main_config(config, raw=None if config is not None else "{")
                wt, _ = self.reviewed_feature()
                if link is None:
                    put(wt / path, "{}\n")
                else:
                    put(wt / link / "config.toml", "{}\n")
                    (wt / path).parent.mkdir(parents=True, exist_ok=True)
                    (wt / path).symlink_to("../" * path.count("/") + link)
                git(wt, "add", "-A", "-f", path, *([link] if link else []))
                git(wt, "commit", "-q", "-m", path)
                self.gate_green(wt, self.head(wt))
                out = self.ok(wt)
                self.assertEqual(out["reasons"], expected)

    def test_last_ledger_line_wins(self) -> None:
        wt, first = self.reviewed_feature()
        self.commit(wt, "src/more.py", "m\n")
        second = self.head(wt)
        self.gate_green(wt, first, second)
        out = self.ok(wt)
        self.assertEqual((out["reviewed"], out["reasons"]), (second, []))

    def test_reviewed_is_a_full_sha(self) -> None:
        """A short sha resolves to the full sha; a ref name or another token is no review."""
        wt, sha = self.reviewed_feature()
        for token, reviewed in ((sha[:7], sha), ("HEAD", None), ("feat/widget", None), (sha + "0", None)):
            with self.subTest(token=token):
                self.gate_green(wt, token)
                out = self.ok(wt)
                self.assertEqual(out["reviewed"], reviewed)
                self.assertEqual(out["reasons"], [] if reviewed else [{"reason": "no-review"}])

    def test_minor_commit_is_listed_not_blocking(self) -> None:
        wt, _ = self.reviewed_feature()
        put(wt / "tests" / "unit" / "test_x.py", "x\n")
        self.commit(wt, "README.md", "r\n")
        out = self.ok(wt)
        self.assertEqual((out["merge"], out["reasons"]), ("auto", []))
        self.assertEqual(out["unreviewed_minor"], [{"commit": self.head(wt), "paths": ["README.md", "tests/unit/test_x.py"]}])

    def test_paths_after_review(self) -> None:
        code_paths = [".github/workflows/deploy.test.yml", ".github/actions/test/action.yml", ".github/README.md",
                      "docker-compose.test.yml", "app/docs/views.py", "docs/conf.toml"]
        minor_sets = [["docs/guide.md", "docs/img/flow.png", "tests/unit/test_x.py", "src/x.test.ts",
                       "docs/test_views.py", "README.md"], ["tests/fixtures/data.json", "tests/fixtures/settings.yaml"]]
        for paths in [[p] for p in code_paths] + minor_sets:
            with self.subTest(paths=paths):
                self.tearDown()
                self.setUp()
                wt, _ = self.reviewed_feature()
                for path in paths:
                    put(wt / path, "x\n")
                git(wt, "add", "-A")
                git(wt, "commit", "-q", "-m", "after review")
                out = self.ok(wt)
                if paths[0] in code_paths:
                    self.assertEqual(self.reasons(out, "unreviewed"),
                                     [{"reason": "unreviewed", "commit": self.head(wt), "paths": paths}])
                else:
                    self.assertEqual((out["merge"], self.reasons(out, "unreviewed")), ("auto", []))
                    self.assertEqual(out["unreviewed_minor"][0]["commit"], self.head(wt))

    def test_replayed_patch(self) -> None:
        """Each reviewed patch vouches for one landing commit: a later copy of it is unreviewed."""
        for row in ("revert of revert", "re-pick", "reviewed replay", "tests only"):
            with self.subTest(row):
                self.tearDown()
                self.setUp()
                wt = self.feature()
                path = "tests/unit/test_x.py" if row == "tests only" else "src/app.py"
                self.commit(wt, path, "a\n")
                r1 = self.head(wt)
                git(wt, "revert", "--no-edit", r1)
                r2 = self.head(wt)
                self.gate_green(wt, r2)
                git(wt, *(("cherry-pick", r1) if row == "re-pick" else ("revert", "--no-edit", r2)))
                r3 = self.head(wt)
                if row == "reviewed replay":
                    self.gate_green(wt, r3)
                    self.commit(self.repo, "other.txt", "x\n")
                    git(self.repo, "push", "-q", "origin", "main")
                    git(wt, "fetch", "-q", "origin")
                    git(wt, "rebase", "-q", "origin/main")
                out = self.ok(wt)
                if row == "reviewed replay":
                    self.assertEqual((out["merge"], out["reasons"]), ("auto", []))
                elif row == "tests only":
                    self.assertEqual(self.reasons(out, "unreviewed"), [])
                    self.assertEqual([m["commit"] for m in out["unreviewed_minor"]], [r3])
                else:
                    self.assertEqual(out["merge"], "human")
                    self.assertEqual(self.reasons(out, "unreviewed"),
                                     [{"reason": "unreviewed", "commit": r3, "paths": ["src/app.py"]}])
                    self.assertEqual(self.reasons(out, "dropped"), [])

    def test_dropped_reviewed_commit(self) -> None:
        """A reviewed fix that no landing commit carries waits, unless main already has it."""
        for case in ("skipped", "upstream", "clean-rebase"):
            with self.subTest(case=case):
                self.tearDown()
                self.setUp()
                wt, _ = self.reviewed_feature()
                self.commit(wt, "src/fix.py", "f\n")
                fix = self.head(wt)
                self.gate_green(wt, fix)
                if case == "skipped":
                    git(wt, "reset", "-q", "--hard", "HEAD~1")
                else:
                    if case == "upstream":
                        git(self.repo, "cherry-pick", fix)
                    else:
                        self.commit(self.repo, "other.txt", "x\n")
                    git(self.repo, "push", "-q", "origin", "main")
                    git(wt, "fetch", "-q", "origin")
                    git(wt, "rebase", "-q", "origin/main")
                out = self.ok(wt)
                expected = [{"reason": "dropped", "commit": fix}] if case == "skipped" else []
                self.assertEqual(out["reasons"], expected)

    def test_dropped_reviewed_patch_counted_per_occurrence(self) -> None:
        """A patch reviewed twice needs two landing commits: a rebase that keeps only one copy drops the other."""
        wt = self.feature()
        self.commit(wt, "src/app.py", "a\n")
        first = self.head(wt)
        git(wt, "revert", "--no-edit", first)
        second = self.head(wt)
        git(wt, "cherry-pick", first)
        third = self.head(wt)
        self.gate_green(wt, third)
        git(wt, "reset", "-q", "--hard", second)
        out = self.ok(wt)
        self.assertEqual(out["merge"], "human")
        self.assertEqual(self.reasons(out, "dropped"), [{"reason": "dropped", "commit": third}])

    def test_feature_binds_a_branch_with_a_prefix_only(self) -> None:
        """`--feature <slug>` binds a branch whose short name ends with `/<slug>`, never a bare `<slug>` branch."""
        wt = self.feature()
        git(self.repo, "branch", "widget")
        out = self.ok(self.repo, "--feature", "widget")
        self.assertEqual(out["branch"], "feat/widget")
        code, out = self.merge_check("--feature", "feat/widget", cwd=self.repo)
        self.assertEqual(code, 1)
        self.assertIn("error", out)
        git(self.repo, "worktree", "remove", "--force", str(wt))
        git(self.repo, "branch", "-m", "feat/widget", "feat-widget")
        code, out = self.merge_check("--feature", "widget", cwd=self.repo)
        self.assertEqual(code, 1)
        self.assertIn("error", out)

    def test_git_failure_exits_1(self) -> None:
        """A path or commit listing git cannot give fails closed: an error, no `merge` value."""
        for case in ("worktree-gone", "no-merge-base"):
            with self.subTest(case=case):
                self.tearDown()
                self.setUp()
                wt, _ = self.reviewed_feature()
                if case == "worktree-gone":
                    shutil.rmtree(wt)
                    cwd = self.repo
                else:
                    git(wt, "switch", "-q", "--orphan", "feat/x")
                    self.commit(wt, "src/app.py", "print('x')\n")
                    git(wt, "branch", "-q", "-D", "feat/widget")
                    git(wt, "branch", "-q", "-m", "feat/widget")
                    self.gate_green(wt, self.head(wt))
                    cwd = wt
                code, out = self.merge_check("--feature", "widget", cwd=cwd)
                self.assertEqual(code, 1, out)
                self.assertIn("error", out)
                self.assertNotIn("merge", out)

    def test_non_utf8_input_gives_json(self) -> None:
        """Bytes that are not UTF-8 in branch input never crash the command."""
        for case in ("config", "path", "ledger"):
            with self.subTest(case=case):
                self.tearDown()
                self.setUp()
                self.main_config({"merge_human_paths": ["src/auth/*"]})
                wt, sha = self.reviewed_feature()
                if case == "config":
                    (wt / "dev-loop.json").write_bytes(b"\xff{")
                    git(wt, "add", "-A")
                    git(wt, "commit", "-q", "-m", "config")
                    self.gate_green(wt, self.head(wt))
                    expected = [{"reason": "config-changed"}]
                elif case == "path":
                    blob = subprocess.run(["git", "-C", str(wt), "hash-object", "-w", "--stdin"], input="t\n",
                                          capture_output=True, text=True, check=True).stdout.strip()
                    subprocess.run([b"git", b"-C", bytes(wt), b"update-index", b"--add", b"--cacheinfo",
                                    b"100644," + blob.encode() + b",src/auth/\xff.py"], check=True)
                    git(wt, "commit", "-q", "-m", "odd name")
                    self.gate_green(wt, self.head(wt))
                    expected = [{"reason": "human-path", "path": "src/auth/�.py", "pattern": "src/auth/*"}]
                else:
                    ledger = wt / ".superpowers" / "sdd" / self.PLAN_NAME / "progress.md"
                    ledger.write_bytes(b"\xff\n" + ledger.read_bytes())
                    expected = []
                out = self.ok(wt)
                self.assertEqual(out["reasons"], expected)

    def test_submodule_pointer_move_is_unreviewed(self) -> None:
        """`.gitmodules` `ignore = all` must not hide a submodule pointer move from the diffs."""
        self.main_config({"merge_human_paths": ["sub"]})
        put(self.repo / ".gitmodules", '[submodule "sub"]\n\tpath = sub\n\turl = ./sub\n\tignore = all\n')
        git(self.repo, "add", ".gitmodules")
        git(self.repo, "update-index", "--add", "--cacheinfo", f"160000,{self.head(self.repo)},sub")
        git(self.repo, "commit", "-q", "-m", "sub")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "origin")
        wt, _ = self.reviewed_feature()
        git(wt, "update-index", "--cacheinfo", f"160000,{self.head(wt)},sub")
        git(wt, "commit", "-q", "-m", "move sub")
        out = self.ok(wt)
        self.assertEqual(out["reasons"], [{"reason": "human-path", "path": "sub", "pattern": "sub"},
                                          {"reason": "unreviewed", "commit": self.head(wt), "paths": ["sub"]}])

    def test_is_test_or_doc(self) -> None:
        merge_check = load_script("merge_check")
        rows = {
            "tests/a.py": True, "a/test/b.go": True, "docs/x.md": True, "test_a.py": True, "pkg/a_test.py": True,
            "a_test.go": True, "x.test.ts": True, "x.spec.js": True, "README.md": True, "sub/CHANGELOG.md": True,
            "skill/SKILL.md": False, "AGENTS.md": False, "src/app.py": False, "testing/a.py": False, "readme.md": False,
            ".github/workflows/deploy.test.yml": False, ".github/actions/test/action.yml": False,
            ".github/README.md": False, "docker-compose.test.yml": False, "app/docs/views.py": False,
            "docs/conf.toml": False, "docs/x.py": False, "config/App.YML": False,
            "docs/guide.md": True, "docs/img/flow.png": True, "docs/Flow.PNG": True, "src/x.test.ts": True,
            "docs/test_views.py": True, "tests/fixtures/data.json": True, "tests/fixtures/settings.yaml": True,
        }
        for path, expected in rows.items():
            with self.subTest(path=path):
                self.assertEqual(merge_check.is_test_or_doc(path), expected)

    def test_empty_commit_is_not_listed(self) -> None:
        wt, _ = self.reviewed_feature()
        git(wt, "commit", "-q", "--allow-empty", "-m", "empty")
        out = self.ok(wt)
        self.assertEqual((out["merge"], out["reasons"], out["unreviewed_minor"]), ("auto", [], []))

    def test_short_ref_shadow_is_ignored(self) -> None:
        self.main_config({"merge": "human"})
        wt, _ = self.reviewed_feature()
        self.commit(wt, "dev-loop.json", json.dumps({"version": "2.0.0"}), "permissive")
        forged = self.head(wt)
        git(wt, "reset", "-q", "--hard", "HEAD~1")
        git(wt, "tag", "origin/main", forged)
        git(wt, "branch", "origin/main", forged)
        out = self.ok(wt)
        self.assertEqual([r["reason"] for r in out["reasons"]], ["merge-human"])

    def test_falls_back_to_local_main_without_origin(self) -> None:
        wt, _ = self.reviewed_feature()
        git(self.repo, "remote", "remove", "origin")
        self.assertEqual(git(self.repo, "for-each-ref", "refs/remotes"), "")
        self.commit(self.repo, "dev-loop.json", json.dumps({"version": "2.0.0", "merge": "human"}))
        out = self.ok(wt)
        self.assertIn({"reason": "merge-human"}, out["reasons"])
        git(self.repo, "branch", "-q", "-m", "main", "trunk")
        code, out = self.merge_check(cwd=wt)
        self.assertEqual(code, 1)
        self.assertIn("error", out)

    def test_no_feature_on_main_exits_1(self) -> None:
        code, out = self.merge_check(cwd=self.repo)
        self.assertEqual(code, 1)
        self.assertIn("error", out)
        code, out = self.merge_check("--feature", "nope", cwd=self.repo)
        self.assertEqual(code, 1)
        self.assertIn("error", out)

    def test_rename_out_of_protected_path_waits(self) -> None:
        self.commit(self.repo, "src/auth/token.py", "t\n")
        self.main_config({"merge_human_paths": ["src/auth/*"]})
        wt = self.feature()
        git(wt, "mv", "src/auth/token.py", "src/token.py")
        git(wt, "commit", "-q", "-m", "move")
        self.gate_green(wt, self.head(wt))
        out = self.ok(wt)
        self.assertEqual(out["reasons"], [{"reason": "human-path", "path": "src/auth/token.py", "pattern": "src/auth/*"}])

    def test_merge_commit_is_checked_against_first_parent(self) -> None:
        wt, _ = self.reviewed_feature()
        git(wt, "switch", "-q", "-c", "side")
        self.commit(wt, "docs/x.md", "d\n")
        git(wt, "switch", "-q", "feat/widget")
        git(wt, "merge", "-q", "--no-ff", "--no-commit", "side")
        put(wt / "src" / "app.py", "print('evil')\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "merge side")
        merge = self.head(wt)
        out = self.ok(wt)
        self.assertEqual(self.reasons(out, "unreviewed"),
                         [{"reason": "unreviewed", "commit": merge, "paths": ["src/app.py"]}])

    def test_indentation_change_is_unreviewed(self) -> None:
        """A conflict resolution that only re-indents a reviewed line changes the code: whitespace counts."""
        wt = self.feature()
        self.commit(wt, "src/app.py", "if x:\n    a()\nb()\n")
        reviewed = self.head(wt)
        self.gate_green(wt, reviewed)
        git(wt, "reset", "-q", "--hard", "HEAD~1")
        self.commit(wt, "src/app.py", "if x:\n    a()\n    b()\n")
        out = self.ok(wt)
        self.assertEqual(out["reasons"], [{"reason": "unreviewed", "commit": self.head(wt), "paths": ["src/app.py"]},
                                          {"reason": "dropped", "commit": reviewed}])

    def test_root_commit_in_the_landing_range_is_unreviewed(self) -> None:
        """A commit whose diff cannot be computed (no first parent) fails closed, even with only doc paths."""
        wt, _ = self.reviewed_feature()
        git(wt, "switch", "-q", "--orphan", "side")
        self.commit(wt, "docs/x.md", "d\n")
        root = self.head(wt)
        git(wt, "switch", "-q", "feat/widget")
        git(wt, "merge", "-q", "--allow-unrelated-histories", "-m", "merge side", "side")
        out = self.ok(wt)
        self.assertEqual(out["reasons"], [{"reason": "unreviewed", "commit": root, "paths": []}])

    def test_gitattributes_binary_does_not_hide_a_change(self) -> None:
        """A reviewed commit swapped for another one with the same subject: the rendered diff must not hide it."""
        for attributes in ("*.py binary\n", "*.py diff=hide\n"):
            with self.subTest(attributes=attributes):
                self.tearDown()
                self.setUp()
                git(self.repo, "config", "diff.hide.textconv", "sh -c 'echo same'")  # User config, not the branch.
                self.commit(self.repo, ".gitattributes", attributes)
                git(self.repo, "push", "-q", "origin", "main")
                git(self.repo, "fetch", "-q", "origin")
                wt, reviewed = self.reviewed_feature()
                git(wt, "reset", "-q", "--hard", "HEAD~1")
                self.commit(wt, "src/app.py", "print('evil')\n")
                out = self.ok(wt)
                self.assertEqual(out["reviewed"], reviewed)
                self.assertEqual(out["reasons"],
                                 [{"reason": "unreviewed", "commit": self.head(wt), "paths": ["src/app.py"]},
                                  {"reason": "dropped", "commit": reviewed}])

    def test_bad_shape_on_main_is_config_unreadable(self) -> None:
        rows = ['[]', '{"merge": "maybe"}', '{"merge": "human", "merge_human_paths": "src/*"}',
                '{"merge_human_paths": [""]}', '{"merge_human_paths": [1]}', '{"merge_human_paths": [" "]}',
                '{"merge_human_paths": ["src/auth/"]}']
        wt, _ = self.reviewed_feature()
        for raw in rows:
            with self.subTest(raw=raw):
                self.main_config(None, raw=raw)
                out = self.ok(wt)
                self.assertEqual(out["reasons"][0], {"reason": "config-unreadable"})
                self.assertFalse({"merge-human", "human-path", "config-changed"} & {r["reason"] for r in out["reasons"]})

    def test_head_config_unparsable_is_config_changed(self) -> None:
        for main, changed in (({"merge_human_paths": ["x/*"]}, True), ({"version": "2.1.0"}, False)):
            with self.subTest(main=main):
                self.tearDown()
                self.setUp()
                self.main_config(main)
                wt = self.feature()
                self.commit(wt, "dev-loop.json", "{")
                self.gate_green(wt, self.head(wt))
                out = self.ok(wt)
                self.assertEqual(out["reasons"], [{"reason": "config-changed"}] if changed else [])

    def test_file_size_exempt_change(self) -> None:
        rows = [
            ("branch adds a pattern", {"version": "2.1.0"}, {"version": "2.1.0", "file_size_exempt": ["x/*"]}, True),
            ("branch adds an empty list", {"version": "2.1.0"}, {"version": "2.1.0", "file_size_exempt": []}, False),
            ("bad value on both sides", {"file_size_exempt": "x"}, {"file_size_exempt": "x", "version": "9"}, False),
            ("branch drops the file", {"file_size_exempt": ["x/*"]}, None, True),
        ]
        for name, main, branch, changed in rows:
            with self.subTest(name):
                self.tearDown()
                self.setUp()
                self.main_config(main)
                wt = self.feature()
                if branch is None:
                    git(wt, "rm", "-q", "dev-loop.json")
                    git(wt, "commit", "-q", "-m", "drop")
                else:
                    self.commit(wt, "dev-loop.json", json.dumps(branch))
                self.gate_green(wt, self.head(wt))
                out = self.ok(wt)
                self.assertEqual(out["reasons"], [{"reason": "config-changed"}] if changed else [], out)

    def test_commands_change(self) -> None:
        m = {"version": "2.1.0", "commands": {"acceptance": "sh run.sh"}}
        rows = [
            ("acceptance to true", m, {**m, "commands": {"acceptance": "true"}}, True),
            ("commands removed", m, {"version": "2.1.0"}, True),
            ("gate added", m, {**m, "commands": {"acceptance": "sh run.sh", "gate": "true"}}, True),
            ("same commands", m, {**m, "version": "2.2.0"}, False),
            ("branch adds commands", {"version": "2.1.0"}, {"version": "2.1.0", "commands": {"acceptance": "true"}}, True),
            ("branch drops the file", m, None, True),
        ]
        for name, main, branch, changed in rows:
            with self.subTest(name):
                self.tearDown()
                self.setUp()
                self.main_config(main)
                wt = self.feature()
                if branch is None:
                    git(wt, "rm", "-q", "dev-loop.json")
                    git(wt, "commit", "-q", "-m", "drop")
                else:
                    self.commit(wt, "dev-loop.json", json.dumps(branch))
                self.gate_green(wt, self.head(wt))
                out = self.ok(wt)
                self.assertEqual(out["reasons"], [{"reason": "config-changed"}] if changed else [], out)
