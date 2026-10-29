"""The git push rule of both git-guard.sh copies on other spellings: abbreviated options, refspec forms, push config."""

from __future__ import annotations

import unittest

from support import GuardRepo

PROTECTED = "a force, delete, mirror, all, prune or tag push"


class PushSpellingTests(GuardRepo, unittest.TestCase):
    """Both guards on hook JSON in the scratch repo of GuardRepo."""

    def test_abbreviated_protected_options_are_refused(self) -> None:
        self.assert_refused(["git push --m origin", "git push --mi origin", "git push --d origin feat/x", "git push --de origin feat/x",
                             "git push --a origin", "git push --al origin", "git push --b origin", "git push --br origin",
                             "git push --t", "git push --ta origin", "git push --p origin", "git push --pr origin", "git push --f",
                             "git push --fo origin feat/x", "git push --fol"], PROTECTED)

    def test_only_the_exact_lease_spelling_keeps_the_exception(self) -> None:
        self.assert_refused(["git push --force-w origin feat/x", "git push --force-i --force-with-lease origin feat/x"], PROTECTED)
        command = "git push --force-with-lease --force-if-includes"
        for harness in ("claude", "codex"):
            with self.subTest(harness=harness):
                self.assertEqual(self.guard(harness, command)[0], "allow")
                self.assertEqual(self.guard(harness, command, agent_id="a1")[0], "deny")

    def test_other_options_run(self) -> None:
        self.assert_allowed([f"git push {option} origin feat/x" for option in (
            "--dry-run", "--porcelain", "--atomic", "--thin", "--progress", "--no-verify", "--set-upstream", "--verbose")])

    def test_recurse_submodules_takes_a_value(self) -> None:
        self.assert_allowed(["git push --recurse-submodules no origin"])
        self.git("checkout", "-q", "main")
        self.assert_refused(["git push --recurse-submodules no origin", "git push --recurse-sub no origin"], "a push to main or master")

    def test_main_spellings_are_refused(self) -> None:
        self.assert_refused(["git push origin heads/main", "git push origin refs/heads/main", "git push origin HEAD:heads/main",
                             "git push origin feat/x:heads/main", "git push origin HEAD:refs/heads/master"], "a push to main or master")

    def test_tag_spellings_carry_release_check(self) -> None:
        self.stub("ok")
        self.assert_refused(["git push origin tags/v1.0.0", "git push origin refs/tags/v1.0.0"], "a tag push", "release-check:")
        self.assertIn("--tag v1.0.0", self.calls.read_text())
        self.assert_refused(["git push origin HEAD:tags/v9.9.9"], f"a tag push of {self.c}")
        self.assertIn(f"--sha {self.c}", self.calls.read_text())

    def test_refs_that_are_no_feature_branch_are_refused(self) -> None:
        self.assert_refused([f"git push origin {spec}" for spec in (
            "HEAD:remotes/origin/main", "HEAD:refs/remotes/origin/main", "HEAD:refs/notes/x", "feat/x:HEAD", "origin/main",
            "nosuch", self.a)], "a push to a ref that is not a feature branch")

    def test_feature_branch_spellings_run(self) -> None:
        self.assert_allowed([f"git push origin {spec}" for spec in (
            "feat/x", "heads/feat/x", "refs/heads/feat/x", "HEAD:feat/y", "HEAD:refs/heads/feat/y", f"{self.a}:refs/heads/feat/y")])

    def test_a_name_both_branch_and_tag_is_a_tag_push(self) -> None:
        self.git("branch", "v2")
        self.git("tag", "v2")
        self.assert_refused(["git push origin v2"], "a tag push")
