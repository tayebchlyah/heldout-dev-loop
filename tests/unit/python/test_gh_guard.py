"""The gh rule of both git-guard.sh copies: gh writes ask, reads and gh as text run."""

from __future__ import annotations

import unittest

from support import GuardRepo

CANNOT = "a gh command the guard cannot read"


class GhGuardTests(GuardRepo, unittest.TestCase):
    """Both guards on hook JSON in the scratch repo of GuardRepo."""

    def test_gh_api_writes_are_refused(self) -> None:
        self.assert_refused(["gh api -X POST repos/o/r/releases -f tag_name=v9.9.9",
                             f"gh api --method=POST repos/o/r/git/refs -f ref=refs/tags/v9.9.9 -f sha={self.a}",
                             "gh api -XPATCH repos/o/r/git/refs/heads/main -F force=true",
                             "gh api -X DELETE repos/o/r/git/refs/tags/v1.0.0", "gh api -X delete repos/o/r/branches/main/protection",
                             "gh api -iX PUT repos/o/r/x", "gh api -X GET -X POST repos/o/r/x",
                             "gh api repos/o/r/releases -f tag_name=v9.9.9", "gh api repos/o/r/releases --input body.json",
                             "gh api graphql -f query='mutation{createRef(input:{repositoryId:\"R\",name:\"refs/tags/v9\","
                             f"oid:\"{self.a}\"}}){{ref{{name}}}}}}'",
                             "gh api graphql -F query=@q.graphql", "gh api graphql --input q.json"], "a gh api write")

    def test_gh_api_reads_run(self) -> None:
        self.assert_allowed(["gh api repos/o/r/pulls/7/comments", "gh api 'repos/{owner}/{repo}/pulls?state=open' --paginate --jq '.[].number'",
                             "gh api -X GET search/issues -f q=repo:o/r", "gh api --method HEAD repos/o/r",
                             "gh api graphql -f query='query{viewer{login}}'",
                             "gh api graphql -f query='query($o:String!){repository(owner:$o,name:\"r\"){id}}' -F o=x",
                             "gh api repos/o/r/pulls > prs.json"])

    def test_gh_api_lines_the_guard_cannot_read_are_refused(self) -> None:
        commands = ["gh api $ARGS", "gh api repos/o/r/x $(echo -X POST)", "gh api {-X,POST} repos/o/r/x",
                    "gh api --unknown graphql repos/o/r -f a=b", "gh api graphql repos/o/r -f a=b"]
        self.assert_refused(commands)
        for command in commands:
            reason = self.guard("claude", command)[1]
            self.assertTrue(CANNOT in reason or "a gh api write" in reason, (command, reason))

    def test_gh_release_writes_are_refused(self) -> None:
        for command, word in (("gh release delete v1.0.0 --cleanup-tag --yes", "delete"),
                              ("gh release edit v1.0.0 --draft=false", "edit"),
                              ("gh release upload v1.0.0 x.tgz --clobber", "upload"),
                              ("gh release delete-asset v1.0.0 x.tgz", "delete-asset"),
                              ("gh release -R o/r delete v1.0.0", "delete"),
                              ("gh release frob v1.0.0", "frob")):
            self.assert_refused([command], f"a gh release {word}")

    def test_release_create_target_off_the_local_tag(self) -> None:
        self.stub("ok")
        self.assert_refused([f"gh release create v1.0.0 --target {self.b}"], "release-check failed",
                            f"local tag v1.0.0 at {self.a}, --target at {self.b}")
        self.assert_refused(["gh release create v1.0.0 --target nowhere"], "release-check failed: target nowhere does not resolve")
        self.assertFalse(self.calls.exists())
        decision, reason, _ = self.guard("claude", f"gh release create v1.0.0 --target {self.a}")
        self.assertEqual(decision, "ask")
        self.assertIn(f"a release create at {self.a}", reason)
        self.assertIn("release-check: ok", reason)
        self.assertIn(f"--sha {self.a}", self.calls.read_text())

    def test_gh_release_reads_run(self) -> None:
        self.assert_allowed(["gh release list", "gh release view v1.0.0", "gh -R o/r release view v1.0.0",
                             "gh release download v1.0.0", "gh release verify v1.0.0",
                             "gh release verify-asset v1.0.0 x.tgz"])

    def test_family_writes_are_refused(self) -> None:
        for command, path in (("gh alias set rel 'release create'", "alias set"), ("gh alias delete rel", "alias delete"),
                              ("gh alias import a.yml", "alias import"), ("gh repo edit --default-branch x", "repo edit"),
                              ("gh repo delete o/r --yes", "repo delete"), ("gh repo sync", "repo sync"),
                              ("gh repo fork", "repo fork"), ("gh repo set-default o/r", "repo set-default"),
                              ("gh repo deploy-key add k.pub --allow-write", "repo deploy-key add"),
                              ("gh repo autolink create x y", "repo autolink create"), ("gh workflow run release.yml", "workflow run"),
                              ("gh workflow disable ci.yml", "workflow disable"), ("gh secret set X", "secret set"),
                              ("gh secret delete X", "secret delete"), ("gh variable set X", "variable set"),
                              ("gh variable delete X", "variable delete"), ("gh ruleset frob 1", "ruleset frob")):
            self.assert_refused([command], f"a gh {path}")

    def test_family_reads_run(self) -> None:
        self.assert_allowed(["gh alias list", "gh ruleset list", "gh ruleset view 1", "gh ruleset check main", "gh repo view",
                             "gh repo list", "gh repo clone o/r", "gh repo deploy-key list", "gh repo autolink list",
                             "gh repo gitignore view Python", "gh repo license list", "gh workflow list", "gh workflow view ci.yml",
                             "gh secret list", "gh variable list", "gh variable get X", "gh repo deploy-key", "gh workflow"])

    def test_admin_and_unreadable_merges_are_refused(self) -> None:
        self.assert_refused(["gh pr merge 12 --admin --rebase", "gh pr merge --admin=true 12", "gh pr -R o/r merge 12 --admin"],
                            "a merge that bypasses branch protection")
        self.assert_refused(["gh pr merge 12 --rebase --match-head-commit $SHA"], CANNOT)

    def test_everyday_merge_runs(self) -> None:
        self.assert_allowed([f"gh pr merge 12 --rebase --match-head-commit {self.a}", "gh pr merge --auto --rebase 12"])

    def test_unknown_words_and_agent_runs_are_refused(self) -> None:
        self.assert_refused(["gh rel v9.9.9", "gh co 12", "gh my-ext", "gh $CMD"], CANNOT)
        self.assert_refused(["gh alias set rel 'release create' && gh rel v9.9.9"], "a gh alias set")
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
        self.assert_refused(["sh -c 'gh api -X POST repos/o/r/releases -f tag_name=v9'",
                             "command gh api -X DELETE repos/o/r/git/refs/tags/v1.0.0",
                             "X=$(gh api -X POST repos/o/r/releases -f tag_name=v9)"], "a gh api write")

    def test_fed_gh_is_refused(self) -> None:
        self.assert_refused(["echo 'api -X POST repos/o/r/releases' | xargs gh", "echo v1.0.0 | xargs gh release view"], CANNOT)
