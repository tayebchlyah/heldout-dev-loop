#!/usr/bin/env python3
"""Held-out oracle tests for the route captures and the kit text of PR captures (spec: PR Captures).

Seams, fixture choices and shared helpers: `pr_captures_support.py`.
"""

from __future__ import annotations

import json
import re
import shutil
import unittest
from pathlib import Path

from pr_captures_support import (attached, CaptureCase, commit_all, edit_calls, END, entry_text, git, page, paragraphs,
                                 put, section_of, SHA, SKILL, SKILL_NAME, START)


class RouteCaptureTests(CaptureCase):
    def test_f1_stage7_capture_of_route_entries(self) -> None:
        """O1, O4, O5, O6 (absent entry), O8, O12, O13, O14, O15, O18, O19, O20, O21: a diff drive, an all
        drive and a since drive on a feature branch, a before run with --routes-from in a detached
        worktree at the base, capture through gh pr edit --attach, then a recapture with the kept
        before.json after the before worktree is gone."""
        self.require_playwright()
        pages = self.server.pages
        url = self.server.url
        entries = {
            "a": entry_text([{"path": "/one", "names": [{"role": "heading", "name": "One"}]}], ["web/a/*"],
                            title="One"),
            "c": entry_text([{"path": "/three", "names": [{"role": "heading", "name": "Three"}]}], ["web/c/*"],
                            title="Three"),
        }
        repo = self.make_repo(self.root / "tea-house", {"skill": SKILL_NAME, "base_url": url}, entries,
                              extra={"web/a/x.ts": "export const a = 1;\n", "web/c/x.ts": "export const c = 1;\n"})
        base = git(repo, self.env, "rev-parse", "HEAD")

        git(repo, self.env, "checkout", "-q", "-b", "feat/tea-banner")
        self.write_map(repo, {
            "b": entry_text([{"path": "/two", "names": [{"role": "heading", "name": "Two"}]}], ["web/b/*"],
                            title="Two"),
            "d": entry_text([{"path": "/four", "names": []}], ["web/d/*"],
                            title="Four"),
        })
        for name in ("a", "b", "d"):
            put(repo / "web" / name / "x.ts", f"export const {name} = 2;\n")
        commit_all(repo, self.env, "Add the tea banner")
        pages.update({"/one": (200, page("<h1>One</h1><script>console.error('banner failed')</script>")), "/two": (200, page("<h1>Two</h1>")),
                      "/three": (200, page("<h1>Three</h1>")), "/four": (404, page("<h1>Nope</h1>"))})

        code, diff_run = self.drive(repo)
        with self.subTest(oracle="1 diff"):
            self.assertEqual(diff_run.get("mode"), "diff", diff_run)
            self.assertIs(self.route(diff_run, "a", "/one").get("ok"), False, diff_run)
            self.assertIs(self.route(diff_run, "b", "/two").get("ok"), True, diff_run)
            self.assertNotIn("c", {r.get("entry") for r in diff_run.get("routes", [])}, diff_run)

        code, all_run = self.drive(repo, "--all")
        with self.subTest(oracle="1 all"):
            self.assertEqual(all_run.get("mode"), "all", all_run)
            self.assertEqual(all_run.get("head"), git(repo, self.env, "rev-parse", "HEAD"), all_run)
            self.assertEqual(json.loads((Path(all_run["evidence"]) / "drive.json").read_text()), all_run)
            self.assertTrue(all_run.get("routes"), all_run)
            for result in all_run["routes"]:
                self.assertIn("video", result)
                self.assertIsNone(result["video"], result)
            self.assertIs(self.route(all_run, "c", "/three").get("ok"), True, all_run)

        fix_start = git(repo, self.env, "rev-parse", "HEAD")
        put(repo / "web" / "a" / "x.ts", "export const a = 3;\n")
        after_head = commit_all(repo, self.env, "Fix the banner heading")
        pages["/one"] = (200, page("<h1>One</h1><p>The new banner.</p>"))
        code, since_run = self.drive(repo, "--since", fix_start)
        with self.subTest(oracle="1 since"):
            self.assertEqual(code, 0, since_run)
            self.assertEqual(since_run.get("mode"), "since", since_run)
            self.assertIs(self.route(since_run, "a", "/one").get("ok"), True, since_run)
        since_shot = Path(self.route(since_run, "a", "/one")["screenshot"]).read_bytes()

        # The before run: a detached worktree at the base, the base app, this repo's CLI.
        before_wt = self.add_worktree(repo, git(repo, self.env, "merge-base", "HEAD", "main"), "tea-house-before")
        put(before_wt / ".agents" / "skills" / SKILL_NAME / "features" / "zz-broken.md",
            "---\nroutes: {not: [a list\npaths: 12\n---\n\n# Broken\n")
        routes_from = self.root / "after-routes" / "drive.json"
        put(routes_from, json.dumps({"mode": "since", "head": after_head, "routes": [
            self.route(since_run, "a", "/one"), self.route(all_run, "b", "/two"),
            self.route(all_run, "d", "/four")]}))
        pages["/one"] = (200, page("<h1>Previous</h1><p>The old banner.</p>"))
        del pages["/two"]
        code, before_run = self.drive(before_wt, "--routes-from", str(routes_from))
        with self.subTest(oracle="4/5/6/8"):
            self.assertEqual(code, 0, before_run)
            self.assertEqual(before_run.get("mode"), "before", before_run)
            self.assertEqual(before_run.get("head"), base, before_run)
            self.assertEqual([r.get("path") for r in before_run.get("routes", [])], ["/one", "/two"], before_run)
            one = self.route(before_run, "a", "/one")
            self.assertEqual(one.get("missing"), [], one)
            self.assertEqual(one.get("status"), 200, one)
            self.assertEqual(self.route(before_run, "b", "/two").get("status"), 404, before_run)
        before_file = Path(before_run["evidence"]) / "drive.json"
        before_shot = Path(self.route(before_run, "a", "/one")["screenshot"]).read_bytes()

        code, first, calls = self.capture(repo, "--pr", "7", "--before", str(before_file))
        self.assertEqual(code, 0, first)
        body_path = Path(first["body"])
        folder = body_path.parent
        edit = self.one_edit(calls, first["body"])
        body = edit["body"]
        section = section_of(body)
        lines = section.splitlines()
        with self.subTest(oracle=12):
            self.assertIs(first.get("ok"), True, first)
            self.assertEqual(first.get("routes"), 2, first)
            self.assertIn(["pr", "view", "7", "--json", "body", "--jq", ".body"], [c["argv"] for c in calls])
            self.assertEqual(body_path.read_text(), body)
            self.assertRegex(body, r"\AIntro text\.\n\n" + re.escape(START))
            self.assertTrue(folder.name.endswith("-capture"), folder)
            self.assertIn("## Screens", lines)
            heads = [re.fullmatch(rf"Before: {SHA}, after: {SHA}\.", line) for line in lines]
            heads = [m for m in heads if m]
            self.assertEqual(len(heads), 1, section)
            self.assertTrue(base.startswith(heads[0].group(1)), section)
            self.assertTrue(after_head.startswith(heads[0].group(2)), section)
            self.assertIn("#### a", lines)
            self.assertIn("| `/one` | ![before a /one](./before-a-1.jpg) | ![after a /one](./after-a-1.jpg) |", lines)
            self.assertEqual((folder / "before-a-1.jpg").read_bytes(), before_shot)
            self.assertTrue((folder / "after-a-1.jpg").is_file())
        with self.subTest(oracle=14):
            self.assertIn("#### b", lines)
            self.assertIn("| `/two` | new | ![after b /two](./after-b-1.jpg) |", lines)
            names = sorted(Path(p).name for p in attached(edit))
            self.assertEqual(names, ["after-a-1.jpg", "after-b-1.jpg", "before-a-1.jpg"], edit)
            self.assertEqual({Path(p).resolve().parent for p in attached(edit)}, {folder.resolve()})
            self.assertEqual(sorted(str(Path(p).resolve()) for p in first.get("files", [])),
                             sorted(str(Path(p).resolve()) for p in attached(edit)))
        with self.subTest(oracle=15):
            self.assertNotIn("/four", section)
            self.assertNotIn("#### d", lines)
        with self.subTest(oracle=20):
            self.assertNotIn("/three", section)
            self.assertNotIn("#### c", lines)
            self.assertEqual((folder / "after-a-1.jpg").read_bytes(), since_shot)
        with self.subTest(oracle="21 kept"):
            kept = json.loads(Path(first["before"]).read_text())
            self.assertEqual(Path(first["before"]).resolve(), (folder / "before.json").resolve())
            self.assertEqual(Path(self.route(kept, "a", "/one")["screenshot"]).resolve(),
                             (folder / "before-a-1.jpg").resolve(), kept)

        # Recapture: the before worktree is gone, the PR body holds the old section between other text.
        git(repo, self.env, "worktree", "remove", "--force", str(before_wt))
        self.assertFalse(before_file.exists())
        self.stub.set(body="Lead text.\n\n" + START + section + END + "\n\nTrailing text.")
        code, second, calls = self.capture(repo, "--pr", "7", "--before", first["before"])
        self.assertEqual(code, 0, second)
        edit = self.one_edit(calls, second["body"])
        body = edit["body"]
        new_folder = Path(second["body"]).parent
        with self.subTest(oracle=13):
            self.assertEqual(body.count(START), 1, body)
            self.assertEqual(body.split(START)[0], "Lead text.\n\n")
            self.assertEqual(body.split(END)[1].rstrip("\n"), "\n\nTrailing text.")
        with self.subTest(oracle=21):
            self.assertNotEqual(new_folder.resolve(), folder.resolve())
            self.assertIn("| `/one` | ![before a /one](./before-a-1.jpg) | ![after a /one](./after-a-1.jpg) |",
                          section_of(body).splitlines())
            self.assertEqual((new_folder / "before-a-1.jpg").read_bytes(), before_shot)

        # The file cap: 30 routes in route entries, each with a before image.
        shots = self.root / "shots"
        shots.mkdir()
        after_routes, before_routes = [], []
        for entry in ("e1", "e2", "e3"):
            for k in range(1, 11):
                for side, bucket in (("after", after_routes), ("before", before_routes)):
                    shot = shots / f"{side}-{entry}-{k}.jpg"
                    shutil.copyfile(folder / "after-a-1.jpg", shot)
                    bucket.append({"entry": entry, "path": f"/{entry}/r{k:02d}", "ok": True, "status": 200,
                                   "missing": [], "console_errors": [], "error": None, "screenshot": str(shot),
                                   "video": None, "seconds": 1.0})
        cap_after = self.root / "cap-after" / "drive.json"
        cap_before = self.root / "cap-before" / "drive.json"
        put(cap_after, json.dumps({"ok": True, "mode": "diff", "head": after_head, "routes": after_routes}))
        put(cap_before, json.dumps({"ok": True, "mode": "before", "head": base, "routes": before_routes}))
        self.stub.set()
        code, capped, calls = self.capture(repo, "--pr", "7", "--after", str(cap_after), "--before", str(cap_before))
        with self.subTest(oracle=18):
            self.assertEqual(code, 0, capped)
            self.assertEqual(capped.get("routes"), 25, capped)
            self.assertEqual(capped.get("omitted"), 5, capped)
            edit = self.one_edit(calls, capped["body"])
            self.assertEqual(len(attached(edit)), 50, edit)
            cap_lines = section_of(edit["body"]).splitlines()
            self.assertEqual(len([line for line in cap_lines if line.startswith("| `/e")]), 25, cap_lines)
            self.assertTrue([line for line in cap_lines
                             if re.fullmatch(r"5 more routes in the evidence: \S.*", line)], cap_lines)

        failing = self.root / "all-failing" / "drive.json"
        put(failing, json.dumps({"ok": False, "mode": "diff", "head": after_head,
                                 "routes": [{**after_routes[0], "ok": False}]}))
        code, none_ok, calls = self.capture(repo, "--pr", "7", "--after", str(failing))
        with self.subTest(oracle=19):
            self.assertEqual(code, 0, none_ok)
            self.assertIs(none_ok.get("ok"), True, none_ok)
            self.assertEqual(none_ok.get("skipped"), "no-routes", none_ok)
            self.assertEqual(edit_calls(calls), [], calls)


class KitTextTests(unittest.TestCase):
    def test_o24_skill_names_capture_and_closing_drive(self) -> None:
        """O24: SKILL.md Stage 7 names devloop.py capture, drive --routes-from and `captures:`; Stage 6 names
        the closing drive --since after the acceptance loop."""
        text = (SKILL / "SKILL.md").read_text()
        stage6 = re.search(r"(?ms)^## 6 .*?(?=^## 7 )", text)
        stage7 = re.search(r"(?ms)^## 7 .*?(?=^## |\Z)", text)
        self.assertIsNotNone(stage6)
        self.assertIsNotNone(stage7)
        self.assertIn("devloop.py capture", stage7.group(0))
        self.assertIn("drive --routes-from", stage7.group(0))
        self.assertIn("captures:", stage7.group(0))
        closing = [p for p in paragraphs(stage6.group(0))
                   if "drive --since" in p and "closing" in p.lower() and "acceptance" in p]
        self.assertTrue(closing, "Stage 6 names no closing drive --since after the acceptance loop")


if __name__ == "__main__":
    unittest.main()
