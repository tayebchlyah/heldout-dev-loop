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


SINCE = "# `--since` is a devloop.py drive and sessions option, never a Stryker one."
EXCEPTION = "# Exception: a bare --force-with-lease (with or without --force-if-includes) to the"
ALLOWED = ["# shortcut: no lock, add one when two writers exist", "# see https://example.invalid/x", "# noqa: E501",
           "// eslint-disable-next-line no-console", "# allow-comment: dependency, git 2.40 drops the flag",
           "# fixes #123", "# RFC 3339 time", "# SPDX-License-Identifier: MIT", "# Copyright 2026",
           "# shellcheck disable=SC2086", "// @ts-expect-error", "# type: ignore", "# pragma: no cover",
           "# NOQA", "# nosec", "# isort: skip"]
LINK_LINES = ["# #136: the pinned image of the canary", "# RFC 3339 time, as the log writes it",
              "# See https://example.invalid/palette for the range.", "// #263: root keys compare by lineKey",
              "# see https://shellcheck.net/wiki/SC2086", "// see https://eslint-community.github.io/eslint-utils/"]


class CommentRuleTests(unittest.TestCase):
    def test_kind_table(self) -> None:
        for text in ALLOWED:
            with self.subTest(text=text):
                self.assertTrue(repo_rules.kind_ok(text))
        for text in [SINCE, EXCEPTION, "# shortcut: no reason", "# plain words", "# 3. A plain push asks.",
                     "# record type: decision", "# License check first", "# no pragma here", "# fmt: see below"]:
            with self.subTest(text=text):
                self.assertFalse(repo_rules.kind_ok(text))

    def test_link_and_block_kinds(self) -> None:
        for text in LINK_LINES:
            with self.subTest(text=text):
                self.assertTrue(repo_rules.link_kind(text))
                self.assertFalse(repo_rules.block_kind(text))
                self.assertTrue(repo_rules.kind_ok(text))
        for text in ["# shortcut: no lock, add one when two writers exist", "# noqa: E501", "#shellcheck disable=SC2086",
                     "// eslint-disable-next-line no-console", "# SPDX-License-Identifier: MIT",
                     "# the tool drops it, allow-comment: dependency"]:
            with self.subTest(text=text):
                self.assertTrue(repo_rules.block_kind(text))
        for text in ["# plain words # noqa", "# see below // eslint-disable-line"]:
            with self.subTest(text=text):
                self.assertFalse(repo_rules.kind_ok(text))

    def test_comment_lines_table(self) -> None:
        for path, text, expected in [
            ("a.py", 'def f():\n    """Doc.\n    # not a comment\n    """\n    # real\n    x = 1  # trailing\n',
             {5: "# real"}),
            ("a.py", "def f(:\n    # x\n", {2: "# x"}),
            ("a.sh", "#!/bin/sh\n  # x\necho '# no'\n", {1: "#!/bin/sh", 2: "# x"}),
            ("a.mjs", "// x\n# y\nconst a = 1 // z\n", {1: "// x"}),
            ("a.ts", "x = 1 // y\n  // z\n", {2: "// z"}),
        ]:
            with self.subTest(path=path, text=text):
                self.assertEqual(repo_rules.comment_lines(path, text), expected)

    def test_trailing_comments_table(self) -> None:
        for path, text, expected in [
            ("a.py", 'def f():\n    # full\n    x = 1  # trailing\n    y = "# no"\n    z = 2 # noqa: E402\n',
             {3: "# trailing", 5: "# noqa: E402"}),
            ("a.py", 'x = """a\n# b\n"""  # after\n', {3: "# after"}),
            ("a.py", "def f(:\n    x = 1  # t\n", {}),
            ("a.sh", "echo x  # t\n", {1: "# t"}),
            ("a.mjs", "const a = 1 // z\n", {}),
        ]:
            with self.subTest(path=path, text=text):
                self.assertEqual(repo_rules.trailing_comments(path, text), expected)

    def test_allowed_lines(self) -> None:
        text = "#!/bin/sh\n# head one\n\n# head two\nset -u\n# plain\n# more\n# shortcut: x, y\n# next\n# after\necho\n# z\n"
        self.assertEqual(repo_rules.allowed_lines("a.sh", text), {1, 2, 4, 8, 9, 10})
        self.assertEqual(repo_rules.allowed_lines("a.py", '"""Doc."""\n# x\n'), set())
        self.assertEqual(repo_rules.allowed_lines("a.py", "x = 1  # t\n# a\n# shortcut: x, y\n# b\n"), {3, 4})

    def test_link_line_carries_nothing(self) -> None:
        for path, text, expected in [
            ("a.sh", "set -u\n# #136: the pinned image of the canary\n# it runs with no network\n", {2}),
            ("a.sh", "set -u\n# RFC 3339 time, as the log writes it\n# it runs with no network\n", {2}),
            ("a.sh", "set -u\n# See https://example.invalid/palette for the range.\n# it runs with no network\n", {2}),
            ("a.sh", "set -u\n# see https://shellcheck.net/wiki/SC2086\n# prose\n", {2}),
            ("a.mjs", "const a = 1\n// #263: root keys compare by lineKey\n// so quotes do not matter\n", {2}),
            ("a.mjs", "const a = 1\n// see https://eslint-community.github.io/eslint-utils/\n// prose\n", {2}),
            ("a.py", "x = 1\n# see #12\n# prose\n", {2}),
            ("a.sh", "set -u\n# see #12\n# see #13\n", {2, 3}),
            ("a.sh", "set -u\n# see #12\n# allow-comment: dependency, x\n# prose\n", {2, 3, 4}),
        ]:
            with self.subTest(path=path, text=text):
                self.assertEqual(repo_rules.allowed_lines(path, text), expected)

    def test_carriers(self) -> None:
        for text, expected in [
            ("set -u\n# allow-comment: dependency, the tool drops the flag\n# https://example.invalid/tool/issues/12\n"
             "# until version 2 ships\n", {2, 3, 4}),
            ("set -u\n# shortcut: no lock, add one when two writers exist\n# a prose line\n", {2, 3}),
            ("set -u\n# shortcut: no lock, add one when two writers exist\n# see #12\n# the second writer lands in #13\n"
             "# more\n", {2, 3, 4, 5}),
            ("#!/bin/sh\n# see #12\n# prose\nset -u\n", {1, 2, 3}),
            ("set -u\n# allow-comment: dependency, x\n\n# prose\n", {2}),
        ]:
            with self.subTest(text=text):
                self.assertEqual(repo_rules.allowed_lines("a.sh", text), expected)


class CommentCommandTests(RulesRepo):
    def base_files(self) -> None:
        put(self.repo / "a.sh", "#!/bin/bash\n# head\nset -u\n\n# 3. A plain push asks.\nhas x\n")
        put(self.repo / "m.py", '"""Mod."""\n\n\ndef f():\n    return 1\n')
        put(self.repo / "old.py", '"""Old."""\n\n\ndef g():\n    # moved words stay the same\n    return 2  # kept words\n')
        put(self.repo / "links.sh", "set -u\n# see #12\n# the old prose\n")

    def comments(self) -> tuple[int, dict]:
        return run_rules(self.repo, "comments")

    def assert_violations(self, expected: list[tuple[str, int, str]]) -> None:
        code, out = self.comments()
        self.assertEqual([(v["path"], v["line"], v["text"]) for v in out["violations"]], expected, out)
        self.assertEqual(code, 1 if expected else 0)
        self.assertEqual(out["ok"], not expected)

    def test_mid_file_shell_comment(self) -> None:
        self.commit("✨(x) Add", "a.sh", "#!/bin/bash\n# head\nset -u\n# plain words\n\n# 3. A plain push asks.\nhas x\n")
        self.assert_violations([("a.sh", 4, "# plain words")])

    def test_since_comment_in_a_function(self) -> None:
        self.commit("✨(x) Add", "m.py", f'"""Mod."""\n\n\ndef f():\n    {SINCE}\n    return 1\n')
        self.assert_violations([("m.py", 5, SINCE)])

    def test_continuation_of_a_base_line_still_fails(self) -> None:
        self.commit("✨(x) Add", "a.sh",
                    f"#!/bin/bash\n# head\nset -u\n\n# 3. A plain push asks.\n{EXCEPTION}\nhas x\n")
        self.assert_violations([("a.sh", 6, EXCEPTION)])

    def test_allowed_kinds_mid_file(self) -> None:
        body = "".join(f"x{i}=1\n{line}\n" for i, line in enumerate(ALLOWED) if line.startswith("#"))
        self.commit("✨(x) Add", "n.sh", "#!/bin/sh\n# lead\n\n# lead two\nset -u\n" + body + "# shortcut: a, b\n# next line\n")
        put(self.repo / "n.mjs", "const a = 1\n// eslint-disable-next-line no-console\n// @ts-expect-error\n")
        self.assert_violations([])

    def test_uncommitted_untracked_and_ignored(self) -> None:
        put(self.repo / "m.py", f'"""Mod."""\n\n\ndef f():\n    # uncommitted\n    return 1\n')
        put(self.repo / "new dir" / "u.sh", "set -u\n# untracked\n")
        put(self.repo / "ign" / "i.sh", "set -u\n# ignored\n")
        put(self.repo / ".gitignore", "ign/\n")
        self.assert_violations([("m.py", 5, "# uncommitted"), ("new dir/u.sh", 2, "# untracked")])

    def test_skipped_paths_and_types(self) -> None:
        for path in ["tests/acceptance/t.py", ".agents/a.sh", ".claude/c.sh", ".codex/c.sh", ".dev-loop/d.sh",
                     "node_modules/x/i.js", "x.md", "x.toml"]:
            put(self.repo / path, "x = 1\n# plain words\n")
        self.assert_violations([])

    def test_base_lines_unchanged(self) -> None:
        self.commit("✨(x) Add", "b.txt", "b\n")
        self.assert_violations([])

    def test_moved_comment_is_not_new(self) -> None:
        self.commit("♻️(x) Move", "new.py", '"""New."""\n\n\ndef g():\n    # moved words stay the same\n    return 2\n')
        (self.repo / "old.py").unlink()
        self.assert_violations([])
        put(self.repo / "new.py", '"""New."""\n\n\ndef g():\n    # moved words changed\n    return 2\n')
        self.assert_violations([("new.py", 5, "# moved words changed")])

    def test_link_line_with_a_reason(self) -> None:
        link = "# #136: the pinned image of the canary"
        for text, expected in [
            (f"set -u\n{link}\n", []),
            (f"set -u\n{link}\n# it runs with no network\n", [("l.sh", 3, "# it runs with no network")]),
            ("set -u\n# see #12\n# see #13\n", []),
            ("set -u\n# see #12\n# the old prose\n", []),
            ("set -u\n# see #12\n# the new prose\n", [("l.sh", 3, "# the new prose")]),
        ]:
            with self.subTest(text=text):
                put(self.repo / "l.sh", text)
                self.assert_violations(expected)
                (self.repo / "l.sh").unlink()

    def test_trailing_comments(self) -> None:
        head = '"""Mod."""\n\n\ndef f():\n'
        old = '"""Old."""\n\n\ndef g():\n    # moved words stay the same\n    return 3  # {}\n'
        for path, text, expected in [
            ("m.py", head + "    return 1  # plain words\n", [("m.py", 5, "# plain words")]),
            ("m.py", head + "    return 1  # noqa: E402\n    y = 2  # see https://example.invalid/x\n", []),
            ("m.py", head + "    return 1  # shortcut: a, b\n    y = 2  # next\n", [("m.py", 6, "# next")]),
            ("m.py", head + "    return 'https://example.invalid/x'  # plain words\n", [("m.py", 5, "# plain words")]),
            ("m.py", head + "    return 1  # fixes #123\n    y = 2  # plain\n", [("m.py", 6, "# plain")]),
            ("m.py", head + "    return 1  # NOQA\n    y = 2  # nosec\n    import a  # isort: skip\n", []),
            ("old.py", old.format("kept words"), []),
            ("old.py", old.format("new words"), [("old.py", 6, "# new words")]),
            ("h.py", "x = 1  # first\n# second\n", [("h.py", 1, "# first"), ("h.py", 2, "# second")]),
        ]:
            with self.subTest(path=path, text=text):
                put(self.repo / path, text)
                self.assert_violations(expected)
                (self.repo / path).unlink()


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
            ('msg="$(printf "%s # %s" a b)"\n', {}),
            ('x="$(echo "a # b")" # c\n', {1: "# c"}),
            ("x=$(( 1 << n ))\nfoo # c\nn\n", {2: "# c"}),
            ("(( a << b )) # c\necho # d\n", {1: "# c", 2: "# d"}),
            ("cat <<\\EOF\nbody  # nc\nEOF\necho # d\n", {4: "# d"}),
            ("cat <<'END-X'\nbody  # nc\nEND-X\necho # d\n", {4: "# d"}),
            ('cat <<"a.b"\nbody  # nc\na.b\necho # d\n', {4: "# d"}),
            ("echo $'it\\'s' # c\necho b  # late\n", {1: "# c", 2: "# late"}),
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
