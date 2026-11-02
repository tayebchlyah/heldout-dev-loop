#!/usr/bin/env python3
"""Tests for devloop.py capture: the before and after screens in the PR body, through a stub gh."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT  # noqa: E402

DEVLOOP = ROOT / "skill" / "scripts" / "devloop.py"
GIT = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "init.defaultBranch=main",
       "-c", "commit.gpgsign=false"]
START = "<!-- dev-loop: captures -->"
END = "<!-- /dev-loop: captures -->"
AFTER_HEAD = "a" * 40
BEFORE_HEAD = "b" * 40

STUB = """#!/usr/bin/env python3
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
if args == ["--version"]:
    print(f"gh version {os.environ.get('STUB_GH_VERSION', '2.103.2')} (2026-01-01)")
    sys.exit(0)
record = {"argv": args}
if args[:2] == ["pr", "edit"]:
    body = args[args.index("--body-file") + 1]
    record["body"] = open(body).read()
    record["exists"] = [os.path.isfile(args[i + 1]) for i, a in enumerate(args) if a == "--attach"]
with open(os.path.join(here, "calls.jsonl"), "a") as calls:
    calls.write(json.dumps(record) + "\\n")
if args[:2] == ["pr", "view"]:
    sys.stdout.write(open(os.path.join(here, "body.txt")).read() + "\\n")
    sys.exit(0)
code = int(os.environ.get("STUB_EDIT_EXIT", "0"))
if code:
    print("edit broke: no permission")
sys.exit(code)
"""


def git(cwd: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class CaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.stub = self.tmp / "stub"
        self.stub.mkdir()
        (self.stub / "gh").write_text(STUB)
        (self.stub / "gh").chmod(0o755)
        self.body("Intro text.")
        self.root = self.repo({"skill": "verify-shop", "base_url": "http://127.0.0.1:4173"})
        self.evidence = Path(git(self.root, "rev-parse", "--path-format=absolute", "--git-dir").strip()) / "dev-loop" / "evidence"

    def body(self, text: str) -> None:
        (self.stub / "body.txt").write_text(text)

    def repo(self, verify: object, name: str = "app") -> Path:
        root = self.tmp / name
        root.mkdir()
        git(root, "init", "-q")
        config: dict = {"version": "2.2.0", "harnesses": ["claude"]}
        if verify != "absent":
            config["verify"] = verify
        (root / "dev-loop.json").write_text(json.dumps(config) + "\n")
        return root

    def drive(self, folder: Path, routes: list[dict], mode: str = "diff", head: str = AFTER_HEAD) -> Path:
        """A drive.json in `folder`, each route's `shot` text written as its screenshot file."""
        folder.mkdir(parents=True, exist_ok=True)
        out = []
        for i, route in enumerate(routes):
            route = {"ok": True, "status": 200, "screenshot": None, "video": None, **route}
            if "shot" in route:
                shot = folder / f"{route['entry']}-{i}.jpg"
                shot.write_text(route.pop("shot"))
                route["screenshot"] = str(shot)
            out.append(route)
        path = folder / "drive.json"
        path.write_text(json.dumps({"ok": True, "routes": out, "mode": mode, "head": head}))
        return path

    def capture(self, *args: str, env: dict | None = None) -> tuple[int, dict]:
        result = subprocess.run([sys.executable, str(DEVLOOP), "capture", *args], cwd=self.root, capture_output=True,
                                text=True, env={**os.environ, "PATH": f"{self.stub}{os.pathsep}{os.environ['PATH']}",
                                                **(env or {})})
        return result.returncode, json.loads(result.stdout)

    def calls(self) -> list[dict]:
        path = self.stub / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def edit(self) -> dict:
        edits = [c for c in self.calls() if c["argv"][:2] == ["pr", "edit"]]
        self.assertEqual(len(edits), 1, edits)
        return edits[0]

    def attached(self, call: dict) -> list[str]:
        argv = call["argv"]
        return [argv[i + 1] for i, a in enumerate(argv) if a == "--attach"]

    def test_same_second_captures_get_own_folders(self) -> None:
        sys.path.insert(0, str(DEVLOOP.parent))
        import capture
        first = capture.new_capture_folder(self.evidence, "20260101T000000Z")
        second = capture.new_capture_folder(self.evidence, "20260101T000000Z")
        third = capture.new_capture_folder(self.evidence, "20260101T000000Z")
        self.assertEqual([f.name for f in (first, second, third)],
                         ["20260101T000000Z-capture", "20260101T000000Z-capture-2", "20260101T000000Z-capture-3"])
        self.assertTrue(second.is_dir())

    def test_suffixed_capture_folder_is_not_a_drive(self) -> None:
        self.drive(self.evidence / "20260101T000000Z", [{"entry": "a", "path": "/one", "shot": "0"}])
        self.drive(self.evidence / "20260101T000001Z-capture-2", [{"entry": "b", "path": "/two", "shot": "x"}])
        code, out = self.capture("--pr", "7")
        self.assertEqual((code, out["routes"]), (0, 1), out)
        self.assertNotIn("/two", self.edit()["body"])

    def test_no_verify_skips_without_gh(self) -> None:
        for verify in ("absent", False):
            with self.subTest(verify=verify):
                self.root = self.repo(verify, name=f"off-{verify}")
                self.assertEqual(self.capture("--pr", "7"), (0, {"ok": True, "skipped": "no-verify"}))
        self.assertEqual(self.calls(), [])

    def test_old_gh_refuses(self) -> None:
        after = self.drive(self.tmp / "after", [{"entry": "a", "path": "/one", "shot": "A"}])
        code, out = self.capture("--pr", "7", "--after", str(after), env={"STUB_GH_VERSION": "2.98.0"})
        self.assertEqual(code, 1, out)
        self.assertIn("2.99.0", out["error"])
        self.assertEqual(self.calls(), [])

    def test_one_route_with_before(self) -> None:
        after = self.drive(self.tmp / "after", [{"entry": "a", "path": "/one", "shot": "A"}])
        before = self.drive(self.tmp / "before", [{"entry": "a", "path": "/one", "shot": "B"}], "before", BEFORE_HEAD)
        code, out = self.capture("--pr", "7", "--after", str(after), "--before", str(before))
        self.assertEqual(code, 0, out)
        call = self.edit()
        folder = Path(out["body"]).parent
        self.assertEqual(call["argv"][:5], ["pr", "edit", "7", "--body-file", out["body"]])
        self.assertEqual(self.attached(call), [str(folder / "before-a-1.jpg"), str(folder / "after-a-1.jpg")])
        self.assertEqual(call["exists"], [True, True])
        self.assertEqual(call["body"], f"""Intro text.

{START}
## Screens

Before: `bbbbbbb`, after: `aaaaaaa`.

#### a

| Route | Before | After |
| --- | --- | --- |
| `/one` | ![before a /one](./before-a-1.jpg) | ![after a /one](./after-a-1.jpg) |
{END}
""")
        self.assertEqual(((folder / "before-a-1.jpg").read_text(), (folder / "after-a-1.jpg").read_text()), ("B", "A"))
        self.assertRegex(folder.name, r"^\d{8}T\d{6}Z-capture$")
        self.assertEqual(folder.parent, self.evidence)
        self.assertEqual({k: out[k] for k in ("ok", "pr", "routes", "omitted", "files")},
                         {"ok": True, "pr": 7, "routes": 1, "omitted": 0, "files": self.attached(call)})
        kept = json.loads(Path(out["before"]).read_text())
        self.assertEqual(kept["routes"][0]["screenshot"], str(folder / "before-a-1.jpg"))

    def test_existing_section_replaced_rest_kept(self) -> None:
        self.body(f"Top\n{START}\nold section\n{END}\nBottom")
        after = self.drive(self.tmp / "after", [{"entry": "a", "path": "/one", "shot": "A"}])
        self.assertEqual(self.capture("--pr", "7", "--after", str(after))[0], 0)
        body = self.edit()["body"]
        self.assertTrue(body.startswith(f"Top\n{START}\n## Screens\n"), body)
        self.assertTrue(body.endswith(f"{END}\nBottom"), body)
        self.assertNotIn("old section", body)

    def test_body_without_newline_and_empty_body(self) -> None:
        after = self.drive(self.tmp / "after", [{"entry": "a", "path": "/one", "shot": "A"}])
        for text, prefix in (("Line", f"Line\n\n{START}"), ("", START)):
            with self.subTest(text=text):
                (self.stub / "calls.jsonl").unlink(missing_ok=True)
                self.body(text)
                self.assertEqual(self.capture("--pr", "7", "--after", str(after))[0], 0)
                self.assertTrue(self.edit()["body"].startswith(prefix))

    def test_before_new_cases(self) -> None:
        after = self.drive(self.tmp / "after", [{"entry": "a", "path": "/one", "shot": "A"}])
        cases = {
            "404": [{"entry": "a", "path": "/one", "shot": "B", "status": 404}],
            "no route": [{"entry": "a", "path": "/other", "shot": "B"}],
            "no file": [{"entry": "a", "path": "/one", "screenshot": "/nowhere/x.jpg"}],
            "no key": [{"entry": "a", "path": "/one"}],
        }
        for case, routes in [*cases.items(), ("no before", None)]:
            with self.subTest(case=case):
                (self.stub / "calls.jsonl").unlink(missing_ok=True)
                args = ["--pr", "7", "--after", str(after)]
                if routes is not None:
                    args += ["--before", str(self.drive(self.tmp / case, routes, "before", BEFORE_HEAD))]
                code, out = self.capture(*args)
                self.assertEqual(code, 0, out)
                call = self.edit()
                self.assertIn("| `/one` | new | ![after a /one](./after-a-1.jpg) |", call["body"])
                self.assertEqual(len(self.attached(call)), 1)
                if routes is None:
                    self.assertIn("Before: none, after: `aaaaaaa`.", call["body"])
                    self.assertEqual(json.loads(Path(out["before"]).read_text()), {"routes": []})

    def test_failed_route_left_out(self) -> None:
        after = self.drive(self.tmp / "after", [{"entry": "a", "path": "/one", "shot": "A"},
                                                {"entry": "a", "path": "/bad", "shot": "X", "ok": False}])
        code, out = self.capture("--pr", "7", "--after", str(after))
        self.assertEqual((code, out["routes"]), (0, 1))
        self.assertNotIn("/bad", self.edit()["body"])

    def video(self, folder: Path, name: str, size: int = 4) -> str:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        with path.open("wb") as f:
            f.truncate(size)
        return str(path)

    def test_journey_block(self) -> None:
        clip = self.video(self.tmp / "after", "j.webm")
        after = self.drive(self.tmp / "after", [{"entry": "j", "path": "/s1", "shot": "1", "video": clip},
                                                {"entry": "j", "path": "/s2", "shot": "2", "video": clip}])
        code, out = self.capture("--pr", "7", "--after", str(after))
        self.assertEqual((code, out["routes"]), (0, 2), out)
        body = self.edit()["body"]
        self.assertIn("#### j (journey)\n\nBefore\n\nnew\n\nAfter\n\n![](./after-j.webm)\n", body)
        self.assertEqual([Path(p).name for p in out["files"]], ["after-j.webm"])
        (self.stub / "calls.jsonl").unlink()
        before = self.drive(self.tmp / "before", [{"entry": "j", "path": "/s1", "video": self.video(self.tmp / "b", "j.webm")}],
                            "before", BEFORE_HEAD)
        code, out = self.capture("--pr", "7", "--after", str(after), "--before", str(before))
        self.assertIn("Before\n\n![](./before-j.webm)\n\nAfter\n\n![](./after-j.webm)\n", self.edit()["body"])
        self.assertEqual([Path(p).name for p in out["files"]], ["before-j.webm", "after-j.webm"])

    def test_big_video_falls_back_to_screenshots(self) -> None:
        clip = self.video(self.tmp / "after", "j.webm", 10485761)
        after = self.drive(self.tmp / "after", [{"entry": "j", "path": "/s1", "shot": "1", "video": clip}])
        code, out = self.capture("--pr", "7", "--after", str(after))
        body = self.edit()["body"]
        self.assertIn("#### j\n\nVideo over 10 MB, screenshots shown.\n\n| Route |", body)
        self.assertIn("![after j /s1](./after-j-1.jpg)", body)
        self.assertEqual([Path(p).name for p in out["files"]], ["after-j-1.jpg"])

    def test_big_image_left_out(self) -> None:
        after = self.drive(self.tmp / "after", [{"entry": "a", "path": "/one", "shot": "A"}])
        data = json.loads(after.read_text())
        with Path(data["routes"][0]["screenshot"]).open("wb") as f:
            f.truncate(10485761)
        code, out = self.capture("--pr", "7", "--after", str(after))
        body = self.edit()["body"]
        self.assertIn("Image over 10 MB, see the evidence.", body)
        self.assertEqual(out["files"], [])

    def test_file_cap(self) -> None:
        routes = [{"entry": "a", "path": f"/r{i}", "shot": str(i)} for i in range(30)]
        after = self.drive(self.evidence / "20260101T000000Z", routes)
        before = self.drive(self.tmp / "before", routes, "before", BEFORE_HEAD)
        code, out = self.capture("--pr", "7", "--after", str(after), "--before", str(before))
        self.assertEqual((code, out["routes"], out["omitted"]), (0, 25, 5), out)
        call = self.edit()
        self.assertEqual(len(self.attached(call)), 50)
        self.assertIn(f"5 more routes in the evidence: {after.parent}", call["body"])

    def test_no_ok_route_skips_edit(self) -> None:
        after = self.drive(self.tmp / "after", [{"entry": "a", "path": "/one", "ok": False}])
        self.assertEqual(self.capture("--pr", "7", "--after", str(after)), (0, {"ok": True, "skipped": "no-routes"}))
        self.assertEqual([c for c in self.calls() if c["argv"][:2] == ["pr", "edit"]], [])

    def test_after_set_from_evidence(self) -> None:
        self.drive(self.evidence / "20260101T000000Z", [{"entry": "a", "path": "/zero", "shot": "0"}], "since")
        self.drive(self.evidence / "20260101T000001Z", [{"entry": "a", "path": "/one", "ok": False, "shot": "x"},
                                                       {"entry": "b", "path": "/two", "shot": "2"}])
        (self.evidence / "20260101T000002Z").mkdir()
        (self.evidence / "20260101T000003Z").mkdir()
        (self.evidence / "20260101T000003Z" / "drive.json").write_text("broken")
        self.drive(self.evidence / "20260101T000004Z", [{"entry": "a", "path": "/one", "shot": "since"}], "since", "c" * 40)
        self.drive(self.evidence / "20260101T000005Z", [{"entry": "c", "path": "/three", "shot": "3"}], "all", "d" * 40)
        self.drive(self.evidence / "20260101T000006Z", [{"entry": "a", "path": "/one", "shot": "bef"}], "before")
        self.drive(self.evidence / "20260101T000007Z-capture", [{"entry": "a", "path": "/one", "shot": "cap"}])
        code, out = self.capture("--pr", "7")
        self.assertEqual((code, out["routes"]), (0, 2), out)
        body = self.edit()["body"]
        self.assertIn("`/one`", body)
        self.assertIn("`/two`", body)
        self.assertNotIn("/three", body)
        self.assertNotIn("/zero", body)
        self.assertIn("after: `ddddddd`.", body)
        self.assertEqual((Path(out["body"]).parent / "after-a-1.jpg").read_text(), "since")

    def test_no_diff_drive_is_an_error(self) -> None:
        self.drive(self.evidence / "20260101T000000Z", [{"entry": "a", "path": "/one", "shot": "0"}], "since")
        code, out = self.capture("--pr", "7")
        self.assertEqual(code, 1, out)
        self.assertIn("diff", out["error"])

    def test_recapture_from_kept_before(self) -> None:
        after = self.drive(self.tmp / "after", [{"entry": "a", "path": "/one", "shot": "A"}])
        before = self.drive(self.tmp / "before", [{"entry": "a", "path": "/one", "shot": "B"}], "before", BEFORE_HEAD)
        first = self.capture("--pr", "7", "--after", str(after), "--before", str(before))[1]
        shutil.rmtree(self.tmp / "before")
        (self.stub / "calls.jsonl").unlink()
        code, second = self.capture("--pr", "7", "--after", str(after), "--before", first["before"])
        self.assertEqual(code, 0, second)
        self.assertIn("![before a /one](./before-a-1.jpg)", self.edit()["body"])
        self.assertEqual((Path(second["body"]).parent / "before-a-1.jpg").read_text(), "B")
        self.assertIn("Before: `bbbbbbb`", self.edit()["body"])

    def test_edit_failure_reports_tail(self) -> None:
        after = self.drive(self.tmp / "after", [{"entry": "a", "path": "/one", "shot": "A"}])
        code, out = self.capture("--pr", "7", "--after", str(after), env={"STUB_EDIT_EXIT": "1"})
        self.assertEqual(code, 1, out)
        self.assertIn("gh pr edit", out["error"])
        self.assertIn("edit broke", out["tail"])

    def test_usage_and_bad_files(self) -> None:
        for args in ((), ("--pr", "0"), ("--pr", "x")):
            with self.subTest(args=args):
                self.assertEqual(self.capture(*args)[0], 2)
        (self.tmp / "bad.json").write_text("[]")
        for flag in ("--after", "--before"):
            with self.subTest(flag=flag):
                code, out = self.capture("--pr", "7", flag, str(self.tmp / "bad.json"))
                self.assertEqual(code, 1, out)
                self.assertIn(flag, out["error"])

    def test_write_after_merges_the_after_set(self) -> None:
        self.drive(self.evidence / "20260101T000000Z", [{"entry": "a", "path": "/one", "shot": "0"},
                                                       {"entry": "b", "path": "/two", "shot": "2"}])
        since = self.drive(self.evidence / "20260101T000001Z", [{"entry": "a", "path": "/one", "shot": "s"}], "since", "c" * 40)
        target = self.tmp / "after.json"
        code, out = self.capture("--write-after", str(target))
        self.assertEqual(code, 0, out)
        self.assertEqual(out, {"ok": True, "after": str(target), "routes": 2, "head": "c" * 40})
        written = json.loads(target.read_text())
        self.assertEqual([r["path"] for r in written["routes"]], ["/one", "/two"])
        self.assertEqual((written["mode"], written["head"], written["folder"]), ("diff", "c" * 40, str(since.parent)))
        self.assertEqual(self.calls(), [])

    def test_write_after_edges(self) -> None:
        target = self.tmp / "after.json"
        code, out = self.capture("--write-after", str(target))
        self.assertEqual(code, 1, out)
        self.assertIn("no diff drive", out["error"])
        self.root = self.repo("absent", name="off")
        self.assertEqual(self.capture("--write-after", str(target)), (0, {"ok": True, "skipped": "no-verify"}))
        for extra in (("--pr", "7"), ("--before", "x"), ("--after", "x")):
            with self.subTest(extra=extra):
                code, out = self.capture("--write-after", str(target), *extra)
                self.assertEqual(code, 2, out)
                self.assertIn("error", out)
        self.assertEqual(self.calls(), [])

    def test_overflow_line_names_the_folder_key(self) -> None:
        routes = [{"entry": "a", "path": f"/r{i}", "shot": str(i)} for i in range(52)]
        after = self.drive(self.tmp / "after", routes)
        data = json.loads(after.read_text())
        folder = Path.home() / "x"
        after.write_text(json.dumps({**data, "folder": str(folder)}))
        self.assertEqual(self.capture("--pr", "7", "--after", str(after))[0], 0)
        self.assertIn("more routes in the evidence: ~/x", self.edit()["body"])
        self.assertNotIn(str(Path.home()), self.edit()["body"])

    def test_route_position_counts_failed_routes(self) -> None:
        after = self.drive(self.tmp / "after", [{"entry": "a", "path": "/one", "shot": "1", "ok": False},
                                                {"entry": "a", "path": "/two", "shot": "2"}])
        code, out = self.capture("--pr", "7", "--after", str(after))
        self.assertEqual((code, out["routes"]), (0, 1), out)
        self.assertEqual([Path(p).name for p in out["files"]], ["after-a-2.jpg"])
        self.assertNotIn("/one", self.edit()["body"])

    def test_journey_found_through_a_failed_route(self) -> None:
        clip = self.video(self.tmp / "after", "j.webm")
        after = self.drive(self.tmp / "after", [{"entry": "j", "path": "/s1", "ok": False, "video": clip},
                                                {"entry": "j", "path": "/s2", "shot": "2"}])
        code, out = self.capture("--pr", "7", "--after", str(after))
        self.assertEqual((code, out["routes"]), (0, 1), out)
        self.assertIn("#### j (journey)", self.edit()["body"])
        self.assertEqual([Path(p).name for p in out["files"]], ["after-j.webm"])

    def test_stray_start_marker_keeps_user_text(self) -> None:
        self.body(f"A\n{START}\nB")
        after = self.drive(self.tmp / "after", [{"entry": "a", "path": "/one", "shot": "A"}])
        self.assertEqual(self.capture("--pr", "7", "--after", str(after))[0], 0)
        first = self.edit()["body"]
        self.assertTrue(first.startswith(f"A\n{START}\nB\n\n{START}\n## Screens"), first)
        (self.stub / "calls.jsonl").unlink()
        self.body(first.removesuffix("\n"))
        self.assertEqual(self.capture("--pr", "7", "--after", str(after))[0], 0)
        second = self.edit()["body"]
        self.assertTrue(second.startswith(f"A\n{START}\nB\n\n{START}\n## Screens"), second)
        self.assertEqual(second.count("## Screens"), 1)


if __name__ == "__main__":
    unittest.main()
