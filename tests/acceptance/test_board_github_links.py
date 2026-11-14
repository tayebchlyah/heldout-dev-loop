#!/usr/bin/env python3
"""Held-out tests for the board GitHub links (spec: 20261114 Board GitHub Links): the board writer.

One test per `## Acceptance oracle` line 1 to 10 and 21 (O<n>) and per flow 1 and 2 (F<n>). Seams: `devloop.py
board` as a process on the temporary repo and notes folder of `board_fixtures.Board` (seam 1), its status note
frontmatter; the `dataviewjs` block of the `Dev Loop Board.md` it writes, run under Node with the stubs of Board
Ledger oracle 23, and `renderBoard` of render.js under Node (seam 2); `devloop.py ci wait` with a stub `gh` on
`PATH` (seam 3). Oracles 11 to 13 are in test_board_github_links_ci.py, 14 to 20 and flow 3 in
board_github_links.test.mjs, 22 and 23 in test_board_github_links_kit_text.py. Every name in here is invented,
except the origin of oracle 7, which the spec names.

Fixture choices the spec leaves open: see board_github_links_support.py; and
- a worktree's git dir is `git rev-parse --absolute-git-dir` in that worktree; `pr.json` goes in its `dev-loop/`;
- a spec `issue` is written as `issue: <value>` on the line after `review_status:`;
- a run that changes the origin is read through the notes its JSON `notes` lists, since the project may change.
"""

from __future__ import annotations

import os
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from board_github_links_support import (PROJECT, LinkBoard, StubGh, ci_wait, github_links, git, names, ok,
                                        put, read_note, render, row_summary, text, threads_doc, view)
from board_fixtures import note_name, note_text, slow, status_note

GITHUB = "https://github.com/acme/widgets"
KEYS = ("issue", "pr", "repo")


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class BoardWriterLinkTests(LinkBoard):
    def test_o1_spec_issue_wins_over_the_slug_number(self) -> None:
        """O1, D1: a spec `issue: 319` gives `issue: 319`, also when the slug starts with 316."""
        self.issue_spec("316-gadget", "Gadget Export", "319")
        self.worktree("316-gadget")
        self.issue_spec("parcel", "Parcel Labels", "319")
        self.worktree("parcel")
        self.ok()
        self.assertEqual(self.note("316-gadget")["issue"], 319)
        self.assertEqual(self.note("parcel")["issue"], 319)

    def test_o2_track_s_issue_from_the_branch_slug(self) -> None:
        """O2, D1: a Track S worktree on `feat/316-board-header`, no spec, gets `issue: 316`."""
        self.worktree("316-board-header")
        self.ok()
        note = self.note("316-board-header")
        self.assertEqual(note["track"], "S")
        self.assertEqual(note["issue"], 316)

    def test_o3_no_issue_and_invalid_spec_issues_give_null(self) -> None:
        """O3, D1: no spec `issue` and no leading number: null; a spec `issue` of abc, 0, -3 or 3.5 counts as
        none."""
        cases = {"alpha": None, "beta": "abc", "gamma": "0", "delta": "-3", "epsilon": "3.5"}
        for slug, issue in cases.items():
            self.issue_spec(slug, f"{slug.title()} Report", issue)
            self.worktree(slug)
        self.ok()
        for slug, issue in cases.items():
            with self.subTest(issue=issue):
                note = self.note(slug)
                self.assertIn("issue", note)
                self.assertIsNone(note["issue"])

    def test_o4_pr_json_of_the_worktree_branch_gives_pr(self) -> None:
        """O4, D2: `pr.json` in the git dir of the worktree on `feat/x`, branch `feat/x`, gives `pr: 318` at the
        next run."""
        wt = self.worktree("x")
        self.ok()
        self.write_pr(wt, {"number": 318, "url": "https://github.com/o/r/pull/318", "branch": "feat/x"})
        self.ok()
        self.assertEqual(self.note("x")["pr"], 318)

    def test_o5_missing_or_refused_pr_json_gives_null(self) -> None:
        """O5, D2: no file, not JSON, a number that is not a positive integer, a branch `feat/y` or no branch
        key: `pr: null`."""
        url = "https://github.com/o/r/pull/318"
        cases = {
            "nofile": None,
            "notjson": "{number: 318, branch: feat/notjson",
            "textnumber": {"number": "318", "url": url, "branch": "feat/textnumber"},
            "zero": {"number": 0, "url": url, "branch": "feat/zero"},
            "negative": {"number": -5, "url": url, "branch": "feat/negative"},
            "fraction": {"number": 3.5, "url": url, "branch": "feat/fraction"},
            "otherbranch": {"number": 318, "url": url, "branch": "feat/y"},
            "nobranch": {"number": 318, "url": url},
        }
        for slug, content in cases.items():
            wt = self.worktree(slug)
            if content is not None:
                self.write_pr(wt, content)
        self.ok()
        for slug in cases:
            with self.subTest(case=slug):
                note = self.note(slug)
                self.assertIn("pr", note)
                self.assertIsNone(note["pr"])

    def test_o6_linked_pr_json_gives_null_and_is_not_read(self) -> None:
        """O6, D2: a `pr.json` that is a symbolic link gives `pr: null`; its target is never read (a link to a
        FIFO would block a read, so the run ends)."""
        elsewhere = self.root / "elsewhere"
        target = elsewhere / "pr.json"
        put(target, '{"number": 318, "url": "https://github.com/o/r/pull/318", "branch": "feat/linked"}\n')
        fifo = elsewhere / "pipe"
        os.mkfifo(fifo)
        for slug, to in (("linked", target), ("piped", fifo)):
            wt = self.worktree(slug)
            link = self.pr_file(wt)
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(to)
        self.ok(timeout=60)
        self.assertIsNone(self.note("linked")["pr"])
        self.assertIsNone(self.note("piped")["pr"])

    def test_o7_github_origin_forms_give_the_repo_url(self) -> None:
        """O7, D3: the scp, https and ssh forms of the origin each give the https repository URL."""
        self.worktree("tiny", prefix="fix")
        for url in ("git@github.com:tayebchlyah/heldout-dev-loop.git",
                    "https://github.com/tayebchlyah/heldout-dev-loop.git",
                    "ssh://git@github.com/tayebchlyah/heldout-dev-loop"):
            with self.subTest(origin=url):
                self.set_origin(url)
                note = self.written(self.ok(), "tiny")
                self.assertEqual(note.get("repo"), "https://github.com/tayebchlyah/heldout-dev-loop")

    def test_o8_other_origins_give_an_empty_repo(self) -> None:
        """O8, D3: gitlab.com, a character outside the allowed set in the owner or the name, no origin: `""`."""
        self.worktree("tiny", prefix="fix")
        for url in ("https://gitlab.com/o/r.git", "https://github.com/o~x/r.git", "git@github.com:o/r+x.git", None):
            with self.subTest(origin=url):
                self.set_origin(url)
                note = self.written(self.ok(), "tiny")
                self.assertIn("repo", note)
                self.assertEqual(note["repo"], "")

    def test_o9_every_written_note_has_the_three_keys(self) -> None:
        """O9, D4: a spec feature, a Track S worktree and a claimed draft spec: each note has `issue`, `pr` and
        `repo`, of the declared types."""
        self.issue_spec("alpha", "Alpha Report", "319")
        self.worktree("alpha")
        self.worktree("tiny", prefix="fix")
        self.spec("beta", "Beta Import", review="draft")
        claimed = self.ok("--claim", "beta")
        out = self.ok()
        paths = {str(Path(p).resolve()) for p in [*claimed["notes"], *out["notes"]]}
        own = self.own_notes()
        self.assertEqual(set(own), {"alpha", "tiny", "beta"})
        self.assertTrue({str(p) for p in own.values()} <= paths, paths)
        for slug, path in own.items():
            with self.subTest(feature=slug):
                note = read_note(path)
                for key in KEYS:
                    self.assertIn(key, note)
                self.assertTrue(note["issue"] is None or type(note["issue"]) is int, note["issue"])
                self.assertTrue(note["pr"] is None or type(note["pr"]) is int, note["pr"])
                self.assertIsInstance(note["repo"], str)

    @slow
    def test_o10_shipped_note_keeps_its_links(self) -> None:
        """O10, D4: once shipped, the note keeps `issue`, `pr` and `repo` of its last write, also when the origin
        and `pr.json` change later."""
        self.set_origin(f"{GITHUB}.git")
        spec = self.issue_spec("gadget", "Gadget Export", "319")
        wt = self.worktree("gadget")
        self.write_pr(wt, {"number": 318, "url": f"{GITHUB}/pull/318", "branch": "feat/gadget"})
        self.ok()
        before = self.note("gadget")
        self.assertEqual((before["issue"], before["pr"], before["repo"]), (319, 318, GITHUB))

        self.set_field(spec, "shipped", "2026-11-14")
        self.ok()
        shipped = self.note("gadget")
        self.assertTrue(shipped["shipped_at"])
        self.assertEqual((shipped["issue"], shipped["pr"], shipped["repo"]), (319, 318, GITHUB))

        self.pr_file(wt).unlink()
        self.set_origin("https://gitlab.com/acme/widgets.git")
        self.ok()
        after = self.note("gadget")
        self.assertTrue(after["shipped_at"])
        self.assertEqual((after["issue"], after["pr"], after["repo"]), (319, 318, GITHUB))

    @slow
    def test_o21_dataviewjs_block_shows_both_links(self) -> None:
        """O21, D5: the block of `Dev Loop Board.md`, run with the Board Ledger oracle 23 stubs and a note of
        oracle 14 (in progress, repo https://github.com/o/r, issue 316, PR 318), shows both links."""
        self.worktree("tiny", prefix="fix")
        self.ok()
        now = time.time()
        fields = status_note(feature="linked", title="Linked Feature", stage=3,
                             stage_times={"2": iso(now - 7200), "3": iso(now - 3600)}, updated=iso(now - 60),
                             issue=316, pr=318, repo="https://github.com/o/r")
        put(self.features_dir / note_name(fields["machine_id"], PROJECT, "linked"), note_text(fields))
        pages = [read_note(p) for p in sorted(self.features_dir.glob("*.md"))]
        html = self.dataview(pages, [f"project:{PROJECT}", f"{PROJECT}/linked"])
        row = row_summary(html, PROJECT, "linked")
        found = {link["attrs"].get("href"): link["attrs"].get("aria-label") for link in github_links(row)}
        self.assertEqual(found, {"https://github.com/o/r/issues/316": "Issue #316, open",
                                 "https://github.com/o/r/pull/318": "Pull request #318, open"})


class BoardLinkFlows(LinkBoard):
    @slow
    def test_f1_track_m_spec_issue_ci_wait_pr_then_board_and_render(self) -> None:
        """F1: a Track M spec with `issue: 319`, a worktree, `ci wait` on a stub `gh` with PR 320 of the worktree
        branch and `pass`, origin on github.com, then `board`: the note has issue 319, PR 320 and the repo;
        `renderBoard` of the note shows "Issue #319, open" and "Pull request #320, open"."""
        self.set_origin("git@github.com:acme/widgets.git")
        self.issue_spec("gadget", "Gadget Export", "319")
        wt = self.worktree("gadget")
        head = git(wt, "rev-parse", "HEAD").strip()
        stub = StubGh(self.root / "stub-bin")
        stub.set(pr_view=[ok(view(head, 320, f"{GITHUB}/pull/320", "feat/gadget"))], api_graphql=[ok(threads_doc())])
        code, verdict = ci_wait(wt, self.env, stub)
        self.assertEqual((code, verdict.get("state")), (0, "pass"), verdict)

        note = self.written(self.ok(cwd=wt), "gadget")
        self.assertEqual((note["issue"], note["pr"], note["repo"]), (319, 320, GITHUB))
        row = row_summary(render(self.root, [note]), note["project"], "gadget")
        self.assertEqual(names(row), ["Issue #319, open", "Pull request #320, open"])
        self.assertEqual([link["attrs"].get("href") for link in github_links(row)],
                         [f"{GITHUB}/issues/319", f"{GITHUB}/pull/320"])

    def test_f2_track_s_on_gitlab_issue_from_branch_no_link(self) -> None:
        """F2: a Track S worktree on `fix/294-wall-gaps`, no spec, origin on gitlab.com, then `board`: issue 294,
        pr null, repo ""; `renderBoard` shows the title "Wall Gaps" and no link to github.com."""
        self.set_origin("https://gitlab.com/acme/widgets.git")
        self.worktree("294-wall-gaps", prefix="fix")
        note = self.written(self.ok(), "294-wall-gaps")
        self.assertEqual((note["issue"], note["pr"], note["repo"]), (294, None, ""))
        row = row_summary(render(self.root, [note]), note["project"], "294-wall-gaps")
        self.assertIn("Wall Gaps", text(row))
        self.assertNotIn("294 Wall Gaps", text(row))
        self.assertEqual(github_links(row), [])
        self.assertNotRegex(row, r"github\.com")


if __name__ == "__main__":
    unittest.main()
