#!/usr/bin/env python3
"""Tests for `mutation/heldout.py`: the held-out folder rules of the mutation setup and adapters."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from seal import ACCEPTANCE_DIR

from mutation import heldout


class MutationHeldOut(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name in ("tests/unit/test_a.py", "tests/acceptance/test_x.py", "app/tests/test_b.py"):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text("")

    def test_constant(self) -> None:
        self.assertEqual(heldout.HELD_OUT, ACCEPTANCE_DIR)

    def test_relative(self) -> None:
        root = self.root
        self.assertEqual(heldout.relative(root, root), "tests/acceptance")
        self.assertEqual(heldout.relative(root / "tests", root), "acceptance")
        self.assertIsNone(heldout.relative(root / "app", root))
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        link = Path(other.name) / "link"
        link.symlink_to(root)
        self.assertEqual(heldout.relative(link, root), "tests/acceptance")

    def test_stryker_refusals(self) -> None:
        self.assertEqual(heldout.stryker_refusals({}), [])
        self.assertEqual(heldout.stryker_refusals({"inPlace": False, "ignorePatterns": ["dist"]}), [])
        self.assertEqual(heldout.stryker_refusals({"inPlace": True}),
                         ['stryker.config.json sets "inPlace": true: the held-out suite then reaches the run'])
        for bad in ("dist", [1], None, {"a": 1}):
            self.assertEqual(heldout.stryker_refusals({"ignorePatterns": bad}),
                             ["stryker.config.json ignorePatterns is not a list of strings"])
        self.assertEqual(heldout.stryker_refusals({"ignorePatterns": ["a,b", "c", "d,e"]}),
                         ["stryker.config.json ignorePatterns entry has a comma: a,b",
                          "stryker.config.json ignorePatterns entry has a comma: d,e"])

    def test_ignore_patterns(self) -> None:
        self.assertEqual(heldout.ignore_patterns({}, "tests/acceptance"), "/tests/acceptance")
        self.assertEqual(heldout.ignore_patterns({"ignorePatterns": []}, "tests/acceptance"), "/tests/acceptance")
        self.assertEqual(heldout.ignore_patterns({"ignorePatterns": ["dist", "/big"]}, "tests/acceptance"),
                         "dist,/big,/tests/acceptance")

    def test_ran(self) -> None:
        root = self.root
        self.assertEqual(heldout.ran(["tests/acceptance/a.test.ts", "tests/unit/b.test.ts"], root, root), 1)
        self.assertEqual(heldout.ran(["../tests/acceptance/a.test.ts"], root / "web", root), 1)
        self.assertEqual(heldout.ran([str(root / "tests/acceptance/x.ts"), "./tests/acceptance/y.ts"], root, root), 2)
        self.assertEqual(heldout.ran(["tests/acceptance-old/z.ts", "tests/unit/b.test.ts"], root, root), 0)

    def test_selection_refusal(self) -> None:
        root = self.root
        for selection in ([], ["-x"], ["-k", "unit"], ["-x", "--tb", "short"], ["-k", "tests"], ["nope/"]):
            self.assertEqual(heldout.selection_refusal(selection, root, root), heldout.SELECTION_NO_PATH, selection)
        for selection in (["tests"], ["."], ["tests/acceptance/test_x.py::test_a"], ["./tests/"], ["tests/unit/.."],
                          ["tests/unit", "tests"]):
            self.assertEqual(heldout.selection_refusal(selection, root, root), heldout.SELECTION_REACHES, selection)
        for selection in (["tests/unit"], ["tests/unit", "--deselect", "tests/unit/test_slow.py::test_t"],
                          ["tests/unit/test_a.py::test_a"]):
            self.assertIsNone(heldout.selection_refusal(selection, root, root), selection)
        self.assertIsNone(heldout.selection_refusal(["tests"], root / "app", root))
        self.assertEqual(heldout.selection_refusal(["../tests"], root / "app", root), heldout.SELECTION_REACHES)

    def test_cli_args_refusal(self) -> None:
        root = self.root
        self.assertEqual(heldout.cli_args_refusal(["tests"], root, root), heldout.CLI_ARGS_REACHES)
        for args in ([], ["-p", "no:cacheprovider"], ["--ignore", "tests/acceptance"], ["tests/unit"]):
            self.assertIsNone(heldout.cli_args_refusal(args, root, root), args)


if __name__ == "__main__":
    unittest.main()
