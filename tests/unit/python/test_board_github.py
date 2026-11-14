"""#319: the GitHub keys `issue`, `pr` and `repo` of the board status notes, and their readers."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / "skill" / "scripts"))
from board import sources  # noqa: E402
from board_fixtures import Board, git, note_text, put, read_note  # noqa: E402

GOOD = "https://github.com/tayebchlyah/heldout-dev-loop"


class GithubReaderTests(unittest.TestCase):
    def test_github_repo(self) -> None:
        rows = [("git@github.com:tayebchlyah/heldout-dev-loop.git", GOOD), (f"{GOOD}.git", GOOD), (f"{GOOD}/", GOOD),
                (GOOD, GOOD), ("ssh://git@github.com/tayebchlyah/heldout-dev-loop", GOOD),
                ("ssh://git@github.com/tayebchlyah/heldout-dev-loop.git", GOOD),
                ("https://github.com/o/r.github", "https://github.com/o/r.github"),
                ("https://gitlab.com/o/r.git", ""), ("https://github.com/o w/r", ""), ("https://github.com/o/r$x", ""),
                ("https://github.com/o/r.git/", "https://github.com/o/r"), ("git@github.com:o/r/", ""),
                ("git@github.com:o/r.git/", ""), ("ssh://git@github.com/o/r/", ""),
                ("ssh://git@github.com/o/r.git/", ""), ("https://github.com/o/r/x", ""), ("http://github.com/o/r", ""), ("/tmp/origin.git", ""), ("", ""),
                (None, "")]
        for origin, want in rows:
            with self.subTest(origin=origin):
                self.assertEqual(sources.github_repo(origin), want)

    def test_issue(self) -> None:
        rows = [("319", "316-x", 319), (None, "316-board-header", 316), (None, "alpha", None), ("", "alpha", None),
                ("abc", "alpha", None), ("0", "alpha", None), ("-3", "alpha", None), ("3.5", "alpha", None),
                ("abc", "316-x", 316), (None, "0-x", None), (None, "316x", None)]
        for spec, slug, want in rows:
            with self.subTest(spec=spec, slug=slug):
                self.assertEqual(sources.issue(spec, slug), want)


class BoardGithubTests(Board):
    def issue_spec(self, slug: str, title: str, value: str) -> Path:
        path = self.spec(slug, title)
        path.write_text(path.read_text().replace("track: M\n", f"track: M\nissue: {value}\n", 1))
        return path

    def pr_file(self, wt: Path, data: object) -> Path:
        path = Path(git(wt, "rev-parse", "--absolute-git-dir").strip()) / "dev-loop" / "pr.json"
        put(path, data if isinstance(data, str) else json.dumps(data))
        return path

    def test_issue_from_the_spec_then_the_slug(self) -> None:
        self.issue_spec("316-board-x", "Board X", "319")
        self.worktree("316-board-x")
        self.worktree("316-board-header")
        self.worktree("alpha")
        self.ok()
        self.assertEqual([self.note(s)["issue"] for s in ("316-board-x", "316-board-header", "alpha")], [319, 316, None])
        for value in ("abc", "0", "-3", "3.5"):
            with self.subTest(value=value):
                spec = self.notes / "Features" / "Beta" / "Spec.md"
                if spec.exists():
                    self.set_field(spec, "issue", value)
                else:
                    self.issue_spec("beta", "Beta", value)
                    self.worktree("beta")
                self.ok()
                self.assertIsNone(self.note("beta")["issue"])

    def test_pr_from_the_worktree_record(self) -> None:
        wt = self.worktree("x")
        good = {"number": 318, "url": "https://github.com/o/r/pull/318", "branch": "feat/x"}
        self.pr_file(wt, good)
        self.ok()
        self.assertEqual(self.note("x")["pr"], 318)
        rows = [("no file", None), ("not json", "not json"), *((f"number {n!r}", {**good, "number": n})
                                                               for n in ("318", 0, -1, True, 3.5)),
                ("other branch", {**good, "branch": "feat/y"}), ("short branch", {**good, "branch": "x"}),
                ("no branch", {"number": 318, "url": good["url"]})]
        for name, data in rows:
            with self.subTest(name):
                path = self.pr_file(wt, "")
                if data is None:
                    path.unlink()
                else:
                    self.pr_file(wt, data)
                self.ok()
                self.assertIsNone(self.note("x")["pr"])

    def test_pr_record_link_is_not_followed(self) -> None:
        wt = self.worktree("x")
        good = json.dumps({"number": 318, "url": "u", "branch": "feat/x"})
        target = self.root / "elsewhere.json"
        put(target, good)
        path = self.pr_file(wt, "")
        path.unlink()
        path.symlink_to(target)
        self.ok()
        self.assertIsNone(self.note("x")["pr"])
        path.unlink()
        path.parent.rmdir()
        put(self.root / "linked" / "pr.json", good)
        path.parent.symlink_to(self.root / "linked")
        self.ok()
        self.assertIsNone(self.note("x")["pr"])

    def test_repo_from_origin(self) -> None:
        self.worktree("x")
        rows = [(None, ""), ("git@github.com:tayebchlyah/acme-widgets.git", "https://github.com/tayebchlyah/acme-widgets"),
                ("https://gitlab.com/o/acme-widgets.git", "")]
        for url, want in rows:
            with self.subTest(url=url):
                if url:
                    git(self.repo, "remote", "set-url", "origin", url)
                self.ok()
                self.assertEqual(self.note("x")["repo"], want)

    def test_every_written_note_has_the_three_keys(self) -> None:
        self.stage5("alpha", "Alpha Report")
        self.worktree("fix-beta", prefix="fix")
        self.ok()
        for slug in ("alpha", "fix-beta"):
            self.assertLessEqual({"issue", "pr", "repo"}, set(self.note(slug)))

    def test_shipped_note_keeps_its_keys(self) -> None:
        wt = self.stage7("320-done", "Done Thing")
        spec = self.notes / "Features" / "Done Thing" / "Spec.md"
        spec.write_text(spec.read_text().replace("track: M\n", "track: M\nissue: 320\n", 1))
        record = self.pr_file(wt, {"number": 321, "url": "u", "branch": "feat/320-done"})
        git(self.repo, "remote", "set-url", "origin", "git@github.com:o/acme-widgets.git")
        self.ok()
        self.set_field(spec, "shipped", "2026-11-14")
        git(self.repo, "remote", "set-url", "origin", "https://gitlab.com/o/acme-widgets.git")
        record.unlink()
        self.ok()
        note = read_note(self.own_notes()["320-done"])
        self.assertTrue(note["shipped_at"])
        self.assertEqual((note["issue"], note["pr"], note["repo"]), (320, 321, "https://github.com/o/acme-widgets"))

    def test_upgrade_keeps_updated(self) -> None:
        wt, plan = self.stage5("alpha", "Alpha Report")
        self.ledger(wt, plan, "Task 1: complete")
        self.ok(cwd=wt)
        path = self.own_notes()["alpha"]
        old = {k: v for k, v in read_note(path).items() if k not in ("issue", "pr", "repo", "backfilled")}
        old["updated"] = "2026-10-01T00:00:00Z"
        path.write_text(note_text(old))
        (self.repo / ".git" / "dev-loop" / "board-state.json").unlink()
        self.ok(cwd=wt)
        note = read_note(path)
        self.assertEqual(note["updated"], "2026-10-01T00:00:00Z")
        self.assertLessEqual({"issue", "pr", "repo"}, set(note))

    def test_worktree_pr_record_in_a_linked_worktree(self) -> None:
        wt = self.worktree("x")
        put(self.repo / ".git" / "dev-loop" / "pr.json", json.dumps({"number": 5, "url": "u", "branch": "feat/x"}))
        self.ok()
        self.assertIsNone(self.note("x")["pr"])
        self.pr_file(wt, {"number": 6, "url": "u", "branch": "feat/x"})
        self.ok()
        self.assertEqual(self.note("x")["pr"], 6)


if __name__ == "__main__":
    unittest.main()
