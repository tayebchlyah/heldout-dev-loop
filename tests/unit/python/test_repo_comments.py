#!/usr/bin/env python3
"""Tests for scripts/repo_rules.py comments: trailing .sh comments, private docstrings."""

from __future__ import annotations

import sys
import unittest
import warnings
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


SOURCE = ('"""Mod."""\ndef load():\n    """Load it."""\ndef _h():\n    """\n    Helper.\n\n    More.\n    """\n'
          'def outer():\n    def inner():\n        """Inner."""\nclass Store:\n    """Store."""\n'
          '    def get(self):\n        """Get."""\n    def _get(self):\n        """Private get."""\n'
          '    def __init__(self):\n        """Init."""\n    def __secret(self):\n        """Secret."""\n'
          '    class Inner:\n        """Nested."""\n        def get(self):\n            """Nested get."""\n'
          'try:\n    def fetch():\n        """Fetch."""\nexcept ImportError:\n    pass\n'
          'async def poll():\n    """Poll."""\n')


class DocstringRuleTests(unittest.TestCase):
    def test_private_docstrings(self) -> None:
        tree = repo_rules.parse_python(SOURCE)
        self.assertEqual(repo_rules.private_docstrings(tree, {"load", "Store", "inner", "fetch", "poll"}), [
            (5, 9, "Helper.\n\nMore."), (12, 12, "Inner."), (18, 18, "Private get."), (22, 22, "Secret."),
            (24, 24, "Nested."), (26, 26, "Nested get.")])
        self.assertEqual(repo_rules.private_docstrings(tree, set()), [
            (3, 3, "Load it."), (5, 9, "Helper.\n\nMore."), (12, 12, "Inner."), (14, 14, "Store."), (16, 16, "Get."),
            (18, 18, "Private get."), (20, 20, "Init."), (22, 22, "Secret."), (24, 24, "Nested."),
            (26, 26, "Nested get."), (29, 29, "Fetch."), (33, 33, "Poll.")])

    def test_docstring_texts(self) -> None:
        self.assertEqual(repo_rules.docstring_texts(SOURCE), {
            "Mod.", "Load it.", "Helper.\n\nMore.", "Inner.", "Store.", "Get.", "Private get.", "Init.", "Secret.",
            "Nested.", "Nested get.", "Fetch.", "Poll."})
        self.assertEqual(repo_rules.docstring_texts("def f(:\n"), set())

    def test_parse_python(self) -> None:
        self.assertIsNone(repo_rules.parse_python("def f(:\n"))
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            self.assertIsNotNone(repo_rules.parse_python('x = "\\d"\n'))
        self.assertEqual(seen, [])

    def test_module_stem(self) -> None:
        for path, stem in [("pkg/state.py", "state"), ("state.py", "state"), ("skill/scripts/seal/__init__.py", "seal")]:
            with self.subTest(path=path):
                self.assertEqual(repo_rules.module_stem(path), stem)

    def test_import_pairs_table(self) -> None:
        for source, expected in [
            ("from state import load, save as s\n", {("state", "load"), ("state", "save")}),
            ("from pkg.state import load\n", {("state", "load")}),
            ("from .state import load\n", {("state", "load")}),
            ("import state\nstate.load()\n", {("state", "load")}),
            ("import pkg.state as st\nst.load()\n", {("state", "load")}),
            ("import pkg.state\npkg.state.load()\n", {("state", "load")}),
            ("from pkg import state\nstate.load()\n", {("pkg", "state"), ("state", "load")}),
            ("from . import state as s\n\n\ndef f():\n    return s.load\n", {("state", "load")}),
            ("import state\nother.load()\n", set()),
            ("x.load()\n", set()),
        ]:
            with self.subTest(source=source):
                self.assertEqual(repo_rules.import_pairs(repo_rules.parse_python(source)), expected)


if __name__ == "__main__":
    unittest.main()
