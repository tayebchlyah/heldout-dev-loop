#!/usr/bin/env python3
"""Tests for scripts/repo_rules.py (comments, subjects), the AGENTS.md budget and its CLI index, the gate scope."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts"))
from test_devloop import git, put  # noqa: E402
import repo_rules  # noqa: E402

RULES = ROOT / "scripts" / "repo_rules.py"
EMOJIS = repo_rules.gitmoji(ROOT)


def run_rules(cwd: Path, *args: str) -> tuple[int, dict]:
    result = subprocess.run([sys.executable, str(RULES), *args], cwd=cwd, capture_output=True, text=True, timeout=60)
    try:
        return result.returncode, json.loads(result.stdout)
    except ValueError:
        raise AssertionError(f"stdout is not one JSON object: {result.stdout!r} {result.stderr!r}")


class RulesRepo(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name).resolve()
        self.repo = tmp / "repo"
        self.repo.mkdir()
        git(tmp, "init", "-q", "--bare", "origin.git")
        git(self.repo, "init", "-q")
        git(self.repo, "remote", "add", "origin", str(tmp / "origin.git"))
        put(self.repo / "cliff.toml", (ROOT / "cliff.toml").read_text())
        put(self.repo / "README.md", "readme\n")
        self.base_files()
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "✨(repo) Start")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "checkout", "-q", "-b", "feat/x")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def base_files(self) -> None:
        pass

    def commit(self, subject: str, path: str = "a.txt", text: str | None = None) -> None:
        put(self.repo / path, text if text is not None else subject + "\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", subject)

    def head(self) -> str:
        return git(self.repo, "rev-parse", "HEAD").strip()


class SubjectRuleTests(unittest.TestCase):
    def test_subject_table(self) -> None:
        for subject, ok in [
            ("✨(skill) Add x", True), ("⚡️(test) Run x", True), ("🧑‍💻(dev) x", True), ("⏪️(codex) Revert x", True),
            ("⚡(test) Run the skill suite in parallel, one process per CPU", False),
            ("⏪(codex) Revert x", False), ("🐛 fix: x", False), ("✨(Skill) Add x", False),
            ("✨(skill)Add x", False), ("✨(skill)  ", False), ("✨ fix(x) y", False), ("✨(-x) y", False),
            ("fix(x): y", False),
        ]:
            with self.subTest(subject=subject):
                self.assertIs(repo_rules.subject_ok(subject, EMOJIS), ok)

    def test_gitmoji_reads_each_parser_group(self) -> None:
        self.assertIn("⚡️", EMOJIS)
        self.assertIn("🧑‍💻", EMOJIS)
        self.assertNotIn("⚡", EMOJIS)


class SubjectCommandTests(RulesRepo):
    def test_older_bad_subjects_are_warnings_oldest_first(self) -> None:
        base = self.head()
        for i, subject in enumerate(["⚡(test) Run x", "⏪(codex) Revert x", "🐛 fix: x", "✨(skill) Add x"]):
            self.commit(subject, f"f{i}.txt")
        code, out = run_rules(self.repo, "subjects")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["ok"], True)
        self.assertEqual(out["base"], base)
        self.assertEqual(out["violations"], [])
        self.assertEqual([w["subject"] for w in out["warnings"]], ["⚡(test) Run x", "⏪(codex) Revert x", "🐛 fix: x"])

    def test_bad_head_subject_fails(self) -> None:
        for subject in ["⚡(test) Run x", "⏪(codex) Revert x", "✨(Skill) Add x", "✨(skill)Add x"]:
            with self.subTest(subject=subject):
                self.commit(subject, "b.txt")
                code, out = run_rules(self.repo, "subjects")
                self.assertEqual(code, 1, out)
                self.assertEqual(out["violations"], [{"commit": self.head(), "subject": subject}])
                git(self.repo, "reset", "-q", "--hard", "HEAD~1")

    def test_merge_head_is_not_checked(self) -> None:
        self.commit("✨(skill) Add a", "a.txt")
        git(self.repo, "checkout", "-q", "-b", "side", "main")
        self.commit("✨(skill) Add b", "b.txt")
        git(self.repo, "checkout", "-q", "feat/x")
        git(self.repo, "merge", "-q", "--no-ff", "-m", "Merge side", "side")
        code, out = run_rules(self.repo, "subjects")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["violations"], [])

    def test_no_cliff_toml_is_an_error(self) -> None:
        (self.repo / "cliff.toml").unlink()
        self.commit("✨(skill) Add x")
        code, out = run_rules(self.repo, "subjects")
        self.assertEqual(code, 1)
        self.assertIn("error", out)

    def test_bad_cliff_toml_is_an_error(self) -> None:
        self.commit("✨(skill) Add x", "cliff.toml", "[git\n")
        code, out = run_rules(self.repo, "subjects")
        self.assertEqual(code, 1)
        self.assertIn("error", out)

    def test_no_base(self) -> None:
        git(self.repo, "remote", "remove", "origin")
        git(self.repo, "branch", "-q", "-m", "main", "trunk")
        for command in ("subjects",):
            with self.subTest(command=command):
                code, out = run_rules(self.repo, command)
                self.assertEqual(code, 0, out)
                self.assertEqual(out, {"ok": True, "base": None, "skipped": "no-base", "violations": []})

    def test_usage_errors(self) -> None:
        for args in (["nope"], ["subjects", "--bad"], []):
            with self.subTest(args=args):
                self.assertEqual(subprocess.run([sys.executable, str(RULES), *args], cwd=self.repo,
                                                capture_output=True).returncode, 2)
        with tempfile.TemporaryDirectory() as outside:
            code, out = run_rules(Path(outside), "subjects")
            self.assertEqual(code, 2)
            self.assertIn("error", out)


class RepoSubjectsTests(unittest.TestCase):
    def test_this_repo(self) -> None:
        code, out = run_rules(ROOT, "subjects")
        self.assertEqual(code, 0, out)
        self.assertTrue(out["ok"])


if __name__ == "__main__":
    unittest.main()
