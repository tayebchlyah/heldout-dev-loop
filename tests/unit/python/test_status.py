"""`devloop.py status`: the stage of a feature and the open features."""

from __future__ import annotations

import subprocess
from pathlib import Path

from test_devloop import GIT, PLAN, Sandbox, frontmatter, git, load_devloop, load_script, put


class StatusTests(Sandbox):
    def spec(self, review: str = "approved", name: str = "20261001 Add Widget.md", **extra: str) -> None:
        fields = {"feature": "widget", "track": "M", "review_status": review, **extra}
        put(self.notes / "specs" / name, frontmatter(**fields))

    def worktree(self) -> Path:
        path = self.root / "wt-widget"
        git(self.repo, "worktree", "add", "-q", "-b", "feat/widget", str(path), "origin/main")
        return path.resolve()

    def acceptance(self, wt: Path) -> None:
        put(wt / "tests" / "acceptance" / "api" / "test_widget_flow.py", "def test_flow():\n    pass\n")

    def ledger(self, wt: Path, text: str) -> None:
        put(wt / ".superpowers" / "sdd" / "20261002 Widget Plan" / "progress.md", text)

    def built(self) -> Path:
        self.spec()
        wt = self.worktree()
        self.acceptance(wt)
        put(self.notes / "plans" / "20261002 Widget Plan.md", PLAN)
        return wt

    def test_stage_0_without_a_glossary(self) -> None:
        git(self.repo, "rm", "-q", "GLOSSARY.md")
        out = self.status()
        self.assertEqual(out["stage"], 0)
        self.assertIn("glossary", out["check"].lower())

    def test_stage_0_without_a_notes_path(self) -> None:
        env = {k: v for k, v in self.env.items() if k != "DEV_LOOP_NOTES"}
        out = self.status(env=env)
        self.assertEqual(out["stage"], 0)
        self.assertTrue(out["next"].startswith(f"no notes path for {self.repo}"), out)
        self.assertIn("dev-loop.local.json", out["next"])

    def test_stage_0_without_a_commit(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        git(empty, "init", "-q")
        put(empty / "GLOSSARY.md")
        self.assertEqual(self.status(cwd=empty)["stage"], 0)

    def test_stage_1_on_main_with_no_open_feature(self) -> None:
        out = self.status()
        self.assertEqual(out["stage"], 1)
        self.assertEqual(out["features"], [])
        self.assertIsNone(out["feature"])

    def test_track_s_branch_is_build_then_ship_after_a_commit(self) -> None:
        git(self.repo, "switch", "-q", "-c", "fix/tiny")
        out = self.status()
        self.assertEqual((out["stage"], out["feature"], out["track"]), (5, "tiny", "S"))
        self.assertNotIn("features", out)
        self.commit(self.repo, "a.txt", "a\n")
        out = self.status()
        self.assertEqual(out["stage"], 6)
        self.assertIn("review-done", out["next"])
        self.assertIn("fix wave, then write the review-done marker", out["next"])
        marker = self.repo / ".git" / "dev-loop" / "review-done"
        put(marker, git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(self.status()["stage"], 7)

    def test_stage_2_unapproved_spec(self) -> None:
        self.spec(review="draft")
        out = self.status("--feature", "widget")
        self.assertEqual((out["stage"], out["feature"], out["track"]), (2, "widget", "M"))

    def test_stage_3_no_acceptance_file(self) -> None:
        self.spec()
        wt = self.worktree()
        out = self.status(cwd=wt)
        self.assertEqual((out["stage"], out["worktree"]), (3, str(wt)))

    GADGET = "tests/acceptance/api/test_gadget.py"

    def test_acceptance_commit_on_the_branch_ends_stage_3(self) -> None:
        """Stage 3 extends earlier acceptance files only, no file named after the slug: the trailer line counts."""
        self.spec()
        self.commit(self.repo, self.GADGET, "def test_gadget():\n    pass\n")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "origin")
        wt = self.worktree()
        self.assertEqual(self.status(cwd=wt)["stage"], 3)
        self.commit(wt, self.GADGET, "def test_gadget():\n    assert True\n",
                    "Extend the gadget flow\n\nAcceptance: widget")
        self.assertEqual(self.status(cwd=wt)["stage"], 4)

    def test_acceptance_commit_with_only_the_slug_in_the_subject_keeps_stage_3(self) -> None:
        self.spec()
        wt = self.worktree()
        self.commit(wt, self.GADGET, "def test_gadget():\n    pass\n", "\u2705(widget) Acceptance for the widget")
        out = self.status(cwd=wt)
        self.assertEqual(out["stage"], 3)
        self.assertIn("Acceptance: widget", out["next"])

    def test_acceptance_line_of_a_longer_slug_does_not_count(self) -> None:
        self.spec()
        wt = self.worktree()
        self.commit(wt, self.GADGET, "def test_gadget():\n    pass\n", "Gadget\n\nAcceptance: widget-export")
        self.assertEqual(self.status(cwd=wt)["stage"], 3)

    def test_acceptance_line_without_a_touch_of_tests_acceptance_does_not_count(self) -> None:
        self.spec()
        wt = self.worktree()
        self.commit(wt, "a.txt", "a\n", "Elsewhere\n\nAcceptance: widget")
        self.assertEqual(self.status(cwd=wt)["stage"], 3)

    def test_acceptance_commit_without_origin_main_ends_stage_3(self) -> None:
        self.spec()
        wt = self.worktree()
        self.commit(wt, self.GADGET, "def test_gadget():\n    pass\n", "Gadget\n\nAcceptance: widget")
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        self.assertEqual(self.status(cwd=wt)["stage"], 4)

    def test_acceptance_commit_still_counts_once_origin_main_holds_it(self) -> None:
        """Branch rebased and pushed, origin/main fast-forwarded to it: origin/main..HEAD is empty."""
        self.spec()
        wt = self.worktree()
        self.commit(wt, self.GADGET, "def test_gadget():\n    pass\n", "Gadget\n\nAcceptance: widget")
        git(wt, "push", "-q", "origin", "HEAD:main")
        git(self.repo, "fetch", "-q", "origin")
        self.assertNotEqual(self.status(cwd=wt)["stage"], 3)

    def test_acceptance_regression_or_none_skips_stage_3(self) -> None:
        wt = self.worktree()
        for value in ["regression", "none", "None"]:
            with self.subTest(value=value):
                self.spec(acceptance=value)
                self.assertEqual(self.status(cwd=wt)["stage"], 4)

    def test_acceptance_new_or_unknown_keeps_stage_3(self) -> None:
        wt = self.worktree()
        for value in ["", "new", "skip"]:
            with self.subTest(value=value):
                self.spec(acceptance=value)
                self.assertEqual(self.status(cwd=wt)["stage"], 3)

    def test_acceptance_skip_still_needs_the_worktree(self) -> None:
        self.spec(acceptance="none")
        self.assertEqual(self.status("--feature", "widget")["stage"], 3)

    def test_stage_4_no_plan_naming_the_spec(self) -> None:
        self.spec()
        wt = self.worktree()
        self.acceptance(wt)
        put(self.notes / "plans" / "20261002 Other Plan.md", "---\nspec: 20260901 Other.md\n---\n")
        self.assertEqual(self.status(cwd=wt)["stage"], 4)

    def test_stage_5_ledger_missing_or_incomplete(self) -> None:
        wt = self.built()
        self.assertEqual(self.status(cwd=wt)["stage"], 5)
        self.ledger(wt, "# Ledger\nTask 1: complete\n")
        out = self.status(cwd=wt)
        self.assertEqual(out["stage"], 5)
        self.assertIn("2", out["check"])

    def test_stage_6_no_gate_line(self) -> None:
        wt = self.built()
        self.ledger(wt, "Task 1: complete\nTask 2: complete (abc123)\n")
        self.assertEqual(self.status(cwd=wt)["stage"], 6)

    def test_stage_7_unmerged_then_shipped_after_a_rebase_merge(self) -> None:
        wt = self.built()
        self.ledger(wt, "Task 1: complete\nTask 2: complete\nFeature gate: green at abc123\n")
        self.commit(wt, "widget.py", "WIDGET = 1\n")
        self.assertEqual(self.status(cwd=wt)["stage"], 7)
        # A rebase merge: same patch, new commit, on origin/main.
        self.commit(self.repo, "other.txt", "other\n")
        git(self.repo, "cherry-pick", "feat/widget")
        git(self.repo, "push", "-q", "origin", "main")
        out = self.status(cwd=wt)
        self.assertEqual(out["stage"], "shipped")

    def test_stage_7_without_origin_main_says_fetch(self) -> None:
        wt = self.built()
        self.ledger(wt, "Task 1: complete\nTask 2: complete\nFeature gate: green at abc123\n")
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        out = self.status(cwd=wt)
        self.assertEqual(out["stage"], 7)
        self.assertIn("git fetch origin", out["next"])

    def test_failed_cherry_says_fetch_and_never_ships(self) -> None:
        wt = self.built()
        self.ledger(wt, "Task 1: complete\nTask 2: complete\nFeature gate: green at abc123\n")
        tree = git(self.repo, "rev-parse", "HEAD^{tree}").strip()
        git(self.repo, "update-ref", "refs/remotes/origin/main", tree)
        out = self.status(cwd=wt)
        self.assertEqual(out["stage"], 7)
        self.assertIn("git fetch origin", out["next"])
        git(self.repo, "switch", "-q", "-c", "fix/tiny")
        out = self.status()
        self.assertEqual(out["stage"], 5)
        self.assertIn("git fetch origin", out["next"])

    def test_approved_spec_with_no_branch_and_acceptance_on_main_is_shipped(self) -> None:
        self.spec()
        self.commit(self.repo, "tests/acceptance/api/test_widget_flow.py", "def test_flow():\n    pass\n")
        self.assertEqual(self.status("--feature", "widget")["stage"], "shipped")
        self.assertEqual(self.status()["features"], [])

    def test_approved_spec_with_no_branch_and_its_acceptance_commit_on_main_is_shipped(self) -> None:
        """A feature that only extended earlier acceptance files, merged, branch gone, `shipped:` not written yet."""
        self.spec()
        self.commit(self.repo, self.GADGET, "def test_gadget():\n    pass\n", "\u2705(widget) Acceptance for the widget")
        self.assertEqual(self.status("--feature", "widget")["stage"], 3)
        self.commit(self.repo, self.GADGET, "def test_gadget():\n    assert 1\n", "Extend gadget\n\nAcceptance: widget")
        out = self.status("--feature", "widget")
        self.assertEqual(out["stage"], "shipped")
        self.assertIn("Acceptance: widget", out["check"])

    def test_acceptance_commit_only_on_origin_main_is_shipped(self) -> None:
        """Local main is behind origin/main."""
        self.spec()
        self.commit(self.repo, self.GADGET, "def test_gadget():\n    assert 1\n", "Extend gadget\n\nAcceptance: widget")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "origin")
        git(self.repo, "reset", "-q", "--hard", "HEAD~1")
        self.assertEqual(self.status("--feature", "widget")["stage"], "shipped")

    def test_shipped_field_wins_over_an_open_branch(self) -> None:
        self.spec(shipped="2026-10-01", pr="https://example.invalid/pr/1")
        wt = self.worktree()
        self.commit(wt, "widget.py", "WIDGET = 1\n")
        out = self.status(cwd=wt)
        self.assertEqual(out["stage"], "shipped")
        self.assertIn("shipped: 2026-10-01", out["check"])
        self.assertEqual(self.status()["features"], [])

    def test_empty_shipped_field_keeps_it_open(self) -> None:
        self.spec(shipped="", pr="")
        self.worktree()
        self.assertEqual(self.status("--feature", "widget")["stage"], 3)

    def test_remote_feature_branch_keeps_it_open(self) -> None:
        self.spec()
        self.commit(self.repo, "tests/acceptance/api/test_widget_flow.py", "def test_flow():\n    pass\n")
        git(self.repo, "update-ref", "refs/remotes/origin/feat/widget", "HEAD")
        self.assertEqual(self.status("--feature", "widget")["stage"], 3)

    def test_slug_matches_a_whole_token_only(self) -> None:
        holds = load_script("status").holds_slug
        self.assertTrue(holds("api/test_add_widget.py", "add-widget"))
        self.assertTrue(holds("e2e/add-widget.spec.ts", "add-widget"))
        self.assertFalse(holds("api/test_oauth_login.py", "auth"))
        self.spec(feature="auth")
        self.commit(self.repo, "tests/acceptance/api/test_oauth_login.py", "def test_flow():\n    pass\n")
        self.assertEqual(self.status("--feature", "auth")["stage"], 3)

    def test_merged_branch_without_worktree_is_shipped(self) -> None:
        self.spec()
        git(self.repo, "switch", "-q", "-c", "feat/widget")
        self.commit(self.repo, "widget.py", "WIDGET = 1\n")
        git(self.repo, "switch", "-q", "main")
        self.commit(self.repo, "other.txt", "other\n")
        git(self.repo, "cherry-pick", "feat/widget")
        git(self.repo, "push", "-q", "origin", "main")
        self.assertEqual(self.status("--feature", "widget")["stage"], "shipped")
        # Stale remote-tracking ref, local branch deleted after the merge.
        git(self.repo, "update-ref", "refs/remotes/origin/feat/widget", "feat/widget")
        git(self.repo, "branch", "-q", "-D", "feat/widget")
        self.assertEqual(self.status("--feature", "widget")["stage"], "shipped")

    def test_rebase_in_progress_is_its_own_stage(self) -> None:
        git(self.repo, "switch", "-q", "-c", "fix/tiny")
        self.commit(self.repo, "a.txt", "mine\n")
        git(self.repo, "switch", "-q", "main")
        self.commit(self.repo, "a.txt", "theirs\n")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "switch", "-q", "fix/tiny")
        subprocess.run([*GIT, "rebase", "origin/main"], cwd=self.repo, capture_output=True)
        for args in [(), ("--feature", "tiny")]:
            out = self.status(*args)
            with self.subTest(args=args):
                self.assertEqual((out["stage"], out["feature"]), ("rebase", "tiny"))
                self.assertIn("finish or abort the rebase", out["next"])

    def test_main_lists_open_features(self) -> None:
        self.spec(review="draft")
        self.spec(name="20261003 Gadget.md", feature="gadget")
        git(self.repo, "branch", "feat/gadget")
        out = self.status()
        self.assertEqual(out["stage"], 1)
        self.assertEqual(
            out["features"],
            [{"feature": "widget", "stage": 2, "worktree": None}, {"feature": "gadget", "stage": 3, "worktree": None}],
        )

    def test_main_leaves_out_an_approved_spec_with_no_branch_and_no_worktree(self) -> None:
        self.spec(name="20250101 Old Feature.md", feature="F1")
        self.spec(name="20261003 Draft.md", feature="draft", review="draft")
        self.spec(name="20261004 Remote.md", feature="remote")
        git(self.repo, "update-ref", "refs/remotes/origin/feat/remote", "HEAD")
        self.spec()
        wt = self.worktree()
        self.assertEqual(
            self.status()["features"],
            [{"feature": "widget", "stage": 3, "worktree": str(wt)},
             {"feature": "draft", "stage": 2, "worktree": None},
             {"feature": "remote", "stage": 3, "worktree": None}],
        )
        self.assertEqual(self.status("--feature", "F1")["stage"], 3)

    def test_slug_comparison_ignores_case(self) -> None:
        self.spec(feature="F1", review="draft")
        self.assertEqual(self.status("--feature", "f1")["stage"], 2)
        self.spec(feature="F1")
        path = self.root / "wt-f1"
        git(self.repo, "worktree", "add", "-q", "-b", "feat/f1", str(path), "origin/main")
        out = self.status(cwd=path)
        self.assertEqual((out["stage"], out["feature"], out["track"]), (3, "f1", "M"))
        put(path / "tests" / "acceptance" / "api" / "test_f1.py", "def test_flow():\n    pass\n")
        self.assertEqual(self.status("--feature", "F1")["stage"], 4)
        self.assertEqual(self.status()["features"], [{"feature": "f1", "stage": 4, "worktree": str(path.resolve())}])

    def test_slug_from_title(self) -> None:
        self.assertEqual(load_devloop().slug_from_title("20250901 Fix: the API, v2.md"), "fix-the-api-v2")

    def test_frontmatter_feature_wins_over_title(self) -> None:
        put(self.notes / "specs" / "20250901 Fix: the API, v2.md", frontmatter(feature="gizmo", review_status="draft"))
        self.assertEqual(self.status("--feature", "gizmo")["stage"], 2)
        self.assertEqual(self.status("--feature", "fix-the-api-v2")["stage"], 1)

    def test_spec_without_feature_resolves_by_its_title(self) -> None:
        put(self.notes / "specs" / "20250901 Fix: the API, v2.md", frontmatter(review_status="draft"))
        self.assertEqual(self.status("--feature", "fix-the-api-v2")["stage"], 2)
