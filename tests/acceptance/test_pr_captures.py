#!/usr/bin/env python3
"""Held-out oracle tests for PR captures (spec: PR Captures).

One test per `## Acceptance flows` item (F<n>), plus the kit text line; each docstring names the
`## Acceptance oracle` lines (O<n>) it checks. The tests use only the declared seams: the stdout JSON
and exit code of `devloop.py drive` and `devloop.py capture`, the output of `check_setup.py`, the
files `capture` writes, the text of `skill/SKILL.md`, and a stub `gh` on `PATH` that records each call
and a copy of the body file (D13). No network, no real GitHub. Every name in the temp repos is invented.

The drive steps need Playwright and Chromium for this repo (`npm ci && npx playwright install
chromium`). Each temp repo finds Playwright through a `node_modules` link at its root.
"""

from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = REPO_ROOT / "skill"
DEVLOOP = SKILL / "scripts" / "devloop.py"
CHECKER = SKILL / "scripts" / "check_setup.py"
INSTALLER = REPO_ROOT / "bin" / "install.mjs"
NODE_MODULES = REPO_ROOT / "node_modules"

SKILL_NAME = "verify-tea-house"
SECTIONS = ("Launch", "Doctor", "Drive", "Evidence", "Cleanup")
START = "<!-- dev-loop: captures -->"
END = "<!-- /dev-loop: captures -->"
TEN_MB = 10485760
FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32 + b"\xff\xd9"
SHA = r"`?([0-9a-f]{7,40})`?"

STUB_GH = """#!{python}
import json, pathlib, sys
state = pathlib.Path({state!r})
config = json.loads((state / "config.json").read_text())
args = sys.argv[1:]
record = {{"argv": args}}
code = 0
if args[:1] == ["--version"]:
    v = config["version"]
    sys.stdout.write(f"gh version {{v}} (2026-10-01)\\nhttps://github.com/cli/cli/releases/tag/v{{v}}\\n")
elif args[:2] == ["pr", "view"]:
    sys.stdout.write(config["body"] + "\\n")
elif args[:2] == ["pr", "edit"]:
    if "--body-file" in args:
        body = pathlib.Path(args[args.index("--body-file") + 1])
        record["body"] = body.read_text() if body.is_file() else None
    attached = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "--attach"]
    record["attached_exist"] = [pathlib.Path(p).is_file() for p in attached]
    if config.get("edit_exit"):
        sys.stderr.write(config.get("edit_output", "") + "\\n")
        code = config["edit_exit"]
    else:
        sys.stdout.write("https://github.com/acme/tea-house/pull/7\\n")
with (state / "calls.jsonl").open("a") as calls:
    calls.write(json.dumps(record) + "\\n")
sys.exit(code)
"""

JOURNEY = """import { readFileSync, writeFileSync, existsSync, statSync, copyFileSync } from 'node:fs';
import { join } from 'node:path';
import { chromium } from 'playwright';
const input = JSON.parse(readFileSync(0, 'utf8'));
let mode = 'record';
try { mode = readFileSync(new URL('../journey-mode.txt', import.meta.url), 'utf8').trim(); } catch {}
const videoIsDir = typeof input.video === 'string' && existsSync(input.video) && statSync(input.video).isDirectory();
writeFileSync(new URL('../journey-input.json', import.meta.url), JSON.stringify({ input, videoIsDir }));
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 800 },
  recordVideo: { dir: input.video, size: { width: 1280, height: 800 } } });
const page = await context.newPage();
const results = [];
for (const [i, r] of input.routes.entries()) {
  const response = await page.goto(new URL(r.path, input.base_url).href);
  await page.waitForTimeout(300);
  const shot = join(input.evidence, `${input.entry}-journey-${i + 1}.jpg`);
  await page.screenshot({ path: shot, type: 'jpeg' });
  results.push({ entry: input.entry, path: r.path, ok: true, status: response.status(), missing: [],
    console_errors: [], error: null, screenshot: shot, seconds: 1.5 });
}
await context.close();
let video = await page.video().path();
await browser.close();
if (mode === 'outside') { const out = join(input.evidence, 'outside.webm'); copyFileSync(video, out); video = out; }
if (mode === 'missing') { video = join(input.video, 'never-written.webm'); }
process.stdout.write(JSON.stringify({ routes: results.map((r) => ({ ...r, video })) }));
"""


def put(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def clean_env(**extra: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("GIT_", "DEV_LOOP", "DEVLOOP", "CLAUDE_", "CODEX_")) and key != "NODE_PATH"}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_EDITOR": "true", "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": "false"})
    env.update(extra)
    return env


def git(cwd: Path, env: dict[str, str], *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
    return result.stdout.strip()


def init_repo(path: Path, env: dict[str, str]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    git(path, env, "init", "-q", "-b", "main")
    for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                       ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
        git(path, env, "config", key, value)


def commit_all(path: Path, env: dict[str, str], message: str) -> str:
    git(path, env, "add", "-A")
    git(path, env, "commit", "-q", "--allow-empty", "-m", message)
    return git(path, env, "rev-parse", "HEAD")


def run_json(cmd: list[str], cwd: Path, env: dict[str, str], timeout: int = 240) -> tuple[int, dict]:
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"stdout is not one JSON object (exit {result.returncode}):\n"
                             f"{result.stdout}\n{result.stderr}") from exc
    if not isinstance(payload, dict):
        raise AssertionError(f"stdout is not a JSON object: {result.stdout}")
    return result.returncode, payload


def entry_text(routes: list, paths: list, script: str | None = None, title: str = "Tea list") -> str:
    front = [f"routes: {json.dumps(routes)}", f"paths: {json.dumps(paths)}"]
    if script is not None:
        front.append(f"script: {json.dumps(script)}")
    return ("---\n" + "\n".join(front) + "\n---\n\n"
            f"# {title}\n\nWhere a guest picks a tea.\n\n"
            "## Sub-features\n\n- The main heading.\n\n"
            "## How to get to it (user POV)\n\nOpen the tea house in a browser.\n\n"
            "## Driving it with Playwright\n\nNavigate to the route and look for the heading.\n\n"
            "## Gotchas\n\nNone known.\n")


def skill_text(name: str) -> str:
    body = "".join(f"## {section}\n\nRun the tea tool for {section.lower()}.\n\n" for section in SECTIONS)
    return (f"---\nname: {name}\ndescription: Launch, check, drive and clean up the tea house app.\n---\n\n"
            f"# {name}\n\n{body}")


def page(body: str) -> str:
    return f"<!doctype html><html lang=\"en\"><head><title>Tea house</title></head><body>{body}</body></html>"


class PageServer:
    """A local HTTP server for fixture pages; the test changes `pages` between runs."""

    def __init__(self) -> None:
        self.pages: dict[str, tuple[int, str]] = {}
        pages = self.pages

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                status, body = pages.get(path, (404, page("<h1>Not found</h1>")))
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: object) -> None:
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class StubGh:
    """A `gh` on PATH that records each call (argv, a copy of the body file) in calls.jsonl."""

    def __init__(self, root: Path) -> None:
        self.state = root / "gh-state"
        self.bin = root / "gh-bin"
        self.state.mkdir(parents=True)
        self.bin.mkdir(parents=True)
        script = self.bin / "gh"
        script.write_text(STUB_GH.format(python=sys.executable, state=str(self.state)))
        script.chmod(0o755)
        self.set()

    def set(self, version: str = "2.103.2", body: str = "Intro text.", edit_exit: int = 0,
            edit_output: str = "") -> None:
        (self.state / "config.json").write_text(json.dumps(
            {"version": version, "body": body, "edit_exit": edit_exit, "edit_output": edit_output}))

    def calls(self) -> list[dict]:
        path = self.state / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def edit_calls(calls: list[dict]) -> list[dict]:
    return [c for c in calls if c["argv"][:2] == ["pr", "edit"]]


def attached(call: dict) -> list[str]:
    argv = call["argv"]
    return [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "--attach"]


def section_of(body: str) -> str:
    match = re.search(re.escape(START) + r"(.*?)" + re.escape(END), body, re.S)
    if match is None:
        raise AssertionError(f"no captures section in the body:\n{body}")
    return match.group(1)


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


class CaptureCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="pr-captures-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.server = PageServer()
        self.addCleanup(self.server.close)
        self.stub = StubGh(self.root)
        self.env = clean_env(PATH=f"{self.stub.bin}{os.pathsep}{os.environ.get('PATH', '')}")
        self.last_run = 0.0

    def require_playwright(self) -> None:
        self.assertTrue((NODE_MODULES / "playwright").is_dir() or (NODE_MODULES / "@playwright" / "test").is_dir(),
                        "Playwright missing from this repo: run npm ci && npx playwright install chromium")

    def make_repo(self, path: Path, verify: object, entries: dict[str, str],
                  extra: dict[str, str] | None = None) -> Path:
        init_repo(path, self.env)
        config: dict[str, object] = {"version": "2.1.1", "harnesses": ["claude", "codex"]}
        if verify is not None:
            config["verify"] = verify
        put(path / "dev-loop.json", json.dumps(config, indent=2) + "\n")
        put(path / ".gitignore", "node_modules\njourney-input.json\njourney-mode.txt\n")
        self.write_map(path, entries)
        (path / ".claude" / "skills").mkdir(parents=True)
        os.symlink(f"../../.agents/skills/{SKILL_NAME}", path / ".claude" / "skills" / SKILL_NAME)
        for rel, text in (extra or {}).items():
            put(path / rel, text)
        os.symlink(NODE_MODULES, path / "node_modules")
        commit_all(path, self.env, "Base")
        return path

    @staticmethod
    def write_map(path: Path, entries: dict[str, str]) -> None:
        skill_dir = path / ".agents" / "skills" / SKILL_NAME
        put(skill_dir / "SKILL.md", skill_text(SKILL_NAME))
        features = skill_dir / "features"
        listed = sorted({p.stem for p in features.glob("*.md") if p.stem != "README"} | set(entries))
        for stem, text in entries.items():
            put(features / f"{stem}.md", text)
        put(features / "README.md", "# Feature map\n\n" + "".join(f"- [{s}]({s}.md)\n" for s in listed))

    def pace(self) -> None:
        """Evidence folders are named to the second: start each run in a new second."""
        while int(time.time()) <= int(self.last_run):
            time.sleep(0.05)
        self.last_run = time.time()

    def devloop(self, repo: Path, *args: str) -> tuple[int, dict]:
        self.pace()
        return run_json([sys.executable, str(DEVLOOP), *args], repo, self.env)

    def drive(self, repo: Path, *args: str) -> tuple[int, dict]:
        return self.devloop(repo, "drive", *args)

    def capture(self, repo: Path, *args: str) -> tuple[int, dict, list[dict]]:
        seen = len(self.stub.calls())
        code, payload = self.devloop(repo, "capture", *args)
        return code, payload, self.stub.calls()[seen:]

    @staticmethod
    def route(payload: dict, entry: str, path: str) -> dict:
        found = [r for r in payload.get("routes", []) if r.get("entry") == entry and r.get("path") == path]
        if len(found) != 1:
            raise AssertionError(f"expected one route {entry} {path}: {payload}")
        return found[0]

    def one_edit(self, calls: list[dict], body_path: str) -> dict:
        edits = edit_calls(calls)
        self.assertEqual(len(edits), 1, calls)
        argv = edits[0]["argv"]
        self.assertEqual(argv[:4], ["pr", "edit", "7", "--body-file"], argv)
        self.assertEqual(Path(argv[4]).resolve(), Path(body_path).resolve(), argv)
        for path in attached(edits[0]):
            self.assertTrue(Path(path).is_absolute(), path)
        self.assertTrue(all(edits[0].get("attached_exist", [])), edits[0])
        return edits[0]

    def add_worktree(self, repo: Path, sha: str, name: str) -> Path:
        folder = Path(tempfile.mkdtemp(prefix="wt-", dir=self.root)) / name
        git(repo, self.env, "worktree", "add", "-q", "--detach", str(folder), sha)
        os.symlink(NODE_MODULES, folder / "node_modules")
        return folder


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
        (notes / "specs").mkdir(parents=True)
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
