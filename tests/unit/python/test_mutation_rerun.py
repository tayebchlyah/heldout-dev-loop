#!/usr/bin/env python3
"""Tests for `devloop.py mutation --entry` and `--only` (rerun) and the integrity check."""

from __future__ import annotations

import json
import shlex
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from mutation_fixtures import commit, make_repo, run_mutation  # noqa: E402
from support import git_in, write  # noqa: E402

GAP = {"file": "core/a.py", "start": 1, "end": 1, "reason": "r"}


def entry(path: str) -> dict:
    return {"path": path, "command": "./adapt", "critical": ["core/**"]}


def adapter(record: Path, units: list[str], mutants: list[dict] | None = None, before: str = "") -> str:
    full = json.dumps({"units": units, "mutants": mutants or [], "not_covered": [GAP]})
    return (f"#!/bin/sh\ninput=$(cat)\nprintf '%s\\n' \"$input\" >> {shlex.quote(str(record))}\n{before}"
            f"case \"$input\" in\n*'\"run\": false'*) printf '%s\\n' {shlex.quote(json.dumps({'units': units}))} ;;\n"
            f"*) printf '%s\\n' {shlex.quote(full)} ;;\nesac\n")


def calls(record: Path) -> list[dict]:
    return [json.loads(line) for line in record.read_text().splitlines()] if record.exists() else []


class MutationRerun(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.count = 0

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def record(self) -> Path:
        self.count += 1
        return self.root / f"record{self.count}"

    def repo(self, entries: list[dict], base: dict[str, str], change: dict[str, str | None]) -> tuple[Path, str]:
        self.count += 1
        folder = self.root / f"case{self.count}"
        folder.mkdir()
        data = {"version": "0.0.0", "mutation": entries}
        repo = make_repo(folder, {"dev-loop.json": json.dumps(data), **base})
        return repo, commit(repo, change)

    def two(self, a: Path, b: Path, units: list[str] = ["u1"]) -> tuple[Path, str]:
        return self.repo([entry("app/"), entry("b/")],
                         {"app/adapt": adapter(a, units), "b/adapt": adapter(b, units), "app/core/a.py": "a\n",
                          "b/core/a.py": "a\n"}, {"app/core/a.py": "b\n", "b/core/a.py": "b\n"})

    def test_only_needs_entry(self) -> None:
        a, b = self.record(), self.record()
        repo, before = self.two(a, b)
        for args in (("--run", "--only", "u1"), ("--entry", "nope/"), ("--run", "--entry", "nope/", "--only", "u1")):
            with self.subTest(args):
                code, out, _ = run_mutation(repo, "--head", "HEAD", "--base", before, *args)
                self.assertEqual(code, 2)
                self.assertIn("error", out)
        self.assertEqual((calls(a), calls(b)), ([], []))

    def test_only_runs_one_entry(self) -> None:
        a, b = self.record(), self.record()
        repo, before = self.two(a, b)
        code, out, _ = run_mutation(repo, "--run", "--head", "HEAD", "--base", before, "--entry", "app/",
                                    "--only", "u1")
        self.assertEqual((code, out["status"]), (0, "pass"))
        self.assertEqual(calls(b), [])
        self.assertEqual([(c["run"], c["only"], c["files"]) for c in calls(a)],
                         [(False, ["u1"], {"core/a.py": [[1, 1]]}), (True, ["u1"], {"core/a.py": [[1, 1]]})])
        self.assertEqual([e["path"] for e in out["entries"]], ["app/"])

    def test_only_unknown_unit(self) -> None:
        record = self.record()
        repo, before = self.repo([entry("app/")], {"app/adapt": adapter(record, ["u2"]), "app/core/a.py": "a\n"},
                                 {"app/core/a.py": "b\n"})
        code, out, _ = run_mutation(repo, "--run", "--head", "HEAD", "--base", before, "--only", "u1")
        self.assertEqual(code, 2)
        self.assertIn("u1", out["error"])
        self.assertEqual([c["run"] for c in calls(record)], [False])
        record = self.record()
        wrong = [{"unit": "u2", "file": "core/a.py", "line": 1, "status": "survived", "diff": None}]
        repo, before = self.repo([entry("app/")],
                                 {"app/adapt": adapter(record, ["u1", "u2"], wrong), "app/core/a.py": "a\n"},
                                 {"app/core/a.py": "b\n"})
        code, out, _ = run_mutation(repo, "--run", "--head", "HEAD", "--base", before, "--only", "u1")
        self.assertEqual((code, out["status"]), (1, "setup"))
        self.assertIn("only", out["entries"][0]["cause"])

    def test_only_no_selected_file(self) -> None:
        record = self.record()
        repo, before = self.repo([entry("app/")], {"app/adapt": adapter(record, ["u1"]), "app/web/b.py": "a\n"},
                                 {"app/web/b.py": "b\n"})
        for args in (("--run",), ()):
            with self.subTest(args):
                code, out, _ = run_mutation(repo, *args, "--head", "HEAD", "--base", before, "--entry", "app/",
                                            "--only", "u1")
                self.assertEqual(code, 2)
                self.assertIn("error", out)
        self.assertEqual(calls(record), [])

    def test_selection_with_units(self) -> None:
        a, b = self.record(), self.record()
        repo, before = self.two(a, b, ["u1", "u3"])
        code, out, _ = run_mutation(repo, "--head", "HEAD", "--base", before, "--entry", "app/", "--only", "u1")
        self.assertEqual(code, 0)
        self.assertEqual(out["entries"], [{"path": "app/", "command": "./adapt", "files": {"core/a.py": [[1, 1]]},
                                           "units": ["u1", "u3"]}])
        self.assertEqual(([c["run"] for c in calls(a)], calls(b)), ([False], []))

    def test_working_tree_config(self) -> None:
        record = self.record()
        a = {"path": "a/", "command": "./adapt", "critical": ["core/**"]}
        repo, before = self.repo([a], {"a/adapt": adapter(record, ["u1"]), "a/core/a.py": "a\n", "b/x": "x\n"},
                                 {"a/core/a.py": "b\n"})
        write(repo / "dev-loop.json", json.dumps({"version": "0.0.0", "mutation": [{**a, "path": "b/"}]}))
        code, out, _ = run_mutation(repo, "--run", "--head", "HEAD", "--base", before)
        self.assertEqual((code, out["status"], out["entries"][0]["status"]), (1, "failed", "failed"))
        self.assertIn("dev-loop.json mutation", out["entries"][0]["cause"])
        self.assertIn("the working tree", out["entries"][0]["cause"])
        self.assertEqual(calls(record), [])

    def test_branch_differs_from_main(self) -> None:
        def changed_config(record: Path) -> dict:
            return {"dev-loop.json": json.dumps({"version": "0.0.0", "mutation": [
                {**entry("app/"), "critical": ["core/**", "x/**"]}]})}

        def changed_adapter(record: Path) -> dict:
            return {"app/adapt": adapter(record, ["u1"]) + "true\n"}

        def edit_file(repo: Path) -> None:
            write(repo / "app/core/a.py", "c\n")

        def drop_folder(repo: Path) -> None:
            shutil.rmtree(repo / "app")

        def new_test(repo: Path) -> None:
            write(repo / "app/tests/test_new.py", "t\n")

        rows = [
            ("mutation differs", changed_config, None, "dev-loop.json mutation", "origin/main"),
            ("adapter differs", changed_adapter, None, "app/adapt", "origin/main"),
            ("selected file edited", None, edit_file, "app/core/a.py", "the working tree"),
            ("entry folder gone", None, drop_folder, "app/", "the working tree"),
            ("new test file", None, new_test, None, None),
        ]
        for name, branch, tree, path, side in rows:
            with self.subTest(name):
                record = self.record()
                repo, before = self.repo([entry("app/")], {"app/adapt": adapter(record, ["u1"]),
                                                           "app/core/a.py": "a\n"},
                                         {"app/core/a.py": "b\n", **(branch(record) if branch else {})})
                git_in(repo, "update-ref", "refs/remotes/origin/main", before)
                git_in(repo, "branch", "-f", "main", "HEAD")
                if tree:
                    tree(repo)
                code, out, _ = run_mutation(repo, "--run", "--head", "HEAD", "--base", before)
                if path is None:
                    self.assertEqual((code, out["status"]), (0, "pass"))
                    self.assertEqual([c["run"] for c in calls(record)], [True])
                    continue
                self.assertEqual((code, out["status"], out["entries"][0]["mutants"]), (1, "failed", 0))
                self.assertIn(path, out["entries"][0]["cause"])
                self.assertIn(side, out["entries"][0]["cause"])
                self.assertEqual(calls(record), [])
        a, b = self.record(), self.record()
        edit = "printf 'true\\n' >> ./adapt\n"
        repo, before = self.repo([entry("app/"), entry("b/")],
                                 {"app/adapt": adapter(a, ["u1"], before=edit), "b/adapt": adapter(b, ["u1"]),
                                  "app/core/a.py": "a\n", "b/core/a.py": "a\n"},
                                 {"app/core/a.py": "b\n", "b/core/a.py": "b\n"})
        code, out, _ = run_mutation(repo, "--run", "--head", "HEAD", "--base", before)
        self.assertEqual((code, out["status"]), (1, "failed"))
        self.assertEqual([e["status"] for e in out["entries"]], ["failed", "failed"])
        self.assertIn("app/adapt", out["entries"][1]["cause"])
        self.assertEqual(([c["run"] for c in calls(a)], calls(b)), ([True], []))


if __name__ == "__main__":
    unittest.main()
