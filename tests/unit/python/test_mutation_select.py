#!/usr/bin/env python3
"""Tests for `devloop.py mutation` selection: config at head, globs, ranges, owner entry, off and invalid."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from mutation_fixtures import adapter_script, commit, make_repo, run_mutation  # noqa: E402


def config(mutation: object = None, **extra: object) -> str:
    data: dict = {"version": "0.0.0", **extra}
    if mutation is not None:
        data["mutation"] = mutation
    return json.dumps(data)


def numbered(count: int) -> str:
    return "".join(f"l{n}\n" for n in range(1, count + 1))


class MutationSelect(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.count = 0

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def select(self, mutation: object, base: dict[str, str], change: dict[str, str | None], **extra: object):
        self.count += 1
        folder = self.root / f"case{self.count}"
        folder.mkdir()
        repo = make_repo(folder, {"dev-loop.json": config(mutation, **extra), **base})
        commit(repo, change)
        return repo, run_mutation(repo, "--head", "HEAD")

    def test_selection_of_one_entry(self) -> None:
        record = self.root / "record"
        entry = {"path": "app/", "command": "./adapt", "critical": ["core/**", "!core/gen/**"]}
        base = {"app/adapt": adapter_script(record, "{}"), "app/core/a.py": numbered(5),
                "app/core/gen/g.py": "g\n", "app/web/b.py": "b\n", "app/core/x/a.py": "x\n"}
        _, (code, out, _) = self.select([entry], base, {"app/core/a.py": "l1\nl2\nX3\nX4\nl5\n",
                                                        "app/core/gen/g.py": "G\n", "app/web/b.py": "B\n"})
        self.assertEqual(code, 0)
        self.assertEqual(out["entries"], [{"path": "app/", "command": "./adapt", "files": {"core/a.py": [[3, 4]]}}])
        self.assertEqual(out["outside"], ["app/core/gen/g.py", "app/web/b.py"])
        self.assertFalse(record.exists())
        shallow = {**entry, "critical": ["core/*"]}
        _, (_, out, _) = self.select([shallow], base, {"app/core/x/a.py": "X\n"})
        self.assertEqual(out["outside"], ["app/core/x/a.py"])
        self.assertEqual(out["entries"][0]["files"], {})

    def test_ranges(self) -> None:
        ten = numbered(10)
        cases = [
            ("delete line 5", {"f.py": ten}, {"f.py": ten.replace("l5\n", "")}, {"f.py": [[4, 4]]}),
            ("lines 3 4 6", {"f.py": ten},
             {"f.py": ten.replace("l3\n", "a\n").replace("l4\n", "b\n").replace("l6\n", "c\n")},
             {"f.py": [[3, 4], [6, 6]]}),
            ("delete line 1", {"f.py": ten}, {"f.py": ten.replace("l1\n", "", 1)}, {"f.py": [[1, 1]]}),
            ("add at end, no final newline", {"f.py": "a\nb"}, {"f.py": "a\nb\nc\nd"}, {"f.py": [[2, 4]]}),
            ("binary", {"f.py": "a\n", "x.bin": "a\0b\n"}, {"x.bin": "a\0c\n"}, {}),
        ]
        entry = {"path": ".", "command": "./adapt", "critical": ["**"]}
        for name, base, change, files in cases:
            with self.subTest(name):
                _, (_, out, _) = self.select([entry], base, change)
                self.assertEqual(out["entries"][0]["files"], files)
                self.assertEqual(out["outside"], [])

    def test_deepest_entry_wins(self) -> None:
        entries = [{"path": ".", "command": "./adapt", "critical": ["**"]},
                   {"path": "app/", "command": "./adapt", "critical": ["core/**"]}]
        _, (_, out, _) = self.select(entries, {"app/core/a.py": "a\n"}, {"app/core/a.py": "b\n"})
        self.assertEqual(out["entries"][0]["files"], {})
        self.assertEqual(out["entries"][1]["files"], {"core/a.py": [[1, 1]]})
        entry = [{"path": "app/", "command": "./adapt", "critical": ["**"]}]
        _, (_, out, _) = self.select(entry, {"app/x.py": "a\n", "lib/z.py": "a\n"}, {"lib/z.py": "b\n"})
        self.assertEqual(out["entries"][0]["files"], {})
        self.assertEqual(out["outside"], [])

    def test_paths_with_space_and_unicode(self) -> None:
        entry = [{"path": "app/", "command": "./adapt", "critical": ["core/**"]}]
        base = {"app/core/my file.py": "a\nb\n", "app/core/é.py": "a\nb\n"}
        change = {"app/core/my file.py": "a\nX\n", "app/core/é.py": "Y\nb\n"}
        _, (_, out, _) = self.select(entry, base, change)
        self.assertEqual(out["entries"][0]["files"], {"core/my file.py": [[2, 2]], "core/é.py": [[1, 1]]})

    def test_globs(self) -> None:
        cases = [
            ("src/**/core/*.py", ["src/core/a.py", "src/x/y/core/a.py"], ["src/core/x/a.py", "src/a.py"]),
            ("Core/*", [], ["core/a.py"]),
            ("core/*", ["core/a.py"], ["core/x/a.py"]),
        ]
        for glob, selected, outside in cases:
            with self.subTest(glob):
                names = selected + outside
                entry = [{"path": ".", "command": "./adapt", "critical": [glob]}]
                _, (_, out, _) = self.select(entry, {n: "a\n" for n in names}, {n: "b\n" for n in names})
                self.assertEqual(sorted(out["entries"][0]["files"]), sorted(selected))
                self.assertEqual(out["outside"], sorted(outside))

    def test_off_and_invalid(self) -> None:
        for name, value in (("no key", None), ("false", False)):
            with self.subTest(name):
                _, (code, out, _) = self.select(value, {"a.py": "a\n"}, {"a.py": "b\n"})
                self.assertEqual((code, out["entries"], out["outside"], out["none"]), (0, [], [], []))
        good = {"path": "a/", "command": "./adapt", "critical": ["**"]}
        bad = [
            {}, [{**good, "path": "../x/"}], [{**good, "critical": []}], [{**good, "critical": ["!a"]}],
            [{**good, "adapter": "mutmut"}], [good, {**good, "path": "A/"}], [{**good, "extra": 1}],
        ]
        for value in bad:
            with self.subTest(value):
                _, (code, out, _) = self.select(value, {"a/x.py": "a\n"}, {"a/x.py": "b\n"})
                self.assertEqual((code, out["status"]), (1, "setup"))
                self.assertTrue(out["cause"].startswith("mutation in dev-loop.json is invalid: "), out["cause"])
        with self.subTest("unknown adapter"):
            value = [{"path": "a/", "adapter": "pit", "critical": ["**"]}]
            _, (code, out, _) = self.select(value, {"a/x.py": "a\n"}, {"a/x.py": "b\n"})
            self.assertEqual((code, out["cause"]), (1, "mutation entry a/: unknown adapter pit"))
        with self.subTest("not a folder"):
            _, (code, out, _) = self.select([good], {"b/x.py": "a\n"}, {"b/x.py": "b\n"})
            self.assertEqual((code, out["status"]), (1, "setup"))

    def test_config_read_at_head(self) -> None:
        a = [{"path": "a/", "command": "./adapt", "critical": ["**"]}]
        b = [{"path": "b/", "command": "./adapt", "critical": ["**"]}]
        repo, _ = self.select(a, {"a/x.py": "a\n", "b/x.py": "a\n"}, {"a/x.py": "b\n", "b/x.py": "b\n"})
        (repo / "dev-loop.json").write_text(config(b))
        _, out, _ = run_mutation(repo, "--head", "HEAD")
        self.assertEqual([e["path"] for e in out["entries"]], ["a/"])

    def test_none_lists_uncovered_stacks(self) -> None:
        stacks = {"stacks": [{"path": "api/", "preset": "go"}]}
        for path, none in (("app/", ["api/"]), (".", [])):
            with self.subTest(path):
                entry = [{"path": path, "command": "./adapt", "critical": ["**"]}]
                _, (_, out, _) = self.select(entry, {"app/x.py": "a\n", "api/y.go": "a\n"},
                                             {"app/x.py": "b\n"}, **stacks)
                self.assertEqual(out["none"], none)


if __name__ == "__main__":
    unittest.main()
