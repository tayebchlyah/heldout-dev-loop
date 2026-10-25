#!/usr/bin/env python3
"""Tests for devloop.py second-review: the locks and the decision order up to the CLI run."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[3] / "skill" / "scripts"
DEVLOOP = SCRIPTS / "devloop.py"
sys.path.insert(0, str(SCRIPTS))
GIT = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "commit.gpgsign=false"]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class SecondReviewDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make(self, project: str = '{"second_review": true}', branch: str = "main", local: str | None = '{"second_review": {"codex": {}}}',
             commit: bool = True) -> Path:
        """A bare origin, a main checkout with the local file, a feature worktree. Returns the worktree."""
        origin, main = self.tmp / "origin.git", self.tmp / "main"
        git(self.tmp, "init", "-q", "--bare", "-b", branch, str(origin))
        git(self.tmp, "clone", "-q", str(origin), str(main))
        git(main, "checkout", "-q", "-b", branch)
        (main / "dev-loop.json").write_text(project)
        git(main, "add", "."), git(main, "commit", "-qm", "base"), git(main, "push", "-q", "origin", branch)
        if local is not None:
            (main / "dev-loop.local.json").write_text(local)
        tree = self.tmp / "feature"
        git(main, "worktree", "add", "-q", "-b", "feat/x", str(tree))
        if commit:
            (tree / "a.txt").write_text("a\n")
            git(tree, "add", "."), git(tree, "commit", "-qm", "change")
        return tree

    def result(self, tree: Path, harness: str = "claude") -> tuple[dict, int]:
        import second_review
        def review(*args: object) -> tuple[dict, int]:
            return {**args[-1], "state": "done"}, 0  # type: ignore[dict-item]

        with mock.patch.object(second_review, "_review", side_effect=review) as patched:
            out, code = second_review.run(tree, harness, 15, {})
        out["reviewed"] = patched.called
        return out, code

    def check(self, tree: Path, state: str, reason: str | None, code: int, harness: str = "claude") -> dict:
        out, got = self.result(tree, harness)
        self.assertEqual((out["state"], out["reason"], got), (state, reason, code), out)
        return out

    def test_base_locks_on_reaches_review(self) -> None:
        out = self.check(self.make(), "done", None, 0)
        self.assertTrue(out["reviewed"])

    def test_project_lock_table(self) -> None:
        for project, state, reason, code in [
            ("{}", "skipped", "project-off", 0), ('{"second_review": false}', "skipped", "project-off", 0),
            ('{"second_review": "yes"}', "failed", "bad-config", 1), ('{"second_review": 1}', "failed", "bad-config", 1),
            ('{"second_review": null}', "failed", "bad-config", 1), ("not json", "failed", "bad-config", 1),
            ('{"second_review": true, "second_review": true}', "failed", "bad-config", 1)]:
            with self.subTest(project=project):
                self.setUp()
                self.check(self.make(project), state, reason, code)

    def test_main_ref_only_and_no_replace(self) -> None:
        tree = self.make('{"second_review": false}')
        (tree / "dev-loop.json").write_text('{"second_review": true}')
        git(tree, "commit", "-qam", "on")
        self.check(tree, "skipped", "project-off", 0)
        git(tree, "tag", "origin/main")
        git(tree, "branch", "origin/main")
        self.check(tree, "skipped", "project-off", 0)
        git(tree, "replace", git(tree, "rev-parse", "refs/remotes/origin/main:dev-loop.json"), git(tree, "rev-parse", "HEAD:dev-loop.json"))
        self.check(tree, "skipped", "project-off", 0)

    def test_local_lock_table(self) -> None:
        for local, state, reason, code in [
            ('{"second_review": false}', "skipped", "user-off", 0),
            ('{"second_review": {"codex": false}}', "skipped", "user-off", 0),
            (None, "skipped", "not-chosen", 0), ('{"second_review": {"claude": {}}}', "skipped", "not-chosen", 0),
            ("{}", "skipped", "not-chosen", 0),
            ('{"second_review": {"codex": {"model": "--oss"}}}', "failed", "bad-config", 1),
            ('{"second_review": {"codex": {"model": "gpt\\n"}}}', "failed", "bad-config", 1),
            ('{"second_review": {"codex": {"effort": "max"}}}', "failed", "bad-config", 1),
            ('{"second_review": {"codex": {"temp": 1}}}', "failed", "bad-config", 1),
            ('{"second_review": {"codex": null}}', "failed", "bad-config", 1),
            ('{"second_review": {"gemini": {}}}', "failed", "bad-config", 1),
            ('{"second_review": true}', "failed", "bad-config", 1), ("not json", "failed", "bad-config", 1),
            ('{"second_review": {"codex": {}}, "second_review": false}', "failed", "bad-config", 1)]:
            with self.subTest(local=local):
                self.setUp()
                self.check(self.make(local=local), state, reason, code)

    def test_claude_accepts_bracket_and_slash_models(self) -> None:
        for model in ("opus[1m]", "vendor/model"):
            with self.subTest(model=model):
                self.setUp()
                local = json.dumps({"second_review": {"claude": {"model": model, "effort": "max"}}})
                out = self.check(self.make(local=local), "done", None, 0, harness="codex")
                self.assertEqual(out["reviewer"], "claude")

    def test_repo_state(self) -> None:
        self.check(self.make(commit=False), "skipped", "nothing-to-review", 0)
        self.setUp()
        tree = self.make()
        (Path(git(tree, "rev-parse", "--path-format=absolute", "--git-dir")) / "rebase-merge").mkdir()
        self.check(tree, "skipped", "rebase", 0)
        self.setUp()
        self.check(self.make(branch="master"), "skipped", "no-main-ref", 0)
        outside = self.tmp / "plain"
        outside.mkdir()
        self.check(outside, "failed", "git", 1)

    def test_payload_fields_fill_as_rules_pass(self) -> None:
        tree = self.make(local='{"second_review": {"codex": {"model": "m1", "effort": "low"}}}')
        out, _ = self.result(tree)
        self.assertEqual(out["head"], git(tree, "rev-parse", "HEAD"))
        self.assertEqual(out["base"], git(tree, "rev-parse", "refs/remotes/origin/main"))
        self.assertEqual((out["reviewer"], out["model"], out["effort"], out["endpoint"]), ("codex", "m1", "low", "default"))

    def test_timeout_must_be_finite_above_zero(self) -> None:
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(value=value):
                done = subprocess.run([sys.executable, str(DEVLOOP), "second-review", "--harness", "claude", "--timeout", value],
                                      capture_output=True, text=True)
                self.assertEqual(done.returncode, 2)


class SecondReviewExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_left_out_table(self) -> None:
        import second_review
        for path, out in [
            ("tests/acceptance/x", True), ("Tests/Acceptance/x", True), ("AGENTS.md", True), ("a/AGENTS.override.md", True),
            ("CLAUDE.md", True), ("sub/Claude.md", True), ("CLAUDE.local.md", True), (".mcp.json", True),
            (".claude/x", True), (".Codex/x", True), ("a/.agents/x", True), (".env", True), ("config/.env.local", True),
            ("src/app.py", False), ("environment.py", False), ("tests/unit/x", False)]:
            with self.subTest(path=path):
                self.assertEqual(second_review.left_out(path), out)

    def branch(self) -> tuple[Path, str, str]:
        """A repo whose branch commit holds every D7 edge case. Returns repo, main sha, branch sha."""
        repo = self.tmp / "repo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        (repo / "src").mkdir()
        (repo / "tests" / "acceptance").mkdir(parents=True)
        (repo / "src" / "app.py").write_text("v1\n")
        (repo / "AGENTS.md").write_text("a1\n")
        (repo / "tests" / "acceptance" / "t.py").write_text("held1\n")
        git(repo, "add", "."), git(repo, "commit", "-qm", "base")
        base = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-qb", "feat/x")
        (repo / "src" / "app.py").write_text("v2\n")
        (repo / "AGENTS.md").write_text("a2\n")
        (repo / "tests" / "acceptance" / "t.py").write_text("held2\n")
        (repo / ".gitattributes").write_text("src/** -diff export-ignore\n")
        (repo / ".env").write_text("SECRET=1\n")
        (repo / ".claude").mkdir()
        (repo / ".claude" / "settings.json").write_text("{}\n")
        (repo / "big.bin").write_bytes(b"\x00" * 1_048_577)
        (repo / "edge.bin").write_bytes(b"\x01\xff" * 524_288)
        (repo / "src" / "café.txt").write_bytes("café\r\n".encode())
        os.symlink("/etc/hosts", repo / "link")
        git(repo, "-c", "core.autocrlf=false", "add", ".")
        def blob(data: bytes) -> str:
            return subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input=data, capture_output=True,
                                  check=True).stdout.decode().strip()

        upper, lower = blob(b"upper\n"), blob(b"lower\n")
        git(repo, "update-index", "--add", "--cacheinfo", f"100644,{upper},DUP.txt")
        git(repo, "update-index", "--add", "--cacheinfo", f"100644,{lower},dup.txt")
        git(repo, "update-index", "--add", "--cacheinfo", f"160000,{base},sub")
        git(repo, "commit", "-qm", "change")
        return repo, base, git(repo, "rev-parse", "HEAD")

    def test_export_tree(self) -> None:
        import second_review
        repo, base, sha = self.branch()
        dest = self.tmp / "out"
        dest.mkdir()
        files, left = second_review.export(repo, base, sha, dest)
        self.assertEqual(sorted(files), [".gitattributes", "DUP.txt", "edge.bin", "src/app.py", "src/café.txt"])
        self.assertEqual(left, 8)
        self.assertEqual(sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()),
                         [".gitattributes", ".second-review.diff", "DUP.txt", "edge.bin", "src/app.py", "src/café.txt"])
        self.assertEqual((dest / "edge.bin").read_bytes(), b"\x01\xff" * 524_288)
        self.assertEqual((dest / "src" / "café.txt").read_bytes(), "café\r\n".encode())
        self.assertEqual((dest / "DUP.txt").read_bytes(), b"upper\n")
        self.assertEqual(second_review.worktrees(repo), [repo.resolve()])

    def test_export_diff_ignores_user_config_and_attributes(self) -> None:
        import second_review
        repo, base, sha = self.branch()
        marker, script, config = self.tmp / "ran", self.tmp / "ext.sh", self.tmp / "gitconfig"
        script.write_text(f"#!/bin/sh\ntouch {marker}\n")
        script.chmod(0o755)
        config.write_text(f"[diff]\n\texternal = {script}\n[color]\n\tui = always\n")
        dest = self.tmp / "out"
        dest.mkdir()
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config)}):
            second_review.export(repo, base, sha, dest)
        diff = (dest / ".second-review.diff").read_bytes().decode(errors="replace")
        self.assertIn("+v2", diff)
        self.assertIn("+a2", diff)
        self.assertNotIn("tests/acceptance", diff)
        self.assertNotIn("held", diff)
        self.assertNotIn("SECRET", diff)
        self.assertNotIn("\x1b[", diff)
        self.assertFalse(marker.exists())

    def test_export_inside_work_tree_raises(self) -> None:
        import second_review
        repo, base, sha = self.branch()
        dest = repo / "out"
        dest.mkdir()
        with self.assertRaises(RuntimeError):
            second_review.export(repo, base, sha, dest)
        self.assertEqual(list(dest.iterdir()), [])

    def test_sweep(self) -> None:
        import second_review
        now = time.time()
        for name, age in [("devloop-second-review-old", 25), ("devloop-second-review-new", 1), ("other-old", 25)]:
            (self.tmp / name).mkdir()
            (self.tmp / name / "f").write_text("x")
            os.utime(self.tmp / name, (now - age * 3600, now - age * 3600))
        second_review.sweep(self.tmp, now)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["devloop-second-review-new", "other-old"])

    def test_lookup(self) -> None:
        import second_review
        tree = self.tmp / "wt"

        def cli(folder: Path, mode: int = 0o755) -> Path:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "codex").write_text("#!/bin/sh\n")
            (folder / "codex").chmod(mode)
            return folder / "codex"

        inside = cli(tree / "bin")
        (self.tmp / "into").symlink_to(tree / "bin")
        (tree / "out").mkdir()
        (tree / "out" / "codex").symlink_to(cli(self.tmp / "elsewhere"))
        cli(self.tmp / "rel" / "bin")
        cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, cwd)
        (self.tmp / "relay").mkdir()
        (self.tmp / "relay" / "codex").symlink_to(inside)
        cli(self.tmp / "plain", 0o644)
        good = cli(self.tmp / "good")
        cli(self.tmp / "later")
        path = os.pathsep.join(["", "rel/bin", str(tree / "bin"), str(tree / "out"), str(self.tmp / "into"),
                                str(self.tmp / "relay"),
                                str(self.tmp / "plain"), str(self.tmp / "good"), str(self.tmp / "later")])
        self.assertEqual(second_review.lookup("codex", path, [tree]), good.resolve())
        self.assertIsNone(second_review.lookup("codex", os.pathsep.join(["", str(tree / "bin")]), [tree]))

    def test_review_no_cli_and_failed_export(self) -> None:
        import second_review
        tree = SecondReviewDecisionTests.make(self)  # type: ignore[arg-type]
        out, code = second_review.run(tree, "claude", 15, {"PATH": "/usr/bin:/bin"})
        self.assertEqual((out["state"], out["reason"], out["record"], code), ("skipped", "no-cli", None, 0))
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "codex").write_text("#!/bin/sh\n")
        (bin_dir / "codex").chmod(0o755)
        inside = tree / "scratch"
        inside.mkdir()
        with mock.patch.object(tempfile, "tempdir", str(inside)):
            out, code = second_review.run(tree, "claude", 15, {"PATH": str(bin_dir)})
        self.assertEqual((out["state"], out["reason"], out["record"], code), ("failed", "export", None, 1))
        self.assertEqual(list(inside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
