#!/usr/bin/env python3
"""Tests for release.py `kit_reference` and `kit_matches`: the kit that a commit's own installer builds."""

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
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
import release  # noqa: E402

GIT = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "init.defaultBranch=main"]
SOURCE_PARTS = ("bin", "lib", "skill", "skills", "claude", "codex")
LINK = ".claude/skills/dev-loop"


def git(repo: Path, *args: str) -> str:
    return subprocess.run([*GIT, "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def write_source(repo: Path, harnesses: list[str] | None = None, name: str = "dev-loop", version: str = "3.0.0") -> None:
    for part in SOURCE_PARTS:
        shutil.copytree(ROOT / part, repo / part, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"), dirs_exist_ok=True)
    (repo / "package.json").write_text(json.dumps({"name": name, "version": version}) + "\n")
    (repo / "dev-loop.json").write_text(json.dumps({"version": version, "harnesses": harnesses or ["claude", "codex"]}) + "\n")


class KitFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="kitref-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "source"
        self.repo.mkdir()
        git(self.repo, "init", "-q")

    def commit(self) -> str:
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "c")
        return git(self.repo, "rev-parse", "HEAD")

    def install(self, command: str, project: Path) -> dict:
        project.mkdir(exist_ok=True)
        if command == "init":
            git(project, "init", "-q")
        home = self.tmp / "home"
        home.mkdir(exist_ok=True)
        args = ["--harness=both", "--yes"] if command == "init" else ["--yes"]
        subprocess.run(["node", str(self.repo / "bin" / "install.mjs"), command, *args], cwd=project,
                       env={**os.environ, "HOME": str(home)}, capture_output=True, text=True, check=True)
        return json.loads((project / ".dev-loop" / "MANIFEST.json").read_text())


class KitReferenceTests(KitFixture):
    def setUp(self) -> None:
        super().setUp()
        write_source(self.repo)
        self.sha = self.commit()

    def test_reference_equals_the_manifest_init_writes(self) -> None:
        ref = release.kit_reference(self.repo, self.sha)
        manifest = self.install("init", self.tmp / "project")
        self.assertEqual({p: e for p, (e, _) in ref.items()}, manifest["files"])

    def test_modes(self) -> None:
        ref = release.kit_reference(self.repo, self.sha)
        hooks = [p for p in ref if p.startswith(".dev-loop/hooks/claude/")]
        self.assertTrue(hooks)
        self.assertEqual({ref[p][1] for p in hooks}, {"100755"})
        self.assertEqual(ref[LINK], ("link:../../.agents/skills/dev-loop", "120000"))
        self.assertEqual(ref[".agents/skills/dev-loop/SKILL.md"][1], "100644")

    def test_untracked_files_are_not_read(self) -> None:
        (self.repo / "skill" / "extra.md").write_text("untracked\n")
        self.assertNotIn(".agents/skills/dev-loop/extra.md", release.kit_reference(self.repo, self.sha))

    def test_harnesses_of_the_commit(self) -> None:
        write_source(self.repo, ["claude"])
        ref = release.kit_reference(self.repo, self.commit())
        self.assertTrue(ref)
        self.assertFalse([p for p in ref if p.startswith(".codex/")])

    def test_none_without_kit_source(self) -> None:
        write_source(self.repo, name="acme")
        self.assertIsNone(release.kit_reference(self.repo, self.commit()))
        write_source(self.repo)
        (self.repo / "lib" / "kit.mjs").unlink()
        self.assertIsNone(release.kit_reference(self.repo, self.commit()))

    def test_none_without_node(self) -> None:
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "git").symlink_to(shutil.which("git"))
        saved = os.environ["PATH"]
        os.environ["PATH"] = str(bin_dir)
        try:
            self.assertIsNone(release.kit_reference(self.repo, self.sha))
        finally:
            os.environ["PATH"] = saved

    def test_no_temp_left(self) -> None:
        scratch = self.tmp / "scratch"
        scratch.mkdir()
        saved = os.environ.get("TMPDIR")
        os.environ["TMPDIR"] = str(scratch)
        tempfile.tempdir = None
        try:
            self.assertIsNotNone(release.kit_reference(self.repo, self.sha))
        finally:
            tempfile.tempdir = None
            if saved is None:
                del os.environ["TMPDIR"]
            else:
                os.environ["TMPDIR"] = saved
        self.assertEqual(list(scratch.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
