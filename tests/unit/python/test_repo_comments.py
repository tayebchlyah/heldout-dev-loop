#!/usr/bin/env python3
"""Tests for scripts/repo_rules.py comments: trailing .sh comments, private docstrings."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_repo_rules import RulesRepo, repo_rules, run_rules  # noqa: E402
from test_devloop import put  # noqa: E402


class CommentsRepo(RulesRepo):
    def assert_violations(self, expected: list[tuple[str, int, str]]) -> None:
        code, out = run_rules(self.repo, "comments")
        self.assertEqual([(v["path"], v["line"], v["text"]) for v in out["violations"]], expected, out)
        self.assertEqual(code, 1 if expected else 0)
        self.assertEqual(out["ok"], not expected)


class ShellTrailingTests(unittest.TestCase):
    def test_shell_trailing_table(self) -> None:
        for text, expected in [
            ('if [ -n "$x" ]; then # no harness\n', {1: "# no harness"}),
            ("echo x\t# tab\n", {1: "# tab"}),
            ('echo "a # b"\n', {}), ("echo 'a # b'\n", {}), ("n=$#\n", {}), ("n=${#list[@]}\n", {}), ("x=a#b\n", {}),
            ("  # full line\n", {}), ("#!/bin/sh\n", {}),
            ('echo "a \\" # b"\n', {}),
            ("# don't\necho x # it's\necho y # c\n", {2: "# it's", 3: "# c"}),
            ("cat <<EOF\nx # y\nEOF\necho z # after\n", {4: "# after"}),
            ("cat <<-'EOF'\n\tx # y\n\tEOF\necho # d\n", {4: "# d"}),
            ('cat <<"END" # c\nx # y\nEND\n', {1: "# c"}),
            ("echo 'open\nstill # in\nclose' # c\necho # d\n", {4: "# d"}),
            ("x=$((a<<2)) # shift\n", {1: "# shift"}),
            ("cat <<< word # c\necho # d\n", {1: "# c", 2: "# d"}),
        ]:
            with self.subTest(text=text):
                self.assertEqual(repo_rules.shell_trailing(text), expected)


class ShellTrailingCommandTests(CommentsRepo):
    def base_files(self) -> None:
        put(self.repo / "s.sh", "#!/bin/sh\nset -u\nrun # kept words\n")

    def test_new_trailing_comment(self) -> None:
        self.commit("✨(x) Add", "s.sh", '#!/bin/sh\nset -u\nrun # kept words\nif [ -n "$x" ]; then # no harness\nfi\n')
        self.assert_violations([("s.sh", 4, "# no harness")])

    def test_allowed_shell_lines(self) -> None:
        lines = ['echo "a # b"', "echo 'a # b'", "n=$#", "n=${#list[@]}", "x=a#b", "cmd # see https://example.com",
                 "cmd # shellcheck disable=SC2086", "cat <<EOF", "x # y", "EOF", "other # kept words"]
        self.commit("✨(x) Add", "t.sh", "#!/bin/sh\n" + "\n".join(lines) + "\n")
        self.assert_violations([])

    def test_skipped_prefixes(self) -> None:
        for prefix in repo_rules.SKIPPED_PREFIXES:
            put(self.repo / prefix / "s.sh", "run # no harness\n")
        self.assert_violations([])


if __name__ == "__main__":
    unittest.main()
