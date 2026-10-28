#!/usr/bin/env python3
"""Held-out oracle tests for the failure paths of PR captures (spec: PR Captures).

Seams, fixture choices and shared helpers: `pr_captures_support.py`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from pr_captures_support import (CaptureCase, CHECKER, DEVLOOP, edit_calls, entry_text, FAKE_JPEG, git, init_repo,
                                 INSTALLER, page, put, SKILL_NAME)


class FailurePathTests(CaptureCase):
    def checker(self, repo: Path, home: Path, path: str, harness: str = "claude") -> list[str]:
        env = {**self.env, "PATH": path}
        result = subprocess.run([sys.executable, str(CHECKER), "--harness", harness, "--home", str(home),
                                 "--repo", str(repo)], env=env, capture_output=True, text=True, timeout=180)
        return (result.stdout + result.stderr).splitlines()

    def path_without_gh(self) -> str:
        folder = self.root / "no-gh-bin"
        folder.mkdir()
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            if not directory or not os.path.isdir(directory):
                continue
            for name in os.listdir(directory):
                target = Path(directory) / name
                link = folder / name
                if name == "gh" or link.exists() or link.is_symlink():
                    continue
                if target.is_file() and os.access(target, os.X_OK):
                    os.symlink(target, link)
        return str(folder)

    def test_f3_failure_paths(self) -> None:
        """O7, O9, O10, O11, O22, O23: a failed runner in the before run, bad --routes-from use, a project
        without verify, an old gh, a failed gh pr edit, and the check_setup gh line."""
        self.require_playwright()
        pages = self.server.pages
        pages.update({"/r": (200, page("<h1>Menu</h1>")), "/s1": (200, page("<h1>Pay</h1>")),
                      "/s2": (200, page("<h1>Paid</h1>"))})
        entries = {
            "r": entry_text([{"path": "/r", "names": [{"role": "heading", "name": "Menu"}]}], ["web/r/*"],
                            title="Menu"),
            "s": entry_text([{"path": "/s1", "names": []}, {"path": "/s2", "names": []}], ["web/s/*"],
                            script="tools/pay.mjs", title="Pay"),
        }
        repo = self.make_repo(self.root / "tea-house", {"skill": SKILL_NAME, "base_url": self.server.url},
                              entries, extra={"tools/pay.mjs": "process.stderr.write('pay broke');\nprocess.exit(1);\n"})
        head = git(repo, self.env, "rev-parse", "HEAD")

        def result(entry: str, path: str, video: str | None) -> dict:
            return {"entry": entry, "path": path, "ok": True, "status": 200, "missing": [], "console_errors": [],
                    "error": None, "screenshot": str(self.root / f"{entry}.jpg"), "video": video, "seconds": 1.0}

        routes_from = self.root / "after" / "drive.json"
        put(routes_from, json.dumps({"ok": True, "mode": "diff", "head": head, "routes": [
            result("r", "/r", None), result("s", "/s1", str(self.root / "pay.webm")),
            result("s", "/s2", str(self.root / "pay.webm"))]}))
        code, run = self.drive(repo, "--routes-from", str(routes_from))
        with self.subTest(oracle=7):
            self.assertEqual(code, 0, run)
            failed = [r for r in run.get("routes", []) if r.get("entry") == "s"]
            self.assertEqual([r.get("path") for r in failed], ["/s1", "/s2"], run)
            for item in failed:
                self.assertIs(item.get("ok"), False, item)
                self.assertEqual(item.get("error"), "runner failed", item)
            self.assertEqual(self.route(run, "r", "/r").get("status"), 200, run)

        with self.subTest(oracle=9):
            for extra in (["--all"], ["--since", head]):
                self.pace()
                usage = subprocess.run([sys.executable, str(DEVLOOP), "drive", "--routes-from", str(routes_from),
                                        *extra], cwd=repo, env=self.env, capture_output=True, text=True, timeout=120)
                self.assertEqual(usage.returncode, 2, (extra, usage.stdout, usage.stderr))
            not_json = self.root / "not-json.txt"
            not_json.write_text("routes: none\n")
            code, bad = self.drive(repo, "--routes-from", str(not_json))
            self.assertEqual(code, 1, bad)
            self.assertTrue(bad.get("error"), bad)

        with self.subTest(oracle=10):
            for label, verify in (("absent", None), ("false", False)):
                plain = self.make_repo(self.root / f"plain-{label}", verify, entries)
                seen = len(self.stub.calls())
                code, payload = self.devloop(plain, "capture", "--pr", "7")
                self.assertEqual(code, 0, payload)
                self.assertEqual(payload.get("skipped"), "no-verify", payload)
                self.assertEqual(self.stub.calls()[seen:], [], label)

        with self.subTest(oracle=11):
            self.stub.set(version="2.98.0")
            code, old, calls = self.capture(repo, "--pr", "7", "--after", str(routes_from))
            self.assertEqual(code, 1, old)
            self.assertIn("2.99.0", old.get("error") or "", old)
            self.assertEqual(edit_calls(calls), [], calls)

        with self.subTest(oracle=22):
            shot = self.root / "r.jpg"
            shot.write_bytes(FAKE_JPEG)
            one = self.root / "one" / "drive.json"
            put(one, json.dumps({"ok": True, "mode": "diff", "head": head, "routes": [result("r", "/r", None)]}))
            self.stub.set(edit_exit=1, edit_output="stub gh: pull request 7 is locked for edits")
            code, refused, calls = self.capture(repo, "--pr", "7", "--after", str(one))
            self.assertEqual(code, 1, refused)
            self.assertIn("gh pr edit", refused.get("error") or "", refused)
            self.assertIn("pull request 7 is locked for edits", json.dumps(refused.get("tail")), refused)
            self.assertEqual(len(edit_calls(calls)), 1, calls)

        # check_setup on an installed project.
        home = self.root / "home"
        home.mkdir()
        notes = self.root / "notes"
        (notes / "Features").mkdir(parents=True)
        shop = self.root / "Tea Shop"
        init_repo(shop, self.env)
        install = subprocess.run(["node", str(INSTALLER), "init", "--yes", "--harness=both", f"--notes={notes}"],
                                 cwd=shop, env={**self.env, "HOME": str(home)}, capture_output=True, text=True,
                                 timeout=180)
        self.assertEqual(install.returncode, 0, install.stdout + install.stderr)
        config_path = shop / "dev-loop.json"
        config = json.loads(config_path.read_text())
        stub_path = self.env["PATH"]
        no_gh = self.path_without_gh()
        config_path.write_text(json.dumps({**config, "verify": {"skill": "verify-tea-shop",
                                                                "base_url": "http://127.0.0.1:4173"}}, indent=2) + "\n")
        self.stub.set(version="2.98.0")
        with self.subTest(oracle=23, case="old gh"):
            for harness in ("claude", "codex"):
                lines = [line for line in self.checker(shop, home, stub_path, harness) if "2.99.0" in line]
                self.assertTrue(lines, harness)
                self.assertTrue([line for line in lines if "found 2.98.0" in line], lines)
        with self.subTest(oracle=23, case="no gh"):
            lines = [line for line in self.checker(shop, home, no_gh) if "2.99.0" in line]
            self.assertTrue([line for line in lines if "found none" in line], lines)
        config_path.write_text(json.dumps({**config, "verify": False}, indent=2) + "\n")
        with self.subTest(oracle=23, case="verify false"):
            for path in (stub_path, no_gh):
                lines = self.checker(shop, home, path)
                self.assertEqual([line for line in lines if "2.99.0" in line or "PR captures" in line], [], lines)


if __name__ == "__main__":
    unittest.main()
