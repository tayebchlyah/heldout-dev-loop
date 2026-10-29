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
