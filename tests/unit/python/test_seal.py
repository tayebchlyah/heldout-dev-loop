#!/usr/bin/env python3
"""Tests for devloop.py seal and seal --rebase-continue (the seal package)."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
from test_devloop import git, put, run_cli  # noqa: E402  (also sets the core.fsmonitor override)
from seal import ACCEPTANCE_DIR  # noqa: E402
from seal.value import commit_entries, seal_value  # noqa: E402

SUITE = f"{ACCEPTANCE_DIR}/x-y/test_flow.py"


def lines_value(lines: list[str]) -> str:
    return hashlib.sha256("".join(f"{line}\n" for line in sorted(lines)).encode()).hexdigest()


class SealCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name).resolve() / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@example.invalid")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "commit.gpgsign", "false")
        put(self.repo / "README.md", "readme\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def stage(self, path: str, text: str) -> None:
        put(self.repo / path, text)
        git(self.repo, "add", "--", path)

    def seal(self, *args: str) -> tuple[int, dict]:
        return run_cli("seal", *args, cwd=self.repo)

    def head(self) -> str:
        return git(self.repo, "rev-parse", "HEAD").strip()

    def body(self) -> str:
        return git(self.repo, "log", "-1", "--format=%B")

    def test_value_is_sha256_of_sorted_path_blob_lines(self) -> None:
        self.assertEqual(seal_value([("b", "2"), ("a", "1")]), hashlib.sha256(b"a 1\nb 2\n").hexdigest())

    def test_seal_commits_with_trailers(self) -> None:
        self.stage(SUITE, "def test_flow():\n    pass\n")
        blob = git(self.repo, "rev-parse", f":{SUITE}").strip()
        code, out = self.seal("--feature", "x-y", "--message", "Acceptance tests for x-y")
        self.assertEqual(code, 0, out)
        value = lines_value([f"{SUITE} {blob}"])
        self.assertEqual(out["commit"], self.head())
        self.assertEqual(out["seal"], value)
        self.assertEqual(out["files"], [SUITE])
        self.assertTrue(self.body().startswith("Acceptance tests for x-y\n\n"))
        self.assertIn("Acceptance: x-y\n", self.body())
        self.assertIn(f"Dev-Loop-Seal: {value}\n", self.body())

    def test_seal_refuses(self) -> None:
        cases = [
            ("a path outside", [(SUITE, "a\n"), ("README.md", "changed\n")], "x-y"),
            ("nothing staged", [], "x-y"),
            ("only a path outside", [("README.md", "changed\n")], "x-y"),
            ("a bad slug", [(SUITE, "a\n")], "Bad_Slug"),
        ]
        for name, staged, slug in cases:
            with self.subTest(name):
                before = self.head()
                for path, text in staged:
                    self.stage(path, text)
                code, out = self.seal("--feature", slug, "--message", "m")
                self.assertEqual(code, 1, out)
                self.assertTrue(out.get("error"), out)
                self.assertEqual(self.head(), before)
                git(self.repo, "reset", "-q", "--hard")
                git(self.repo, "clean", "-qfd")

    def test_reverts_writes_one_line_per_sha(self) -> None:
        first = self.head()
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "second")
        second = self.head()
        self.stage(SUITE, "a\n")
        code, out = self.seal("--feature", "x-y", "--message", "m", "--reverts", first, "--reverts", second)
        self.assertEqual(code, 0, out)
        self.assertIn(f"\n\nThis reverts commit {first}.\nThis reverts commit {second}.\nAcceptance: x-y\n", self.body())

    def test_deleted_file_entry_is_deleted(self) -> None:
        self.stage(SUITE, "a\n")
        self.assertEqual(self.seal("--feature", "x-y", "--message", "add")[0], 0)
        git(self.repo, "rm", "-q", "--", SUITE)
        code, out = self.seal("--feature", "x-y", "--message", "remove")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["seal"], lines_value([f"{SUITE} deleted"]))

    def test_path_with_space_and_unicode(self) -> None:
        path = f"{ACCEPTANCE_DIR}/x-y/café flow.py"
        self.stage(path, "a\n")
        blob = git(self.repo, "rev-parse", f":{path}").strip()
        code, out = self.seal("--feature", "x-y", "--message", "m")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["files"], [path])
        self.assertEqual(out["seal"], lines_value([f"{path} {blob}"]))

    def test_rebase_continue_rewrites_the_trailer(self) -> None:
        self.stage(SUITE, "base\n")
        git(self.repo, "commit", "-q", "-m", "suite")
        git(self.repo, "checkout", "-q", "-b", "feat/x-y")
        self.stage(SUITE, "feature\n")
        self.assertEqual(self.seal("--feature", "x-y", "--message", "seal")[0], 0)
        git(self.repo, "checkout", "-q", "main")
        self.stage(SUITE, "upstream\n")
        git(self.repo, "commit", "-q", "-m", "upstream")
        git(self.repo, "checkout", "-q", "feat/x-y")
        with self.assertRaises(Exception):
            git(self.repo, "rebase", "main")
        self.stage(SUITE, "resolved\n")
        code, out = self.seal("--rebase-continue")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["rebase"], "continued")
        entries, outside = commit_entries(self.repo, self.head())
        self.assertEqual(outside, [])
        self.assertEqual(out["seal"], seal_value(entries))
        self.assertIn(f"Dev-Loop-Seal: {seal_value(entries)}\n", self.body())
        self.assertEqual(self.body().count("Dev-Loop-Seal:"), 1)
        self.assertIn("Acceptance: x-y\n", self.body())

    def test_rebase_continue_without_rebase(self) -> None:
        code, out = self.seal("--rebase-continue")
        self.assertEqual(code, 1, out)
        self.assertTrue(out.get("error"), out)


if __name__ == "__main__":
    unittest.main()
