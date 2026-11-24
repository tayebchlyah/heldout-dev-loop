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


def compare(repo: str, base: str, head: str, files: list[dict], commits: list[str] | tuple[str, ...] = (), total: int | None = None) -> dict:
    return {f"repos/{repo}/compare/{base}...{head}": {
        "html_url": f"https://github.com/{repo}/compare/{base}...{head}", "files": files,
        "commits": [{"sha": sha} for sha in commits], "total_commits": len(commits) if total is None else total},
        f"repos/{repo}/releases?per_page=20&page=1": []}


def path_commits(repo: str, head: str, path: str, log: list[tuple[str, str]]) -> dict:
    return {f"repos/{repo}/commits?path={path}&sha={head}&per_page=100":
            [{"sha": sha, "commit": {"message": message}} for sha, message in log]}


def release(tag: str, body: str = "", draft: bool = False, prerelease: bool = False) -> dict:
    return {"tag_name": tag, "body": body, "draft": draft, "prerelease": prerelease,
            "html_url": f"https://github.com/o/rel/releases/tag/{tag}"}


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
        for path in ("skills/a/SKILL.md", "AGENTS.md", "skills/a/gone.md", "skills/a/new.md", "skills/a/hidden.md"):
            routes.update(path_commits("o/rel", "v1.1.0", path, []))
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
                  **trees("o/rel", "v1.0.0", "v1.1.0", {"AGENTS.md": "1"}, {"AGENTS.md": "2"}),
                  **path_commits("o/rel", "v1.1.0", "AGENTS.md", [])}
        _, out = self.run_watch([src], routes, "--dry-run")
        self.assertLess(len(out["body"]), 65536)
        self.assertIn("AGENTS.md", out["body"])
        self.assertNotIn("+line", out["body"])
        self.assertIn("over the GitHub limit", out["body"])

    def test_commits_that_changed_a_watched_file(self) -> None:
        src = source("rel", "release", pin="v1.0.0", version="1.0.0")
        files = [{"filename": "AGENTS.md", "status": "modified"}, {"filename": "skills/a/x.md", "status": "added"}]
        a, b, c, old = "1" * 40, "2" * 40, "3" * 40, "4" * 40
        thanks = "docs: thanks @bob, fixes #12 and o/x#3\n\nbody line"
        routes = {"repos/o/rel/releases/latest": {"tag_name": "v1.1.0"},
                  **compare("o/rel", "v1.0.0", "v1.1.0", files, commits=[a, b, c]),
                  **trees("o/rel", "v1.0.0", "v1.1.0", {"AGENTS.md": "1"}, {"AGENTS.md": "2", "skills/a/x.md": "1"}),
                  **path_commits("o/rel", "v1.1.0", "AGENTS.md", [(c, thanks), (old, "old: before the pin")]),
                  **path_commits("o/rel", "v1.1.0", "skills/a/x.md", [(c, thanks), (a, "feat: use `x` here")])}
        code, out = self.run_watch([src], routes, "--dry-run")
        self.assertEqual(code, 0, out)
        body = out["body"]
        self.assertIn("Commits that changed a watched file:", body)
        first = f"- [`1111111`](https://github.com/o/rel/commit/{a}) `` feat: use `x` here ``"
        last = f"- [`3333333`](https://github.com/o/rel/commit/{c}) ` docs: thanks @bob, fixes #12 and o/x#3 `"
        self.assertIn(first, body)
        self.assertIn(last, body)
        self.assertLess(body.index(first), body.index(last))
        self.assertEqual(body.count(f"commit/{c})"), 1)
        for text in ("2222222", "4444444", "before the pin", "body line"):
            self.assertNotIn(text, body)

    def test_commit_list_over_the_compare_limit_says_so(self) -> None:
        src = source("rel", "release", pin="v1.0.0", version="1.0.0")
        routes = {"repos/o/rel/releases/latest": {"tag_name": "v1.1.0"},
                  **compare("o/rel", "v1.0.0", "v1.1.0", [{"filename": "AGENTS.md", "status": "modified"}],
                            commits=["1" * 40], total=300),
                  **trees("o/rel", "v1.0.0", "v1.1.0", {"AGENTS.md": "1"}, {"AGENTS.md": "2"}),
                  **path_commits("o/rel", "v1.1.0", "AGENTS.md", [("1" * 40, "fix: one")])}
        _, out = self.run_watch([src], routes, "--dry-run")
        self.assertIn("Only the first 1 of 300 commits of the range are checked", out["body"])

    def test_commit_list_over_the_compare_limit_says_so_without_a_matched_commit(self) -> None:
        src = source("rel", "release", pin="v1.0.0", version="1.0.0")
        routes = {"repos/o/rel/releases/latest": {"tag_name": "v1.1.0"},
                  **compare("o/rel", "v1.0.0", "v1.1.0", [{"filename": "AGENTS.md", "status": "modified"}],
                            commits=[str(n).zfill(40) for n in range(250)], total=300),
                  **trees("o/rel", "v1.0.0", "v1.1.0", {"AGENTS.md": "1"}, {"AGENTS.md": "2"}),
                  **path_commits("o/rel", "v1.1.0", "AGENTS.md", [])}
        _, out = self.run_watch([src], routes, "--dry-run")
        self.assertIn("Only the first 250 of 300 commits of the range are checked", out["body"])
        self.assertNotIn("Commits that changed a watched file:", out["body"])

    def test_tag_names_and_paths_stay_in_code(self) -> None:
        src = source("rel", "release", pin="@bob", version="1.0.0")
        tags = ("@bob", "o/x#3")
        listing = [release("o/x#3", "n" * 5000), release("@bob")]
        routes = {"repos/o/rel/releases/latest": {"tag_name": "o/x#3"},
                  **compare("o/rel", "@bob", "o/x#3", []), **trees("o/rel", "@bob", "o/x#3", {}, {}),
                  "repos/o/rel/releases?per_page=20&page=1": listing}
        _, out = self.run_watch([src], routes, "--dry-run")
        self.assertIn("## rel: ` @bob ` to ` o/x#3 `", out["body"])
        self.assertIn("<summary><code>o/x#3</code></summary>", out["body"])
        long_notes = [release(f"v{n}.0.0", "z" * 3000) for n in range(30, 0, -1)] + [release("@bob")]
        long_notes[0] = release("o/x#3", "z" * 3000)
        routes["repos/o/rel/releases?per_page=20&page=1"] = long_notes
        _, out = self.run_watch([src], routes, "--dry-run")
        self.assertIn("- ` o/x#3 `: https://github.com/o/rel/releases/tag/o/x#3", out["body"])
        for tag in tags:
            outside = out["body"].replace(f"` {tag} `", "").replace(f"<code>{tag}</code>", "")
            self.assertNotIn(f"- {tag}", outside)
            self.assertNotIn(f": {tag} to", outside)

    def test_commits_query_encodes_the_path(self) -> None:
        src = source("rel", "release", pin="v1.0.0", version="1.0.0")
        name = "skills/a/my file#1.md"
        routes = {"repos/o/rel/releases/latest": {"tag_name": "v1.1.0"},
                  **compare("o/rel", "v1.0.0", "v1.1.0", [{"filename": name, "status": "added"}]),
                  **trees("o/rel", "v1.0.0", "v1.1.0", {}, {name: "1"}),
                  **path_commits("o/rel", "v1.1.0", "skills/a/my%20file%231.md", [])}
        code, out = self.run_watch([src], routes, "--dry-run")
        self.assertEqual(code, 0, out)

    def test_left_out_lines_name_only_parts_the_source_had(self) -> None:
        patch = {"filename": "AGENTS.md", "status": "modified", "patch": "@@ -1 +1 @@\n+diff line"}
        bare = {"filename": "AGENTS.md", "status": "modified"}
        log = [(str(n).zfill(40), f"fix: commit number {n} " + "y" * 150) for n in range(400)]
        shas = [sha for sha, _ in log]
        pv = source("pv", "plugin-version", pin="f" * 40, version="0.1.0", file="p/plugin.json")
        rel = source("rel", "release", pin="v1.0.0", version="1.0.0")
        tree = {"AGENTS.md": "1"}, {"AGENTS.md": "2"}
        with_patch = {**plugin_routes("0.2.0"), **compare("o/pv", "f" * 40, "c" * 40, [patch], commits=shas),
                      **trees("o/pv", "f" * 40, "c" * 40, *tree), **path_commits("o/pv", "c" * 40, "AGENTS.md", log)}
        no_patch = {"repos/o/rel/releases/latest": {"tag_name": "v1.1.0"},
                    **compare("o/rel", "v1.0.0", "v1.1.0", [bare], commits=shas), **trees("o/rel", "v1.0.0", "v1.1.0", *tree),
                    **path_commits("o/rel", "v1.1.0", "AGENTS.md", log),
                    "repos/o/rel/releases?per_page=20&page=1": [release("v1.0.0")]}
        for name, src, routes, shown in (("patch, no notes", pv, with_patch, ("Diffs", "Commits")),
                                         ("no patch, no release", rel, no_patch, ("Commits",))):
            with self.subTest(name):
                _, out = self.run_watch([src], routes, "--dry-run")
                for part in ("Diffs", "Release note bodies", "Commits"):
                    self.assertEqual(f"{part} left out" in out["body"], part in shown, part)

    def test_release_notes_after_the_pin(self) -> None:
        src = source("rel", "release", pin="v1.0.0", version="1.0.0")
        listing = [release("v1.3.0-rc.1", "rc notes", prerelease=True), release("v1.2.0", "## Fixes\n- by @ann in #7\n```\ncode"),
                   release("v1.1.1", "draft notes", draft=True), release("v1.1.0", "x" * 5000),
                   release("v1.0.0", "pin notes"), release("v0.9.0", "older notes")]
        routes = {"repos/o/rel/releases/latest": {"tag_name": "v1.2.0"},
                  **compare("o/rel", "v1.0.0", "v1.2.0", []), **trees("o/rel", "v1.0.0", "v1.2.0", {}, {}),
                  "repos/o/rel/releases?per_page=20&page=1": listing}
        code, out = self.run_watch([src], routes, "--dry-run")
        self.assertEqual(code, 0, out)
        body = out["body"]
        self.assertIn("<details><summary><code>v1.2.0</code></summary>\n\nhttps://github.com/o/rel/releases/tag/v1.2.0\n\n"
                      "````text\n## Fixes\n- by @ann in #7\n```\ncode\n````\n\n</details>", body)
        self.assertIn("<details><summary><code>v1.1.0</code></summary>", body)
        self.assertIn("x" * 3000 + "\n[cut at 3000 characters, full notes at the link]", body)
        self.assertNotIn("x" * 3001, body)
        self.assertLess(body.index("v1.2.0</code></summary>"), body.index("v1.1.0</code></summary>"))
        for text in ("rc notes", "draft notes", "pin notes", "older notes"):
            self.assertNotIn(text, body)

    def test_release_notes_when_the_pin_is_not_listed(self) -> None:
        src = source("rel", "release", pin="v1.0.0", version="1.0.0")
        routes = {"repos/o/rel/releases/latest": {"tag_name": "v1.1.0"},
                  **compare("o/rel", "v1.0.0", "v1.1.0", []), **trees("o/rel", "v1.0.0", "v1.1.0", {}, {}),
                  "repos/o/rel/releases?per_page=20&page=1": [release("v1.1.0", "new notes")]}
        _, out = self.run_watch([src], routes, "--dry-run")
        self.assertIn("new notes", out["body"])
        self.assertIn("The pin has no release in the list: every release is shown.", out["body"])
        self.assertNotIn("newest releases", out["body"])

    def test_release_list_reads_pages_of_20_up_to_5(self) -> None:
        src = source("rel", "release", pin="v1.0.0", version="1.0.0")
        alphas = [release(f"v1.1.0-alpha.{n}", "alpha notes", prerelease=True) for n in range(20)]
        base = {"repos/o/rel/releases/latest": {"tag_name": "v1.1.0"},
                **compare("o/rel", "v1.0.0", "v1.1.0", []), **trees("o/rel", "v1.0.0", "v1.1.0", {}, {})}
        found = {"repos/o/rel/releases?per_page=20&page=1": alphas,
                 "repos/o/rel/releases?per_page=20&page=2": [release("v1.1.0", "page two notes"), release("v1.0.0")]}
        capped = {f"repos/o/rel/releases?per_page=20&page={n}": alphas for n in range(1, 7)}
        for name, pages, last, listed in (("pin on page 2", found, 2, True), ("five full pages", capped, 5, False)):
            with self.subTest(name):
                self.log.write_text("")
                code, out = self.run_watch([src], {**base, **pages}, "--dry-run")
                self.assertEqual(code, 0, out)
                read = [c["args"][1] for c in self.calls() if "/releases?" in c["args"][1]]
                self.assertEqual(read, [f"repos/o/rel/releases?per_page=20&page={n}" for n in range(1, last + 1)])
                self.assertEqual("page two notes" in out["body"], listed)
                self.assertEqual("The pin is not among the 100 newest releases: the list may be incomplete."
                                 in out["body"], not listed)
                self.assertNotIn("every release is shown", out["body"])

    def test_no_release_notes_for_other_signals(self) -> None:
        src = source("pv", "plugin-version", pin="f" * 40, version="0.1.0", file="p/plugin.json")
        routes = {**plugin_routes("0.2.0"), **compare("o/pv", "f" * 40, "c" * 40, []),
                  **trees("o/pv", "f" * 40, "c" * 40, {}, {})}
        code, out = self.run_watch([src], routes, "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertNotIn("<details>", out["body"])
        self.assertNotIn("releases?", " ".join(c["args"][1] for c in self.calls()))

    def test_body_too_long_drops_diffs_then_notes_then_commits(self) -> None:
        src = source("rel", "release", pin="v1.0.0", version="1.0.0")
        files = [{"filename": "AGENTS.md", "status": "modified", "patch": "@@ -1 +1 @@\n+diff line"}]
        log = [(str(n).zfill(40), f"fix: commit number {n} " + "y" * 150) for n in range(400)]
        base = {"repos/o/rel/releases/latest": {"tag_name": "v1.1.0"},
                **trees("o/rel", "v1.0.0", "v1.1.0", {"AGENTS.md": "1"}, {"AGENTS.md": "2"})}
        notes = [release(f"v1.{n}.0", "z" * 3000) for n in range(30, 0, -1)] + [release("v1.0.0")]
        rows = [("notes too long", log[:5], notes, ["notes", "diffs"], ["commit number 4"]),
                ("commits too long", log, notes, ["notes", "diffs", "commits"], ["v1.30.0"])]
        for name, commits, listing, dropped, kept in rows:
            with self.subTest(name):
                routes = {**base, **compare("o/rel", "v1.0.0", "v1.1.0", files, commits=[s for s, _ in commits]),
                          **path_commits("o/rel", "v1.1.0", "AGENTS.md", commits),
                          "repos/o/rel/releases?per_page=20&page=1": listing}
                _, out = self.run_watch([src], routes, "--dry-run")
                body = out["body"]
                self.assertLess(len(body), 65536)
                self.assertNotIn("+diff line", body)
                self.assertNotIn("z" * 100, body)
                self.assertIn("Release note bodies left out", body)
                self.assertIn("https://github.com/o/rel/releases/tag/v1.30.0", body)
                self.assertEqual("commit number" in body, "commits" not in dropped)
                for text in kept:
                    self.assertIn(text, body)

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
