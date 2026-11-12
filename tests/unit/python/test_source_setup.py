"""source_dirs.setup: the check_setup.py failure, user action and notice lines of the `source_dirs` key."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from source_fixtures import key, set_branch, set_main, source_repo
from support import CHECKER, checker_env, git_in, make_git_repo, write

import source_dirs

HELD = "tests/" + "acceptance/"
DEFAULT_LIST = "src/ app/ backend/ frontend/"


class SourceSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def check(self, repo: Path, harness: str) -> tuple[str, str]:
        out = subprocess.run([sys.executable, str(CHECKER), "--harness", harness, "--home", str(self.root / "home"),
                              "--repo", str(repo)], capture_output=True, text=True, check=False,
                             env=checker_env(self.root / "bin")).stdout
        return out.splitlines()[0], out.partition("User actions required")[2]

    def test_missing_key_lists_the_folders(self) -> None:
        repo = source_repo(self.root)
        for name in ("tests/unit/test_y.py", "README.md", "setup.py", ".github/workflows/ci.yml"):
            write(repo / name, "x\n")
        git_in(repo, "add", "-A")
        git_in(repo, "commit", "-q", "-m", "more")
        failures, actions, notices = source_dirs.setup(repo)
        self.assertEqual((failures, notices), ([], []))
        self.assertEqual(actions, [
            f"source_dirs missing in dev-loop.json: the spec-tester wall uses {DEFAULT_LIST}. Top-level folders: "
            "api/ cmd/ docs/ internal/ pkg/ scripts/ src/ tests/. List in \"source_dirs\" each folder that holds "
            "implementation. An entry matches from the repo root only"])
        for harness in ("claude", "codex"):
            with self.subTest(harness=harness):
                verdict, after = self.check(repo, harness)
                self.assertEqual(verdict, f"{harness}: FAIL")
                self.assertIn("Top-level folders: api/ cmd/", after)

    def test_no_folder_no_line(self) -> None:
        repo = make_git_repo(self.root, {"README.md": "x\n", "dev-loop.json": "{}", ".github/workflows/ci.yml": "x\n"})
        self.assertEqual(source_dirs.setup(repo), ([], [], []))

    def test_pending_change_is_a_notice(self) -> None:
        repo = source_repo(self.root)
        set_branch(repo, key(["internal/"]))
        notice = f"the spec-tester wall at main uses {DEFAULT_LIST} until this dev-loop.json change reaches main"
        self.assertEqual(source_dirs.setup(repo), ([], [], [notice]))
        before = {harness: self.check(repo, harness) for harness in ("claude", "codex")}
        set_main(repo, key(["internal/"]))
        self.assertEqual(source_dirs.setup(repo), ([], [], []))
        for harness, (verdict, after) in before.items():
            with self.subTest(harness=harness):
                self.assertIn(notice, after)
                self.assertEqual(verdict, self.check(repo, harness)[0])

    def test_branch_before_the_key(self) -> None:
        repo = source_repo(self.root)
        set_main(repo, key(["internal/"]))
        self.assertEqual(source_dirs.setup(repo), ([], [], [
            "no source_dirs in this checkout: the spec-tester wall at main uses internal/. Rebase onto main"]))
        set_main(repo, key([]))
        self.assertEqual(source_dirs.setup(repo)[2], [
            "no source_dirs in this checkout: the spec-tester wall at main uses no folder. Rebase onto main"])

    def test_main_ref_lines(self) -> None:
        repo = source_repo(self.root)
        set_branch(repo, key(["internal/"]))
        set_main(repo, "[]")
        self.assertEqual(source_dirs.setup(repo), ([
            "dev-loop.json at main is not a JSON object. Every spec-tester call is denied until a valid file reaches main"],
            [], []))
        set_main(repo, key("internal/"))
        failures, actions, notices = source_dirs.setup(repo)
        self.assertEqual((len(failures), actions, notices), (1, [], []))
        self.assertTrue(failures[0].startswith("source_dirs at main is invalid: "), failures)
        self.assertTrue(failures[0].endswith(". Every spec-tester call is denied until a valid value reaches main"))

    def test_invalid_checkout_values(self) -> None:
        repo = source_repo(self.root)
        invalid = [["internal"], ["/internal/"], ["../x/"], ["src//"], ["*/src/"], ["src/", "SRC/"], [HELD],
                   [HELD + "x/"], ["My App/"], ["app/(admin)/"], None, "internal/"]
        for value in invalid + [[], ["tests/"], ["café/"], [".agents/skills/"]]:
            with self.subTest(value=value):
                write(repo / "dev-loop.json", key(value))
                failures = source_dirs.setup(repo)[0]
                if value in invalid:
                    self.assertEqual(failures, [f"source_dirs in dev-loop.json is invalid: {source_dirs.validate(value)}"])
                else:
                    self.assertEqual(failures, [])

    def test_no_main_ref_notice(self) -> None:
        repo = source_repo(self.root)
        set_branch(repo, key(["internal/"]))
        git_in(repo, "branch", "-q", "-D", "main")
        self.assertEqual(source_dirs.setup(repo), ([], [], [f"no main ref: the spec-tester wall uses {DEFAULT_LIST}"]))


if __name__ == "__main__":
    unittest.main()
