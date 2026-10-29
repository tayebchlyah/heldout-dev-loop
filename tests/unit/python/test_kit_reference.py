#!/usr/bin/env python3
"""Tests for release.py `kit_reference` and `kit_matches`: the kit that a commit's own installer builds."""

from __future__ import annotations

import hashlib
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
DEVLOOP = ROOT / "skill" / "scripts" / "devloop.py"
COPY = ".agents/skills/dev-loop/SKILL.md"
OTHER = ".agents/skills/release/SKILL.md"
RECORD = "## Release audits\n- 2026-10-25 Run 1 at {sha}: confirmed 0, needs validation 0, hardening 0, profile quick, scope whole\n"


def git(repo: Path, *args: str) -> str:
    return subprocess.run([*GIT, "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def write_source(repo: Path, harnesses: list[str] | None = None, name: str = "dev-loop", version: str = "3.0.0") -> None:
    for part in SOURCE_PARTS:
        shutil.copytree(ROOT / part, repo / part, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"), dirs_exist_ok=True)
    (repo / "package.json").write_text(json.dumps({"name": name, "version": version}) + "\n")
    (repo / "dev-loop.json").write_text(json.dumps({"version": version, "harnesses": harnesses or ["claude", "codex"]}) + "\n")


def rehash(project: Path, path: str, text: str) -> None:
    """Write `text` to a kit copy and the hash of it into the MANIFEST, as a PR author would."""
    (project / path).write_text(text)
    manifest = project / release.MANIFEST
    data = json.loads(manifest.read_text())
    data["files"][path] = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
    manifest.write_text(json.dumps(data, indent=2) + "\n")


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


class KitMatchesTests(KitFixture):
    def setUp(self) -> None:
        super().setUp()
        write_source(self.repo)
        self.commit()
        self.project = self.tmp / "project"
        self.install("init", self.project)
        git(self.project, "add", "-A")
        git(self.project, "commit", "-q", "-m", "init")
        self.base = git(self.project, "rev-parse", "HEAD")
        write_source(self.repo, version="3.0.1")
        skill = self.repo / "skill" / "SKILL.md"
        skill.write_text(skill.read_text() + "\nMore.\n")
        self.ref = release.kit_reference(self.repo, self.commit())
        self.install("upgrade", self.project)
        git(self.project, "add", "-A")
        git(self.project, "commit", "-q", "-m", "upgrade")
        self.head = git(self.project, "rev-parse", "HEAD")
        diff = git(self.project, "diff", "--name-only", self.base, self.head)
        self.among = set(diff.splitlines())

    def amend(self, change) -> str:
        change()
        git(self.project, "add", "-A")
        git(self.project, "commit", "-q", "-m", "edit")
        return git(self.project, "rev-parse", "HEAD")

    def matches(self, commit: str, among: set[str] | None = None, ref: dict | None = None) -> set[str]:
        return release.kit_matches(self.project, commit, among or self.among, ref or self.ref)

    def test_upgrade_copies_match(self) -> None:
        kit = {p for p in self.among if p.startswith(release.KIT_ROOTS)}
        self.assertIn(".agents/skills/dev-loop/SKILL.md", kit)
        self.assertEqual(self.matches(self.head), kit | {release.MANIFEST})

    def test_edited_copy_and_rewritten_hash(self) -> None:
        path = ".agents/skills/dev-loop/SKILL.md"
        found = self.matches(self.amend(lambda: rehash(self.project, path, "evil\n")), self.among | {path, release.MANIFEST})
        self.assertNotIn(path, found)
        self.assertNotIn(release.MANIFEST, found)
        rest = {p for p in self.among if p.startswith(release.KIT_ROOTS)} - {path}
        self.assertEqual(found, rest)

    def test_mode_flip(self) -> None:
        path = ".dev-loop/hooks/claude/git-guard.sh"
        head = self.amend(lambda: os.chmod(self.project / path, 0o644))
        self.assertNotIn(path, self.matches(head, self.among | {path}))

    def test_link_target_changed(self) -> None:
        def retarget() -> None:
            (self.project / LINK).unlink()
            (self.project / LINK).symlink_to("../../elsewhere")
        head = self.amend(retarget)
        self.assertNotIn(LINK, self.matches(head, self.among | {LINK}))

    def test_extra_manifest_key(self) -> None:
        def extend() -> None:
            manifest = self.project / release.MANIFEST
            data = json.loads(manifest.read_text())
            data["files"][".agents/skills/dev-loop/extra.md"] = "sha256:" + "0" * 64
            manifest.write_text(json.dumps(data, indent=2) + "\n")
        self.assertNotIn(release.MANIFEST, self.matches(self.amend(extend)))

    def test_outside_kit_roots(self) -> None:
        head = self.amend(lambda: (self.project / "src").mkdir() or (self.project / "src" / "app.py").write_text("x\n"))
        entry = "sha256:" + hashlib.sha256(b"x\n").hexdigest()
        self.assertEqual(self.matches(head, {"src/app.py"}, {"src/app.py": (entry, "100644")}), set())


class KitSourceFlowTests(KitFixture):
    """The repo is the kit source and an installed project, as dev-loop is. A is the current kit at 3.0.0."""

    def setUp(self) -> None:
        super().setUp()
        write_source(self.repo)
        (self.repo / "dev-loop.json").unlink()
        self.install("init", self.repo)
        config = json.loads((self.repo / "dev-loop.json").read_text())
        (self.repo / "dev-loop.json").write_text(json.dumps({**config, "version_files": ["package.json"]}) + "\n")
        (self.repo / "CHANGELOG.md").write_text("# Changelog\n\n## [3.0.0] - 2026-10-01\n")
        self.a = self.commit()
        self.notes = self.tmp / "Notes"
        (self.notes / "Security Audit" / "Run 1").mkdir(parents=True)
        (self.notes / "Project Note.md").write_text(RECORD.format(sha=self.a))
        (self.notes / "Security Audit" / "Run 1" / "run-metadata.json").write_text(
            json.dumps({"source_ref": {"commit": self.a}, "run_status": "complete"}))

    def stale_base(self) -> str:
        """A commit whose kit copy and MANIFEST version are older: the upgrade that follows restores the A build."""
        manifest = self.repo / release.MANIFEST
        data = json.loads(manifest.read_text())
        manifest.write_text(json.dumps({**data, "version": "2.9.0"}) + "\n")
        config = json.loads((self.repo / "dev-loop.json").read_text())
        (self.repo / "dev-loop.json").write_text(json.dumps({**config, "version": "2.9.0"}) + "\n")
        rehash(self.repo, COPY, "stale\n")
        return self.commit()

    def release(self, edit: bool = False) -> str:
        """The release commit B: version files and changelog for 3.0.1, then the upgrade."""
        package = json.loads((self.repo / "package.json").read_text())
        (self.repo / "package.json").write_text(json.dumps({**package, "version": "3.0.1"}) + "\n")
        (self.repo / "CHANGELOG.md").write_text("# Changelog\n\n## [3.0.1] - 2026-11-01\n\n## [3.0.0] - 2026-10-01\n")
        self.install("upgrade", self.repo)
        if edit:
            rehash(self.repo, OTHER, "evil\n")
        return self.commit()

    def devloop(self, *args: str) -> dict:
        done = subprocess.run([sys.executable, str(DEVLOOP), *args, "--repo", str(self.repo)], capture_output=True, text=True,
                              env={**os.environ, "DEV_LOOP_NOTES": str(self.notes)})
        return json.loads(done.stdout)

    def test_ci_skip_dogfood_upgrade_skips(self) -> None:
        base = self.stale_base()
        self.install("upgrade", self.repo)
        out = self.devloop("ci-skip", "--base", base, "--head", self.commit())
        self.assertEqual((out["skip"], out["kind"], out["blocking"]), (True, "kit-upgrade", []))

    def test_ci_skip_rewritten_hash_blocks(self) -> None:
        base = self.stale_base()
        self.install("upgrade", self.repo)
        rehash(self.repo, OTHER, "evil\n")
        out = self.devloop("ci-skip", "--base", base, "--head", self.commit())
        self.assertEqual((out["skip"], out["blocking"]), (False, [OTHER, release.MANIFEST]))

    def test_release_check_dogfood_release_ok(self) -> None:
        out = self.devloop("release-check", "--sha", self.release())
        self.assertEqual((out["ok"], out["reasons"]), (True, []))

    def test_release_check_rewritten_hash_is_stale(self) -> None:
        out = self.devloop("release-check", "--sha", self.release(edit=True))
        self.assertEqual(out["reasons"][0]["reason"], "stale-audit")
        self.assertIn(OTHER, out["reasons"][0]["paths"])

    def plan_from_stale_record(self, edit: bool) -> list[str]:
        stale = self.stale_base()
        (self.notes / "Project Note.md").write_text(RECORD.format(sha=stale))
        (self.notes / "Security Audit" / "Run 1" / "run-metadata.json").write_text(
            json.dumps({"source_ref": {"commit": stale}, "run_status": "complete"}))
        self.release(edit)
        return self.devloop("release-plan")["exclude"]

    def test_release_plan_exclude_binds(self) -> None:
        self.assertEqual(self.plan_from_stale_record(False), [COPY, release.MANIFEST])

    def test_release_plan_exclude_with_edit(self) -> None:
        exclude = self.plan_from_stale_record(True)
        self.assertEqual(exclude, [COPY])


if __name__ == "__main__":
    unittest.main()
