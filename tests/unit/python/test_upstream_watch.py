#!/usr/bin/env python3
"""Tests for scripts/upstream_watch.py: the signal per source, the diff filter, the one issue, the pin text."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "upstream_watch.py"

# Answers a route from FAKE_GH_ROUTES: `gh api <path>` by its path, any other call by its first two words.
# Each call goes to FAKE_GH_LOG as one JSON line, stdin included (the issue body).
FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
key = args[1] if args[0] == "api" else " ".join(args[:2])
stdin = sys.stdin.read() if "-" in args else ""
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps({"args": args, "stdin": stdin}) + "\\n")
routes = json.load(open(os.environ["FAKE_GH_ROUTES"]))
if key not in routes:
    sys.stderr.write("gh: Not Found (HTTP 404)\\n")
    sys.exit(1)
sys.stdout.write(routes[key] if isinstance(routes[key], str) else json.dumps(routes[key]))
"""

PIN_TREE = "a" * 40
NEW_TREE = "b" * 40


def source(name: str, signal: str, **extra) -> dict:
    return {"name": name, "repo": f"o/{name}", "signal": signal, "paths": ["skills/a/", "AGENTS.md"],
            "local": [], **extra}


def compare(repo: str, base: str, head: str, files: list[dict]) -> dict:
    return {f"repos/{repo}/compare/{base}...{head}": {
        "html_url": f"https://github.com/{repo}/compare/{base}...{head}", "files": files}}


def plugin_routes(version: str) -> dict:
    content = base64.b64encode(json.dumps({"version": version}).encode()).decode()
    return {"repos/o/pv/commits?path=p/plugin.json&per_page=1": [{"sha": "c" * 40}],
            f"repos/o/pv/contents/p/plugin.json?ref={'c' * 40}": {"content": content}}


def tree_routes(tree: str) -> dict:
    return {"repos/o/tr/commits/HEAD": {"sha": "d" * 40},
            f"repos/o/tr/contents/skills?ref={'d' * 40}": [{"name": "other", "sha": "e" * 40},
                                                            {"name": "a", "sha": tree}]}


CASES = [
    # name, source, routes, state, from, to
    ("release new", source("rel", "release", pin="v1.0.0", version="1.0.0"),
     {"repos/o/rel/releases/latest": {"tag_name": "v1.1.0"}}, "new", "v1.0.0", "v1.1.0"),
    ("release current", source("rel", "release", pin="v1.0.0", version="1.0.0"),
     {"repos/o/rel/releases/latest": {"tag_name": "v1.0.0"}}, "current", "v1.0.0", "v1.0.0"),
    ("release ignored", source("rel", "release", pin="v1.0.0", version="1.0.0", ignored="v1.1.0"),
     {"repos/o/rel/releases/latest": {"tag_name": "v1.1.0"}}, "ignored", "v1.0.0", "v1.1.0"),
    ("release newer than ignored", source("rel", "release", pin="v1.0.0", version="1.0.0", ignored="v1.1.0"),
     {"repos/o/rel/releases/latest": {"tag_name": "v1.2.0"}}, "new", "v1.0.0", "v1.2.0"),
    ("plugin version new", source("pv", "plugin-version", pin="f" * 40, version="0.1.0", file="p/plugin.json"),
     plugin_routes("0.2.0"), "new", "0.1.0", "0.2.0"),
    ("plugin version current", source("pv", "plugin-version", pin="f" * 40, version="0.1.0", file="p/plugin.json"),
     plugin_routes("0.1.0"), "current", "0.1.0", "0.1.0"),
    ("tree new", source("tr", "tree", pin="f" * 40, version="f" * 40, folder="skills/a", tree=PIN_TREE),
     tree_routes(NEW_TREE), "new", PIN_TREE, NEW_TREE),
    ("tree current", source("tr", "tree", pin="f" * 40, version="f" * 40, folder="skills/a", tree=PIN_TREE),
     tree_routes(PIN_TREE), "current", PIN_TREE, PIN_TREE),
    ("not watched", source("cf", "none", pin="f" * 40, version="f" * 40), {}, "unwatched", None, None),
]

# The compare head of each signal in a "new" case: the release tag, the plugin.json commit, the branch head.
HEADS = {"plugin-version": "c" * 40, "tree": "d" * 40}


class UpstreamWatchTests(unittest.TestCase):
    def setUp(self) -> None:
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.work = Path(work.name)
        (self.work / "bin").mkdir()
        gh = self.work / "bin" / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.log = self.work / "gh.log"
        self.log.touch()

    def run_watch(self, sources: list[dict], routes: dict, *flags: str) -> tuple[int, dict]:
        (self.work / "upstream.json").write_text(json.dumps({"sources": sources}))
        (self.work / "routes.json").write_text(json.dumps(routes))
        env = {**os.environ, "PATH": f"{self.work / 'bin'}{os.pathsep}{os.environ['PATH']}",
               "FAKE_GH_ROUTES": str(self.work / "routes.json"), "FAKE_GH_LOG": str(self.log)}
        result = subprocess.run([sys.executable, str(SCRIPT), "--sources", str(self.work / "upstream.json"), *flags],
                                capture_output=True, text=True, env=env)
        return result.returncode, json.loads(result.stdout)

    def calls(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_state_per_signal(self) -> None:
        for name, src, routes, state, old, new in CASES:
            with self.subTest(name):
                if state == "new":
                    routes = {**routes, **compare(src["repo"], src["pin"], HEADS.get(src["signal"], new), [])}
                code, out = self.run_watch([src], routes, "--dry-run")
                self.assertEqual(code, 0, out)
                [row] = out["sources"]
                self.assertEqual((row["state"], row["from"], row["to"]), (state, old, new))
                self.assertEqual(f"## {src['name']}" in out["body"], state == "new")

    def test_body_shows_only_watched_files(self) -> None:
        src = source("rel", "release", pin="v1.0.0", version="1.0.0")
        files = [{"filename": "skills/a/SKILL.md", "status": "modified", "patch": "@@ -1 +1 @@\n-old\n+```new"},
                 {"filename": "AGENTS.md", "status": "modified"},
                 {"filename": "skills/ab/SKILL.md", "status": "modified", "patch": "@@ -1 +1 @@\n-x\n+y"},
                 {"filename": "README.md", "status": "modified", "patch": "@@ -1 +1 @@\n-r\n+s"}]
        routes = {"repos/o/rel/releases/latest": {"tag_name": "v1.1.0"},
                  **compare("o/rel", "v1.0.0", "v1.1.0", files)}
        code, out = self.run_watch([src], routes, "--dry-run")
        self.assertEqual(code, 0, out)
        body = out["body"]
        self.assertIn("https://github.com/o/rel/compare/v1.0.0...v1.1.0", body)
        self.assertIn("+```new", body)
        self.assertIn("````diff", body)  # a fence longer than any backtick run in the patch
        self.assertIn("AGENTS.md", body)  # no patch from the API: named, with the compare link
        self.assertNotIn("skills/ab/", body)
        self.assertNotIn("README.md", body)
        self.assertEqual(self.calls()[-1]["args"][:2], ["api", "repos/o/rel/compare/v1.0.0...v1.1.0"])

    def test_release_with_no_watched_file_says_so(self) -> None:
        src = source("rel", "release", pin="v1.0.0", version="1.0.0")
        routes = {"repos/o/rel/releases/latest": {"tag_name": "v1.1.0"},
                  **compare("o/rel", "v1.0.0", "v1.1.0", [{"filename": "README.md", "status": "modified"}])}
        _, out = self.run_watch([src], routes, "--dry-run")
        self.assertIn("No watched file changed.", out["body"])

    def test_body_too_long_drops_the_patches(self) -> None:
        src = source("rel", "release", pin="v1.0.0", version="1.0.0")
        patch = "@@ -1 +1 @@\n" + "+line\n" * 20000
        routes = {"repos/o/rel/releases/latest": {"tag_name": "v1.1.0"},
                  **compare("o/rel", "v1.0.0", "v1.1.0", [{"filename": "AGENTS.md", "status": "modified", "patch": patch}])}
        _, out = self.run_watch([src], routes, "--dry-run")
        self.assertLess(len(out["body"]), 65536)
        self.assertIn("AGENTS.md", out["body"])
        self.assertNotIn("+line", out["body"])

    def test_issue_action(self) -> None:
        new = {"repos/o/rel/releases/latest": {"tag_name": "v1.1.0"}, **compare("o/rel", "v1.0.0", "v1.1.0", [])}
        current = {"repos/o/rel/releases/latest": {"tag_name": "v1.0.0"}}
        rows = [
            # name, release routes, open issues, action, issue, the gh issue and label calls
            ("nothing new", current, [], "none", None, []),
            ("nothing new, issue open", current, [{"number": 7}], "none", None, []),
            ("new, no issue", new, [], "created", None, ["label create", "issue create"]),
            ("new, issue open", new, [{"number": 7}], "edited", 7, ["issue edit"]),
        ]
        for name, routes, issues, action, issue, writes in rows:
            with self.subTest(name):
                self.log.write_text("")
                routes = {**routes, "issue list": issues, "issue create": "https://github.com/x/y/issues/9\n",
                          "issue edit": "", "label create": ""}
                code, out = self.run_watch([source("rel", "release", pin="v1.0.0", version="1.0.0")], routes)
                self.assertEqual(code, 0, out)
                self.assertEqual(out["action"], action)
                self.assertEqual(out["issue"], 9 if action == "created" else issue)
                made = [" ".join(c["args"][:2]) for c in self.calls() if c["args"][0] != "api"]
                self.assertEqual([m for m in made if m != "issue list"], writes)
                for call in self.calls():
                    if call["args"][:2] in (["issue", "create"], ["issue", "edit"]):
                        self.assertIn("## rel", call["stdin"])
                        if call["args"][1] == "create":
                            self.assertIn("upstream-watch", call["args"])

    def test_gh_failure_fails_the_run(self) -> None:
        code, out = self.run_watch([source("rel", "release", pin="v1.0.0", version="1.0.0")], {})
        self.assertEqual(code, 1)
        self.assertIn("rel", out["error"])
        self.assertFalse(any(c["args"][0] == "issue" for c in self.calls()))


class UpstreamListTests(unittest.TestCase):
    def test_each_local_file_cites_the_pinned_version(self) -> None:
        sources = json.loads((ROOT / "upstream.json").read_text())["sources"]
        self.assertGreaterEqual(len(sources), 6)
        for src in sources:
            self.assertIn(src["signal"], ("release", "plugin-version", "tree", "none"))
            self.assertTrue(src["local"], src["name"])
            for local in src["local"]:
                with self.subTest(source=src["name"], file=local):
                    self.assertIn(src["version"], (ROOT / local).read_text())


if __name__ == "__main__":
    unittest.main()
