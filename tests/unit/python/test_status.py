"""`devloop.py status`: the stage of a feature and the open features."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from board_fixtures import slow
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
        put(wt / ".superpowers" / "sdd" / "20261002 Widget Plan" / "plan-path", f"{self.notes / 'plans' / '20261002 Widget Plan.md'}\n")
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
        self.assertIn("fix wave, then write the review-done marker (review-save --done)", out["next"])
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

    def with_effort(self, level: str | None, drop_notes: bool = False) -> dict[str, str]:
        env = {k: v for k, v in self.env.items() if k != "CLAUDE_EFFORT" and not (drop_notes and k == "DEV_LOOP_NOTES")}
        if level is not None:
            env["CLAUDE_EFFORT"] = level
        return env

    def test_effort_want_per_stage(self) -> None:
        def want(level: str, value: str | None) -> dict:
            return {"level": level, "want": value}

        self.assertEqual(self.status(env=self.with_effort("medium"))["effort"], want("medium", "high"))
        self.spec(review="draft")
        out = self.status("--feature", "widget", env=self.with_effort("high"))
        self.assertEqual((out["stage"], out["effort"]), (2, want("high", "high")))
        wt = self.built()
        out = self.status(cwd=wt, env=self.with_effort("high"))
        self.assertEqual((out["stage"], out["effort"]), (5, want("high", "medium")))
        self.ledger(wt, "Task 1: complete\nTask 2: complete\nFeature gate: green at abc123\n")
        self.commit(wt, "widget.py", "WIDGET = 1\n")
        out = self.status(cwd=wt, env=self.with_effort("high"))
        self.assertEqual((out["stage"], out["effort"]), (7, want("high", "medium")))

    def test_effort_want_stage_3(self) -> None:
        self.spec()
        wt = self.worktree()
        out = self.status(cwd=wt, env=self.with_effort("high"))
        self.assertEqual((out["stage"], out["effort"]), (3, {"level": "high", "want": "medium"}))

    def test_effort_want_track_s_shipped_and_stage_0(self) -> None:
        git(self.repo, "switch", "-q", "-c", "fix/tiny")
        out = self.status(env=self.with_effort("low"))
        self.assertEqual((out["stage"], out["effort"]), (5, {"level": "low", "want": "medium"}))
        self.spec(shipped="2026-10-02")
        out = self.status("--feature", "widget", env=self.with_effort("high"))
        self.assertEqual((out["stage"], out["effort"]), ("shipped", {"level": "high", "want": None}))
        git(self.repo, "rm", "-q", "GLOSSARY.md")
        out = self.status(env=self.with_effort("high"))
        self.assertEqual((out["stage"], out["effort"]), (0, {"level": "high", "want": None}))
        out = self.status(env=self.with_effort("high", drop_notes=True))
        self.assertEqual((out["stage"], out["effort"]), (0, {"level": "high", "want": None}))

    def test_effort_level_values(self) -> None:
        for raw in (None, "", "turbo", "HIGH", " high"):
            with self.subTest(raw=raw):
                self.assertEqual(self.status(env=self.with_effort(raw))["effort"]["level"], None)
        for level in ("low", "medium", "high", "xhigh", "max"):
            with self.subTest(level=level):
                self.assertEqual(self.status(env=self.with_effort(level))["effort"]["level"], level)

    def test_effort_want_in_a_rebase(self) -> None:
        wt = self.worktree()
        git(self.repo, "switch", "-q", "-c", "fix/tiny")
        self.commit(self.repo, "a.txt", "mine\n")
        self.commit(wt, "a.txt", "wt-mine\n")
        git(self.repo, "switch", "-q", "main")
        self.commit(self.repo, "a.txt", "theirs\n")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "switch", "-q", "fix/tiny")
        for cwd, want in ((self.repo, "high"), (wt, "medium")):
            subprocess.run([*GIT, "rebase", "origin/main"], cwd=cwd, capture_output=True)
            out = self.status(cwd=cwd, env=self.with_effort("low"))
            with self.subTest(cwd=cwd):
                self.assertEqual((out["stage"], out["effort"]), ("rebase", {"level": "low", "want": want}))

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

    def test_main_scan_cost_grows_with_neither_specs_nor_detached_worktrees(self) -> None:
        for n in range(2):
            self.spec(name=f"2025010{n} Old {n}.md", feature=f"old{n}")
        detached = []
        for n in range(2):
            path = self.root / f"wt-agent-{n}"
            git(self.repo, "worktree", "add", "-q", "--detach", str(path), "HEAD")
            detached.append(str(path.resolve()))
        log, bins = self.root / "git.log", self.root / "bins"
        bins.mkdir()
        shim = bins / "git"
        shim.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nexec "{shutil.which("git")}" "$@"\n')
        shim.chmod(0o755)
        env = {**self.env, "PATH": f"{bins}{os.pathsep}{self.env['PATH']}"}
        self.assertEqual(self.status(env=env)["features"], [])
        calls = log.read_text().splitlines()
        self.assertEqual(sum(call.endswith("worktree list --porcelain") for call in calls), 1, calls)
        self.assertEqual([call for call in calls if any(path in call for path in detached)], [])

    def test_main_scan_reads_each_spec_once(self) -> None:
        paths = []
        for n in range(3):
            name = f"2025010{n} Draft {n}.md"
            self.spec(name=name, feature=f"draft{n}", review="draft")
            paths.append(self.notes / "specs" / name)
        for n in range(3, 5):
            path = self.notes / "Features" / f"2025010{n} Draft {n}" / "Spec.md"
            put(path, frontmatter(feature=f"draft{n}", review_status="draft"))
            paths.append(path)
        log, site = self.root / "reads.log", self.root / "site"
        put(site / "sitecustomize.py", "import pathlib\n_read = pathlib.Path.read_text\n"
            "def read_text(self, *args, **kwargs):\n"
            f"    with open({str(log)!r}, 'a') as out:\n        out.write(str(self) + '\\n')\n"
            "    return _read(self, *args, **kwargs)\npathlib.Path.read_text = read_text\n")
        env = {**self.env, "PYTHONPATH": str(site)}
        self.assertEqual([f["stage"] for f in self.status(env=env)["features"]], [2] * 5)
        reads = [os.path.realpath(line) for line in log.read_text().splitlines()]
        want = [os.path.realpath(path) for path in paths]
        self.assertEqual({path: reads.count(path) for path in want}, dict.fromkeys(want, 1), reads)

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


class KitDriftTests(Sandbox):
    def manifest(self, cwd: Path, version: object) -> None:
        put(cwd / ".dev-loop" / "MANIFEST.json", json.dumps({"version": version}))

    def commit_manifest(self, version: str) -> None:
        self.manifest(self.repo, version)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", f"kit {version}")

    def feature_worktree(self, version: str) -> Path:
        path = self.root / "wt-kit"
        git(self.repo, "worktree", "add", "-q", "-b", "feat/kit", str(path), "main")
        self.manifest(path, version)
        return path

    def remote_main(self, raw: str | None) -> None:
        """Point `refs/remotes/origin/main` at a commit holding `raw` as the MANIFEST (none: no MANIFEST)."""
        base = git(self.repo, "rev-parse", "HEAD").strip()
        if raw is not None:
            put(self.repo / ".dev-loop" / "MANIFEST.json", raw)
            git(self.repo, "add", "-A")
            git(self.repo, "commit", "-q", "--allow-empty", "-m", "remote kit")
        git(self.repo, "update-ref", "refs/remotes/origin/main", git(self.repo, "rev-parse", "HEAD").strip())
        git(self.repo, "reset", "-q", "--hard", base)

    def drift(self, cwd: Path) -> dict | None:
        return self.status(cwd=cwd).get("kit_drift")

    def test_main_newer_than_the_worktree_or_the_main_worktree(self) -> None:
        self.commit_manifest("3.0.0-rc.1")
        wt = self.feature_worktree("3.0.0-rc.1")
        self.remote_main(json.dumps({"version": "3.0.0-rc.2"}))
        want = {"worktree": "3.0.0-rc.1", "main": "3.0.0-rc.2", "direction": "main-newer"}
        self.assertEqual(self.drift(wt), want)
        self.assertEqual(self.drift(self.repo), want)
        self.remote_main(json.dumps({"version": "3.0.0"}))
        self.commit_manifest("3.0.0-rc.2")
        self.manifest(wt, "3.0.0-rc.2")
        self.assertEqual(self.drift(wt), {"worktree": "3.0.0-rc.2", "main": "3.0.0", "direction": "main-newer"})

    def test_worktree_newer_than_the_main_worktree(self) -> None:
        self.commit_manifest("3.0.0-rc.1")
        wt = self.feature_worktree("3.0.0-rc.2")
        self.assertEqual(self.drift(wt), {"worktree": "3.0.0-rc.2", "main": "3.0.0-rc.1", "direction": "worktree-newer"})

    def test_equal_versions_report_no_drift(self) -> None:
        self.commit_manifest("3.0.0")
        self.remote_main(json.dumps({"version": "3.0.0"}))
        self.assertIsNone(self.drift(self.feature_worktree("3.0.0")))

    @slow
    def test_an_unreadable_main_manifest_reports_no_main_newer(self) -> None:
        self.commit_manifest("3.0.0-rc.1")
        wt = self.feature_worktree("3.0.0-rc.1")
        for raw in (None, "not json", "{}", json.dumps({"version": 3}), json.dumps({"version": None}), "[]"):
            with self.subTest(raw=raw):
                self.remote_main(raw)
                self.assertIsNone(self.drift(wt))
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        self.assertIsNone(self.drift(wt))

    def test_a_local_branch_named_origin_main_is_not_the_main_ref(self) -> None:
        self.commit_manifest("3.0.0-rc.1")
        wt = self.feature_worktree("3.0.0-rc.1")
        self.remote_main(json.dumps({"version": "3.0.0-rc.1"}))
        self.commit_manifest("3.0.0-rc.9")
        git(self.repo, "update-ref", "refs/heads/origin/main", "HEAD")
        git(self.repo, "reset", "-q", "--hard", "HEAD~1")
        self.assertIsNone(self.drift(wt))


class FeatureFolderStatusTests(Sandbox):
    def worktree(self) -> Path:
        path = self.root / "wt-widget"
        git(self.repo, "worktree", "add", "-q", "-b", "feat/widget", str(path), "origin/main")
        return path.resolve()

    def folder(self) -> Path:
        folder = self.notes / "Features" / "20261101 Demo Work"
        put(folder / "Spec.md", frontmatter(feature="widget", track="M", review_status="approved"))
        return folder

    def test_status_reads_plan_and_marker_ledger(self) -> None:
        folder = self.folder()
        put(folder / "Plan.md", "# Plan\n\n### Task 1: one\n")
        wt = self.worktree()
        put(wt / "tests" / "acceptance" / "api" / "test_widget_flow.py", "def test_flow():\n    pass\n")
        sdd = wt / ".superpowers" / "sdd" / "Plan-x"
        put(sdd / "plan-path", f"{folder / 'Plan.md'}\n")
        put(sdd / "progress.md", "Task 1: complete\n")
        self.assertNotEqual(self.status("--feature", "widget", cwd=self.repo)["stage"], 5)

    def test_status_message_names_folder(self) -> None:
        self.folder()
        wt = self.worktree()
        put(wt / "tests" / "acceptance" / "api" / "test_widget_flow.py", "def test_flow():\n    pass\n")
        out = self.status("--feature", "widget", cwd=self.repo)
        self.assertEqual(out["stage"], 4)
        self.assertIn("20261101 Demo Work", out["check"])
        self.assertNotIn("Spec.md", out["check"])


class FindPlanLedgerTests(Sandbox):
    def test_feature_folder_spec_without_plan_ignores_the_plans_folder(self) -> None:
        dl = load_devloop()
        spec = self.notes / "Features" / "20261101 Demo Work" / "Spec.md"
        put(spec, frontmatter(feature="widget"))
        put(self.notes / "plans" / "x.md", "---\nspec: Spec.md\n---\n# Plan\n")
        self.assertIsNone(dl.find_plan(self.notes, spec))

    def test_old_specs_folder_spec_still_matches_the_plans_folder(self) -> None:
        dl = load_devloop()
        spec = self.notes / "specs" / "widget.md"
        put(spec, frontmatter(feature="widget"))
        plan = self.notes / "plans" / "widget-plan.md"
        put(plan, "---\nspec: widget.md\n---\n# Plan\n")
        self.assertEqual(dl.find_plan(self.notes, spec), plan)

    def test_ledger_falls_back_to_a_folder_with_no_marker(self) -> None:
        dl = load_devloop()
        top = self.root / "wt"
        plan = self.notes / "plans" / "widget-plan.md"
        put(plan, "# Plan\n")
        put(top / ".superpowers" / "sdd" / "widget-plan" / "progress.md", "Task 1: complete\n")
        self.assertEqual(dl.ledger(top, plan), top / ".superpowers" / "sdd" / "widget-plan" / "progress.md")

    def test_ledger_ignores_a_folder_with_a_marker_for_another_plan(self) -> None:
        dl = load_devloop()
        top = self.root / "wt"
        plan = self.notes / "plans" / "widget-plan.md"
        put(plan, "# Plan\n")
        sdd = top / ".superpowers" / "sdd" / "widget-plan"
        put(sdd / "progress.md", "Task 1: complete\n")
        put(sdd / "plan-path", str(self.notes / "plans" / "other.md") + "\n")
        self.assertIsNone(dl.ledger(top, plan))
