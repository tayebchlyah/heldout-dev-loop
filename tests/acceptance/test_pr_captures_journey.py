#!/usr/bin/env python3
"""Held-out oracle tests for the journey captures of PR captures (spec: PR Captures).

Seams, fixture choices and shared helpers: `pr_captures_support.py`.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from pr_captures_support import (attached, CaptureCase, commit_all, entry_text, git, JOURNEY, page, paragraphs, put,
                                 section_of, SHA, SKILL_NAME, TEN_MB)


class JourneyCaptureTests(CaptureCase):
    def test_f2_journey_capture(self) -> None:
        """O2, O3, O6 (missing script), O16, O17: a script entry records a WebM in its video folder, the
        before run records one too, capture writes one block with two players; a video over 10 MB falls
        back to the screenshots."""
        self.require_playwright()
        pages = self.server.pages
        entries = {"j": entry_text([{"path": "/j/start", "names": []}, {"path": "/j/end", "names": []}],
                                   ["web/j/*"], script="tools/journey.mjs", title="Order a tea")}
        repo = self.make_repo(self.root / "tea-house", {"skill": SKILL_NAME, "base_url": self.server.url},
                              entries, extra={"tools/journey.mjs": JOURNEY, "web/j/x.ts": "export const j = 1;\n"})
        base = git(repo, self.env, "rev-parse", "HEAD")
        git(repo, self.env, "checkout", "-q", "-b", "feat/tea-order")
        put(repo / "web" / "j" / "x.ts", "export const j = 2;\n")
        after_head = commit_all(repo, self.env, "Shorten the tea order")
        pages.update({"/j/start": (200, page("<h1>Pick a tea</h1>")), "/j/end": (200, page("<h1>Enjoy</h1>"))})

        for mode in ("missing", "outside"):
            (repo / "journey-mode.txt").write_text(mode)
            code, run = self.drive(repo, "--since", base)
            with self.subTest(oracle=3, mode=mode):
                self.assertEqual(code, 0, run)
                for path in ("/j/start", "/j/end"):
                    self.assertIsNone(self.route(run, "j", path).get("video"), run)

        (repo / "journey-mode.txt").write_text("record")
        code, after_run = self.drive(repo)
        evidence = Path(after_run["evidence"]).resolve()
        with self.subTest(oracle=2):
            self.assertEqual(code, 0, after_run)
            received = json.loads((repo / "journey-input.json").read_text())
            video_dir = Path(received["input"]["video"])
            self.assertTrue(video_dir.is_absolute(), video_dir)
            self.assertTrue(received["videoIsDir"], received)
            self.assertEqual(video_dir.resolve(), evidence / "video" / "j")
            for path in ("/j/start", "/j/end"):
                video = self.route(after_run, "j", path).get("video")
                self.assertIsNotNone(video, after_run)
                self.assertTrue(Path(video).is_file(), video)
                self.assertEqual(Path(video).resolve().parent, evidence / "video" / "j")
                self.assertTrue(Path(video).name.endswith(".webm"), video)
        after_video = Path(self.route(after_run, "j", "/j/start")["video"])
        after_file = evidence / "drive.json"

        code, first, calls = self.capture(repo, "--pr", "7")
        with self.subTest(oracle="16 no before"):
            self.assertEqual(code, 0, first)
            edit = self.one_edit(calls, first["body"])
            section = section_of(edit["body"])
            parts = paragraphs(section)
            self.assertIn("#### j (journey)", parts)
            self.assertTrue([line for line in section.splitlines()
                             if re.fullmatch(rf"Before: none, after: {SHA}\.", line)], section)
            start = parts.index("Before")
            self.assertEqual(parts[start:start + 4], ["Before", "new", "After", "![](./after-j.webm)"], parts)
            self.assertEqual([Path(p).name for p in attached(edit)], ["after-j.webm"], edit)
            self.assertEqual((Path(first["body"]).parent / "after-j.webm").read_bytes(), after_video.read_bytes())

        pages.update({"/j/start": (200, page("<h1>Choose a tea</h1>")), "/j/end": (200, page("<h1>Thanks</h1>"))})
        before_wt = self.add_worktree(repo, base, "tea-house-before")
        (before_wt / "tools" / "journey.mjs").unlink()
        code, no_script = self.drive(before_wt, "--routes-from", str(after_file))
        with self.subTest(oracle=6):
            self.assertEqual(code, 0, no_script)
            self.assertEqual([r for r in no_script.get("routes", []) if r.get("entry") == "j"], [], no_script)
        git(before_wt, self.env, "checkout", "--", "tools/journey.mjs")
        code, before_run = self.drive(before_wt, "--routes-from", str(after_file))
        self.assertEqual(code, 0, before_run)
        before_video = Path(self.route(before_run, "j", "/j/start")["video"])
        self.assertTrue(before_video.is_file(), before_run)

        code, second, calls = self.capture(repo, "--pr", "7", "--before",
                                           str(Path(before_run["evidence"]) / "drive.json"))
        with self.subTest(oracle="16 with before"):
            self.assertEqual(code, 0, second)
            edit = self.one_edit(calls, second["body"])
            parts = paragraphs(section_of(edit["body"]))
            self.assertEqual(parts.count("#### j (journey)"), 1, parts)
            start = parts.index("Before")
            self.assertEqual(parts[start:start + 4],
                             ["Before", "![](./before-j.webm)", "After", "![](./after-j.webm)"], parts)
            self.assertEqual(sorted(Path(p).name for p in attached(edit)), ["after-j.webm", "before-j.webm"], edit)
            folder = Path(second["body"]).parent
            self.assertEqual((folder / "before-j.webm").read_bytes(), before_video.read_bytes())

        big = self.root / "big" / "huge.webm"
        big.parent.mkdir()
        with big.open("wb") as handle:
            handle.truncate(TEN_MB + 1)
        payload = json.loads(after_file.read_text())
        payload["routes"] = [{**r, "video": str(big)} for r in payload["routes"]]
        big_after = self.root / "big-after" / "drive.json"
        put(big_after, json.dumps(payload))
        code, third, calls = self.capture(repo, "--pr", "7", "--after", str(big_after))
        with self.subTest(oracle=17):
            self.assertEqual(code, 0, third)
            edit = self.one_edit(calls, third["body"])
            section = section_of(edit["body"])
            lines = section.splitlines()
            self.assertIn("Video over 10 MB, screenshots shown.", lines)
            self.assertNotIn("#### j (journey)", lines)
            self.assertTrue([line for line in lines if line.startswith("| `/j/start` |")
                             and "](./after-j-1.jpg)" in line], section)
            self.assertTrue([line for line in lines if line.startswith("| `/j/end` |")
                             and "](./after-j-2.jpg)" in line], section)
            self.assertEqual([p for p in attached(edit) if p.endswith(".webm")], [], edit)


if __name__ == "__main__":
    unittest.main()
