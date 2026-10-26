#!/usr/bin/env python3
"""Held-out oracle tests for the file-size ratchet (spec: File-Size Ratchet).

One test per `## Acceptance flows` item (F<n>); each docstring names the `## Acceptance oracle` lines (O<n>)
it checks. The tests use only the declared seams: the stdout JSON and exit code of `devloop.py size-check`,
`gate --summary`, `seal`, `seal --rebase-continue` and `merge-check` (the kit source,
`skill/scripts/devloop.py`), the output of `skill/scripts/check_setup.py`, and the kit text files. Each
repo is a temporary git repo with a bare `origin`, `main` pushed, and a feature worktree on `feat/widget`.
Every name in the temp repos is invented.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = REPO_ROOT / "skill"
DEVLOOP = SKILL / "scripts" / "devloop.py"
CHECKER = SKILL / "scripts" / "check_setup.py"
INSTALLER = REPO_ROOT / "bin" / "install.mjs"
REFS = SKILL / "references"
AGENTS = (REPO_ROOT / "claude" / "agents" / "spec-tester.md", REPO_ROOT / "codex" / "agents" / "spec-tester.toml")

SLUG = "widget"
BRANCH = f"feat/{SLUG}"
LIMIT = 40000
BASE_CONFIG = {"version": "2.1.1", "harnesses": ["claude", "codex"]}


def put(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)


def filler(size: int, char: str = "x") -> str:
    """Exactly `size` bytes of text: lines of 100 bytes, then one shorter line."""
    full, rest = divmod(size, 100)
    text = (char * 99 + "\n") * full
    if rest:
        text += char * (rest - 1) + "\n"
    return text


def py_unit(header: str, body_lines: int) -> str:
    """A Python unit: its start line at column 0, then `body_lines` indented lines of 100 bytes each."""
    line = "    x = 0  # " + "p" * 86 + "\n"
    assert len(line) == 100
    return header + "\n" + line * body_lines


def md_section(heading: str, body_lines: int) -> str:
    return heading + "\n" + ("m" * 99 + "\n") * body_lines


def config_text(**extra: object) -> str:
    return json.dumps({**BASE_CONFIG, **extra}, indent=2) + "\n"


class Project:
    """A bare origin, a main checkout on `main`, a notes folder with the widget spec, a feature worktree."""

    def __init__(self, root: Path, base_files: dict[str, str | bytes], main_config: str | None = None) -> None:
        self.root = root
        self.home = root / "home"
        self.home.mkdir()
        self.notes = root / "notes"
        put(self.notes / "specs" / "20261026 Widget.md",
            "---\ntype: spec\nfeature: widget\ntrack: M\nreview_status: approved\nacceptance:\nshipped:\n---\n\n"
            "# Widget\n")
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("GIT_", "DEV_LOOP", "DEVLOOP", "CLAUDE_", "CODEX_"))}
        self.env.update({"HOME": str(self.home), "DEV_LOOP_NOTES": str(self.notes), "GIT_CONFIG_NOSYSTEM": "1",
                         "GIT_EDITOR": "true", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor",
                         "GIT_CONFIG_VALUE_0": "false", "GIT_CEILING_DIRECTORIES": str(root)})
        self.origin = root / "origin.git"
        self.git(root, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.main = root / "acme-widgets"
        self.main.mkdir()
        self.git(self.main, "init", "-q", "-b", "main")
        for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
            self.git(self.main, "config", key, value)
        self.git(self.main, "remote", "add", "origin", str(self.origin))
        self.base = self.commit(self.main, {
            "scripts/gate.sh": "#!/bin/sh\necho all unit tests pass\nexit 0\n",
            "dev-loop.json": main_config if main_config is not None else config_text(),
            "README.md": "# Acme widgets\n",
            "GLOSSARY.md": "# Glossary\n\n**Widget**:\nA thing with doors.\n",
            **base_files,
        }, "Base")
        self.git(self.main, "push", "-q", "-u", "origin", "main")
        self.wt = (root / "worktrees" / "acme-widgets-widget").resolve()
        self.git(self.main, "worktree", "add", "-q", "-b", BRANCH, str(self.wt), "refs/remotes/origin/main")

    def git(self, cwd: Path, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=cwd, env=self.env, capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
        return result.stdout.strip()

    def commit(self, cwd: Path, files: dict[str, str | bytes], message: str, removed: tuple[str, ...] = ()) -> str:
        for path, content in files.items():
            put(cwd / path, content)
        for path in removed:
            self.git(cwd, "rm", "-q", "--", path)
        if "scripts/gate.sh" in files:
            (cwd / "scripts" / "gate.sh").chmod(0o755)
        if files:
            self.git(cwd, "add", "-A", "--", *files)
        self.git(cwd, "commit", "-q", "-m", message)
        return self.git(cwd, "rev-parse", "HEAD")

    def head(self) -> str:
        return self.git(self.wt, "rev-parse", "HEAD")

    def run(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(DEVLOOP), *args], cwd=cwd or self.wt, env=self.env,
                              capture_output=True, text=True, timeout=180)

    def cli(self, *args: str) -> tuple[int, dict]:
        result = self.run(*args)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            raise AssertionError(f"devloop {' '.join(args)}: stdout is not one JSON object "
                                 f"(exit {result.returncode}): {result.stdout[-400:]!r} {result.stderr[-400:]!r}")
        if not isinstance(payload, dict):
            raise AssertionError(f"devloop {' '.join(args)}: stdout is not a JSON object: {payload!r}")
        return result.returncode, payload


def by_path(payload: dict) -> dict[str, dict]:
    return {v.get("path"): v for v in payload.get("violations") or []}


def reasons(payload: dict, name: str) -> list[dict]:
    return [item for item in payload.get("reasons") or [] if isinstance(item, dict) and item.get("reason") == name]


class RatchetCase(unittest.TestCase):
    def project(self, base_files: dict[str, str | bytes] | None = None, main_config: str | None = None) -> Project:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Project(Path(tmp.name).resolve(), base_files or {}, main_config)

    def size_check(self, project: Project, *args: str) -> tuple[int, dict]:
        code, out = project.cli("size-check", *args)
        self.assertEqual(out.get("limit"), LIMIT, out)
        self.assertIsInstance(out.get("violations"), list, out)
        self.assertIs(out.get("ok"), not out["violations"], out)
        self.assertEqual(code, 0 if out["ok"] else 1, out)
        return code, out

    def assert_clean(self, project: Project, *args: str) -> dict:
        code, out = self.size_check(project, *args)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["violations"], [], out)
        return out

    def assert_split_hints(self, violation: dict) -> None:
        seam = violation.get("seam")
        self.assertIsInstance(seam, dict, violation)
        self.assertIsInstance(seam.get("line"), int, violation)
        self.assertIsInstance(seam.get("name"), str, violation)
        self.assertTrue(violation.get("units"), violation)
        for unit in violation["units"]:
            self.assertEqual(set(unit) & {"name", "start", "end"}, {"name", "start", "end"}, unit)
        self.assertIn(violation["path"], violation.get("hint", ""), violation)
        self.assertIn(str(seam["line"]), violation.get("hint", ""), violation)


class BranchRatchetTests(RatchetCase):
    def test_f1_branch_ratchet(self) -> None:
        """F1, O1 to O5, O7, O8, O11, O12, O14: the base compare, the path filters, the seam and units."""
        engine = py_unit("def start():", 200) + py_unit("def run():", 150) + py_unit("def stop():", 150)
        project = self.project({"src/engine.py": engine})
        grown = engine + "    x = 1\n"
        planner = (py_unit("def plan():", 150) + py_unit("def route():", 150) + py_unit("def score():", 150))
        project.commit(project.wt, {"src/engine.py": grown, "src/planner.py": planner,
                                    "src/small.py": "def tiny():\n    return 1\n"}, "Grow the engine, add a planner")
        code, out = self.size_check(project)
        self.assertEqual(code, 1, out)
        self.assertEqual(out.get("mode"), "committed", out)
        self.assertEqual(out.get("base"), project.base, out)
        found = by_path(out)
        self.assertEqual(set(found), {"src/engine.py", "src/planner.py"}, out)
        self.assertEqual(found["src/engine.py"]["reason"], "grown", out)
        self.assertEqual(found["src/engine.py"]["bytes"], len(grown), out)
        self.assertEqual(found["src/engine.py"]["base_bytes"], len(engine), out)
        self.assertEqual(found["src/planner.py"]["reason"], "new", out)
        for violation in found.values():
            self.assert_split_hints(violation)
        # The split commit: every file at or under the limit, the engine smaller than at the base.
        project.commit(project.wt, {
            "src/engine.py": py_unit("def start():", 200),
            "src/engine_run.py": py_unit("def run():", 150) + py_unit("def stop():", 150) + "    x = 1\n",
            "src/planner.py": py_unit("def plan():", 150) + py_unit("def route():", 150),
            "src/planner_score.py": py_unit("def score():", 150),
        }, "Split the engine and the planner")
        self.assert_clean(project)

        with self.subTest(oracle="1, 2, 7, 8"):
            tool = "def tool():\n    return 1\n"
            manifest = {"version": "1.0.0", "files": {"kit/tool.py": "sha256:" + hashlib.sha256(tool.encode()).hexdigest()}}
            project = self.project({"kit/tool.py": tool, ".dev-loop/MANIFEST.json": json.dumps(manifest) + "\n"})
            big_tool = tool + filler(45000, "#")
            manifest["files"]["kit/tool.py"] = "sha256:" + hashlib.sha256(big_tool.encode()).hexdigest()
            binary = b"head" + b"\0" + b"b" * (60000 - 5)
            project.commit(project.wt, {"data/over.txt": filler(40001), "data/at.txt": filler(40000),
                                        "assets/blob.dat": binary, "kit/tool.py": big_tool,
                                        ".dev-loop/MANIFEST.json": json.dumps(manifest) + "\n"}, "Add data")
            code, out = self.size_check(project)
            self.assertEqual(code, 1, out)
            self.assertEqual(len(out["violations"]), 1, out)
            violation = out["violations"][0]
            self.assertEqual(violation["path"], "data/over.txt", out)
            self.assertEqual(violation["reason"], "new", out)
            self.assertEqual(violation["bytes"], 40001, out)
            self.assertIsNone(violation["base_bytes"], out)

        with self.subTest(oracle=3):
            project = self.project({"data/big.txt": filler(50000), "data/other.txt": "one\n"})
            project.commit(project.wt, {"data/other.txt": "two\n"}, "Touch another file")
            self.assert_clean(project)
            project.commit(project.wt, {"data/big.txt": filler(50001)}, "Grow by one byte")
            code, out = self.size_check(project)
            self.assertEqual(code, 1, out)
            violation = by_path(out)["data/big.txt"]
            self.assertEqual((violation["reason"], violation["bytes"], violation["base_bytes"]),
                             ("grown", 50001, 50000), out)
            project.commit(project.wt, {"data/big.txt": filler(49000)}, "Shrink below the base")
            self.assert_clean(project)
            project.commit(project.wt, {"data/big.txt": filler(50000)}, "Back to the base content")
            self.assert_clean(project)

        with self.subTest(oracle=4):
            project = self.project({"data/big.txt": filler(50000)})
            project.commit(project.wt, {"data/big.txt": filler(50010)}, "Grow by 10 bytes")
            project.commit(project.wt, {"data/big.txt": filler(49990)}, "Lose 20 bytes")
            self.assert_clean(project)

        with self.subTest(oracle=5):
            project = self.project({"data/big.txt": filler(50000)})
            (project.wt / "moved").mkdir()
            project.git(project.wt, "mv", "data/big.txt", "moved/big.txt")
            project.commit(project.wt, {}, "Move the big file")
            code, out = self.size_check(project)
            self.assertEqual(code, 1, out)
            found = by_path(out)
            self.assertEqual(set(found), {"moved/big.txt"}, out)
            self.assertEqual(found["moved/big.txt"]["reason"], "new", out)

        with self.subTest(oracle="11, 12"):
            project = self.project()
            # alpha: 20,013 bytes (lines 1 to 201), beta: 5,012 (202 to 252), gamma: 18,013 (253 to 433).
            # Middle 21,519: beta starts at byte 20,013, gamma at 25,025, so the seam is beta, line 202.
            python = py_unit("def alpha():", 200) + py_unit("def beta():", 50) + py_unit("def gamma():", 180)
            markdown = ("# Notes\n" + md_section("## Part one", 150) + md_section("## Part two", 150)
                        + md_section("## Part three", 150))
            project.commit(project.wt, {"src/units.py": python, "docs/notes.md": markdown,
                                        "docs/plain.txt": filler(41000)}, "Add big files")
            code, out = self.size_check(project)
            self.assertEqual(code, 1, out)
            found = by_path(out)
            py = found["src/units.py"]
            self.assertEqual(py["seam"], {"line": 202, "name": "def beta():"}, py)
            self.assertEqual([(u["name"], u["start"], u["end"]) for u in py["units"]],
                             [("def alpha():", 1, 201), ("def gamma():", 253, 433), ("def beta():", 202, 252)], py)
            md = found["docs/notes.md"]
            self.assertIsInstance(md.get("seam"), dict, md)
            self.assertTrue(markdown.splitlines()[md["seam"]["line"] - 1].startswith("## "), md)
            txt = found["docs/plain.txt"]
            self.assertIsNone(txt["seam"], txt)
            self.assertEqual(txt["units"], [], txt)

        with self.subTest(oracle=14):
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            root = Path(tmp.name).resolve()
            lone = Project.__new__(Project)
            lone.env = {key: value for key, value in os.environ.items()
                        if not key.startswith(("GIT_", "DEV_LOOP", "DEVLOOP", "CLAUDE_", "CODEX_"))}
            lone.env.update({"HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CEILING_DIRECTORIES": str(root)})
            lone.wt = root / "trunk-only"
            lone.wt.mkdir()
            lone.git(lone.wt, "init", "-q", "-b", "trunk")
            for key, value in (("user.name", "Robin Quill"), ("user.email", "robin@example.invalid"),
                               ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
                lone.git(lone.wt, "config", key, value)
            lone.commit(lone.wt, {"dev-loop.json": config_text(), "data/big.txt": filler(41000)}, "Base")
            code, out = lone.cli("size-check")
            self.assertEqual(code, 0, out)
            self.assertIs(out.get("ok"), True, out)
            self.assertEqual(out.get("skipped"), "no-base", out)
            self.assertFalse(out.get("violations"), out)


class GateTests(RatchetCase):
    def test_f2_gate_fails_on_a_big_untracked_file(self) -> None:
        """F2, O6, O16: `size` in `gate --summary` is the `size-check --uncommitted` payload."""
        project = self.project()
        put(project.wt / "logs" / "dump.txt", filler(41000))
        code, out = self.size_check(project, "--uncommitted")
        self.assertEqual(code, 1, out)
        self.assertEqual(out.get("mode"), "uncommitted", out)
        self.assertEqual(by_path(out)["logs/dump.txt"]["reason"], "new", out)
        self.assert_clean(project)

        code, gate = project.cli("gate", "--summary")
        self.assertEqual(code, 1, gate)
        self.assertEqual(gate.get("exit"), 0, gate)
        self.assertIs(gate["size"]["ok"], False, gate)
        self.assertIn("logs/dump.txt", by_path(gate["size"]), gate)

        project.commit(project.wt, {".gitignore": "logs/\n"}, "Ignore the logs")
        self.assert_clean(project, "--uncommitted")
        code, gate = project.cli("gate", "--summary")
        self.assertEqual(code, 0, gate)
        self.assertEqual(gate.get("exit"), 0, gate)
        self.assertIs(gate["size"]["ok"], True, gate)


class SealTests(RatchetCase):
    def test_f3_seal_refuses_a_big_acceptance_file(self) -> None:
        """F3, O17, O18, O19."""
        shared = "tests/acceptance/test_shared.py"
        tail = "# " + "s" * 97 + "\n"
        project = self.project({shared: "VALUE = 1\n" + tail * 410})
        before = project.head()
        put(project.wt / "tests" / "acceptance" / "test_x.py", filler(41000, "#"))
        project.git(project.wt, "add", "--", "tests/acceptance")
        code, out = project.cli("seal", "--feature", SLUG, "--message", "Add widget tests")
        self.assertEqual(code, 1, out)
        self.assertEqual(out.get("error"), "file over the size limit", out)
        self.assertIn("tests/acceptance/test_x.py", [v.get("path") for v in out.get("size") or []], out)
        self.assertEqual(project.head(), before)

        put(project.wt / "tests" / "acceptance" / "test_x.py", filler(39000, "#"))
        project.git(project.wt, "add", "--", "tests/acceptance")
        code, out = project.cli("seal", "--feature", SLUG, "--message", "Add widget tests")
        self.assertEqual(code, 0, out)
        self.assertNotEqual(project.head(), before)
        self.assertEqual(project.git(project.wt, "status", "--porcelain", "--", "tests/acceptance"), "")

        # A sealed change that shrinks the big shared file, then a conflicting change on main.
        put(project.wt / shared, "VALUE = 2\n" + tail * 409)
        project.git(project.wt, "add", "--", "tests/acceptance")
        code, out = project.cli("seal", "--feature", SLUG, "--message", "Shrink the shared tests")
        self.assertEqual(code, 0, out)
        project.commit(project.main, {shared: "VALUE = 3\n" + tail * 410}, "Change the shared tests")
        rebase = subprocess.run(["git", "rebase", "main"], cwd=project.wt, env=project.env, capture_output=True,
                                text=True)
        self.assertNotEqual(rebase.returncode, 0, "the rebase should stop on the conflict")
        resolved = "VALUE = 3\n" + tail * 409 + "#" * 89 + "\n"
        self.assertEqual(len(resolved), 41000)
        put(project.wt / shared, resolved)
        project.git(project.wt, "add", "--", shared)
        code, out = project.cli("seal", "--rebase-continue")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.get("rebase"), "continued", out)
        self.assertEqual((project.wt / shared).read_text(), resolved)


class ExemptListTests(RatchetCase):
    def test_f4_exempt_list(self) -> None:
        """F4, O9, O10, O20, O24."""
        project = self.project()
        exempt = config_text(file_size_exempt=["docs/*"])
        project.commit(project.wt, {"dev-loop.json": exempt, "docs/a/b.md": filler(41000)}, "Exempt the docs")
        self.assert_clean(project)

        put(project.wt / "dev-loop.json", config_text(file_size_exempt="docs"))
        code, out = project.cli("size-check")
        self.assertEqual(code, 1, out)
        self.assertTrue(out.get("error"), out)
        put(project.wt / "dev-loop.json", exempt)

        code, out = project.cli("merge-check")
        self.assertEqual(code, 0, out)
        self.assertTrue(reasons(out, "config-changed"), out)
        self.assertEqual(out.get("merge"), "human", out)

        project.commit(project.wt, {"dev-loop.json": config_text(file_size_exempt=[])}, "Empty the exempt list")
        code, out = project.cli("merge-check")
        self.assertEqual(code, 0, out)
        self.assertFalse(reasons(out, "config-changed"), out)

        project = self.project(main_config=config_text(file_size_exempt="x"))
        project.commit(project.wt, {"src/app.py": "def colour():\n    return 'blue'\n"}, "Build the feature")
        code, out = project.cli("merge-check")
        self.assertEqual(code, 0, out)
        self.assertFalse(reasons(out, "config-unreadable"), out)

        # O10: check_setup.py on an installed project names the bad key, not a good one.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        home, notes, fresh = root / "home", root / "notes", root / "fresh-project"
        for folder in (home, notes, fresh):
            folder.mkdir()
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("DEV_LOOP", "DEVLOOP", "CLAUDE_", "CODEX_"))}
        env.update({"HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1"})
        subprocess.run(["git", "init", "-q"], cwd=fresh, env=env, check=True, capture_output=True)
        init = subprocess.run(["node", str(INSTALLER), "init", "--yes", "--harness=both", f"--notes={notes}"],
                              cwd=fresh, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
        config = json.loads((fresh / "dev-loop.json").read_text())
        for value, named in (("docs", True), (["docs/*"], False)):
            with self.subTest(oracle=10, file_size_exempt=value):
                put(fresh / "dev-loop.json", json.dumps({**config, "file_size_exempt": value}, indent=2) + "\n")
                for harness in ("claude", "codex"):
                    result = subprocess.run([sys.executable, str(CHECKER), "--harness", harness, "--home", str(home),
                                             "--repo", str(fresh)], env=env, capture_output=True, text=True,
                                            timeout=120)
                    output = result.stdout + result.stderr
                    lines = [line for line in output.splitlines() if "file_size_exempt" in line]
                    if named:
                        self.assertNotEqual(result.returncode, 0, output)
                        self.assertTrue(lines, output)
                    else:
                        self.assertEqual(lines, [], output)


class FullListingTests(RatchetCase):
    def test_f5_full_listing(self) -> None:
        """F5, O13, O15."""
        project = self.project({"data/big.txt": filler(45000)})
        project.commit(project.wt, {"src/app.py": "def colour():\n    return 'blue'\n"}, "Build the feature")
        code, out = self.size_check(project, "--all")
        self.assertEqual(code, 1, out)
        self.assertEqual(out.get("mode"), "all", out)
        self.assertIsNone(out.get("base"), out)
        violation = by_path(out)["data/big.txt"]
        self.assertEqual((violation["reason"], violation["bytes"]), ("over", 45000), out)
        self.assert_clean(project)

        both = project.run("size-check", "--uncommitted", "--all")
        self.assertEqual(both.returncode, 2, both.stdout + both.stderr)
        plain = project.root / "plain-folder"
        plain.mkdir()
        outside = project.run("size-check", cwd=plain)
        self.assertEqual(outside.returncode, 2, outside.stdout + outside.stderr)


class KitTextTests(unittest.TestCase):
    def section(self, text: str, number: int) -> str:
        match = re.search(rf"^## {number} .*?(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match, f"no Stage {number} section")
        return match.group(0)

    def test_f6_kit_text_contract(self) -> None:
        """F6, O21, O22, O23."""
        claude = (REFS / "claude.md").read_text()
        codex = (REFS / "codex.md").read_text()
        with self.subTest(oracle=21):
            self.assertTrue([line for line in claude.splitlines()
                             if "Read" in line and "Edit" in line and "sed -n" in line], "claude.md")
            building = (REFS / "building.md").read_text()
            self.assertIn("40,000 bytes", building)
            self.assertIn("`size`", building)
            for agent in AGENTS:
                text = agent.read_text()
                self.assertIn("40,000 bytes", text, agent.name)
                self.assertIn("seal", text, agent.name)
        with self.subTest(oracle=22):
            skill = (SKILL / "SKILL.md").read_text()
            self.assertNotIn("`gate --summary` (`exit` 0)", skill)
            stage6 = self.section(skill, 6)
            self.assertTrue([line for line in stage6.splitlines()
                             if "`size`" in line and "`seal`" in line and "red" in line], "Stage 6 triage")
            self.assertIn("size-check --all", self.section(skill, 4))
        with self.subTest(oracle=23):
            for name, text in (("claude.md", claude), ("codex.md", codex)):
                lines = text.splitlines()
                self.assertTrue([line for line in lines if "riage" in line and "`size`" in line
                                 and "`seal`" in line and "`weakened`" in line], name)
                self.assertTrue([line for line in lines if "Fix wave" in line and "process exits 0" in line], name)


if __name__ == "__main__":
    unittest.main()
