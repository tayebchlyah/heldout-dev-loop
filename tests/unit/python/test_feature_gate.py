#!/usr/bin/env python3
"""Tests for feature_gate.py: `review-merge` and `acceptance`."""

from __future__ import annotations

import itertools
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
from feature_gate import TableError, guard, merge, parse_table  # noqa: E402

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



RUN_TAGS = itertools.count()


def run(failing: int | None, ids: list[str] | None = None, exit: int | None = None, mode: str = "full",
        counted: bool = True) -> dict:
    """A record run. Default ids differ from those of every other run: no id stays red by accident."""
    tag = next(RUN_TAGS)
    ids = ids if ids is not None else ([f"t::{tag}-{i}" for i in range(failing)] if failing is not None else None)
    return {"failing": failing, "ids": ids, "exit": exit if exit is not None else int(bool(failing)), "mode": mode,
            "counted": counted}


class ProgressGuardTests(unittest.TestCase):
    def test_no_progress(self) -> None:
        self.assertEqual(guard([run(5), run(3), run(3), run(4)])["verdict"], "stop")
        self.assertEqual(guard([run(5), run(3), run(3), run(4)])["reason"], "no-progress")
        self.assertEqual(guard([run(5), run(3), run(3)]),
                         {"verdict": "continue", "reason": None, "ids": [], "counts": [5, 3, 3]})

    def test_stuck(self) -> None:
        runs = [run(n, ["t::a"] + [f"t::{n}{i}" for i in range(n - 1)]) for n in (6, 5, 4, 3)]
        self.assertEqual(guard(runs), {"verdict": "stop", "reason": "stuck", "ids": ["t::a"], "counts": [6, 5, 4, 3]})

    def test_green_then_new_loop(self) -> None:
        self.assertEqual(guard([run(3), run(0)]), {"verdict": "green", "reason": None, "ids": [], "counts": []})
        self.assertEqual(guard([run(3), run(0), run(5)])["counts"], [5])
        self.assertEqual(guard([run(None, exit=0)])["verdict"], "green")

    def test_failed_mode_green_is_continue(self) -> None:
        self.assertEqual(guard([run(2), run(0, mode="failed")])["verdict"], "continue")

    def test_exit_one_zero_failing_unknown(self) -> None:
        self.assertEqual(guard([run(0, exit=1)])["verdict"], "unknown")

    def test_none_failing_unknown(self) -> None:
        self.assertEqual(guard([run(None, exit=1)])["verdict"], "unknown")

    def test_uncounted_run_skipped_in_loop(self) -> None:
        self.assertEqual(guard([run(5), run(5, counted=False), run(3)])["counts"], [5, 3])
        self.assertEqual(guard([run(5), run(5, counted=False), run(3)])["verdict"], "continue")

    def test_uncounted_run_gives_none(self) -> None:
        self.assertIsNone(guard([run(5), run(3, counted=False)]))


RUN_SH = """#!/bin/sh
touch ran.marker
[ -f fixture.xml ] && cp fixture.xml "$DEVLOOP_REPORT_DIR/"
exit $(cat exit.txt 2>/dev/null || echo 0)
"""


def junit(failing: list[tuple[str, str]], passing: int = 1, kind: str = "failure") -> str:
    cases = "".join(f'<testcase classname="{c}" name="{n}"><{kind} message="x"/></testcase>' for c, n in failing)
    cases += "".join(f'<testcase classname="ok" name="p{i}"/>' for i in range(passing))
    return f'<?xml version="1.0"?><testsuites><testsuite name="s">{cases}</testsuite></testsuites>'


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class RepoCase(TempDir):
    def make_repo(self, commands: dict | None = None) -> Path:
        repo = self.tmp / "repo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "t@example.com")
        git(repo, "config", "user.name", "t")
        (repo / "run.sh").write_text(RUN_SH)
        (repo / "run.sh").chmod(0o755)
        config = {"version": "2.1.1", "harnesses": ["claude"]}
        if commands is not None:
            config["commands"] = commands
        (repo / "dev-loop.json").write_text(json.dumps(config))
        (repo / "app.py").write_text("x = 1\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "init")
        return repo

    def record(self, repo: Path) -> Path:
        return Path(git(repo, "rev-parse", "--absolute-git-dir")) / "dev-loop" / "acceptance.json"


class AcceptanceCommandTests(RepoCase):
    def test_two_failing_sorted(self) -> None:
        repo = self.make_repo({"acceptance": "./run.sh"})
        (repo / "fixture.xml").write_text(junit([("t", "b"), ("t", "a")]))
        (repo / "exit.txt").write_text("1")
        code, out, err = self.devloop("acceptance", cwd=repo)
        self.assertEqual(code, 1)
        self.assertEqual((out["failing"], out["ids"], out["counted"], out["mode"]), (2, ["t::a", "t::b"], True, "full"))
        self.assertEqual(out["stale"]["state"], "skipped")
        self.assertIn(f"acceptance log: {out['log']}", err)
        self.assertTrue(Path(out["log"]).is_file())
        self.assertEqual(out["head"], git(repo, "rev-parse", "HEAD"))
        self.assertEqual(out["record"], str(self.record(repo)))
        runs = json.loads(self.record(repo).read_text())["runs"]
        self.assertEqual(set(runs[0]), {"at", "head", "mode", "exit", "failing", "ids", "stale", "counted", "log"})
        self.assertEqual(runs[0]["stale"], "skipped")
        self.assertRegex(runs[0]["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(out["guard"]["verdict"], "continue")

    def test_error_child_counts_and_no_classname(self) -> None:
        repo = self.make_repo({"acceptance": "./run.sh"})
        (repo / "fixture.xml").write_text(junit([("", "solo")], kind="error").replace('classname="" ', ""))
        (repo / "exit.txt").write_text("1")
        _, out, _ = self.devloop("acceptance", cwd=repo)
        self.assertEqual(out["ids"], ["solo"])

    def test_no_xml_hint(self) -> None:
        repo = self.make_repo({"acceptance": "./run.sh"})
        (repo / "exit.txt").write_text("1")
        code, out, _ = self.devloop("acceptance", cwd=repo)
        self.assertEqual((code, out["failing"], out["ids"]), (1, None, None))
        self.assertIn("DEVLOOP_REPORT_DIR", out["hint"])
        self.assertEqual(out["guard"]["verdict"], "unknown")

    def test_bad_xml_hint(self) -> None:
        repo = self.make_repo({"acceptance": "./run.sh"})
        (repo / "fixture.xml").write_text("<testsuites>")
        _, out, _ = self.devloop("acceptance", cwd=repo)
        self.assertIsNone(out["failing"])
        self.assertIn("hint", out)

    def test_no_command_no_log(self) -> None:
        repo = self.make_repo({"gate": "true"})
        code, out, _ = self.devloop("acceptance", cwd=repo)
        self.assertEqual(code, 1)
        self.assertIn("commands.acceptance", out["error"])
        logs = Path(git(repo, "rev-parse", "--absolute-git-dir")) / "dev-loop" / "logs"
        self.assertEqual(list(logs.glob("*")) if logs.exists() else [], [])

    def test_failed_needs_failed_command(self) -> None:
        repo = self.make_repo({"acceptance": "./run.sh"})
        code, out, _ = self.devloop("acceptance", "--failed", cwd=repo)
        self.assertEqual(code, 1)
        self.assertIn("commands.acceptance_failed", out["error"])
        self.assertFalse((repo / "ran.marker").exists())

    def test_failed_mode_runs_failed_command(self) -> None:
        repo = self.make_repo({"acceptance": "false", "acceptance_failed": "./run.sh"})
        (repo / "fixture.xml").write_text(junit([]))
        code, out, _ = self.devloop("acceptance", "--failed", cwd=repo)
        self.assertEqual((code, out["mode"], out["guard"]["verdict"]), (0, "failed", "continue"))

    def test_bad_record_runs_nothing(self) -> None:
        repo = self.make_repo({"acceptance": "./run.sh"})
        for text in ("not json", '{"runs": 3}', "[]", '{"runs": [1]}'):
            self.record(repo).parent.mkdir(parents=True, exist_ok=True)
            self.record(repo).write_text(text)
            code, out, _ = self.devloop("acceptance", cwd=repo)
            self.assertEqual(code, 1, text)
            self.assertIn("error", out)
            self.assertFalse((repo / "ran.marker").exists())

    def test_green_exit_zero_and_record_grows(self) -> None:
        repo = self.make_repo({"acceptance": "./run.sh"})
        (repo / "fixture.xml").write_text(junit([("t", "a")]))
        (repo / "exit.txt").write_text("1")
        self.devloop("acceptance", cwd=repo)
        (repo / "fixture.xml").write_text(junit([]))
        (repo / "exit.txt").write_text("0")
        code, out, _ = self.devloop("acceptance", cwd=repo)
        self.assertEqual((code, out["guard"]["verdict"], out["counted"]), (0, "green", True))
        self.assertEqual(len(json.loads(self.record(repo).read_text())["runs"]), 2)

    def test_not_a_git_repo(self) -> None:
        code, out, _ = self.devloop("acceptance", cwd=self.tmp)
        self.assertEqual(code, 1)
        self.assertIn("error", out)


if __name__ == "__main__":
    unittest.main()
