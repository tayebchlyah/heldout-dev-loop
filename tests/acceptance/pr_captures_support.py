#!/usr/bin/env python3
"""Shared fixtures of the held-out PR captures tests (spec: PR Captures).

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
