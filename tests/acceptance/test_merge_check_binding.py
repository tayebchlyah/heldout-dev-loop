#!/usr/bin/env python3
"""Held-out acceptance tests for the merge-check binding (spec: Merge Check Binding), flows 1, 2, 3 and 6.

One test per `## Acceptance flows` item (F<n>), one subtest per `## Acceptance oracle` line (O<n>) it proves, plus
one test for the kit text line O32, which no flow names. Flows 4 and 5 are in `test_merge_check_binding_gate.py`.
Seams and fixture choices: `merge_check_binding_support.py`.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from merge_check_binding_support import (APP_BASE, APP_FEATURE, REPO_ROOT, SLUG, TEST_BASE, Project, config_text,
                                         put, reasons, section)

SKILL = REPO_ROOT / "skill" / "SKILL.md"
CLI_DOCS = REPO_ROOT / "reference" / "cli"
KIT_DOC = REPO_ROOT / "reference" / "kit.md"


class Case(unittest.TestCase):

    def project(self, config: str | None = None, local_notes: bool = True) -> Project:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Project(Path(tmp.name).resolve(), config, local_notes)

    def check(self, project: Project, cwd: Path, *args: str) -> dict:
        code, out = project.merge_check(cwd, *args)
        self.assertEqual(code, 0, out)
        self.assertIn(out.get("merge"), ("auto", "human"), out)
        return out

    def assert_bound(self, project: Project, out: dict, branch: str, head: str) -> None:
        self.assertEqual(out.get("branch"), branch, out)
        self.assertEqual(out.get("head"), head, out)
        self.assertRegex(str(out.get("head")), r"^[0-9a-f]{40}$", out)

    def assert_refused(self, code: int, out: dict, *branches: str) -> None:
        self.assertEqual(code, 1, out)
        self.assertNotIn("merge", out)
        error = str(out.get("error") or "")
        for branch in branches:
            self.assertIn(branch, error, out)

    def assert_unreviewed(self, out: dict, commit: str, path: str) -> None:
        found = [item for item in reasons(out, "unreviewed") if item.get("commit") == commit
                 or (isinstance(item.get("commit"), str) and len(item["commit"]) >= 7
                     and commit.startswith(item["commit"]))]
        self.assertTrue(found, f"no unreviewed reason for {commit}: {out}")
        self.assertIn(path, found[0].get("paths") or [], out)
        self.assertEqual(out["merge"], "human", out)

    def assert_minor(self, out: dict, commit: str) -> None:
        self.assertEqual(reasons(out, "unreviewed"), [], out)
        commits = [str(item.get("commit")) for item in out.get("unreviewed_minor") or [] if isinstance(item, dict)]
        self.assertTrue([c for c in commits if len(c) >= 7 and commit.startswith(c)], out)


class StageSevenMergeTests(Case):

    def test_f1_stage_7_merge_from_the_right_worktree(self) -> None:
        """F1, O1, O5, O31: `merge-check` reports the `head` and `branch` it checked; the merge rule compares
        `head` with the sha it merges."""
        project = self.project()
        project.spec(SLUG, "M")
        wt = project.worktree(f"feat/{SLUG}")
        project.commit(wt, {"tests/acceptance/test_demo.py": "def test_flow():\n    assert True\n"},
                       "Add the acceptance tests")
        reviewed = project.commit(wt, {"src/app.py": APP_FEATURE}, "Build the feature")
        project.review(wt, reviewed, "M")

        with self.subTest(oracle=1):
            out = self.check(project, wt)
            self.assertEqual(out["merge"], "auto", out)
            self.assert_bound(project, out, "feat/demo", project.head(wt))

        with self.subTest(oracle=5, worktree=True):
            out = self.check(project, project.repo, "--feature", SLUG)
            self.assert_bound(project, out, "feat/demo", project.head(wt))

        with self.subTest(oracle=5, worktree=False):
            project.git(project.repo, "worktree", "remove", "--force", str(wt))
            out = self.check(project, project.repo, "--feature", SLUG)
            self.assert_bound(project, out, "feat/demo", project.git(project.repo, "rev-parse", "refs/heads/feat/demo"))

        with self.subTest(oracle=31):
            stage7 = section(SKILL.read_text(), 7)
            sentences = re.split(r"(?<=[.:;])\s+|\n", stage7)
            self.assertIn("--match-head-commit", stage7)
            self.assertIn("`head`", stage7)
            self.assertNotIn("merge-check --feature", stage7)
            self.assertTrue([s for s in sentences if "merge-check" in s and "worktree" in s
                             and re.search(r"(no|without)\s+`?--feature", s)], stage7)
            self.assertTrue([s for s in sentences if "`head`" in s and "user" in s], stage7)


class WrongBranchTests(Case):

    def test_f2_a_second_worktree_cannot_lend_its_review(self) -> None:
        """F2, O2, O3, O4: the same slug suffix on two branches."""
        project = self.project()
        spike = project.worktree("spike/demo", "a-demo-spike")
        project.review(spike, project.commit(spike, {"src/spike.py": "SPIKE = 1\n"}, "Try an idea"), "S")
        feat = project.worktree("feat/demo", "b-demo-feat")
        project.commit(feat, {"src/app.py": APP_FEATURE}, "Build the feature")

        with self.subTest(oracle=2):
            out = self.check(project, feat)
            self.assertTrue(reasons(out, "no-review"), out)
            self.assertEqual(out["merge"], "human", out)
            self.assert_bound(project, out, "feat/demo", project.head(feat))

        with self.subTest(oracle=3):
            code, out = project.merge_check(project.repo, "--feature", SLUG)
            self.assert_refused(code, out, "feat/demo", "spike/demo")

        with self.subTest(oracle=4):
            other = self.project()
            other.git(other.repo, "branch", "feat/Demo")
            other.git(other.repo, "branch", "fix/demo")
            code, out = other.merge_check(other.repo, "--feature", SLUG)
            self.assert_refused(code, out, "feat/Demo", "fix/demo")


class ReplayedPatchTests(Case):

    def replay(self, path: str, base: str, last: str) -> tuple[Project, Path, str]:
        """R1 adds a line to `path`, R2 reverts R1, the review covers R2; R3 is `git revert R2` or
        `git cherry-pick R1`."""
        project = self.project()
        wt = project.worktree(f"feat/{SLUG}")
        r1 = project.commit(wt, {path: base + "\nEXTRA = 1\n"}, "R1")
        project.git(wt, "revert", "--no-edit", r1)
        r2 = project.head(wt)
        project.review(wt, r2, "S")
        if last == "revert":
            project.git(wt, "revert", "--no-edit", r2)
        else:
            project.git(wt, "cherry-pick", r1)
        return project, wt, project.head(wt)

    def test_f3_a_replayed_patch_after_the_review_is_unreviewed(self) -> None:
        """F3, O6, O7, O8: each reviewed patch-id vouches for one landing commit."""
        for last in ("revert", "cherry-pick"):
            with self.subTest(oracle=6, r3=last):
                project, wt, r3 = self.replay("src/app.py", APP_BASE, last)
                self.assert_unreviewed(self.check(project, wt), r3, "src/app.py")

        with self.subTest(oracle=7):
            project = self.project()
            wt = project.worktree(f"feat/{SLUG}")
            r1 = project.commit(wt, {"src/app.py": APP_BASE + "\nEXTRA = 1\n"}, "R1")
            project.git(wt, "revert", "--no-edit", r1)
            project.git(wt, "revert", "--no-edit", project.head(wt))
            project.review(wt, project.head(wt), "S")
            project.advance_main({"src/billing.py": "RATE = 3\n"}, "Add billing on main")
            project.rebase(wt)
            out = self.check(project, wt)
            self.assertEqual(reasons(out, "unreviewed"), [], out)
            self.assertEqual(out["merge"], "auto", out)

        for last in ("revert", "cherry-pick"):
            with self.subTest(oracle=8, r3=last):
                project, wt, r3 = self.replay("tests/unit/test_x.py", TEST_BASE, last)
                self.assert_minor(self.check(project, wt), r3)


class NotesRedirectTests(Case):

    def feature(self, project: Project, track: str = "M", notes: Path | None = None,
                files: dict[str, str] | None = None) -> Path:
        wt = project.worktree(f"feat/{SLUG}")
        reviewed = project.commit(wt, {"src/app.py": APP_FEATURE, **(files or {})}, "Build the feature")
        project.review(wt, reviewed, track, notes=notes)
        return wt

    def assert_names(self, text: str, path: Path) -> None:
        self.assertIn(str(path), text)

    def test_f6_a_feature_worktree_file_cannot_move_the_notes_or_set_the_track(self) -> None:
        """F6, O13 to O19: the notes folder comes from `DEV_LOOP_NOTES`, then the main worktree local file, then
        the main worktree `dev-loop.json` `notes`."""
        with self.subTest(oracle=13):
            project = self.project()
            project.spec(SLUG, "L")
            fake = project.root / "fake-notes"
            project.spec(SLUG, "M", notes=fake)
            wt = self.feature(project)
            put(wt / "dev-loop.local.json", json.dumps({"notes": str(fake)}) + "\n")
            self.assertEqual(project.git(wt, "status", "--porcelain", "--", "dev-loop.local.json"), "")
            out = self.check(project, wt)
            self.assertEqual(out.get("track"), "L", out)
            self.assertTrue(reasons(out, "track-l"), out)

        with self.subTest(oracle=14):
            project = self.project(config_text(notes="real-notes"), local_notes=False)
            real = project.repo / "real-notes"
            project.spec(SLUG, "L", notes=real)
            wt = self.feature(project, notes=real, files={"dev-loop.json": config_text(notes="fake-notes")})
            project.spec(SLUG, "M", notes=wt / "fake-notes")
            out = self.check(project, wt)
            self.assertEqual(out.get("track"), "L", out)
            self.assertTrue(reasons(out, "track-l"), out)

        with self.subTest(oracle=15):
            project = self.project(config_text(notes="json-notes"))
            env_notes, json_notes = project.root / "env-notes", project.repo / "json-notes"
            env_notes.mkdir()
            json_notes.mkdir()
            wt = project.worktree(f"feat/{SLUG}")
            code, out, err = project.notes_path(wt, {"DEV_LOOP_NOTES": str(env_notes)})
            self.assertEqual(code, 0, out + err)
            self.assert_names(out, env_notes)
            self.assertNotIn(str(project.notes), out)
            code, out, err = project.notes_path(wt)
            self.assertEqual(code, 0, out + err)
            self.assert_names(out, project.notes)
            self.assertNotIn("json-notes", out)
            project.local_file.unlink()
            code, out, err = project.notes_path(wt)
            self.assertEqual(code, 0, out + err)
            self.assert_names(out, json_notes)

        with self.subTest(oracle=16):
            project = self.project(local_notes=False)
            wt = project.worktree(f"feat/{SLUG}")
            put(wt / "dev-loop.local.json", json.dumps({"notes": str(project.notes)}) + "\n")
            code, out, err = project.notes_path(wt)
            self.assertEqual(code, 1, out + err)
            self.assert_names(out + err, project.local_file)

        with self.subTest(oracle=17):
            project = self.project(config_text(notes="vault-n"), local_notes=False)
            (project.repo / "vault-n").mkdir()
            wt = project.worktree(f"feat/{SLUG}")
            project.commit(wt, {"dev-loop.json": config_text(notes="vault-x")}, "Point at another vault")
            (wt / "vault-x").mkdir()
            code, out, err = project.notes_path(wt)
            self.assertEqual(code, 0, out + err)
            self.assert_names(out, project.repo / "vault-n")
            self.assertNotIn("vault-x", out)

        with self.subTest(oracle=18):
            project = self.project()
            wt = self.feature(project, track="S")
            project.git(project.repo, "add", "-f", "dev-loop.local.json")
            project.git(project.repo, "commit", "-q", "-m", "Track the local file")
            for repo in (project.repo, wt):
                code, out, err = project.notes_path(repo)
                self.assertEqual(code, 1, out + err)
                for needle in ("dev-loop.local.json", "tracked", "git rm --cached"):
                    self.assertIn(needle, err)
            for command in ("merge-check", "status"):
                code, out, _ = project.devloop(wt, command)
                self.assertEqual(code, 1, out)
                self.assertTrue(out.get("error"), out)
            env_notes = project.root / "env-notes"
            env_notes.mkdir()
            code, out, err = project.notes_path(wt, {"DEV_LOOP_NOTES": str(env_notes)})
            self.assertEqual(code, 0, out + err)
            self.assert_names(out, env_notes)

        for bad in ("{not json\n", "{}\n"):
            with self.subTest(oracle=19, local=bad):
                project = self.project(local_notes=False)
                put(project.local_file, bad)
                wt = project.worktree(f"feat/{SLUG}")
                code, out, err = project.notes_path(wt)
                self.assertEqual(code, 1, out + err)
                self.assertIn("dev-loop.local.json", out + err)


class KitTextTests(unittest.TestCase):

    def test_o32_reference_docs(self) -> None:
        """O32: the merge-check, acceptance and kit reference docs."""
        merge = (CLI_DOCS / "merge-check.md").read_text()
        with self.subTest(doc="merge-check.md"):
            for needle in ("`head`", "`branch`", ".github"):
                self.assertIn(needle, merge)
            for kind in (".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".conf"):
                self.assertIn(kind, merge)
            self.assertRegex(merge, r"(?i)more than one|ambiguous")
        acceptance = (CLI_DOCS / "acceptance.md").read_text()
        with self.subTest(doc="acceptance.md"):
            self.assertIn("cases", acceptance)
            self.assertRegex(acceptance, r"main[ _-]ref|<main-ref>")
        kit = KIT_DOC.read_text()
        with self.subTest(doc="kit.md"):
            self.assertIn("tracked", kit)
            paragraphs = [text for text in re.split(r"\n\s*\n", kit)
                          if "DEV_LOOP_NOTES" in text and "dev-loop.local.json" in text]
            self.assertTrue(paragraphs, "no notes path text naming DEV_LOOP_NOTES and dev-loop.local.json")
            text = paragraphs[0]
            self.assertIn("main worktree", text)
            env, local = text.index("DEV_LOOP_NOTES"), text.index("dev-loop.local.json")
            self.assertLess(env, local, text)
            self.assertIn("dev-loop.json", text[local:], text)
            self.assertNotRegex(kit, r"dev-loop\.local\.json`?(?: file)? in the (?:current )?worktree")


if __name__ == "__main__":
    unittest.main()
