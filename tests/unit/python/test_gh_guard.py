"""The gh rule of both git-guard.sh copies: gh writes ask, reads and gh as text run."""

from __future__ import annotations

import unittest

from support import GuardRepo

CANNOT = "a gh command the guard cannot read"


class GhGuardTests(GuardRepo, unittest.TestCase):
    """Both guards on hook JSON in the scratch repo of GuardRepo."""

    def test_gh_release_writes_are_refused(self) -> None:
        for command, word in (("gh release delete v1.0.0 --cleanup-tag --yes", "delete"),
                              ("gh release edit v1.0.0 --draft=false", "edit"),
                              ("gh release upload v1.0.0 x.tgz --clobber", "upload"),
                              ("gh release delete-asset v1.0.0 x.tgz", "delete-asset"),
                              ("gh release -R o/r delete v1.0.0", "delete"),
                              ("gh release frob v1.0.0", "frob")):
            self.assert_refused([command], f"a gh release {word}")

    def test_gh_release_reads_run(self) -> None:
        self.assert_allowed(["gh release list", "gh release view v1.0.0", "gh -R o/r release view v1.0.0",
                             "gh release download v1.0.0", "gh release verify v1.0.0",
                             "gh release verify-asset v1.0.0 x.tgz"])

    def test_unknown_words_and_agent_runs_are_refused(self) -> None:
        self.assert_refused(["gh rel v9.9.9", "gh co 12", "gh my-ext", "gh $CMD"], CANNOT)
        self.assert_refused(["gh extension exec my-ext", "gh copilot"], "a gh extension or agent run")

    def test_unchanged_builtins_run(self) -> None:
        self.assert_allowed(['gh pr create --title x --body "$(cat notes.md)"', "gh pr view 7 --json state",
                             "gh issue view 89", "gh run list", "gh run watch 1", "gh auth status", "gh label list",
                             "gh help api", "gh --version", "gh extension list",
                             "gh pr view 7 --json state > pr.json", "gh pr view 7 2>&1"])

    def test_gh_as_text_runs(self) -> None:
        self.assert_allowed(["grep -rn gh skill", "rg gh api", "which gh git", "ls ~/.config/gh", "command -v gh >/dev/null",
                             "command -v gh > /dev/null 2>&1", "brew install gh", "mkdir /tmp/gh /tmp/x",
                             "echo gh api -X POST repos/o/r/releases"])

    def test_wrapped_gh_is_refused(self) -> None:
        delete = "gh release delete v1.0.0"
        self.assert_refused([f"timeout 30 {delete}", f"/opt/homebrew/bin/{delete}", f"GH_REPO=o/r {delete}",
                             f"sudo -u me {delete}", f"env -i {delete}", f"nice -n 5 {delete}", f"exec {delete}",
                             f"if {delete}; then :; fi", f"sudo --user me {delete}", f"timeout --signal KILL 30 {delete}",
                             f"env --unset X {delete}"], "a gh release delete")

    def test_fed_gh_is_refused(self) -> None:
        self.assert_refused(["echo 'api -X POST repos/o/r/releases' | xargs gh", "echo v1.0.0 | xargs gh release view"], CANNOT)
