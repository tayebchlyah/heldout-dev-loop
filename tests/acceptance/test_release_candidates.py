#!/usr/bin/env python3
"""Held-out oracle tests of the installer, the version order, the board and `kit_drift` (spec: Release
Candidates). Seams, fixture choices and shared helpers: `release_candidates_support.py`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from release_candidates_support import CHECKER, MANIFEST, REPO_ROOT, InstallerCase, snapshot

# Shared board fixtures live in tests/unit/python; the unit suite never imports this held-out file.
sys.path.insert(0, str(REPO_ROOT / "tests" / "unit" / "python"))
from board_fixtures import BOARD_NOTE, RobustBoard, frontmatter_version, git, put

sys.path.insert(0, str(REPO_ROOT / "skill" / "scripts"))

CHANGELOG = "\n".join([
    "# Changelog", "",
    "## [3.0.0-rc.1] - 2026-10-27", "", "- rc one marker", "",
    "## [2.1.1] - 2026-10-16", "", "- two one one marker", "",
    "## [2.1.0] - 2026-10-01", "", "- two one zero marker", "",
])


# ---------------------------------------------------------------------------------------------------------------
# Installer (O1 to O7)
# ---------------------------------------------------------------------------------------------------------------

class InstallerTests(InstallerCase):

    def test_f1_upgrade_through_a_cycle(self) -> None:
        """F1 (O1, O2, O3, O6): 2.1.1 to 3.0.0-rc.1 (notes of rc.1 only), rc.2, 3.0.0; rc.1 then refuses."""
        repo = self.init_at("2.1.1")
        result = self.upgrade_to(repo, "3.0.0-rc.1", CHANGELOG)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.versions(repo), ("3.0.0-rc.1", "3.0.0-rc.1"))
        self.assertIn("rc one marker", result.stdout)
        self.assertNotIn("two one one marker", result.stdout)
        for version in ("3.0.0-rc.2", "3.0.0"):
            result = self.upgrade_to(repo, version)
            self.assertEqual(result.returncode, 0, f"{version}: {result.stdout}{result.stderr}")
            self.assertEqual(self.versions(repo), (version, version))
        before = snapshot(repo)
        result = self.upgrade_to(repo, "3.0.0-rc.1")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        output = result.stdout + result.stderr
        for word in ("3.0.0-rc.1", "3.0.0", "older"):
            self.assertIn(word, output)
        self.assertEqual(snapshot(repo), before)

    def test_o4_rc_numbers_compare_as_numbers(self) -> None:
        """O4: rc.9 to rc.10 upgrades; rc.10 to rc.9 refuses."""
        repo = self.init_at("3.0.0-rc.9")
        result = self.upgrade_to(repo, "3.0.0-rc.10")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.versions(repo), ("3.0.0-rc.10", "3.0.0-rc.10"))
        result = self.upgrade_to(repo, "3.0.0-rc.9")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_o5_other_version_text_is_refused(self) -> None:
        """O5, threat 3: a package version not in a D2 form: exit 1, the error, no file changes."""
        repo = self.init_at("2.1.1")
        for version in ("3.0.0-beta.1", "3.0", "3.0.0-rc.0", "03.0.0"):
            with self.subTest(version):
                before = snapshot(repo)
                result = self.upgrade_to(repo, version)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(f"not a x.y.z or x.y.z-rc.n version: {version}", result.stdout + result.stderr)
                self.assertEqual(snapshot(repo), before)

    def test_o7_init_writes_the_rc_version(self) -> None:
        """O7: `init` with package 3.0.0-rc.1 writes dev-loop.json version 3.0.0-rc.1."""
        repo = self.project()
        result = self.install(self.package("3.0.0-rc.1"), "init", repo, "--yes", "--harness=claude")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads((repo / "dev-loop.json").read_text()).get("version"), "3.0.0-rc.1")


# ---------------------------------------------------------------------------------------------------------------
# Version order and board (O8 to O10)
# ---------------------------------------------------------------------------------------------------------------

class VersionKeyTests(unittest.TestCase):

    def test_o8_version_key_order(self) -> None:
        """O8, D2: 2.1.1 < 3.0.0-rc.1 < 3.0.0-rc.2 < 3.0.0-rc.10 < 3.0.0 < 3.0.1."""
        from board.store import version_key
        order = ["2.1.1", "3.0.0-rc.1", "3.0.0-rc.2", "3.0.0-rc.10", "3.0.0", "3.0.1"]
        for low, high in zip(order, order[1:]):
            with self.subTest(f"{low} < {high}"):
                self.assertLess(version_key(low), version_key(high))
        self.assertEqual(sorted(reversed(order), key=version_key), order)


class KitVersionBoard(RobustBoard):
    """The board sandbox with the kit at `kit` in `dev-loop.json` and the MANIFEST of the main checkout."""

    def set_main_kit(self, version: str) -> None:
        put(self.repo / "dev-loop.json", json.dumps({"version": version, "harnesses": ["claude"]}) + "\n")
        put(self.repo / MANIFEST, json.dumps({"version": version, "files": {}}) + "\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", f"kit {version}")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "origin")


class BoardVersionTests(KitVersionBoard):

    def test_o9_board_note_of_an_older_rc_is_written_again(self) -> None:
        """O9: a board note at 3.0.0-rc.1 and kit 3.0.0-rc.2: `board` writes it again at 3.0.0-rc.2."""
        self.set_main_kit("3.0.0-rc.2")
        self.worktree("tiny", prefix="fix")
        self.ok()
        path = self.board_dir / BOARD_NOTE
        path.write_text('---\nversion: "3.0.0-rc.1"\n---\n\nold rc body marker\n')
        self.ok()
        self.assertEqual(frontmatter_version(path), "3.0.0-rc.2")
        self.assertNotIn("old rc body marker", path.read_text())

    def test_o10_check_setup_sees_an_older_rc_note_as_older(self) -> None:
        """O10: check_setup.py, board note 3.0.0-rc.1, kit 3.0.0-rc.2: no "differs from the dev-loop template"
        line (control: the same planted body at 3.0.0-rc.2 gets it)."""
        self.set_main_kit("3.0.0-rc.2")
        path = self.board_dir / BOARD_NOTE
        line = "differs from the dev-loop template"
        for version, expected in (("3.0.0-rc.2", True), ("3.0.0-rc.1", False)):
            with self.subTest(version):
                put(path, f'---\nversion: "{version}"\n---\n\nplanted body\n')
                result = subprocess.run([sys.executable, str(CHECKER), "--harness", "claude", "--repo", str(self.repo)],
                                        cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=120)
                self.assertEqual(line in result.stdout + result.stderr, expected, result.stdout + result.stderr)


# ---------------------------------------------------------------------------------------------------------------
# kit_drift (O11 to O17)
# ---------------------------------------------------------------------------------------------------------------

class KitDriftTests(KitVersionBoard):
    """Main checkout and origin/main at kit 3.0.0-rc.1, a spec `gadget` and its worktree."""

    def setUp(self) -> None:
        super().setUp()
        self.set_main_kit("3.0.0-rc.1")
        self.spec("gadget", "Gadget Export")
        self.wt = self.worktree("gadget")

    def manifest_text(self, version: str) -> str:
        return json.dumps({"version": version, "files": {}}) + "\n"

    def push_main(self, manifest: str | None) -> str:
        """A commit on origin/main with this MANIFEST text (None: no MANIFEST); local main stays where it is."""
        git(self.repo, "checkout", "-q", "-b", "side-push", "origin/main")
        path = self.repo / MANIFEST
        if manifest is None:
            git(self.repo, "rm", "-q", MANIFEST)
        else:
            put(path, manifest)
            git(self.repo, "add", MANIFEST)
        git(self.repo, "commit", "-q", "-m", "origin main change")
        sha = git(self.repo, "rev-parse", "HEAD").strip()
        git(self.repo, "push", "-q", "origin", "side-push:main")
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "branch", "-q", "-D", "side-push")
        git(self.repo, "fetch", "-q", "origin")
        return sha

    def wt_kit(self, version: str) -> None:
        put(self.wt / MANIFEST, self.manifest_text(version))

    def main_kit(self, version: str) -> None:
        put(self.repo / MANIFEST, self.manifest_text(version))

    def drift(self, cwd: Path | None = None) -> dict | None:
        return self.status(cwd=cwd or self.wt).get("kit_drift")

    def test_f2_old_kit_in_a_session(self) -> None:
        """F2 (O11, O13): a worktree at rc.1 has no kit_drift; origin/main gets the rc.2 upgrade: main-newer."""
        self.assertIsNone(self.drift())
        self.push_main(self.manifest_text("3.0.0-rc.2"))
        self.assertEqual(self.drift(), {"worktree": "3.0.0-rc.1", "main": "3.0.0-rc.2", "direction": "main-newer"})

    def test_o12_newer_worktree_kit(self) -> None:
        """O12: worktree kit rc.2, main worktree rc.1: worktree-newer."""
        self.wt_kit("3.0.0-rc.2")
        self.assertEqual(self.drift(), {"worktree": "3.0.0-rc.2", "main": "3.0.0-rc.1", "direction": "worktree-newer"})

    def test_o14_final_release_on_main_ref(self) -> None:
        """O14: worktree kit rc.2 (main worktree rc.2), <main-ref> MANIFEST 3.0.0: main-newer."""
        self.push_main(self.manifest_text("3.0.0"))
        self.wt_kit("3.0.0-rc.2")
        self.main_kit("3.0.0-rc.2")
        self.assertEqual(self.drift(), {"worktree": "3.0.0-rc.2", "main": "3.0.0", "direction": "main-newer"})

    def test_o15_main_worktree_behind_origin_main(self) -> None:
        """O15: the main worktree itself at rc.1, refs/remotes/origin/main at rc.2: main-newer."""
        self.push_main(self.manifest_text("3.0.0-rc.2"))
        self.assertEqual(self.drift(cwd=self.repo),
                         {"worktree": "3.0.0-rc.1", "main": "3.0.0-rc.2", "direction": "main-newer"})

    def test_o16_no_readable_main_ref_manifest(self) -> None:
        """O16: no MANIFEST at <main-ref>, one that is not JSON, or no main ref at all: no main-newer, exit 0
        (control: origin/main at rc.2 gives main-newer)."""
        self.push_main(self.manifest_text("3.0.0-rc.2"))
        self.assertEqual((self.drift() or {}).get("direction"), "main-newer")
        for name, manifest in (("no MANIFEST", None), ("not JSON", "{not json\n")):
            with self.subTest(name):
                self.push_main(manifest)
                self.assertNotEqual((self.drift() or {}).get("direction"), "main-newer")
        with self.subTest("no main ref"):
            self.push_main(self.manifest_text("3.0.0-rc.2"))
            git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
            git(self.repo, "branch", "-q", "-m", "main", "trunk")
            self.assertNotEqual((self.drift() or {}).get("direction"), "main-newer")

    def test_o17_local_branch_named_origin_main_is_ignored(self) -> None:
        """O17, threat 3: a local branch `origin/main` with a newer MANIFEST, refs/remotes/origin/main at the
        worktree version: no kit_drift (control: the real origin/main at rc.2 gives main-newer)."""
        newer = self.push_main(self.manifest_text("3.0.0-rc.2"))
        self.assertEqual((self.drift() or {}).get("direction"), "main-newer")
        self.push_main(self.manifest_text("3.0.0-rc.1"))
        git(self.repo, "branch", "-q", "origin/main", newer)
        self.assertIsNone(self.drift())


if __name__ == "__main__":
    unittest.main()
