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


BASE_STATE = '"""State."""\n\n\ndef keep():\n    """Kept doc."""\n    return 1\n'
LOAD = '\n\ndef load():\n    """Load the state."""\n    return 2\n'
LOAD_ROW = ("pkg/state.py", 10, "Load the state.")
STORE = ('class Store:\n    def get(self):\n        """Get it."""\n\n    def _get(self):\n        """Get it raw."""\n\n'
         '    def __init__(self):\n        """Make it."""\n')


class DocstringCommandTests(CommentsRepo):
    def base_files(self) -> None:
        put(self.repo / "pkg/state.py", BASE_STATE)
        put(self.repo / "seal/__init__.py", '"""Seal."""\n')

    def test_private_function_docstring(self) -> None:
        self.commit("✨(x) Add", "pkg/state.py", BASE_STATE + LOAD)
        self.assert_violations([LOAD_ROW])

    def test_importer_forms(self) -> None:
        put(self.repo / "pkg/state.py", BASE_STATE + LOAD)
        for path, text, expected in [
            ("bin/run.py", "from state import load\n", []),
            ("bin/run.py", "import state\nstate.load()\n", []),
            ("bin/run.py", "import pkg.state as st\nst.load()\n", []),
            ("bin/run.py", "from other import load\n", [LOAD_ROW]),
            ("bin/run.py", "from state import load\ndef f(:\n", [LOAD_ROW]),
            ("tests/unit/test_state.py", "from state import load\n", [LOAD_ROW]),
        ]:
            with self.subTest(path=path, text=text):
                put(self.repo / path, text)
                self.assert_violations(expected)
                (self.repo / path).unlink()

    def test_self_import_is_not_public(self) -> None:
        put(self.repo / "pkg/state.py", BASE_STATE + LOAD + "\n\nfrom state import load\n")
        self.assert_violations([LOAD_ROW])

    def test_module_and_test_docstrings(self) -> None:
        put(self.repo / "pkg/new.py", '"""New module."""\n')
        put(self.repo / "tests/unit/test_x.py", 'def helper():\n    """Help the test."""\n')
        self.assert_violations([("tests/unit/test_x.py", 2, "Help the test.")])

    def test_class_methods(self) -> None:
        put(self.repo / "pkg/store.py", STORE)
        self.assert_violations([("pkg/store.py", 3, "Get it."), ("pkg/store.py", 6, "Get it raw."),
                                ("pkg/store.py", 9, "Make it.")])
        put(self.repo / "bin/run.py", "from store import Store\n")
        self.assert_violations([("pkg/store.py", 6, "Get it raw.")])

    def test_nested_function(self) -> None:
        put(self.repo / "pkg/state.py", BASE_STATE + '\n\ndef outer():\n    def load():\n        """Load the state."""\n')
        put(self.repo / "bin/run.py", "from state import load\n")
        self.assert_violations([("pkg/state.py", 11, "Load the state.")])

    def test_escapes(self) -> None:
        put(self.repo / "pkg/esc.py", 'def a():\n    """See https://example.com."""\n\n\ndef b():\n    """Fixes #215."""\n\n\n'
                                      'def c():\n    """allow-comment: dependency, git drops it."""\n')
        self.assert_violations([])

    def test_moved_docstring_is_not_new(self) -> None:
        moved = BASE_STATE.replace("State.", "Other.")
        put(self.repo / "pkg/other.py", moved)
        put(self.repo / "pkg/state.py", '"""State."""\n')
        self.assert_violations([])
        put(self.repo / "pkg/other.py", moved.replace("Kept doc.", "Kept doc, changed."))
        self.assert_violations([("pkg/other.py", 5, "Kept doc, changed.")])

    def test_unchanged_docstring_in_a_changed_file(self) -> None:
        self.commit("✨(x) Add", "pkg/state.py", BASE_STATE.replace("return 1", "return 3"))
        self.assert_violations([])

    def test_unparsable_file_keeps_the_line_rule(self) -> None:
        put(self.repo / "bad.py", "def f(:\n    # plain words\n")
        self.assert_violations([("bad.py", 2, "# plain words")])

    def test_multi_line_docstring(self) -> None:
        put(self.repo / "pkg/multi.py", '"""M."""\n\n\ndef f():\n    """\n    First words.\n\n    More.\n    """\n')
        self.assert_violations([("pkg/multi.py", 5, "First words.")])

    def test_skipped_prefixes(self) -> None:
        for prefix in repo_rules.SKIPPED_PREFIXES:
            put(self.repo / (prefix + "p.py"), 'def f():\n    """Plain words."""\n')
        self.assert_violations([])

    def test_init_stem(self) -> None:
        put(self.repo / "seal/__init__.py", '"""Seal."""\n\n\ndef merge_base():\n    """Find the base."""\n')
        self.assert_violations([("seal/__init__.py", 5, "Find the base.")])
        put(self.repo / "bin/run.py", "from seal import merge_base\n")
        self.assert_violations([])


if __name__ == "__main__":
    unittest.main()
