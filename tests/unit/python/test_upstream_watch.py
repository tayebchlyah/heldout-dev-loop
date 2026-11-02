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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT  # noqa: E402

SCRIPT = ROOT / "scripts" / "upstream_watch.py"

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


def trees(repo: str, base: str, head: str, before: dict, after: dict, truncated: bool = False) -> dict:
    def listing(blobs: dict) -> dict:
        entries = [{"path": "skills", "type": "tree", "sha": "0" * 40}]
        entries += [{"path": path, "type": "blob", "sha": sha} for path, sha in blobs.items()]
        return {"tree": entries, "truncated": truncated}
    return {f"repos/{repo}/git/trees/{base}?recursive=1": listing(before),
            f"repos/{repo}/git/trees/{head}?recursive=1": listing(after)}


def plugin_routes(version: str) -> dict:
    content = base64.b64encode(json.dumps({"version": version}).encode()).decode()
    return {"repos/o/pv/commits?path=p/plugin.json&per_page=1": [{"sha": "c" * 40}],
            f"repos/o/pv/contents/p/plugin.json?ref={'c' * 40}": {"content": content}}


def tree_routes(tree: str) -> dict:
    return {"repos/o/tr/commits/HEAD": {"sha": "d" * 40},
            f"repos/o/tr/contents/skills?ref={'d' * 40}": [{"name": "other", "sha": "e" * 40},
                                                            {"name": "a", "sha": tree}]}


CASES = [
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
]

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
                    head = HEADS.get(src["signal"], new)
                    routes = {**routes, **compare(src["repo"], src["pin"], head, []),
                              **trees(src["repo"], src["pin"], head, {}, {})}
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
        before = {"skills/a/SKILL.md": "1", "AGENTS.md": "1", "skills/ab/SKILL.md": "1", "README.md": "1",
                  "skills/a/gone.md": "1", "skills/a/same.md": "1"}
        after = {"skills/a/SKILL.md": "2", "AGENTS.md": "2", "skills/ab/SKILL.md": "2", "README.md": "2",
                 "skills/a/new.md": "1", "skills/a/same.md": "1", "skills/a/hidden.md": "9"}
        routes = {"repos/o/rel/releases/latest": {"tag_name": "v1.1.0"},
                  **compare("o/rel", "v1.0.0", "v1.1.0", files), **trees("o/rel", "v1.0.0", "v1.1.0", before, after)}
        code, out = self.run_watch([src], routes, "--dry-run")
        self.assertEqual(code, 0, out)
        body = out["body"]
        self.assertIn("https://github.com/o/rel/compare/v1.0.0...v1.1.0", body)
        self.assertIn("+```new", body)
        self.assertIn("````diff", body)
        self.assertIn("`AGENTS.md` (modified), diff on the compare page", body)
        self.assertIn("`skills/a/gone.md` (removed), diff on the compare page", body)
        self.assertIn("`skills/a/new.md` (added), diff on the compare page", body)
        self.assertIn("`skills/a/hidden.md` (added), diff on the compare page", body)
        self.assertNotIn("same.md", body)
        self.assertNotIn("skills/ab/", body)
        self.assertNotIn("README.md", body)
        self.assertIn(["api", "repos/o/rel/compare/v1.0.0...v1.1.0"], [c["args"][:2] for c in self.calls()])

    def test_release_with_no_watched_file_says_so(self) -> None:
        src = source("rel", "release", pin="v1.0.0", version="1.0.0")
        routes = {"repos/o/rel/releases/latest": {"tag_name": "v1.1.0"},
                  **compare("o/rel", "v1.0.0", "v1.1.0", [{"filename": "README.md", "status": "modified"}]),
                  **trees("o/rel", "v1.0.0", "v1.1.0", {"README.md": "1"}, {"README.md": "2"})}
        _, out = self.run_watch([src], routes, "--dry-run")
        self.assertIn("No watched file changed.", out["body"])

    def test_body_too_long_drops_the_patches(self) -> None:
        src = source("rel", "release", pin="v1.0.0", version="1.0.0")
        patch = "@@ -1 +1 @@\n" + "+line\n" * 20000
        routes = {"repos/o/rel/releases/latest": {"tag_name": "v1.1.0"},
                  **compare("o/rel", "v1.0.0", "v1.1.0", [{"filename": "AGENTS.md", "status": "modified", "patch": patch}]),
                  **trees("o/rel", "v1.0.0", "v1.1.0", {"AGENTS.md": "1"}, {"AGENTS.md": "2"})}
        _, out = self.run_watch([src], routes, "--dry-run")
        self.assertLess(len(out["body"]), 65536)
        self.assertIn("AGENTS.md", out["body"])
        self.assertNotIn("+line", out["body"])
        self.assertIn("over the GitHub limit", out["body"])

    def test_issue_action(self) -> None:
        new = {"repos/o/rel/releases/latest": {"tag_name": "v1.1.0"}, **compare("o/rel", "v1.0.0", "v1.1.0", []),
               **trees("o/rel", "v1.0.0", "v1.1.0", {}, {})}
        current = {"repos/o/rel/releases/latest": {"tag_name": "v1.0.0"}}
        rows = [
            ("nothing new", current, [], "none", None, []),
            ("nothing new, issue open", current, [{"number": 7}], "none", None, []),
            ("new, no issue", new, [], "created", 9, ["label create", "issue create"]),
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
                self.assertEqual(out["issue"], issue)
                made = [" ".join(c["args"][:2]) for c in self.calls() if c["args"][0] != "api"]
                self.assertEqual([m for m in made if m != "issue list"], writes)
                for call in self.calls():
                    if call["args"][:2] in (["issue", "create"], ["issue", "edit"]):
                        self.assertIn("## rel", call["stdin"])
                        if call["args"][1] == "create":
                            self.assertIn("upstream-watch", call["args"])

    def test_a_failing_source_keeps_the_news_of_the_others(self) -> None:
        rel = source("rel", "release", pin="v1.0.0", version="1.0.0")
        bad = source("bad", "release", pin="v1.0.0", version="1.0.0")
        tr = source("tr", "tree", pin="f" * 40, version="f" * 40, folder="skills/a", tree=PIN_TREE)
        news = {"repos/o/rel/releases/latest": {"tag_name": "v1.1.0"}, **compare("o/rel", "v1.0.0", "v1.1.0", []),
                **trees("o/rel", "v1.0.0", "v1.1.0", {}, {})}
        truncated = {**news, **trees("o/rel", "v1.0.0", "v1.1.0", {}, {}, truncated=True)}
        not_a_list = {"repos/o/tr/commits/HEAD": {"sha": "d" * 40}, f"repos/o/tr/contents/skills?ref={'d' * 40}": {"a": 1}}
        rows = [
            ("one fails, one new", [rel, bad], news, 1, "created", "bad:", ["rel"], {"bad": "error", "rel": "new"}),
            ("only a failing source", [bad], {}, 1, "none", "bad:", [], {"bad": "error"}),
            ("truncated tree", [rel], truncated, 1, "none", "rel:", [], {"rel": "error"}),
            ("tree contents not a list", [tr], not_a_list, 1, "none", "tr:", [], {"tr": "error"}),
            ("two fail", [bad, tr], not_a_list, 1, "none", "bad: ", [], {"bad": "error", "tr": "error"}),
            ("issue url without a number", [rel], {**news, "issue create": "created it\n"}, 1, "none",
             "issue create", ["rel"], {"rel": "new"}),
        ]
        for name, sources, routes, code, action, error, titled, states in rows:
            with self.subTest(name):
                self.log.write_text("")
                routes = {"issue list": [], "issue create": "https://github.com/x/y/issues/9\n",
                          "label create": "", **routes}
                got, out = self.run_watch(sources, routes)
                self.assertEqual(got, code, out)
                self.assertEqual(out["action"], action)
                self.assertIn(error, out["error"])
                self.assertEqual({r["name"]: r["state"] for r in out["sources"]}, states)
                created = [c["args"][c["args"].index("--title") + 1] for c in self.calls() if c["args"][:2] == ["issue", "create"]]
                self.assertEqual(created, [f"Upstream watch: {', '.join(titled)}"] if titled else [])
                if name == "two fail":
                    self.assertEqual(out["error"].count("; "), 1)


class UpstreamListTests(unittest.TestCase):
    def test_each_local_file_cites_the_pinned_version(self) -> None:
        sources = json.loads((ROOT / "upstream.json").read_text())["sources"]
        for src in sources:
            self.assertIn(src["signal"], ("release", "plugin-version", "tree"))
            self.assertTrue(src["local"], src["name"])
            for local in src["local"]:
                with self.subTest(source=src["name"], file=local):
                    self.assertIn(src["version"], (ROOT / local).read_text())
            if src.get("tree"):
                self.assertTrue(any(src["tree"] in (ROOT / local).read_text() for local in src["local"]), src["name"])


if __name__ == "__main__":
    unittest.main()
