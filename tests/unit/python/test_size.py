#!/usr/bin/env python3
"""Tests for size.py: devloop.py size-check, the seam finder, and the size rule in gate, seal and merge-check."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT, budget_report  # noqa: E402

sys.path.insert(0, str(ROOT / "skill" / "scripts"))
from test_devloop import git, put, run_cli  # noqa: E402
import size  # noqa: E402


def blob(n: int, line: bytes = b"x" * 79 + b"\n") -> bytes:
    return (line * (n // len(line) + 1))[:n]


class SizeRepo(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name).resolve() / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        put(self.repo / "README.md", "readme\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        git(self.repo, "checkout", "-q", "-b", "feat/x")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, path: str, data: bytes) -> None:
        (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / path).write_bytes(data)

    def commit(self, path: str, data: bytes, message: str = "change") -> None:
        self.write(path, data)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)

    def on_main(self, path: str, data: bytes) -> None:
        git(self.repo, "checkout", "-q", "main")
        self.commit(path, data, "on main")
        git(self.repo, "checkout", "-q", "feat/x")
        git(self.repo, "rebase", "-q", "main")

    def check(self, *args: str) -> tuple[int, dict]:
        return run_cli("size-check", *args, cwd=self.repo)

    def reasons(self, *args: str) -> list[tuple[str, str]]:
        code, out = self.check(*args)
        self.assertEqual(code, 0 if out["ok"] else 1, out)
        return [(v["path"], v["reason"]) for v in out["violations"]]


class SizeRuleTests(SizeRepo):
    def test_new_file_limit(self) -> None:
        self.commit("a.txt", blob(40001))
        code, out = self.check()
        self.assertEqual((code, out["ok"], out["mode"], out["limit"], out["base"]),
                         (1, False, "committed", 40000, git(self.repo, "rev-parse", "main").strip()))
        [v] = out["violations"]
        self.assertEqual((v["path"], v["reason"], v["bytes"], v["base_bytes"]), ("a.txt", "new", 40001, None))
        self.commit("a.txt", blob(40000))
        self.assertEqual(self.check(), (0, {"ok": True, "mode": "committed", "base": out["base"], "limit": 40000,
                                            "violations": []}))

    def test_base_compare(self) -> None:
        cases = [
            ("grown", [50001], [("big.txt", "grown")]),
            ("unchanged", [], []),
            ("shrunk", [49000], []),
            ("net shrink over two commits", [50010, 49990], []),
        ]
        for name, steps, expected in cases:
            with self.subTest(name):
                self.tearDown()
                self.setUp()
                self.on_main("big.txt", blob(50000))
                for i, n in enumerate(steps):
                    self.commit("big.txt", blob(n), f"step {i}")
                self.assertEqual(self.reasons(), expected)
                if expected:
                    self.assertEqual(self.check()[1]["violations"][0]["base_bytes"], 50000)

    def test_moved_file_is_new(self) -> None:
        self.on_main("big.txt", blob(50000))
        git(self.repo, "mv", "big.txt", "moved.txt")
        git(self.repo, "commit", "-q", "-m", "move")
        self.assertEqual(self.reasons(), [("moved.txt", "new")])

    def test_untracked_and_ignored(self) -> None:
        self.write("loose.txt", blob(41000))
        self.assertEqual(self.reasons("--uncommitted"), [("loose.txt", "new")])
        self.assertEqual(self.reasons(), [])
        self.commit(".gitignore", b"loose.txt\n")
        self.assertEqual(self.reasons("--uncommitted"), [])

    def test_binary_and_gitlink(self) -> None:
        self.write("bin.dat", b"a" * 100 + b"\0" + blob(60000))
        sha = git(self.repo, "rev-parse", "HEAD").strip()
        git(self.repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},sub")
        git(self.repo, "add", "bin.dat")
        git(self.repo, "commit", "-q", "-m", "skips")
        for mode in ((), ("--uncommitted",), ("--all",)):
            with self.subTest(mode):
                self.assertEqual(self.reasons(*mode), [])

    def test_symlink_to_big_file(self) -> None:
        self.on_main("big.txt", blob(50000))
        os.symlink("big.txt", self.repo / "link.txt")
        git(self.repo, "add", "link.txt")
        git(self.repo, "commit", "-q", "-m", "link")
        for mode in ((), ("--uncommitted",)):
            with self.subTest(mode):
                self.assertEqual(self.reasons(*mode), [])
        self.assertEqual(self.reasons("--all"), [("big.txt", "over")])

    def test_manifest_keys(self) -> None:
        manifest = {"version": "1", "files": {"kit/big.py": "sha256:0"}}
        self.on_main(".dev-loop/MANIFEST.json", json.dumps(manifest).encode())
        self.commit("kit/big.py", blob(50000))
        self.assertEqual(self.reasons(), [])
        self.commit(".dev-loop/MANIFEST.json", b"{not json")
        self.assertEqual(self.reasons("--uncommitted"), [])
        self.assertEqual(self.reasons("--all"), [("kit/big.py", "over")])

    def test_exempt_list(self) -> None:
        self.commit("dev-loop.json", json.dumps({"file_size_exempt": ["docs/*"]}).encode())
        self.commit("docs/a/b.md", blob(41000))
        self.assertEqual(self.reasons(), [])
        for bad in ("docs", [" "], ["docs/"], [1]):
            with self.subTest(bad):
                self.write("dev-loop.json", json.dumps({"file_size_exempt": bad}).encode())
                code, out = self.check("--uncommitted")
                self.assertEqual((code, out["ok"], out["error"]),
                                 (1, False, "file_size_exempt in dev-loop.json must be a list of non-empty strings"))

    def test_manifest_is_read_at_the_base(self) -> None:
        manifest = {"version": "1", "files": {"kit/big.py": "sha256:0"}}
        self.on_main(".dev-loop/MANIFEST.json", json.dumps(manifest).encode())
        self.commit("kit/big.py", blob(50000))
        self.assertEqual(self.reasons(), [])
        self.commit("kit/big.py", blob(60000))
        self.assertEqual(self.reasons(), [])
        self.commit("kit/other.py", blob(50000))
        manifest["files"]["kit/other.py"] = "sha256:0"
        self.commit(".dev-loop/MANIFEST.json", json.dumps(manifest).encode())
        self.assertEqual(self.reasons(), [("kit/other.py", "new")])
        self.assertEqual(self.reasons("--uncommitted"), [("kit/other.py", "new")])

    def test_git_failure_is_an_error(self) -> None:
        self.commit("a.txt", blob(41000))
        obj = git(self.repo, "rev-parse", "HEAD:a.txt").strip()
        path = self.repo / ".git" / "objects" / obj[:2] / obj[2:]
        path.chmod(0o644)
        path.unlink()
        code, out = self.check()
        self.assertEqual((code, out["ok"]), (1, False))
        self.assertTrue(out["error"].startswith("git "), out)

    def test_name_that_is_not_utf8(self) -> None:
        try:
            self.write("x/placeholder", b"")
            (self.repo / os.fsdecode(b"big\xff.txt")).write_bytes(blob(41000))
        except OSError:
            self.skipTest("the file system refuses a name that is not UTF-8")
        self.assertEqual(len(self.check("--uncommitted")[1]["violations"]), 1)
        self.assertEqual(len(self.check("--all")[1]["violations"]), 1)

    def test_all_lists_unchanged_big_file(self) -> None:
        self.on_main("big.txt", blob(45000))
        self.assertEqual(self.reasons(), [])
        code, out = self.check("--all")
        self.assertEqual((code, out["mode"], out["base"]), (1, "all", None))
        [v] = out["violations"]
        self.assertEqual((v["path"], v["reason"], v["bytes"], v["base_bytes"]), ("big.txt", "over", 45000, None))

    def test_odd_paths(self) -> None:
        self.on_main("gone.txt", blob(50000))
        git(self.repo, "rm", "-q", "gone.txt")
        self.commit("a b/é.txt", blob(41000).replace(b"\n", b"\r\n")[:41000])
        self.assertEqual(self.reasons(), [("a b/é.txt", "new")])
        self.assertEqual(self.reasons("--uncommitted"), [("a b/é.txt", "new")])


class SizeCommandTests(SizeRepo):
    def test_no_base(self) -> None:
        git(self.repo, "branch", "-q", "-m", "main", "trunk")
        self.commit("a.txt", blob(41000))
        self.assertEqual(self.check(), (0, {"ok": True, "mode": "committed", "base": None, "limit": 40000,
                                            "violations": [], "skipped": "no-base"}))

    def test_no_base_still_checks_the_exempt_shape(self) -> None:
        git(self.repo, "branch", "-q", "-m", "main", "trunk")
        self.commit("dev-loop.json", json.dumps({"file_size_exempt": "docs"}).encode())
        code, out = self.check()
        self.assertEqual((code, out["ok"]), (1, False))
        self.assertIn("file_size_exempt", out["error"])

    def test_usage(self) -> None:
        result = subprocess.run([sys.executable, str(ROOT / "skill" / "scripts" / "devloop.py"),
                                 "size-check", "--uncommitted", "--all"], cwd=self.repo, capture_output=True)
        self.assertEqual(result.returncode, 2)
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        self.assertEqual(run_cli("size-check", cwd=outside)[0], 2)

    def test_violation_has_seam_and_hint(self) -> None:
        text = b"def a():\n" + blob(30000) + b"def b():\n" + blob(15000)
        self.commit("m.py", text)
        [v] = self.check()[1]["violations"]
        self.assertEqual(v["seam"], {"line": text[:text.index(b"def b")].count(b"\n") + 1, "name": "def b():"})
        self.assertEqual([u["name"] for u in v["units"]], ["def a():", "def b():"])
        self.assertEqual(v["hint"], f"split m.py at line {v['seam']['line']} (def b():), or by responsibility")
        self.commit("m.txt", blob(41000))
        hints = {v["path"]: v["hint"] for v in self.check()[1]["violations"]}
        self.assertEqual(hints["m.txt"], "split m.txt by responsibility")


class GateSizeTests(SizeRepo):
    def gate(self) -> tuple[int, dict]:
        self.write("scripts/gate.sh", b"#!/bin/sh\necho all good\n")
        (self.repo / "scripts" / "gate.sh").chmod(0o755)
        return run_cli("gate", "--summary", cwd=self.repo)

    def test_size_fails_a_passing_gate(self) -> None:
        self.write("big.txt", blob(41000))
        code, out = self.gate()
        self.assertEqual((code, out["exit"], out["size"]["ok"], out["size"]["mode"]), (1, 0, False, "uncommitted"))
        self.assertEqual([v["path"] for v in out["size"]["violations"]], ["big.txt"])
        (self.repo / "big.txt").unlink()
        code, out = self.gate()
        self.assertEqual((code, out["size"]["ok"]), (0, True), out)


class SealSizeTests(SizeRepo):
    SUITE = "tests/acceptance/test_x.py"

    def seal(self) -> tuple[int, dict]:
        return run_cli("seal", "--feature", "x", "--message", "acceptance", cwd=self.repo)

    def head(self) -> str:
        return git(self.repo, "rev-parse", "HEAD").strip()

    def test_seal_refuses_a_big_file(self) -> None:
        self.write(self.SUITE, blob(41000))
        git(self.repo, "add", self.SUITE)
        before = self.head()
        code, out = self.seal()
        self.assertEqual((code, out["error"], [v["path"] for v in out["size"]], self.head()),
                         (1, "file over the size limit", [self.SUITE], before))
        self.write(self.SUITE, blob(39000))
        git(self.repo, "add", self.SUITE)
        code, out = self.seal()
        self.assertEqual((code, out["commit"]), (0, self.head()), out)

    def test_rebase_continue_takes_a_big_file(self) -> None:
        self.write(self.SUITE, b"a\n")
        git(self.repo, "add", self.SUITE)
        self.assertEqual(self.seal()[0], 0)
        git(self.repo, "checkout", "-q", "main")
        self.commit(self.SUITE, b"b\n", "on main")
        git(self.repo, "checkout", "-q", "feat/x")
        self.assertNotEqual(subprocess.run(["git", "rebase", "-q", "main"], cwd=self.repo,
                                           capture_output=True).returncode, 0)
        self.write(self.SUITE, blob(41000))
        git(self.repo, "add", self.SUITE)
        code, out = run_cli("seal", "--rebase-continue", cwd=self.repo)
        self.assertEqual((code, out["rebase"]), (0, "continued"), out)

    def test_from_a_subfolder(self) -> None:
        self.on_main(self.SUITE, blob(50000))
        self.write(self.SUITE, blob(49000))
        git(self.repo, "add", self.SUITE)
        code, out = run_cli("seal", "--feature", "x", "--message", "shrink", cwd=self.repo / "tests" / "acceptance")
        self.assertEqual((code, out.get("commit")), (0, self.head()), out)

    def test_glob_name_is_literal(self) -> None:
        os.makedirs(self.repo / "tests" / "acceptance")
        os.symlink("elsewhere", self.repo / "tests" / "acceptance" / "0.py")
        self.write("tests/acceptance/?.py", blob(41000))
        git(self.repo, "add", "tests/acceptance")
        code, out = self.seal()
        self.assertEqual((code, [v["path"] for v in out["size"]]), (1, ["tests/acceptance/?.py"]), out)

    def test_git_failure_is_an_error(self) -> None:
        self.write(self.SUITE, blob(41000))
        git(self.repo, "add", self.SUITE)
        obj = git(self.repo, "rev-parse", f":{self.SUITE}").strip()
        path = self.repo / ".git" / "objects" / obj[:2] / obj[2:]
        path.chmod(0o644)
        path.unlink()
        code, out = self.seal()
        self.assertEqual(code, 1)
        self.assertTrue(out["error"].startswith("git "), out)


class SeamTests(unittest.TestCase):
    def test_seam(self) -> None:
        unit = blob(960)
        cases = [
            ("python: three units, by bytes", "a.py",
             b"import x\n" + b"def a():\n" + unit + b"class B:\n" + unit * 3 + b"async def c():\n" + unit * 2,
             (52, "async def c():"), [("class B:", 15, 51), ("async def c():", 52, 76), ("def a():", 2, 14)]),
            ("python: indented def is not a start", "a.py", b"def a():\n    def b():\n" + unit,
             None, [("def a():", 1, 14)]),
            ("js starts", "a.mjs", b"export const a = 1\n" + unit + b"describe('x', () => {\n" + unit,
             (14, "describe('x', () => {"), [("describe('x', () => {", 14, 26), ("export const a = 1", 1, 13)]),
            ("markdown heading", "a.md", b"# T\n" + unit + b"## H\n" + unit + b"### not\n",
             (14, "## H"), [("## H", 14, 27)]),
            ("txt: none", "a.txt", unit * 3, None, []),
            ("tie takes the earlier", "a.py", b"def a():\n" + b"def b():\n" + b"def c():\n", (2, "def b():"),
             [("def a():", 1, 1), ("def b():", 2, 2), ("def c():", 3, 3)]),
        ]
        for name, path, data, seam, units in cases:
            with self.subTest(name):
                got_seam, got_units = size.seam(data, path)
                self.assertEqual(got_seam, None if seam is None else {"line": seam[0], "name": seam[1]})
                self.assertEqual([(u["name"], u["start"], u["end"]) for u in got_units], units)

    def test_name_is_cut(self) -> None:
        line = b"def " + b"a" * 100 + b"():\n"
        _, [u] = size.seam(line, "a.py")
        self.assertEqual(len(u["name"]), 80)

    def test_crlf_offsets(self) -> None:
        data = (b"def a():\r\n" + b"x\r\n" * 10 + b"def b():\r\n" + b"x\r\n" * 10)
        self.assertEqual(size.seam(data, "a.py")[0], {"line": 12, "name": "def b():"})


class SizeTextTests(unittest.TestCase):
    def text(self, path: str) -> str:
        return (ROOT / path).read_text()

    def section(self, heading: str) -> str:
        return self.text("skill/SKILL.md").split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]

    def lines(self, path: str, *words: str) -> list[str]:
        return [line for line in self.text(path).splitlines() if all(w in line for w in words)]

    def test_skill(self) -> None:
        self.assertNotIn("`gate --summary` (`exit` 0)", self.text("skill/SKILL.md"))
        report = budget_report(ROOT / "skill/SKILL.md", size.LIMIT)
        if report:
            self.fail(report)
        self.assertIn("size-check --all", self.section("4 Plan"))
        self.assertIn("`seam`", self.section("5 Build"))
        self.assertTrue([line for line in self.section("6 Feature gate").splitlines()
                         if all(w in line for w in ("`size`", "`seal`", "red"))])

    def test_references_and_agents(self) -> None:
        self.assertTrue(self.lines("skill/references/building.md", "40,000 bytes", "`size`"))
        self.assertTrue(self.lines("skill/references/claude.md", "`Read`", "`Edit`", "`sed -n`"))
        for ref in ("skill/references/claude.md", "skill/references/codex.md"):
            with self.subTest(ref):
                self.assertTrue(self.lines(ref, "`size`", "`seal`", "`weakened`"))
                self.assertTrue(self.lines(ref, "Fix wave", "process exits 0"))
        for agent in ("claude/agents/spec-tester.md", "codex/agents/spec-tester.toml"):
            with self.subTest(agent):
                self.assertTrue(self.lines(agent, "40,000 bytes", "`seal`"))

    def test_repo_docs(self) -> None:
        agents = self.text("AGENTS.md")
        for word in ("`size-check", "`skill/scripts/size.py`"):
            self.assertIn(word, agents)
        self.assertIn("`test_size.py`", self.text("tests/unit/python/README.md"))
        self.assertIn("`file_size_exempt`", self.text("reference/cli/size-check.md"))
        self.assertIn("`file_size_exempt`", self.text("reference/kit.md"))
        self.assertIn("`size-check", self.text("README.md"))


if __name__ == "__main__":
    unittest.main()
