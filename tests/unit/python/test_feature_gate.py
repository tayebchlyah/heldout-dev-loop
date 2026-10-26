#!/usr/bin/env python3
"""Tests for feature_gate.py: `review-merge` and `acceptance`."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
from feature_gate import TableError, merge, parse_table  # noqa: E402

DEVLOOP = HERE.parents[2] / "skill" / "scripts" / "devloop.py"
HEADER = "| file | line | finding | class | verdict |\n| --- | --- | --- | --- | --- |\n"


def table(*rows: str) -> str:
    return HEADER + "".join(f"| {row} |\n" for row in rows)


class TempDir(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def devloop(self, *args: str, cwd: Path | None = None, env: dict | None = None) -> tuple[int, dict, str]:
        result = subprocess.run([sys.executable, str(DEVLOOP), *args], cwd=cwd or self.tmp,
                                capture_output=True, text=True, env=env)
        return result.returncode, json.loads(result.stdout), result.stderr


class ReviewMergeTests(TempDir):
    def test_same_file_and_line_merge_into_one_row(self) -> None:
        a = parse_table(table("a.py | 3 | x | correctness | CONFIRMED: y"), None)
        b = parse_table(table("a.py | 3 | z | spec | PLAUSIBLE", "a.py | 5 | w | standards | PLAUSIBLE"), None)
        merged = merge([("a", a), ("b", b)])
        self.assertEqual(len(merged["rows"]), 2)
        first = merged["rows"][0]
        self.assertEqual((first["n"], first["file"], first["line"], first["reviewers"]), (1, "a.py", 3, ["a", "b"]))
        self.assertEqual([f["finding"] for f in first["findings"]], ["x", "z"])
        self.assertEqual(first["findings"][0], {"reviewer": "a", "finding": "x", "class": "correctness",
                                                "verdict": "CONFIRMED: y"})
        self.assertEqual(merged["counts"], {"a": 1, "b": 2})

    def test_file_forms_normalize(self) -> None:
        rows = parse_table(table("./a.py | 1 | x | spec | PLAUSIBLE", "`a.py` | 1 | x | spec | PLAUSIBLE",
                                 "/w/a.py | 1 | x | spec | PLAUSIBLE",
                                 "/tmp/devloop-second-review-x/a.py | 1 | x | spec | PLAUSIBLE"), "/w")
        self.assertEqual({r["file"] for r in rows}, {"a.py"})

    def test_line_forms(self) -> None:
        rows = parse_table(table(*(f"a.py | {cell} | x | spec | PLAUSIBLE"
                                   for cell in ("12-15", "L12", "12", "-", "", "n/a", "N/A"))), None)
        self.assertEqual([r["line"] for r in rows], [12, 12, 12, None, None, None, None])
        with self.assertRaises(TableError) as caught:
            parse_table(table("a.py | 1 | x | spec | PLAUSIBLE", "a.py | x | x | spec | PLAUSIBLE"), None)
        self.assertEqual(caught.exception.row, 2)

    def test_columns_any_order_extra_ignored(self) -> None:
        text = ("Intro text.\n\n| verdict | class | finding | severity | line | file |\n|---|---|---|---|---|---|\n"
                "| PLAUSIBLE | Security | leak | high | 4 | b.py |\n\nAfter.\n")
        self.assertEqual(parse_table(text, None), [{"file": "b.py", "line": 4, "finding": "leak", "class": "security",
                                                    "verdict": "PLAUSIBLE"}])

    def test_bad_class_is_an_error(self) -> None:
        with self.assertRaises(TableError) as caught:
            parse_table(table("a.py | 1 | x | style | PLAUSIBLE"), None)
        self.assertEqual(caught.exception.row, 1)
        path = self.tmp / "r.md"
        path.write_text(table("a.py | 1 | x | style | PLAUSIBLE"))
        code, out, _ = self.devloop("review-merge", f"r={path}")
        self.assertEqual(code, 1)
        self.assertEqual((out["file"], out["row"]), (str(path), 1))
        self.assertIn("class", out["error"])

    def test_header_only_gives_zero(self) -> None:
        empty, full = self.tmp / "e.md", self.tmp / "f.md"
        empty.write_text(HEADER)
        full.write_text(table("a.py | 1 | x | spec | PLAUSIBLE"))
        code, out, _ = self.devloop("review-merge", f"e={empty}", f"f={full}")
        self.assertEqual(code, 0)
        self.assertEqual(out["counts"], {"e": 0, "f": 1})
        self.assertEqual(len(out["rows"]), 1)

    def test_security_lists_row_numbers(self) -> None:
        rows = parse_table(table("a.py | 1 | x | spec | PLAUSIBLE", "b.py | 2 | y | Security | CONFIRMED: z",
                                 "c.py | 3 | y | correctness | PLAUSIBLE"), None)
        self.assertEqual(merge([("a", rows)])["security"], [2])

    def test_escaped_pipe_stays_in_cell(self) -> None:
        rows = parse_table(table(r"a.py | 1 | `a \| b` breaks | spec | PLAUSIBLE"), None)
        self.assertEqual(rows[0]["finding"], "`a | b` breaks")

    def test_first_matching_table_only(self) -> None:
        text = ("| a | b |\n|---|---|\n| 1 | 2 |\n\n" + table("a.py | 1 | x | spec | PLAUSIBLE")
                + "\nMore:\n\n" + table("z.py | 9 | q | spec | PLAUSIBLE"))
        self.assertEqual([r["file"] for r in parse_table(text, None)], ["a.py"])

    def test_rows_sorted_null_line_first(self) -> None:
        rows = parse_table(table("b.py | 2 | x | spec | PLAUSIBLE", "a.py | - | x | spec | PLAUSIBLE",
                                 "a.py | 1 | x | spec | PLAUSIBLE"), None)
        merged = merge([("r", rows)])["rows"]
        self.assertEqual([(r["n"], r["file"], r["line"]) for r in merged], [(1, "a.py", None), (2, "a.py", 1),
                                                                           (3, "b.py", 2)])

    def test_cli_usage_errors(self) -> None:
        path = self.tmp / "r.md"
        path.write_text(HEADER)
        for args in (["x"], ["=f"], ["a="], [f"a={path}", f"a={path}"], [f"a={self.tmp / 'missing.md'}"]):
            code, out, _ = self.devloop("review-merge", *args)
            self.assertEqual(code, 2, args)
            self.assertIn("error", out)

    def test_missing_table_and_cell_count(self) -> None:
        with self.assertRaises(TableError) as caught:
            parse_table("no table here\n", None)
        self.assertIsNone(caught.exception.row)
        with self.assertRaises(TableError) as caught:
            parse_table(table("a.py | 1 | x | spec"), None)
        self.assertEqual(caught.exception.row, 1)
        for row in (" | 1 | x | spec | PLAUSIBLE", "a.py | 1 |  | spec | PLAUSIBLE"):
            with self.assertRaises(TableError):
                parse_table(table(row), None)


if __name__ == "__main__":
    unittest.main()
