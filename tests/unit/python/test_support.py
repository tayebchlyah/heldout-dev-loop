#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent

PROBE = f"""
import json, sys
sys.path.insert(0, {str(HERE)!r})
import support
eager = "check_setup" in vars(support)
loaded = support.check_setup.__file__
from support import check_setup as c
try:
    getattr(support, "nope")
    other = False
except AttributeError:
    other = True
print(json.dumps([eager, loaded, str(support.CHECKER), c is support.check_setup, other]))
"""


class LazyCheckSetupTests(unittest.TestCase):
    def test_check_setup_loads_on_first_use(self) -> None:
        run = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        eager, loaded, checker, same, other = json.loads(run.stdout)
        self.assertFalse(eager)
        self.assertEqual(loaded, checker)
        self.assertTrue(same)
        self.assertTrue(other)


PROBE2 = f"""
import json, sys
sys.path.insert(0, {str(HERE)!r})
import support
a = support.sandbox_template()
b = support.sandbox_template()
print(json.dumps([str(a), str(b), (a / "repo" / ".git").is_dir(), (a / "origin.git").is_dir()]))
"""


class SandboxTemplateTests(unittest.TestCase):
    def test_a_copy_matches_a_fresh_build_and_leaves_the_template_alone(self) -> None:
        import support
        from support import git_in
        with tempfile.TemporaryDirectory() as tmp:
            template = support.build_sandbox_template(Path(tmp, "t"))
            with mock.patch.dict(os.environ, {support.TEMPLATE_ENV: str(Path(tmp, "t"))}):
                root = Path(tmp, "root").resolve()
                root.mkdir()
                repo, origin = support.copy_sandbox(root)
            self.assertEqual((repo, origin), (root / "repo", root / "origin.git"))
            self.assertEqual(git_in(repo, "status", "--porcelain"), "")
            self.assertEqual(git_in(repo, "branch", "--show-current"), "main\n")
            self.assertEqual(git_in(repo, "log", "--format=%s"), "base\n")
            self.assertEqual(git_in(repo, "remote", "get-url", "origin"), f"{origin}\n")
            self.assertEqual(git_in(repo, "rev-parse", "origin/main"), git_in(repo, "rev-parse", "HEAD"))
            self.assertEqual((repo / "GLOSSARY.md").read_text(), "# Glossary\n")
            head = git_in(template / "repo", "rev-parse", "HEAD")
            bare = git_in(template, "--git-dir", "origin.git", "rev-parse", "main")
            (repo / "x").write_text("x\n")
            git_in(repo, "add", "x")
            git_in(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "x")
            git_in(repo, "push", "-q", "origin", "main")
            self.assertEqual(git_in(template / "repo", "rev-parse", "HEAD"), head)
            self.assertEqual(git_in(template, "--git-dir", "origin.git", "rev-parse", "main"), bare)
            self.assertEqual(git_in(root, "--git-dir", "origin.git", "rev-parse", "main"),
                             git_in(repo, "rev-parse", "HEAD"))

    def test_the_template_comes_from_the_env_or_once_per_process(self) -> None:
        import support
        env = {k: v for k, v in os.environ.items() if k != support.TEMPLATE_ENV}
        run = subprocess.run([sys.executable, "-c", PROBE2], capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(run.returncode, 0, run.stderr)
        a, b, repo, origin = json.loads(run.stdout)
        self.assertEqual(a, b)
        self.assertTrue(repo and origin)
        self.assertFalse(Path(a).exists())
        with tempfile.TemporaryDirectory() as tmp:
            given = Path(tmp, "given")
            run = subprocess.run([sys.executable, "-c", PROBE2], capture_output=True, text=True, timeout=60,
                                 env={**env, support.TEMPLATE_ENV: str(given)})
            self.assertEqual(run.returncode, 0, run.stderr)
            a, b, repo, origin = json.loads(run.stdout)
            self.assertEqual(a, str(given.resolve()))
            self.assertFalse(given.exists())


if __name__ == "__main__":
    unittest.main()
