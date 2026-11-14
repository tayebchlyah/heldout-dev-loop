"""The `stacks` key of dev-loop.json: validator and the check_setup.py stacks lines."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import CHECKER, ROOT, checker_env, write

import stacks


class StacksSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.count = 0

    def repo(self, config: object, folders: tuple[str, ...] = (), files: tuple[str, ...] = ()) -> Path:
        self.count += 1
        repo = self.root / f"repo{self.count}"
        repo.mkdir()
        write(repo / "dev-loop.json", json.dumps(config) + "\n")
        for folder in folders:
            (repo / folder).mkdir(parents=True)
        for rel in files:
            write(repo / rel)
        return repo

    def test_invalid_shapes(self) -> None:
        values = ({}, "api/", [{"path": "../x/", "preset": "go"}], [{"path": "api/"}], [{"path": "api/", "preset": None, "x": 1}],
                  [{"path": "api/", "preset": None}] * 2, [{"path": "api/", "preset": None}, {"path": "API/", "preset": None}],
                  [{"path": "api/", "preset": 3}], ["api/"], [{"path": "api", "preset": None}], [{"path": "./", "preset": None}],
                  [{"path": "tests/acceptance/", "preset": None}], [{"path": 3, "preset": None}], [{"path": ".", "preset": None}] * 2)
        for value in values:
            with self.subTest(value=value):
                lines = stacks.setup(self.repo({"stacks": value, "source_dirs": ["api/"]}, ("api/",)))
                self.assertEqual(len(lines), 1, lines)
                self.assertTrue(lines[0].startswith("stacks in dev-loop.json is invalid: "), lines)

    def test_valid_shapes(self) -> None:
        for value in ([], [{"path": ".", "preset": None}], [{"path": "api/", "preset": "go"}, {"path": "web/", "preset": None}]):
            with self.subTest(value=value):
                self.assertIsNone(stacks.validate(value))

    def test_unknown_preset(self) -> None:
        self.assertEqual(stacks.setup(self.repo({"stacks": [{"path": "api/", "preset": "gradel"}], "source_dirs": ["api/"]}, ("api/",))),
                         ["stacks in dev-loop.json names an unknown preset: gradel"])
        self.assertEqual(stacks.setup(self.repo({"stacks": [{"path": "api/", "preset": None}], "source_dirs": ["api/"]}, ("api/",))), [])

    def test_not_a_folder(self) -> None:
        self.assertEqual(stacks.setup(self.repo({"stacks": [{"path": "web/", "preset": "npm"}], "source_dirs": []})),
                         ["stacks path web/ is not a folder in this checkout"])
        self.assertEqual(stacks.setup(self.repo({"stacks": [{"path": "web/", "preset": "npm"}], "source_dirs": []}, files=("web",))),
                         ["stacks path web/ is not a folder in this checkout"])

    def test_path_coverage(self) -> None:
        self.assertEqual(stacks.setup(self.repo({"stacks": [{"path": "api/", "preset": "go"}], "source_dirs": ["src/"]}, ("api/",))),
                         ['stacks path api/ is not in source_dirs: add "api/"'])
        self.assertEqual(stacks.setup(self.repo({"stacks": [{"path": "api/", "preset": "go"}], "source_dirs": ["api/"]}, ("api/",))), [])
        self.assertEqual(stacks.setup(self.repo({"stacks": [{"path": "backend/api/", "preset": None}], "source_dirs": ["backend/"]},
                                                ("backend/api/",))), [])

    def test_root_coverage(self) -> None:
        self.assertEqual(stacks.setup(self.repo({"stacks": [{"path": ".", "preset": "go"}], "source_dirs": ["cmd/"]}, ("cmd/", "internal/"))),
                         ['stacks path . preset go: internal/ is not in source_dirs: add "internal/"'])
        self.assertEqual(stacks.setup(self.repo({"stacks": [{"path": ".", "preset": "go"}], "source_dirs": ["cmd/"]}, ("cmd/",))), [])

    def test_root_no_coverage(self) -> None:
        for preset in ("dotnet", None):
            with self.subTest(preset=preset):
                self.assertEqual(stacks.setup(self.repo({"stacks": [{"path": ".", "preset": preset}], "source_dirs": []}, ("src/",))), [])
        self.assertEqual(stacks.setup(self.repo({"stacks": [{"path": ".", "preset": "uv"}], "source_dirs": []}, files=("src",))), [])

    def test_cargo_held_out_target(self) -> None:
        line = stacks.CARGO_SUITE
        entry = '[[test]]\nname = "acceptance"\npath = "tests/acceptance/main.rs"\n'
        rows = [
            (".", '[package]\nname = "p"\n', True, [line]),
            (".", '[package]\nname = "p"\n' + entry, True, [line]),
            (".", '[package]\nname = "p"\n' + entry + "test = false\n", True, []),
            (".", '[package]\nname = "p"\nautotests = false\n', True, []),
            (".", '[package]\nname = "p"\nautotests = false\n' + entry, True, [line]),
            (".", "[package\n", True, [line]),
            (".", '[package]\nname = "p"\n', False, []),
            ("api/", '[package]\nname = "p"\n', True, []),
        ]
        for path, cargo, suite, want in rows:
            with self.subTest(path=path, cargo=cargo, suite=suite):
                files = ("tests/acceptance/main.rs",) if suite else ()
                repo = self.repo({"stacks": [{"path": path, "preset": "cargo"}], "source_dirs": ["src/", "api/"]},
                                 ("src/", "api/"), files)
                write(repo / path / "Cargo.toml", cargo)
                self.assertEqual(stacks.setup(repo), want)

    def test_no_check_without_valid_source_dirs(self) -> None:
        for config in ({"stacks": [{"path": "api/", "preset": "go"}]}, {"stacks": [{"path": "api/", "preset": "go"}], "source_dirs": "x"}):
            with self.subTest(config=config):
                self.assertEqual(stacks.setup(self.repo(config, ("api/",))), [])

    def test_no_key(self) -> None:
        self.assertEqual(stacks.setup(self.repo({"version": "3.0.0"})), [])
        self.assertEqual(stacks.setup(self.repo([1, 2])), [])

    def check(self, repo: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(CHECKER), "--harness", "claude", "--home", str(self.root / "home"),
                               "--repo", str(repo)], capture_output=True, text=True, check=False, env=checker_env(self.root / "bin"))

    def test_check_setup_process(self) -> None:
        drift = self.check(self.repo({"version": "3.0.0", "stacks": [{"path": "api/", "preset": "go"}], "source_dirs": ["src/"]}, ("api/",)))
        self.assertEqual(drift.returncode, 1)
        self.assertIn('- stacks path api/ is not in source_dirs: add "api/"', drift.stdout.splitlines())
        fixed = self.check(self.repo({"version": "3.0.0", "stacks": [{"path": "api/", "preset": "go"}], "source_dirs": ["api/", "src/"]},
                                     ("api/",)))
        self.assertEqual([line for line in fixed.stdout.splitlines() if "stacks" in line], [])
        here = self.check(ROOT)
        self.assertEqual([line for line in here.stdout.splitlines() if "stacks" in line], [])


if __name__ == "__main__":
    unittest.main()
