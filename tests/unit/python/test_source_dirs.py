"""source_dirs.py test: the validator, the matcher, the main ref read, the top-level folder list."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from source_fixtures import key, set_branch, set_main, source_repo
from support import git_in, write

import source_dirs
from source_dirs import Main

HELD = "tests/" + "acceptance/"


class SourceDirsTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def test_valid_values(self) -> None:
        for value in ([], ["tests/"], ["café/"], [".agents/skills/"], ["src/main/java/"]):
            with self.subTest(value=value):
                self.assertIsNone(source_dirs.validate(value))

    def test_invalid_values(self) -> None:
        cases = [(["internal"], '"internal"'), (["/internal/"], '"/internal/"'), (["../x/"], '"../x/"'),
                 (["src//"], '"src//"'), (["*/src/"], '"*/src/"'), (["src/", "SRC/"], '"SRC/"'),
                 ([HELD], HELD), ([HELD + "x/"], HELD + "x/"), (["My App/"], '"My App/"'),
                 (["app/(admin)/"], '"app/(admin)/"'), (None, "list"), ("internal/", "list"), ([1], "1")]
        for value, named in cases:
            with self.subTest(value=value):
                rule = source_dirs.validate(value)
                self.assertIsInstance(rule, str)
                self.assertIn(named, rule)

    def test_matches_from_the_root_only(self) -> None:
        self.assertEqual(source_dirs.matches(["internal/"], "internal/x.go"), "internal/")
        self.assertEqual(source_dirs.matches(["internal/"], "internal"), "internal/")
        self.assertIsNone(source_dirs.matches(["internal/"], "internals/x"))
        self.assertIsNone(source_dirs.matches(["internal/"], "pkg/internal/x"))
        self.assertEqual(source_dirs.matches(["café/"], "CAFÉ/x"), "café/")
        self.assertEqual(source_dirs.matches(["café/"], "café/x"), "café/")

    def test_in_dot_folder(self) -> None:
        self.assertTrue(source_dirs.in_dot_folder(".agents/skills/"))
        self.assertTrue(source_dirs.in_dot_folder(".agents/skills/k/x.go"))
        self.assertFalse(source_dirs.in_dot_folder(".gitignore"))
        self.assertFalse(source_dirs.in_dot_folder("docs/x"))

    def test_read_main_states(self) -> None:
        repo = source_repo(self.root)
        self.assertEqual(source_dirs.read_main(repo), Main("main", "no-key"))
        set_main(repo, key(["internal/"]))
        self.assertEqual(source_dirs.read_main(repo), Main("main", "configured", ("internal/",)))
        set_main(repo, key("internal/"))
        main = source_dirs.read_main(repo)
        self.assertEqual(main.state, "invalid")
        self.assertIn("source_dirs at main is invalid", main.error())
        for text in ("[]", "{"):
            set_main(repo, text)
            main = source_dirs.read_main(repo)
            self.assertEqual(main.state, "not-object")
            self.assertIn("source_dirs", main.error())
        set_main(repo, '{"version": "0.0.0"}')
        set_branch(repo, key(["internal/"]))
        self.assertEqual(source_dirs.read_main(repo), Main("main", "no-key"))
        git_in(repo, "update-ref", "refs/remotes/origin/main", "refs/heads/main")
        self.assertEqual(source_dirs.read_main(repo).ref, "origin/main")

    def test_read_main_without_a_ref(self) -> None:
        repo = self.root / "bare"
        repo.mkdir()
        git_in(repo, "init", "-q", "-b", "other")
        write(repo / "dev-loop.json", "{}")
        git_in(repo, "add", "-A")
        git_in(repo, "commit", "-q", "-m", "init")
        self.assertEqual(source_dirs.read_main(repo), Main(None, "no-ref"))

    def test_top_folders(self) -> None:
        repo = source_repo(self.root)
        write(repo / ".github/workflows/ci.yml", "x\n")
        write(repo / "README.md", "x\n")
        git_in(repo, "add", "-A")
        self.assertEqual(source_dirs.top_folders(repo),
                         ["api/", "cmd/", "docs/", "internal/", "pkg/", "scripts/", "src/", "tests/"])
        plain = self.root / "plain"
        plain.mkdir()
        self.assertEqual(source_dirs.top_folders(plain), [])


if __name__ == "__main__":
    unittest.main()
