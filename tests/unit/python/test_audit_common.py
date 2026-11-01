#!/usr/bin/env python3
"""Tests for audit/common.py: the shared core of `audit-run`: ids, refs, paths, scope, ledger write."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "skill" / "scripts"))
sys.path.insert(1, str(HERE))
from audit import common  # noqa: E402
from audit_fixtures import stub_skill, validator_calls, validator_exit  # noqa: E402
from seal import ACCEPTANCE_DIR  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def unit(coverage_id: str) -> dict:
    return {"coverage_id": coverage_id, "status": "planned"}


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class AuditCommonTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_coverage_id(self) -> None:
        refs = {"surface": "a/b.py#x y", "boundary": "B", "subsystem": "S", "attack_class": "ATTACK-CLASSES.md#Access control"}
        self.assertEqual(common.coverage_id(refs), "a%2Fb.py%23x%20y::B::S::ATTACK-CLASSES.md%23Access%20control")
        self.assertEqual(common.coverage_id({**refs, "lifecycle": "é~"}), common.coverage_id(refs) + "::%C3%A9~")

    def test_ref_errors(self) -> None:
        for bad in ["é", " a", "a ", "a\u0007b", "a​b", "", "ㅤ", "️", 3, None]:
            self.assertTrue(common.ref_errors(bad), repr(bad))
        self.assertEqual(common.ref_errors("café #x"), [])

    def test_path_error(self) -> None:
        for bad in ["/x", "../x", "a/../b", "a//b", "./a", ".", "", "a\0b", "a\\b", 7]:
            self.assertIsNotNone(common.path_error(bad), repr(bad))
        for good in ["a.py", "src/", "d i/é.py"]:
            self.assertIsNone(common.path_error(good), good)

    def test_in_scope(self) -> None:
        whole = {"kind": "whole", "paths": None, "exclude": ["k/x.py"]}
        self.assertTrue(common.in_scope(whole, ["a.py"]))
        self.assertFalse(common.in_scope(whole, ["k/x.py"]))
        self.assertFalse(common.in_scope(whole, [ACCEPTANCE_DIR + "/t.py"]))
        self.assertTrue(common.in_scope(whole, ["k/x.py", "a.py"]))
        diff = {"kind": "diff", "paths": ["src/a.py"], "exclude": []}
        for path, want in [("src/", True), ("src", True), ("src/a.py", True), ("sr", False), ("c.py", False)]:
            self.assertEqual(common.in_scope(diff, [path]), want, path)

    def test_write_ledger_validates_a_temp_file_first(self) -> None:
        run, skill = self.root / "run", self.root / "skill"
        run.mkdir()
        stub_skill(skill)
        (run / "coverage-ledger.json").write_bytes(b"[]\n")
        validator_exit(skill, "validate-coverage-ledger.cjs", 1)
        with self.assertRaises(common.StepError) as caught:
            common.write_ledger(run, skill, [unit("a")])
        self.assertEqual(caught.exception.payload["error"], "ledger-invalid")
        self.assertIn("stub failure", caught.exception.payload["tail"])
        self.assertEqual(sorted(p.name for p in run.iterdir()), ["coverage-ledger.json"])
        self.assertEqual((run / "coverage-ledger.json").read_bytes(), b"[]\n")
        validator_exit(skill, "validate-coverage-ledger.cjs", 0)
        common.write_ledger(run, skill, [unit("b"), unit("a")])
        self.assertEqual([u["coverage_id"] for u in json.loads((run / "coverage-ledger.json").read_text())], ["a", "b"])
        self.assertEqual(sorted(p.name for p in run.iterdir()), ["coverage-ledger.json"])
        self.assertTrue(all(Path(call).parent == run for call in validator_calls(skill, "validate-coverage-ledger.cjs")))

    def test_companion_files(self) -> None:
        units = [{"selected_companion_blocks": ["AI-AND-LLM.md#Core discipline", "WEB.md#X"]},
                 {"selected_companion_blocks": ["AI-AND-LLM.md#Validation rules"]}]
        self.assertEqual(common.companion_files(units), ["AI-AND-LLM.md", "WEB.md"])

    def test_run_folder(self) -> None:
        notes = self.root / "notes"
        for name in ("Security Audit/Run 2", "Security Audit/Run x", "Other/Run 2"):
            (notes / name).mkdir(parents=True)
        old = os.environ.get("DEV_LOOP_NOTES")
        os.environ["DEV_LOOP_NOTES"] = str(notes)
        self.addCleanup(lambda: os.environ.pop("DEV_LOOP_NOTES") if old is None else os.environ.update(DEV_LOOP_NOTES=old))
        self.assertEqual(common.run_folder(self.root, str(notes / "Security Audit" / "Run 2")),
                         (notes / "Security Audit" / "Run 2").resolve())
        for bad in ("Other/Run 2", "Security Audit/Run x", "Security Audit/Run 9"):
            with self.assertRaises(common.StepError) as caught:
                common.run_folder(self.root, str(notes / bad))
            self.assertEqual(caught.exception.payload["error"], "bad-run", bad)

    def test_write_json_atomic_leaves_one_file(self) -> None:
        common.write_json_atomic(self.root / "x.json", {"a": "é"})
        self.assertEqual([p.name for p in self.root.iterdir()], ["x.json"])
        self.assertEqual(json.loads((self.root / "x.json").read_text()), {"a": "é"})

    def test_git_paths_keeps_spaces_and_non_ascii(self) -> None:
        repo = self.root / "repo"
        repo.mkdir()

        def git(*args: str) -> str:
            done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
                                  env={**os.environ, **GIT_ENV})
            return done.stdout.strip()

        git("init", "-q", "-b", "main")
        (repo / "d i").mkdir()
        (repo / "d i" / "é.py").write_text("1")
        git("add", "-A")
        git("commit", "-q", "-m", "a")
        a = git("rev-parse", "HEAD")
        (repo / "d i" / "é.py").write_text("2")
        git("commit", "-q", "-am", "b")
        b = git("rev-parse", "HEAD")
        self.assertEqual(common.git_paths(repo, "diff", "--name-only", a, b), ["d i/é.py"])
        self.assertIsNone(common.git_paths(repo, "diff", "--name-only", "nope", b))


if __name__ == "__main__":
    unittest.main()
