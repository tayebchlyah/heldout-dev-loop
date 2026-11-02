#!/usr/bin/env python3
"""Tests for feature_gate.py: `review-merge` and `acceptance`."""

from __future__ import annotations

import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from support import ROOT

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
from feature_gate import TableError, created_seconds, guard, merge, parse_table, stale_check  # noqa: E402

DEVLOOP = ROOT / "skill" / "scripts" / "devloop.py"
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



class ReviewSaveTests(TempDir):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.worktree = self.tmp / "wt"
        for args in (["init", "-q", "-b", "main"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                                                      "--allow-empty", "-m", "init"],
                     ["worktree", "add", "-q", "-b", "feat", str(self.worktree)]):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)
        self.gitdir = Path(subprocess.run(["git", "-C", str(self.worktree), "rev-parse", "--absolute-git-dir"],
                                          check=True, capture_output=True, text=True).stdout.strip())

    def save(self, *args: str, text: str = "") -> tuple[int, dict]:
        result = subprocess.run([sys.executable, str(DEVLOOP), "review-save", *args], cwd=self.worktree, input=text,
                                capture_output=True, text=True)
        return result.returncode, json.loads(result.stdout)

    def test_saves_table_in_the_worktree_git_dir(self) -> None:
        text = table("a.py | 3 | x | correctness | CONFIRMED: y")
        code, out = self.save("--reviewer", "code-review", text=text)
        target = self.gitdir / "dev-loop" / "review" / "code-review.md"
        self.assertEqual((code, Path(out["file"]), out["findings"]), (0, target, 1))
        self.assertIn("worktrees", target.parts)
        self.assertEqual(target.read_text(), text)

    def test_bad_table_writes_nothing(self) -> None:
        code, out = self.save("--reviewer", "code-review", text=table("a.py | 1 | x | nope | PLAUSIBLE"))
        self.assertEqual(code, 1)
        self.assertIn("bad class", out["error"])
        self.assertEqual(out["row"], 1)
        self.assertFalse((self.gitdir / "dev-loop").exists())

    def test_bad_names_are_refused(self) -> None:
        for name in ("../x", "a/b", "..", ".", "", ".hidden", "a\\b", "café", "٣"):
            with self.subTest(name=name):
                code, out = self.save("--reviewer", name, text=HEADER)
                self.assertEqual(code, 2)
                self.assertIn("error", out)
        self.assertFalse((self.gitdir / "dev-loop").exists())

    def test_empty_table_is_valid(self) -> None:
        code, out = self.save("--reviewer", "security-review", text=HEADER)
        self.assertEqual((code, out["findings"]), (0, 0))
        self.assertTrue((self.gitdir / "dev-loop" / "review" / "security-review.md").is_file())

    def test_done_writes_head(self) -> None:
        head = subprocess.run(["git", "-C", str(self.worktree), "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()
        code, out = self.save("--done")
        marker = self.gitdir / "dev-loop" / "review-done"
        self.assertEqual((code, Path(out["file"]), out["sha"]), (0, marker, head))
        self.assertEqual(marker.read_text().strip(), head)

    def test_usage_errors(self) -> None:
        for args in ([], ["--done", "--reviewer", "a"]):
            result = subprocess.run([sys.executable, str(DEVLOOP), "review-save", *args], cwd=self.worktree,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, args)


RUN_TAGS = itertools.count()


def run(failing: int | None, ids: list[str] | None = None, exit: int | None = None, mode: str = "full",
        counted: bool = True, cases: int | None = None) -> dict:
    tag = next(RUN_TAGS)
    ids = ids if ids is not None else ([f"t::{tag}-{i}" for i in range(failing)] if failing is not None else None)
    cases = cases if cases is not None else (failing + 1 if failing is not None else None)
    return {"failing": failing, "ids": ids, "exit": exit if exit is not None else int(bool(failing)), "mode": mode,
            "counted": counted, "cases": cases}


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
        self.assertEqual(guard([run(None, exit=0)])["verdict"], "unknown")

    def test_executed_cases_rule(self) -> None:
        old = run(0)
        del old["cases"]
        rows = [
            ("no executed case", [run(0, cases=0)], "unknown", None),
            ("old run without cases", [old, run(2)], "continue", [2]),
            ("failed mode, no executed case", [run(0, mode="failed", cases=0)], "unknown", None),
        ]
        for name, runs, verdict, counts in rows:
            with self.subTest(name):
                out = guard(runs)
                self.assertEqual(out["verdict"], verdict)
                if counts is not None:
                    self.assertEqual(out["counts"], counts)

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


def junit(failing: list[tuple[str, str]], passing: int = 1, kind: str = "failure", skipped: int = 0) -> str:
    cases = "".join(f'<testcase classname="{c}" name="{n}"><{kind} message="x"/></testcase>' for c, n in failing)
    cases += "".join(f'<testcase classname="ok" name="p{i}"/>' for i in range(passing))
    cases += "".join(f'<testcase classname="skip" name="s{i}"><skipped/></testcase>' for i in range(skipped))
    return f'<?xml version="1.0"?><testsuites><testsuite name="s">{cases}</testsuite></testsuites>'


def git(cwd: Path, *args: str, when: str | None = None) -> str:
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when} if when else None
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env).stdout.strip()


class RepoCase(TempDir):
    def make_repo(self, commands: dict | None = None, when: str | None = None) -> Path:
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
        git(repo, "commit", "-qm", "init", when=when)
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
        self.assertEqual(out["cases"], 3)
        self.assertEqual(out["stale"]["state"], "skipped")
        self.assertIn(f"acceptance log: {out['log']}", err)
        self.assertTrue(Path(out["log"]).is_file())
        self.assertEqual(out["head"], git(repo, "rev-parse", "HEAD"))
        self.assertEqual(out["record"], str(self.record(repo)))
        runs = json.loads(self.record(repo).read_text())["runs"]
        self.assertEqual(set(runs[0]), {"at", "head", "mode", "exit", "failing", "ids", "stale", "counted", "log",
                                        "cases", "tests", "floor", "short", "suite"})
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
        for text in ("not json", '{"runs": 3}', "[]", '{"runs": [1]}', '{"runs": [{}]}'):
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
        (repo / "fixture.xml").write_text(junit([], passing=2))
        (repo / "exit.txt").write_text("0")
        code, out, _ = self.devloop("acceptance", cwd=repo)
        self.assertEqual((code, out["guard"]["verdict"], out["counted"]), (0, "green", True))
        self.assertEqual(len(json.loads(self.record(repo).read_text())["runs"]), 2)

    def test_executed_cases(self) -> None:
        rows = [
            ("no testcase", '<testsuites><testsuite name="s"/></testsuites>', 0, (), 1, 0, "unknown", True),
            ("all skipped", junit([], passing=0, skipped=2), 0, (), 1, 0, "unknown", True),
            ("three passing", junit([], passing=3), 0, (), 0, 3, "green", False),
            ("failed mode", junit([], passing=1), 0, ("--failed",), 0, 1, "continue", False),
            ("failing and skipped", junit([("t", "a")], passing=0, skipped=2), 1, (), 1, 1, "continue", False),
        ]
        for name, xml, exit, args, code, cases, verdict, hint in rows:
            with self.subTest(name):
                shutil.rmtree(self.tmp)
                self.tmp.mkdir()
                repo = self.make_repo({"acceptance": "./run.sh", "acceptance_failed": "./run.sh"})
                (repo / "fixture.xml").write_text(xml)
                (repo / "exit.txt").write_text(str(exit))
                got, out, _ = self.devloop("acceptance", *args, cwd=repo)
                self.assertEqual((got, out["cases"], out["guard"]["verdict"], "hint" in out), (code, cases, verdict, hint))
                self.assertEqual(json.loads(self.record(repo).read_text())["runs"][-1]["cases"], cases)
                if name == "three passing":
                    self.assertEqual(out["failing"], 0)

    def test_old_record_runs_are_read(self) -> None:
        repo = self.make_repo({"acceptance": "./run.sh"})
        base = {"at": "2026-10-01T00:00:00Z", "head": "x", "mode": "full", "stale": "skipped", "counted": True,
                "log": "x.log"}
        self.record(repo).parent.mkdir(parents=True)
        self.record(repo).write_text(json.dumps({"runs": [
            {**base, "exit": 1, "failing": 3, "ids": ["t::a", "t::b", "t::c"]},
            {**base, "exit": 0, "failing": 0, "ids": []}]}))
        (repo / "fixture.xml").write_text(junit([("t", "a"), ("t", "b")]))
        (repo / "exit.txt").write_text("1")
        _, out, _ = self.devloop("acceptance", cwd=repo)
        self.assertEqual((out["guard"]["verdict"], out["guard"]["counts"]), ("continue", [2]))

    def test_not_a_git_repo(self) -> None:
        code, out, _ = self.devloop("acceptance", cwd=self.tmp)
        self.assertEqual(code, 1)
        self.assertIn("error", out)



class AcceptanceMainRefTests(RepoCase):
    """`acceptance` runs `commands.<key>` of `dev-loop.json` at the main ref, and only when the worktree agrees."""

    RECORD = {"runs": [{"at": "2026-10-01T00:00:00Z", "head": "x", "mode": "full", "exit": 1, "failing": 1,
                        "ids": ["t::a"], "stale": "skipped", "counted": True, "log": "x.log"}]}

    def linked(self, repo: Path) -> Path:
        wt = self.tmp / "wt"
        git(repo, "worktree", "add", "-q", "-b", "feat/x", str(wt))
        return wt

    def test_refused_runs_nothing(self) -> None:
        both = {"acceptance": "./run.sh", "acceptance_failed": "./run.sh"}
        rows = [
            ("changed", {"acceptance": "./run.sh"}, {"acceptance": "true"}, (), "commands.acceptance"),
            ("absent here", {"acceptance": "./run.sh"}, None, (), "commands.acceptance"),
            ("failed differs", both, {**both, "acceptance_failed": "true"}, ("--failed",), "commands.acceptance_failed"),
            ("absent on main", {"gate": "true"}, {"gate": "true", "acceptance": "./run.sh"}, (), "commands.acceptance"),
            ("commands not an object", "sh x", "sh x", (), "commands.acceptance"),
            ("no main ref", {"acceptance": "./run.sh"}, {"acceptance": "./run.sh"}, (), "main"),
        ]
        for name, main, change, args, needle in rows:
            with self.subTest(name):
                shutil.rmtree(self.tmp)
                self.tmp.mkdir()
                repo = self.make_repo(main)
                wt = self.linked(repo)
                config = {"version": "2.1.1", "harnesses": ["claude"]}
                if change is not None:
                    config["commands"] = change
                (wt / "dev-loop.json").write_text(json.dumps(config))
                if name == "no main ref":
                    git(repo, "branch", "-m", "main", "trunk")
                record = self.record(wt)
                record.parent.mkdir(parents=True, exist_ok=True)
                record.write_text(json.dumps(self.RECORD))
                code, out, _ = self.devloop("acceptance", *args, cwd=wt)
                self.assertEqual(code, 1, out)
                self.assertIn(needle, out["error"])
                self.assertIn("refs/heads/main", out["error"])
                self.assertFalse((wt / "ran.marker").exists())
                logs = record.parent / "logs"
                self.assertEqual(list(logs.glob("*")) if logs.exists() else [], [])
                self.assertEqual(json.loads(record.read_text()), self.RECORD)

    def test_main_command_runs_in_a_linked_worktree(self) -> None:
        repo = self.make_repo({"acceptance": "./run.sh"})
        wt = self.linked(repo)
        (wt / "fixture.xml").write_text(junit([]))
        code, out, _ = self.devloop("acceptance", cwd=wt)
        self.assertEqual(code, 0, out)
        self.assertTrue((wt / "ran.marker").exists())


FAKE_DOCKER = """#!/usr/bin/env python3
import json, os, sys
spec = json.load(open(os.environ["FAKE_DOCKER"]))
args = sys.argv[1:]
key = "config" if "config" in args else "ps" if "ps" in args else "inspect"
if spec.get("fail") == key:
    sys.exit(1)
if key == "config":
    print(json.dumps({"services": spec["services"]}))
elif key == "ps":
    print("\\n".join(spec["ps"].get(args[-1], [])))
else:
    print(json.dumps([c for c in spec["inspect"] if any(c["Id"].startswith(i) for i in args[1:])]))
"""
T0, T1, T2 = "2026-10-01T10:00:00Z", "2026-10-02T10:00:00Z", "2026-10-03T10:00:00Z"
API_ID, DB_ID = "a" * 12 + "1" * 52, "b" * 12 + "2" * 52


class StaleCheckTests(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.make_repo({"acceptance": "./run.sh"}, when=T0)
        (self.repo / "compose.yaml").write_text("services: {}\n")
        (self.repo / "app.py").write_text("x = 2\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "code", when=T1)
        self.code_sha = git(self.repo, "rev-parse", "HEAD")
        bins = self.tmp / "bin"
        bins.mkdir()
        (bins / "docker").write_text(FAKE_DOCKER)
        (bins / "docker").chmod(0o755)
        self.spec_file = self.tmp / "docker.json"
        self.env = {**os.environ, "PATH": f"{bins}{os.pathsep}{os.environ['PATH']}", "FAKE_DOCKER": str(self.spec_file)}

    def docker(self, api: str | None, db: str | None = None, **extra: object) -> None:
        services = {"api": {"build": {"context": "."}}, "db": {"image": "postgres"}}
        ps = {"api": [API_ID[:12]] if api else [], "db": [DB_ID[:12]] if db else []}
        inspect = [{"Id": i, "Created": t} for i, t in ((API_ID, api), (DB_ID, db)) if t]
        self.spec_file.write_text(json.dumps({"services": services, "ps": ps, "inspect": inspect, **extra}))

    def test_no_compose_skipped(self) -> None:
        (self.repo / "compose.yaml").unlink()
        self.assertEqual(stale_check(self.repo, self.env), {"state": "skipped", "reason": "no-compose"})

    def test_compose_file_env_counts(self) -> None:
        (self.repo / "compose.yaml").unlink()
        self.docker(api="2026-10-02T11:00:00Z")
        self.assertEqual(stale_check(self.repo, {**self.env, "COMPOSE_FILE": "x.yaml"})["state"], "fresh")

    def test_no_build_service(self) -> None:
        self.spec_file.write_text(json.dumps({"services": {"db": {"image": "x"}}, "ps": {}, "inspect": []}))
        self.assertEqual(stale_check(self.repo, self.env), {"state": "skipped", "reason": "no-build-service"})

    def test_no_container(self) -> None:
        self.docker(api=None)
        self.assertEqual(stale_check(self.repo, self.env), {"state": "skipped", "reason": "no-container"})

    def test_old_build_container_is_stale(self) -> None:
        self.docker(api="2026-10-02T09:59:59.900Z")
        self.assertEqual(stale_check(self.repo, self.env), {"state": "stale", "checked": ["api"], "services": ["api"],
                                                            "reference": self.code_sha})
        (self.repo / "fixture.xml").write_text(junit([]))
        code, out, _ = self.devloop("acceptance", cwd=self.repo, env=self.env)
        self.assertEqual((code, out["counted"], out["guard"], out["stale"]["state"]), (1, False, None, "stale"))
        runs = json.loads(self.record(self.repo).read_text())["runs"]
        self.assertEqual((runs[-1]["stale"], runs[-1]["counted"]), ("stale", False))

    def test_same_second_is_fresh(self) -> None:
        self.docker(api="2026-10-02T10:00:00.100Z")
        self.assertEqual(stale_check(self.repo, self.env)["state"], "fresh")

    def test_old_image_only_container_fresh(self) -> None:
        self.docker(api="2026-10-02T11:00:00Z", db="2026-09-01T00:00:00Z")
        self.assertEqual(stale_check(self.repo, self.env), {"state": "fresh", "checked": ["api"], "services": [],
                                                            "reference": self.code_sha})

    def test_test_and_doc_commit_not_reference(self) -> None:
        (self.repo / "tests" / "acceptance").mkdir(parents=True)
        (self.repo / "tests" / "acceptance" / "x_test.py").write_text("")
        (self.repo / "README.md").write_text("r\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "tests", when=T2)
        self.docker(api="2026-10-02T11:00:00Z")
        self.assertEqual(stale_check(self.repo, self.env)["state"], "fresh")
        self.assertEqual(stale_check(self.repo, self.env)["reference"], self.code_sha)

    def test_ci_commit_is_reference(self) -> None:
        (self.repo / ".github" / "workflows").mkdir(parents=True)
        (self.repo / ".github" / "workflows" / "x.test.yml").write_text("on: push\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "ci", when=T2)
        self.docker(api="2026-10-02T11:00:00Z")
        out = stale_check(self.repo, self.env)
        self.assertEqual((out["state"], out["reference"]), ("stale", git(self.repo, "rev-parse", "HEAD")))

    def test_non_ascii_test_path_not_reference(self) -> None:
        (self.repo / "tests" / "acceptance").mkdir(parents=True)
        (self.repo / "tests" / "acceptance" / "café_test.py").write_text("")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "tests", when=T2)
        self.docker(api="2026-10-02T11:00:00Z")
        out = stale_check(self.repo, self.env)
        self.assertEqual((out["state"], out["reference"]), ("fresh", self.code_sha))

    def test_docker_fails_error(self) -> None:
        for step in ("config", "ps", "inspect"):
            self.docker(api="2026-10-02T11:00:00Z", fail=step)
            out = stale_check(self.repo, self.env)
            self.assertEqual(out["state"], "error", step)
            self.assertTrue(out["message"])
        self.docker(api="2026-10-02T11:00:00Z", fail="inspect")
        (self.repo / "fixture.xml").write_text(junit([]))
        _, out, _ = self.devloop("acceptance", cwd=self.repo, env=self.env)
        self.assertEqual((out["stale"]["state"], out["counted"]), ("error", False))

    def test_bad_time_error(self) -> None:
        self.docker(api="yesterday")
        self.assertEqual(stale_check(self.repo, self.env)["state"], "error")

    def test_short_ids_match_full_ids(self) -> None:
        self.docker(api="2026-10-02T11:00:00Z", db="2026-10-02T11:00:00Z")
        self.assertEqual(stale_check(self.repo, self.env)["checked"], ["api"])

    def test_nine_digit_fraction_with_offset(self) -> None:
        self.assertEqual(created_seconds("2026-10-26T10:00:00.123456789+02:00"),
                         int(datetime(2026, 10, 26, 8, tzinfo=timezone.utc).timestamp()))
        self.assertEqual(created_seconds("2026-10-26T08:00:00Z"),
                         int(datetime(2026, 10, 26, 8, tzinfo=timezone.utc).timestamp()))
        with self.assertRaises(ValueError):
            created_seconds("2026-10-26 08:00:00")





class AcceptanceScriptTests(TempDir):
    def test_script_writes_both_reports(self) -> None:
        folder, reports = self.tmp / "suite", self.tmp / "reports"
        folder.mkdir()
        reports.mkdir()
        (folder / "test_one.py").write_text(
            "import unittest\n\n\nclass One(unittest.TestCase):\n    def test_ok(self):\n        pass\n\n"
            "    def test_bad(self):\n        self.assertEqual(1, 2)\n")
        (folder / "one.test.mjs").write_text("import test from 'node:test';\ntest('passes', () => {});\n")
        result = subprocess.run(["bash", str(ROOT / "scripts" / "acceptance.sh"), str(folder)], cwd=self.tmp,
                                capture_output=True, text=True, env={**os.environ, "DEVLOOP_REPORT_DIR": str(reports), "DEVLOOP_SUITE_LOCK_HELD": "1"})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        python = {c.get("name"): c for c in ET.parse(reports / "acceptance-python.xml").getroot().iter("testcase")}
        self.assertEqual(set(python), {"test_ok", "test_bad"})
        self.assertEqual(python["test_bad"].get("classname"), "test_one.One")
        self.assertIsNotNone(python["test_bad"].find("failure"))
        self.assertIsNone(python["test_ok"].find("failure"))
        node = list(ET.parse(reports / "acceptance-node.xml").getroot().iter("testcase"))
        self.assertEqual([c.get("name") for c in node], ["passes"])

    def test_script_discovers_files_named_like_the_unit_modules(self) -> None:
        folder, reports = self.tmp / "suite", self.tmp / "reports"
        folder.mkdir()
        reports.mkdir()
        ok = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n"
        (folder / "test_devloop.py").write_text(ok)
        (folder / "test_seal.py").write_text(ok + "\n    def test_bad(self):\n        self.assertEqual(1, 2)\n")
        (folder / "one.test.mjs").write_text("import test from 'node:test';\ntest('passes', () => {});\n")
        result = subprocess.run(["bash", str(ROOT / "scripts" / "acceptance.sh"), str(folder)], cwd=self.tmp,
                                capture_output=True, text=True, env={**os.environ, "DEVLOOP_REPORT_DIR": str(reports), "DEVLOOP_SUITE_LOCK_HELD": "1"})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        python = {(c.get("classname"), c.get("name")): c
                  for c in ET.parse(reports / "acceptance-python.xml").getroot().iter("testcase")}
        self.assertEqual(set(python), {("test_devloop.T", "test_ok"), ("test_seal.T", "test_ok"),
                                       ("test_seal.T", "test_bad")})
        self.assertIsNotNone(python[("test_seal.T", "test_bad")].find("failure"))

    def test_script_without_report_dir_passes(self) -> None:
        folder = self.tmp / "suite"
        folder.mkdir()
        (folder / "test_one.py").write_text("import unittest\n\n\nclass One(unittest.TestCase):\n"
                                            "    def test_ok(self):\n        pass\n")
        (folder / "one.test.mjs").write_text("import test from 'node:test';\ntest('passes', () => {});\n")
        env = {k: v for k, v in os.environ.items() if k != "DEVLOOP_REPORT_DIR"}
        result = subprocess.run(["bash", str(ROOT / "scripts" / "acceptance.sh"), str(folder)], cwd=self.tmp,
                                capture_output=True, text=True, env={**env, "DEVLOOP_SUITE_LOCK_HELD": "1"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list(self.tmp.glob("*.xml")), [])

    def test_config_and_ci_run_the_script(self) -> None:
        config = json.loads((ROOT / "dev-loop.json").read_text())
        self.assertEqual(config["commands"]["acceptance"], "scripts/acceptance.sh")
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        self.assertIn("run: scripts/acceptance.sh\n", ci)
        self.assertNotIn("unittest discover -s tests/acceptance", ci)



def section(text: str, start: str, end: str) -> str:
    return text[text.index(start):text.index(end, text.index(start))]


class FeatureGateTextTests(unittest.TestCase):
    def test_skill_stage_6(self) -> None:
        stage6 = section((ROOT / "skill" / "SKILL.md").read_text(), "## 6 Feature gate", "## 7 Ship")
        for needle in (
        "devloop.py review-merge", "devloop.py acceptance", "--force-recreate", "`stale`",
        "`file`, `line`, `finding`, `class`, `verdict`", "`counted` false, `guard` null", "`cases` > 0",
        "changed by the branch", "`cases` 0: never green", "under the case floor",
        "never green: the user rules. Never count by hand", "new seal"):
            self.assertIn(needle, stage6)
        contract = (ROOT / "reference" / "cli" / "acceptance.md").read_text()
        for needle in ("`floor`", "`tests`", "`short`", "`floor_head`", "HEAD:tests/acceptance", "`suite`", "`reason` `short`"):
            self.assertIn(needle, contract)
        self.assertNotIn("Merge the tables, dedupe by file and line", stage6)
        self.assertNotIn("docker compose ps --format", stage6)
        self.assertNotIn("up -d --build --wait", stage6)

    def test_lf_only_when_acceptance_absent(self) -> None:
        stage6 = section((ROOT / "skill" / "SKILL.md").read_text(), "## 6 Feature gate", "## 7 Ship")
        texts = [stage6] + [(ROOT / "skill" / "references" / n).read_text() for n in ("claude.md", "codex.md")]
        for text in texts:
            hits = [x for x in text.split(". ") if "--lf" in x or "--last-failed" in x]
            self.assertTrue(hits)
            for sentence in hits:
                self.assertIn("`commands.acceptance` absent", sentence)

    def test_skill_stage_0(self) -> None:
        stage0 = section((ROOT / "skill" / "SKILL.md").read_text(), "## 0 Bootstrap", "## 1 Frame")
        self.assertIn("DEVLOOP_REPORT_DIR", stage0)
        self.assertIn("commands.stack_reset", stage0)
        self.assertIn("recreate", stage0)

    def test_routes(self) -> None:
        claude = (ROOT / "skill" / "references" / "claude.md").read_text()
        codex = (ROOT / "skill" / "references" / "codex.md").read_text()
        for needle in ("review-merge", "review-save --reviewer", "devloop.py acceptance", "run_in_background"):
            self.assertIn(needle, claude)
        for needle in ("review-merge", "review-save --reviewer", "devloop.py acceptance"):
            self.assertIn(needle, codex)
        self.assertNotIn("Monitor", codex)
        self.assertNotIn("verdict or severity", claude + codex)

    def test_repo_docs(self) -> None:
        agents, readme = (ROOT / "AGENTS.md").read_text(), (ROOT / "README.md").read_text()
        for needle in ("review-merge", "acceptance [--failed]", "scripts/acceptance.sh"):
            self.assertIn(needle, agents)
            self.assertIn(needle, readme)
        self.assertIn("feature_gate.py", agents)
        self.assertIn("test_feature_gate.py", (ROOT / "tests" / "unit" / "python" / "README.md").read_text())


if __name__ == "__main__":
    unittest.main()
