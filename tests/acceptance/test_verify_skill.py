#!/usr/bin/env python3
"""Held-out oracle tests for the verify skill (spec: Verify Skill).

One test per `## Acceptance flows` item (F<n>), plus the kit text line; each docstring names the
`## Acceptance oracle` lines (O<n>) it checks. The tests use only the declared seams: the stdout JSON
and exit code of `devloop.py verify-init` and `devloop.py drive`, the output of `check_setup.py`, and
the text of `skill/SKILL.md`. Every name in the temp repos is invented.

The drive tests need Playwright and Chromium for this repo (`npm ci && npx playwright install
chromium`). Each temp repo finds Playwright through a `node_modules` link at its root (D13).
"""

from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = REPO_ROOT / "skill"
DEVLOOP = SKILL / "scripts" / "devloop.py"
CHECKER = SKILL / "scripts" / "check_setup.py"
INSTALLER = REPO_ROOT / "bin" / "install.mjs"
NODE_MODULES = REPO_ROOT / "node_modules"

FILL = "<!-- dev-loop: fill -->"
SECTIONS = ("Launch", "Doctor", "Drive", "Evidence", "Cleanup")
DRIVE_SKILL = "verify-corner-shop"


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


def snapshot(root: Path) -> dict[str, object]:
    """Every file (bytes) and link (target) under root, .git left out."""
    state: dict[str, object] = {}
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in [*dirs, *files]:
            path = Path(folder) / name
            rel = str(path.relative_to(root))
            if path.is_symlink():
                state[rel] = ("link", os.readlink(path))
            elif path.is_file():
                state[rel] = path.read_bytes()
            else:
                state[rel] = "dir"
    return state


def run_json(cmd: list[str], cwd: Path, env: dict[str, str], timeout: int = 180) -> tuple[int, dict]:
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"stdout is not one JSON object (exit {result.returncode}):\n"
                             f"{result.stdout}\n{result.stderr}") from exc
    if not isinstance(payload, dict):
        raise AssertionError(f"stdout is not a JSON object: {result.stdout}")
    return result.returncode, payload


def entry_text(routes: list, paths: list, script: str | None = None, title: str = "Home page") -> str:
    front = [f"routes: {json.dumps(routes)}", f"paths: {json.dumps(paths)}"]
    if script is not None:
        front.append(f"script: {json.dumps(script)}")
    return ("---\n" + "\n".join(front) + "\n---\n\n"
            f"# {title}\n\nWhere a shopper lands and starts browsing.\n\n"
            "## Sub-features\n\n- The main heading.\n\n"
            "## How to get to it (user POV)\n\nOpen the shop in a browser.\n\n"
            "## Driving it with Playwright\n\nNavigate to the route and look for the heading.\n\n"
            "## Gotchas\n\nNone known.\n")


def skill_text(name: str) -> str:
    body = "".join(f"## {section}\n\nRun the shop tool for {section.lower()}.\n\n" for section in SECTIONS)
    return (f"---\nname: {name}\ndescription: Launch, check, drive and clean up the corner shop app.\n---\n\n"
            f"# {name}\n\n{body}")


class PageServer:
    """A local HTTP server for fixture pages; the test changes `pages` between runs."""

    def __init__(self) -> None:
        self.pages: dict[str, tuple[int, str]] = {}
        pages = self.pages

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                if path == "/account":
                    cookie = self.headers.get("Cookie") or ""
                    heading = "Account" if "session=letmein" in cookie else "Sign in"
                    status, body = 200, page(f"<h1>{heading}</h1>")
                else:
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


def page(body: str) -> str:
    return f"<!doctype html><html lang=\"en\"><head><title>Corner shop</title></head><body>{body}</body></html>"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class VerifyInitAndSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="verify-skill-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = self.root / "home"
        self.home.mkdir()
        self.notes = self.root / "notes"
        (self.notes / "specs").mkdir(parents=True)
        self.env = clean_env(HOME=str(self.home))

    def checker(self, repo: Path, harnesses: tuple[str, ...] = ("claude", "codex")) -> str:
        output = ""
        for harness in harnesses:
            result = subprocess.run([sys.executable, str(CHECKER), "--harness", harness, "--home", str(self.home),
                                     "--repo", str(repo)], env=self.env, capture_output=True, text=True,
                                    timeout=180)
            output += result.stdout + result.stderr
        return output

    @staticmethod
    def verify_lines(output: str, *extra: str) -> list[str]:
        return [line for line in output.splitlines()
                if "verify" in line or "base_url" in line or any(token in line for token in extra)]

    def verify_init(self, repo: Path, *args: str) -> tuple[int, dict]:
        return run_json([sys.executable, str(DEVLOOP), "verify-init", *args], repo, self.env)

    def test_f1_stage0_on_a_ui_repo(self) -> None:
        """O1 to O12: check_setup fails on the missing skill, verify-init writes the skeleton, check_setup
        fails on the markers and the empty map, the agent fills them, check_setup passes."""
        repo = self.root / "Shop App"
        init_repo(repo, self.env)
        install = subprocess.run(["node", str(INSTALLER), "init", "--yes", "--harness=both",
                                  f"--notes={self.notes}"], cwd=repo, env=self.env, capture_output=True,
                                 text=True, timeout=180)
        self.assertEqual(install.returncode, 0, install.stdout + install.stderr)
        agents = repo / "AGENTS.md"
        agents_base = agents.read_text() if agents.exists() else "# AGENTS.md\n\n"
        agents.write_text(agents_base.rstrip("\n") + "\n\nstacks: python, ui\n")
        commit_all(repo, self.env, "Install dev-loop")
        config_path = repo / "dev-loop.json"
        base_config = json.loads(config_path.read_text())
        self.assertEqual(base_config.get("harnesses"), ["claude", "codex"])

        with self.subTest(oracle=5):
            lines = [line for line in self.checker(repo).splitlines() if "verify-init" in line]
            self.assertTrue(lines, "no check_setup line names verify-init")

        with self.subTest(oracle=6):
            config_path.write_text(json.dumps({**base_config, "verify": False}, indent=2) + "\n")
            output = self.checker(repo)
            self.assertEqual(self.verify_lines(output), [], output)
            config_path.write_text(json.dumps(base_config, indent=2) + "\n")

        with self.subTest(oracle=7):
            agents.write_text(agents_base.rstrip("\n") + "\n\nstacks: python\n")
            output = self.checker(repo)
            self.assertEqual(self.verify_lines(output), [], output)
            agents.write_text(agents_base.rstrip("\n") + "\n\nstacks: python, ui\n")

        name = "verify-shop-app"
        skill_dir = repo / ".agents" / "skills" / name
        link = repo / ".claude" / "skills" / name
        code, payload = self.verify_init(repo, "--base-url", "http://127.0.0.1:4173")
        with self.subTest(oracle=1):
            self.assertEqual(code, 0, payload)
            self.assertEqual(payload.get("skill"), name)
            self.assertEqual(payload.get("config"), {"skill": name, "base_url": "http://127.0.0.1:4173"})
            skill_md = (skill_dir / "SKILL.md").read_text()
            self.assertRegex(skill_md, rf"(?s)\A---\n.*^name: {name}\s*$.*?\n---\n", skill_md)
            for section in SECTIONS:
                self.assertRegex(skill_md, rf"(?m)^## {section}\s*$", section)
            self.assertTrue((skill_dir / "features" / "README.md").is_file())
            self.assertTrue(link.is_symlink(), "no .claude/skills link")
            self.assertEqual(os.readlink(link).rstrip("/"), f"../../.agents/skills/{name}")
            self.assertEqual(link.resolve(), skill_dir.resolve())
            config = json.loads(config_path.read_text())
            self.assertEqual(config.get("verify"), {"skill": name, "base_url": "http://127.0.0.1:4173"})
            self.assertEqual({k: v for k, v in config.items() if k != "verify"}, base_config)
            self.assertEqual([k for k in config if k != "verify"], list(base_config))

        with self.subTest(oracle="3a"):
            before = snapshot(repo)
            code, again = self.verify_init(repo, "--base-url", "http://127.0.0.1:4173")
            self.assertEqual(code, 1, again)
            self.assertTrue(again.get("error"), again)
            self.assertEqual(snapshot(repo), before)

        with self.subTest(oracle=8):
            output = self.checker(repo)
            self.assertTrue([line for line in output.splitlines()
                             if "SKILL.md" in line and "fill" in line.lower()], output)
            self.assertTrue([line for line in output.splitlines()
                             if "features" in line and re.search(r"entr(y|ies)", line, re.I)], output)

        for path in skill_dir.rglob("*.md"):
            path.write_text(path.read_text().replace(FILL, "Run the shop dev tool."))
        put(skill_dir / "features" / "home.md",
            entry_text([{"path": "/", "names": [{"role": "heading", "name": "Shop"}]}], ["web/home/*"]))
        with self.subTest(oracle=9):
            output = self.checker(repo)
            self.assertEqual(self.verify_lines(output, name), [], output)

        broken = skill_dir / "features" / "broken.md"
        for label, routes in (("routes not a list", {"path": "/cart", "names": []}),
                              ("path without slash", [{"path": "cart", "names": []}]),
                              ("names item without role", [{"path": "/cart", "names": [{"name": "Cart"}]}])):
            put(broken, entry_text(routes, ["web/cart/*"], title="Cart"))
            with self.subTest(oracle=10, case=label):
                output = self.checker(repo, ("claude",))
                self.assertTrue([line for line in output.splitlines() if "broken.md" in line], output)
        broken.unlink()

        good_config = json.loads(config_path.read_text())
        for field, verify, token in (("base_url", {"skill": name, "base_url": "localhost:5173"}, "base_url"),
                                     ("skill", {"skill": "verify-nowhere", "base_url": "http://127.0.0.1:4173"},
                                      "verify-nowhere")):
            config_path.write_text(json.dumps({**good_config, "verify": verify}, indent=2) + "\n")
            with self.subTest(oracle=11, field=field):
                output = self.checker(repo, ("claude",))
                self.assertTrue([line for line in output.splitlines() if token in line], output)
        config_path.write_text(json.dumps(good_config, indent=2) + "\n")

        link.unlink()
        with self.subTest(oracle=12):
            output = self.checker(repo, ("claude",))
            self.assertTrue([line for line in output.splitlines() if f".claude/skills/{name}" in line], output)

        kiosk = self.root / "kiosk"
        init_repo(kiosk, self.env)
        put(kiosk / "dev-loop.json", json.dumps({"version": "2.1.1", "harnesses": ["codex"]}, indent=2) + "\n")
        put(kiosk / ".agents" / "skills" / "verify-store" / "notes.txt", "kept\n")
        commit_all(kiosk, self.env, "Base")
        for label, args in (("bad name", ("--base-url", "http://127.0.0.1:4173", "--name", "Bad_Name")),
                            ("bad url", ("--base-url", "ftp://x")),
                            ("folder present", ("--base-url", "http://127.0.0.1:4173", "--name", "verify-store"))):
            with self.subTest(oracle="3b/4", case=label):
                before = snapshot(kiosk)
                code, refused = self.verify_init(kiosk, *args)
                self.assertEqual(code, 1, refused)
                self.assertTrue(refused.get("error"), refused)
                self.assertEqual(snapshot(kiosk), before)

        with self.subTest(oracle=2):
            code, payload = self.verify_init(kiosk, "--base-url", "http://127.0.0.1:4173")
            self.assertEqual(code, 0, payload)
            self.assertEqual(payload.get("skill"), "verify-kiosk")
            self.assertTrue((kiosk / ".agents" / "skills" / "verify-kiosk" / "SKILL.md").is_file())
            claude_link = kiosk / ".claude" / "skills" / "verify-kiosk"
            self.assertFalse(claude_link.exists() or claude_link.is_symlink())


class DriveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="verify-drive-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.env = clean_env()
        self.server = PageServer()
        self.addCleanup(self.server.close)

    def make_repo(self, path: Path, verify: object, entries: dict[str, str], link_playwright: bool = True,
                  extra: dict[str, str] | None = None) -> Path:
        init_repo(path, self.env)
        config: dict[str, object] = {"version": "2.1.1", "harnesses": ["claude", "codex"]}
        if verify is not None:
            config["verify"] = verify
        put(path / "dev-loop.json", json.dumps(config, indent=2) + "\n")
        put(path / ".gitignore", "node_modules\n.auth/\nscript-input.json\nsetup-ran\n")
        skill_dir = path / ".agents" / "skills" / DRIVE_SKILL
        put(skill_dir / "SKILL.md", skill_text(DRIVE_SKILL))
        put(skill_dir / "features" / "README.md",
            "# Feature map\n\n" + "".join(f"- [{stem}]({stem}.md)\n" for stem in entries))
        for stem, text in entries.items():
            put(skill_dir / "features" / f"{stem}.md", text)
        (path / ".claude" / "skills").mkdir(parents=True)
        os.symlink(f"../../.agents/skills/{DRIVE_SKILL}", path / ".claude" / "skills" / DRIVE_SKILL)
        for rel, text in (extra or {}).items():
            put(path / rel, text)
        if link_playwright:
            os.symlink(NODE_MODULES, path / "node_modules")
        commit_all(path, self.env, "Base")
        return path

    def drive(self, repo: Path, *args: str) -> tuple[int, dict]:
        return run_json([sys.executable, str(DEVLOOP), "drive", *args], repo, self.env)

    @staticmethod
    def route(payload: dict, entry: str, path: str) -> dict:
        found = [r for r in payload.get("routes", []) if r.get("entry") == entry and r.get("path") == path]
        if len(found) != 1:
            raise AssertionError(f"expected one route {entry} {path}: {payload}")
        return found[0]

    def test_f2_stage6_drive(self) -> None:
        """O13 to O25: a feature branch changes a mapped path, drive selects the entry, reports a failure,
        a fix lands, drive --since selects only what the fix touched and passes."""
        self.assertTrue((NODE_MODULES / "playwright").is_dir() or (NODE_MODULES / "@playwright" / "test").is_dir(),
                        "Playwright missing from this repo: run npm ci && npx playwright install chromium")
        url = self.server.url
        pages = self.server.pages
        home_entry = entry_text([{"path": "/home", "names": [{"role": "heading", "name": "Shop"}]}],
                                ["app/home/*"])
        simple_entries = {"home": home_entry}

        with self.subTest(oracle=13):
            for label, verify in (("absent", None), ("false", False)):
                repo = self.make_repo(self.root / f"no-verify-{label}", verify, simple_entries,
                                      link_playwright=False)
                code, payload = self.drive(repo, "--all")
                self.assertEqual(code, 0, payload)
                self.assertEqual(payload.get("skipped"), "no-verify", payload)
                self.assertEqual(payload.get("routes"), [], payload)

        pages["/home"] = (200, page("<h1>Shop</h1>"))
        with self.subTest(oracle=18):
            dead = f"http://127.0.0.1:{free_port()}"
            repo = self.make_repo(self.root / "dead-url", {"skill": DRIVE_SKILL, "base_url": dead,
                                                           "setup": ["touch setup-ran"]}, simple_entries)
            code, payload = self.drive(repo, "--all")
            self.assertEqual(code, 1, payload)
            self.assertIn("base_url", payload.get("error") or "", payload)
            self.assertFalse(payload.get("routes"), payload)
            self.assertFalse((repo / "setup-ran").exists(), "a setup command ran")

        with self.subTest(oracle=22):
            repo = self.make_repo(self.root / "bad-setup",
                                  {"skill": DRIVE_SKILL, "base_url": url, "setup": ["sh tools/fail-login.sh"]},
                                  simple_entries, extra={"tools/fail-login.sh": "exit 1\n"})
            code, payload = self.drive(repo, "--all")
            self.assertEqual(code, 1, payload)
            self.assertIn("sh tools/fail-login.sh", payload.get("error") or "", payload)
            self.assertFalse(payload.get("routes"), payload)

        with self.subTest(oracle=25):
            parent = self.root / "parent"
            parent.mkdir()
            os.symlink(NODE_MODULES, parent / "node_modules")
            repo = self.make_repo(parent / "shop", {"skill": DRIVE_SKILL, "base_url": url}, simple_entries,
                                  link_playwright=False)
            code, payload = self.drive(repo, "--all")
            self.assertEqual(code, 1, payload)
            self.assertIn("playwright", (payload.get("error") or "").lower(), payload)

        # The shop: five entries, all passing on main.
        pages.update({
            "/a": (200, page("<h1>Alpha</h1>")),
            "/a/more": (200, page("<h1>Alpha more</h1>")),
            "/b": (200, page("<h1>Beta</h1>")),
            "/b/more": (200, page("<h1>Beta more</h1>")),
        })
        login = ("import json, pathlib\n"
                 "p = pathlib.Path('.auth/state.json')\n"
                 "p.parent.mkdir(exist_ok=True)\n"
                 "p.write_text(json.dumps({'cookies': [{'name': 'session', 'value': 'letmein', "
                 "'domain': '127.0.0.1', 'path': '/', 'expires': -1, 'httpOnly': False, 'secure': False, "
                 "'sameSite': 'Lax'}], 'origins': []}))\n")
        scripted = ("import { readFileSync, writeFileSync } from 'node:fs';\n"
                    "import { join } from 'node:path';\n"
                    "const input = JSON.parse(readFileSync(0, 'utf8'));\n"
                    "writeFileSync(new URL('../script-input.json', import.meta.url), JSON.stringify(input));\n"
                    "const routes = input.routes.map((r, i) => ({entry: input.entry, path: r.path, ok: true, "
                    "status: 200, missing: [], console_errors: [], error: null, "
                    "screenshot: join(input.evidence, `${input.entry}-${i + 1}.jpg`), seconds: 4.25}));\n"
                    "process.stdout.write(JSON.stringify({routes}));\n")
        scripted_routes = [{"path": "/scripted", "names": [{"role": "heading", "name": "Scripted"}]}]
        entries = {
            "home": home_entry,
            "account": entry_text([{"path": "/account", "names": [{"role": "heading", "name": "Account"}]}],
                                  ["app/account/*"], title="Account"),
            "scripted": entry_text(scripted_routes, ["app/scripted/*"], script="tools/scripted-drive.mjs",
                                   title="Scripted"),
            "a": entry_text([{"path": "/a", "names": [{"role": "heading", "name": "Alpha"}]},
                             {"path": "/a/more", "names": []}], ["web/a*"], title="Alpha"),
            "b": entry_text([{"path": "/b", "names": [{"role": "heading", "name": "Beta"}]},
                             {"path": "/b/more", "names": []}], ["web/b/*"], title="Beta"),
        }
        repo = self.make_repo(self.root / "corner-shop",
                              {"skill": DRIVE_SKILL, "base_url": url, "setup": ["python3 tools/login.py"],
                               "storage_state": ".auth/state.json"},
                              entries, extra={"tools/login.py": login, "tools/scripted-drive.mjs": scripted,
                                              "web/a.ts": "export const a = 1;\n",
                                              "web/b/x.ts": "export const x = 1;\n",
                                              "docs/guide.md": "# Guide\n"})
        features = repo / ".agents" / "skills" / DRIVE_SKILL / "features"

        code, payload = self.drive(repo, "--all")
        with self.subTest(oracle=14):
            self.assertEqual(code, 0, payload)
            self.assertIs(payload.get("ok"), True, payload)
            home = self.route(payload, "home", "/home")
            self.assertIs(home.get("ok"), True, home)
            self.assertEqual(home.get("status"), 200, home)
            evidence = Path(payload["evidence"])
            shot = Path(home["screenshot"])
            self.assertTrue(shot.is_absolute(), shot)
            self.assertEqual(shot.resolve().parent, evidence.resolve())
            self.assertEqual(shot.read_bytes()[:3], b"\xff\xd8\xff", "screenshot is not a JPEG")
            self.assertEqual(json.loads((evidence / "drive.json").read_text()), payload)
        with self.subTest(oracle=23):
            self.assertIs(self.route(payload, "account", "/account").get("ok"), True, payload)
        with self.subTest(oracle=24):
            received = json.loads((repo / "script-input.json").read_text())
            self.assertEqual(received.get("entry"), "scripted")
            self.assertEqual(received.get("base_url"), url)
            self.assertEqual(received.get("routes"), scripted_routes)
            self.assertEqual(Path(received.get("storage_state")).resolve(),
                             (repo / ".auth" / "state.json").resolve())
            self.assertEqual(Path(received.get("evidence")).resolve(), Path(payload["evidence"]).resolve())
            self.assertEqual(self.route(payload, "scripted", "/scripted").get("seconds"), 4.25, payload)

        git(repo, self.env, "checkout", "-q", "-b", "feat/shop-banner")
        put(repo / "docs" / "guide.md", "# Guide\n\nThe banner.\n")
        commit_all(repo, self.env, "Document the banner")
        with self.subTest(oracle=21):
            code, payload = self.drive(repo)
            self.assertEqual(code, 0, payload)
            self.assertIs(payload.get("ok"), True, payload)
            self.assertEqual(payload.get("routes"), [], payload)

        put(repo / "web" / "a.ts", "export const a = 2;\n")
        commit_all(repo, self.env, "Change the alpha banner")
        pages["/a"] = (200, page("<h1>Banner</h1>"))
        pages["/a/more"] = (404, page("<h1>Gone</h1>"))
        code, payload = self.drive(repo)
        with self.subTest(oracle="19a"):
            self.assertEqual(payload.get("selected"), ["a"], payload)
        with self.subTest(oracle=15):
            self.assertEqual(code, 1, payload)
            self.assertIs(payload.get("ok"), False, payload)
            alpha = self.route(payload, "a", "/a")
            self.assertIs(alpha.get("ok"), False, alpha)
            self.assertIn({"role": "heading", "name": "Alpha"}, alpha.get("missing", []), alpha)
        with self.subTest(oracle=17):
            gone = self.route(payload, "a", "/a/more")
            self.assertEqual(gone.get("status"), 404, gone)
            self.assertIs(gone.get("ok"), False, gone)

        entry_b = features / "b.md"
        entry_b.write_text(entry_b.read_text().replace("## Gotchas\n\nNone known.\n",
                                                       "## Gotchas\n\nThe cart logs its total.\n"))
        commit_all(repo, self.env, "Note the beta gotcha")
        pages["/b"] = (200, page("<h1>Beta</h1><script>console.error('cart total failed')</script>"))
        pages["/b/more"] = (200, page("<h1>Beta more</h1><script>throw new Error('boom in cart')</script>"))
        code, payload = self.drive(repo)
        with self.subTest(oracle="19b"):
            self.assertEqual(payload.get("selected"), ["a", "b"], payload)
        with self.subTest(oracle=16):
            self.assertEqual(code, 1, payload)
            for path in ("/b", "/b/more"):
                beta = self.route(payload, "b", path)
                self.assertTrue(beta.get("console_errors"), beta)
                self.assertIs(beta.get("ok"), False, beta)

        fix_start = git(repo, self.env, "rev-parse", "HEAD")
        put(repo / "web" / "b" / "x.ts", "export const x = 2;\n")
        commit_all(repo, self.env, "Fix the beta cart errors")
        pages["/b"] = (200, page("<h1>Beta</h1>"))
        pages["/b/more"] = (200, page("<h1>Beta more</h1>"))
        with self.subTest(oracle=20):
            code, payload = self.drive(repo, "--since", fix_start)
            self.assertEqual(payload.get("selected"), ["b"], payload)
            self.assertEqual(code, 0, payload)
            self.assertIs(payload.get("ok"), True, payload)
            self.assertEqual({r.get("path") for r in payload.get("routes", [])}, {"/b", "/b/more"}, payload)


class KitTextTests(unittest.TestCase):
    def test_o26_skill_names_verify_init_and_drive(self) -> None:
        """O26: SKILL.md Stage 0 names verify-init and references/verify.md; Stage 6 names drive and `drive:`."""
        text = (SKILL / "SKILL.md").read_text()
        stage0 = re.search(r"(?ms)^## 0 .*?(?=^## 1 )", text)
        stage6 = re.search(r"(?ms)^## 6 .*?(?=^## 7 )", text)
        self.assertIsNotNone(stage0)
        self.assertIsNotNone(stage6)
        self.assertIn("devloop.py verify-init", stage0.group(0))
        self.assertIn("references/verify.md", stage0.group(0))
        self.assertIn("devloop.py drive", stage6.group(0))
        self.assertIn("drive:", stage6.group(0))


if __name__ == "__main__":
    unittest.main()
