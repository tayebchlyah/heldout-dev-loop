#!/usr/bin/env python3
"""Held-out acceptance tests for Stage 6 Mechanics (spec: Stage 6 Mechanics).

One test per `## Acceptance flows` item (F1 to F5); each docstring names the `## Acceptance oracle` lines (O<n>)
it proves. Seams: `devloop.py review-merge` and `devloop.py acceptance` (the kit source,
`skill/scripts/devloop.py`), run as processes in temporary git repos whose `dev-loop.json` maps the commands to a
shell script that copies a fixture JUnit file into `$DEVLOOP_REPORT_DIR`, with a fake `docker` first in `PATH`;
`scripts/acceptance.sh` of this repo on a fixture folder; the kit text and the repo config files.

Fixture choices the spec leaves open:
- The fake `docker` answers by the words of its arguments (`compose ... config`, `compose ... ps ... <service>`,
  `inspect <id> ...`) and ignores any other flag. `ps -q` gives 12-character ids; `inspect` gives the full id.
- A failing `docker` call exits 1 with a message on stderr, for every call.
- Every name in the temp repos is invented. HOME is a temporary folder.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEVLOOP = REPO_ROOT / "skill" / "scripts" / "devloop.py"
SKILL = REPO_ROOT / "skill" / "SKILL.md"
REFS = REPO_ROOT / "skill" / "references"
ACCEPTANCE_SH = REPO_ROOT / "scripts" / "acceptance.sh"

COLUMNS = ("file", "line", "finding", "class", "verdict")
RUN_KEYS = {"at", "head", "mode", "exit", "failing", "ids", "stale", "counted", "log"}
UTC_SECONDS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

RUNNER = """#!/bin/sh
F='{fix}'
echo ran >> "$F/ran.log"
mkdir -p "$DEVLOOP_REPORT_DIR"
if [ -f "$F/report.xml" ]; then cp "$F/report.xml" "$DEVLOOP_REPORT_DIR/fixture.xml"; fi
exit "$(cat "$F/exit")"
"""

FAKE_DOCKER = r'''import json
import pathlib
import sys

fix = pathlib.Path(sys.argv[1])
args = sys.argv[2:]
with open(fix / "docker-calls.log", "a") as log:
    log.write(" ".join(args) + "\n")
if (fix / "docker-fail").exists():
    sys.stderr.write("Cannot connect to the Docker daemon\n")
    sys.exit(1)
state = json.loads((fix / "docker-state.json").read_text())
words = [a for a in args if not a.startswith("-")]
if args and args[0] == "compose":
    if "config" in words:
        print(json.dumps({"name": "acme", "services": state["services"]}))
    elif "ps" in words:
        service = words[-1]
        for cid in state["containers"].get(service, []):
            print(cid[:12])
    else:
        sys.exit(1)
elif args and args[0] == "inspect":
    out = []
    for word in words[1:]:
        for cid, created in state["created"].items():
            if cid.startswith(word):
                out.append({"Id": cid, "Created": created, "Name": "/acme-" + cid[:4]})
    print(json.dumps(out, indent=4))
else:
    sys.exit(1)
'''


def junit(failing: list[str], passing: int = 1, errors: bool = False) -> str:
    """A JUnit file: each failing id (`<classname>::<name>`) a `testcase` with a `failure` child (the last one an
    `error` child when `errors`), plus `passing` passing cases."""
    cases = []
    for index, test_id in enumerate(failing):
        classname, _, name = test_id.partition("::")
        child = "<error message=\"boom\"/>" if errors and index == len(failing) - 1 else "<failure message=\"no\"/>"
        cases.append(f'<testcase classname="{classname}" name="{name}">{child}</testcase>')
    for index in range(passing):
        cases.append(f'<testcase classname="ok.Passing" name="test_{index}"/>')
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<testsuites><testsuite name="fixture" tests="{len(cases)}">'
            + "".join(cases) + "</testsuite></testsuites>\n")


def table(rows: list[tuple], header: tuple = COLUMNS, before: str = "", after: str = "") -> str:
    lines = [before, "", "| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines + ["", after, ""])


class Sandbox(unittest.TestCase):
    """A temporary git repo `acme-widgets` with a code file, a fixture folder outside it, a fake `docker` first
    in `PATH` and a sandboxed HOME."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(os.path.realpath(self.tmp.name))
        self.home = self.root / "home"
        self.home.mkdir()
        self.fix = self.root / "fixture"
        self.fix.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.repo = self.root / "acme-widgets"
        self.repo.mkdir()
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("GIT_", "DEVLOOP_", "COMPOSE_", "DOCKER_", "CLAUDE_", "CODEX_"))}
        env.update(HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1", GIT_CONFIG_NOSYSTEM="1",
                   GIT_CONFIG_GLOBAL=str(self.home / ".gitconfig"))
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        self.env = env
        (self.fix / "fake_docker.py").write_text(FAKE_DOCKER)
        docker = self.bin / "docker"
        docker.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{self.fix / 'fake_docker.py'}' '{self.fix}' \"$@\"\n")
        docker.chmod(0o755)
        runner = self.fix / "run.sh"
        runner.write_text(RUNNER.format(fix=self.fix))
        runner.chmod(0o755)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "dev@example.invalid")
        self.git("config", "user.name", "Acme Dev")
        self.git("config", "commit.gpgsign", "false")
        self.config({"acceptance": f"sh '{runner}'", "acceptance_failed": f"sh '{runner}' --failed"})
        (self.repo / "app.py").write_text("def widget():\n    return 1\n")
        self.git_dir = Path(os.path.realpath(self.repo / ".git"))

    def git(self, *args: str, when: str | None = None) -> str:
        env = dict(self.env)
        if when:
            env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        result = subprocess.run(["git", *args], cwd=self.repo, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self, message: str, when: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message, when=when)
        return self.git("rev-parse", "HEAD")

    def config(self, commands: dict) -> None:
        data = {"version": "2.1.1", "harnesses": ["claude", "codex"]}
        if commands:
            data["commands"] = commands
        (self.repo / "dev-loop.json").write_text(json.dumps(data, indent=2) + "\n")

    def cli(self, *args: str) -> tuple[int, dict | None, str, str]:
        result = subprocess.run([sys.executable, str(DEVLOOP), *args], cwd=self.repo, env=self.env,
                                capture_output=True, text=True, timeout=180)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            payload = None
        tail = result.stdout[-800:] + result.stderr[-800:]
        self.assertIsInstance(payload, dict, f"devloop {' '.join(args)}: stdout is not one JSON object: {tail}")
        return result.returncode, payload, result.stderr, tail

    def same(self, a, b) -> bool:
        return a is not None and b is not None and os.path.realpath(a) == os.path.realpath(b)

    # acceptance helpers

    def fixture(self, failing: list[str] | None, exit_code: int, errors: bool = False) -> None:
        report = self.fix / "report.xml"
        if failing is None:
            report.unlink(missing_ok=True)
        else:
            report.write_text(junit(failing, errors=errors))
        (self.fix / "exit").write_text(f"{exit_code}\n")

    def ran(self) -> int:
        log = self.fix / "ran.log"
        return len(log.read_text().splitlines()) if log.exists() else 0

    def accept(self, failing: list[str] | None, exit_code: int, failed: bool = False,
               errors: bool = False) -> tuple[int, dict, str]:
        self.fixture(failing, exit_code, errors=errors)
        before = self.ran()
        code, payload, stderr, tail = self.cli("acceptance", *(["--failed"] if failed else []))
        self.assertNotIn("error", payload, tail)
        self.assertEqual(self.ran(), before + 1, f"the acceptance command did not run once: {tail}")
        return code, payload, stderr

    def record(self) -> list[dict]:
        data = json.loads((self.git_dir / "dev-loop" / "acceptance.json").read_text())
        self.assertIsInstance(data, dict)
        self.assertIsInstance(data.get("runs"), list)
        return data["runs"]

    def acceptance_logs(self) -> list[Path]:
        logs = self.git_dir / "dev-loop" / "logs"
        return sorted(logs.glob("acceptance-*.log")) if logs.is_dir() else []


class ReviewMergeTests(Sandbox):

    def merge(self, *pairs: tuple[str, Path]) -> tuple[int, dict, str]:
        code, payload, _, tail = self.cli("review-merge", *(f"{name}={path}" for name, path in pairs))
        return code, payload, tail

    def test_f1_review_merge(self) -> None:
        """F1, O1, O4, O6, O7 (three tables, one duplicate, one empty, one security row); O2, O3 (the `file` and
        `line` forms); O5 (a `class` out of the set)."""
        self.commit("Initial", "2026-01-01T00:00:00Z")
        top = self.git("rev-parse", "--show-toplevel")
        reviews = self.root / "review"
        reviews.mkdir()
        code_review = reviews / "code-review.md"
        code_review.write_text(table(
            [("./a.py", 3, "Off by one on an empty list", "correctness", "CONFIRMED: widgets=[] gives IndexError"),
             ("a.py", 5, "Token written to the log", "security", "PLAUSIBLE")],
            before="Review of the branch. Findings below.", after="End of review."))
        second = reviews / "output.md"
        second.write_text(table(
            [("PLAUSIBLE", "correctness", "Index not checked", 3, "/tmp/devloop-second-review-x/a.py", "high")],
            header=("Verdict", "CLASS", "finding", "Line", "File", "severity"), before="# Second review"))
        empty = reviews / "spec-review.md"
        empty.write_text(table([]))

        code, payload, tail = self.merge(("code-review", code_review), ("second-review:codex", second),
                                         ("spec-review", empty))
        self.assertEqual(code, 0, tail)
        rows = payload.get("rows")
        with self.subTest(oracle=1):
            self.assertIsInstance(rows, list, payload)
            self.assertEqual(len(rows), 2, rows)
            first, other = rows
            self.assertEqual((first["n"], first["file"], first["line"]), (1, "a.py", 3))
            self.assertEqual(first["reviewers"], ["code-review", "second-review:codex"])
            self.assertEqual([f["reviewer"] for f in first["findings"]], ["code-review", "second-review:codex"])
            self.assertEqual(first["findings"][0]["finding"], "Off by one on an empty list")
            self.assertEqual(first["findings"][0]["class"], "correctness")
            self.assertEqual(first["findings"][0]["verdict"], "CONFIRMED: widgets=[] gives IndexError")
            self.assertEqual((other["n"], other["file"], other["line"]), (2, "a.py", 5))
            self.assertEqual(other["reviewers"], ["code-review"])
        with self.subTest(oracle=4):
            mine = rows[0]["findings"][1]
            self.assertEqual(mine["finding"], "Index not checked")
            self.assertEqual(mine["class"], "correctness")
            self.assertEqual(mine["verdict"], "PLAUSIBLE")
        with self.subTest(oracle=6):
            self.assertEqual(payload.get("counts"), {"code-review": 2, "second-review:codex": 1, "spec-review": 0})
        with self.subTest(oracle=7):
            self.assertEqual(payload.get("security"), [2])

        forms = reviews / "forms.md"
        forms.write_text(table(
            [("./a.py", "L12", "first", "spec", "PLAUSIBLE"),
             ("`a.py`", "12-15", "second", "standards", "PLAUSIBLE"),
             (f"{top}/a.py", "12", r"a \| b", "correctness", "PLAUSIBLE"),
             ("/tmp/devloop-second-review-x/a.py", "`12`", "fourth", "security", "PLAUSIBLE"),
             ("a.py", "-", "whole file", "standards", "PLAUSIBLE")]))
        code, payload, tail = self.merge(("code-review", forms))
        self.assertEqual(code, 0, tail)
        rows = payload.get("rows") or []
        with self.subTest(oracle=2):
            self.assertEqual([(r["n"], r["file"]) for r in rows], [(1, "a.py"), (2, "a.py")], rows)
        with self.subTest(oracle=3):
            self.assertEqual([r["line"] for r in rows], [None, 12], rows)
            self.assertEqual([f["finding"] for f in rows[1]["findings"]], ["first", "second", "a | b", "fourth"])
            self.assertEqual(payload.get("security"), [2])

        bad = reviews / "bad.md"
        bad.write_text(table([("a.py", 1, "fine", "spec", "PLAUSIBLE"), ("a.py", 2, "ugly", "style", "PLAUSIBLE")]))
        code, payload, tail = self.merge(("code-review", code_review), ("style-review", bad))
        with self.subTest(oracle=5):
            self.assertEqual(code, 1, tail)
            self.assertTrue(payload.get("error"), payload)
            self.assertIn("bad.md", str(payload.get("file")), payload)
            self.assertEqual(payload.get("row"), 2, payload)


class AcceptanceLoopTests(Sandbox):

    def guard(self, payload: dict) -> dict:
        guard = payload.get("guard")
        self.assertIsInstance(guard, dict, payload)
        return guard

    def test_f2_acceptance_loop(self) -> None:
        """F2, O8, O9, O10, O11, O12, O13, O14, O15 (no compose file), O20, O21, and the record (D11)."""
        head = self.commit("Initial", "2026-01-01T00:00:00Z")

        code, run, stderr = self.accept(["b.Widgets::test_y", "a.Widgets::test_x"], 1, errors=True)
        with self.subTest(oracle=8):
            self.assertEqual(code, 1, run)
            self.assertEqual(run.get("exit"), 1)
            self.assertEqual(run.get("failing"), 2)
            self.assertEqual(run.get("ids"), ["a.Widgets::test_x", "b.Widgets::test_y"])
            self.assertIs(run.get("counted"), True)
            self.assertEqual(run.get("mode"), "full")
            self.assertEqual(run.get("head"), head)
            self.assertIsInstance(run.get("seconds"), (int, float))
            self.assertTrue(self.same(run.get("record"), self.git_dir / "dev-loop" / "acceptance.json"), run)
            log = run.get("log")
            self.assertTrue(log and Path(log).name.startswith("acceptance-") and Path(log).is_file(), run)
            self.assertIn(f"acceptance log: {log}", stderr)
        with self.subTest(oracle=15):
            self.assertEqual(run.get("stale", {}).get("state"), "skipped", run)

        code, run, _ = self.accept(None, 1)
        with self.subTest(oracle=9):
            self.assertEqual(code, 1, run)
            self.assertIn("failing", run)
            self.assertIsNone(run["failing"])
            self.assertIsNone(run.get("ids"))
            self.assertIn("junit", str(run.get("hint")).lower(), run)
            self.assertEqual(self.guard(run).get("verdict"), "unknown")

        code, run, _ = self.accept([], 1)
        with self.subTest(oracle=14):
            self.assertEqual(code, 1, run)
            self.assertEqual(run.get("failing"), 0)
            self.assertEqual(self.guard(run).get("verdict"), "unknown")

        code, run, _ = self.accept([], 0, failed=True)
        with self.subTest(oracle=13):
            self.assertEqual(code, 0, run)
            self.assertEqual(run.get("mode"), "failed")
            self.assertEqual(self.guard(run).get("verdict"), "continue")

        code, run, _ = self.accept([], 0)
        with self.subTest(oracle=12, step="green"):
            self.assertEqual(code, 0, run)
            self.assertEqual(self.guard(run).get("verdict"), "green")

        _, run, _ = self.accept(["c::t1", "c::t2", "c::t3", "c::t4", "c::t5"], 1)
        with self.subTest(oracle=12, step="first run of the new loop"):
            self.assertEqual(self.guard(run).get("counts"), [5])
        _, run, _ = self.accept(["d::t1", "d::t2", "d::t3"], 1)
        with self.subTest(oracle=12, step="new loop"):
            self.assertEqual(self.guard(run).get("counts"), [5, 3])
        _, run, _ = self.accept(["e::t1", "e::t2", "e::t3"], 1)
        with self.subTest(oracle=10, step="5, 3, 3"):
            guard = self.guard(run)
            self.assertEqual(guard.get("verdict"), "continue")
            self.assertIsNone(guard.get("reason"))
            self.assertEqual(guard.get("counts"), [5, 3, 3])
        _, run, _ = self.accept(["f::t1", "f::t2", "f::t3", "f::t4"], 1)
        with self.subTest(oracle=10, step="5, 3, 3, 4"):
            guard = self.guard(run)
            self.assertEqual((guard.get("verdict"), guard.get("reason")), ("stop", "no-progress"))
            self.assertEqual(guard.get("counts"), [5, 3, 3, 4])

        self.accept([], 0)
        stuck = "s.Widgets::test_stuck"
        for count in (6, 5, 4):
            self.accept([stuck] + [f"g{count}::t{i}" for i in range(count - 1)], 1)
        _, run, _ = self.accept([stuck, "h::t1", "h::t2"], 1)
        with self.subTest(oracle=11):
            guard = self.guard(run)
            self.assertEqual((guard.get("verdict"), guard.get("reason")), ("stop", "stuck"))
            self.assertEqual(guard.get("ids"), [stuck])
            self.assertEqual(guard.get("counts"), [6, 5, 4, 3])

        runs = self.record()
        with self.subTest(record=True):
            self.assertEqual(len(runs), 14, runs)
            for item in runs:
                self.assertTrue(RUN_KEYS <= set(item), item)
                self.assertRegex(item["at"], UTC_SECONDS)
            self.assertEqual([r["mode"] for r in runs].count("failed"), 1)
            self.assertEqual(runs[0]["ids"], ["a.Widgets::test_x", "b.Widgets::test_y"])
            self.assertEqual(runs[0]["stale"], "skipped")
            self.assertEqual(runs[-1]["failing"], 3)

        (self.git_dir / "dev-loop" / "acceptance.json").write_text("{not json")
        before, logs = self.ran(), self.acceptance_logs()
        code, payload, _, tail = self.cli("acceptance")
        with self.subTest(oracle=21):
            self.assertEqual(code, 1, tail)
            self.assertTrue(payload.get("error"), payload)
            self.assertEqual(self.ran(), before, "the command ran with a bad record")
            self.assertEqual(self.acceptance_logs(), logs)

        (self.git_dir / "dev-loop" / "acceptance.json").unlink()
        self.config({})
        code, payload, _, tail = self.cli("acceptance")
        with self.subTest(oracle=20):
            self.assertEqual(code, 1, tail)
            self.assertTrue(payload.get("error"), payload)
            self.assertEqual(self.ran(), before)
            self.assertEqual(self.acceptance_logs(), logs, "a log was written with no commands.acceptance")


API = "a1" * 32
DB = "d2" * 32


class StaleRunTests(Sandbox):

    def docker(self, api_created: str, db_created: str = "2025-12-01T00:00:00.5Z") -> None:
        state = {"services": {"api": {"build": {"context": ".", "dockerfile": "Dockerfile"}},
                              "db": {"image": "postgres:17"}},
                 "containers": {"api": [API], "db": [DB]},
                 "created": {API: api_created, DB: db_created}}
        (self.fix / "docker-state.json").write_text(json.dumps(state))

    def test_f3_stale_run(self) -> None:
        """F3, O16, O17, O18, O19: the stale check after the run, through a fake `docker`."""
        (self.repo / "compose.yaml").write_text(
            "services:\n  api:\n    build: .\n    profiles: [test]\n  db:\n    image: postgres:17\n")
        (self.repo / "Dockerfile").write_text("FROM scratch\n")
        self.commit("Initial", "2026-01-01T00:00:00Z")
        (self.repo / "app.py").write_text("def widget():\n    return 2\n")
        code_commit = self.commit("Change the widget", "2026-01-10T00:00:00Z")

        self.docker("2026-01-05T00:00:00.123456789Z")
        code, run, _ = self.accept([], 0)
        stale = run.get("stale") or {}
        with self.subTest(oracle=16):
            self.assertEqual(stale.get("state"), "stale", run)
            self.assertEqual(stale.get("services"), ["api"])
            self.assertEqual(stale.get("reference"), code_commit)
            self.assertIs(run.get("counted"), False)
            self.assertIn("guard", run)
            self.assertIsNone(run["guard"])
            self.assertEqual(code, 1, run)
            last = self.record()[-1]
            self.assertEqual((last["stale"], last["counted"], last["head"]), ("stale", False, code_commit))
            self.assertTrue((self.fix / "docker-calls.log").is_file())

        self.docker("2026-01-10T01:00:00Z")
        code, run, _ = self.accept([], 0)
        stale = run.get("stale") or {}
        with self.subTest(oracle=17):
            self.assertEqual(stale.get("state"), "fresh", run)
            self.assertEqual(stale.get("checked"), ["api"])
            self.assertEqual(stale.get("services"), [])
            self.assertIs(run.get("counted"), True)
            self.assertEqual(code, 0, run)
            self.assertEqual(self.guard_verdict(run), "green")

        (self.repo / "tests" / "acceptance").mkdir(parents=True)
        (self.repo / "tests" / "acceptance" / "x_test.py").write_text("def test_x():\n    assert True\n")
        (self.repo / "README.md").write_text("# Acme widgets\n")
        self.commit("Add a test and a readme", "2026-01-12T00:00:00Z")
        code, run, _ = self.accept([], 0)
        stale = run.get("stale") or {}
        with self.subTest(oracle=18):
            self.assertEqual(stale.get("state"), "fresh", run)
            self.assertEqual(stale.get("reference"), code_commit)
            self.assertIs(run.get("counted"), True)

        (self.fix / "docker-fail").write_text("1\n")
        code, run, _ = self.accept([], 0)
        stale = run.get("stale") or {}
        with self.subTest(oracle=19):
            self.assertEqual(stale.get("state"), "error", run)
            self.assertTrue(stale.get("message"), run)
            self.assertIs(run.get("counted"), False)
            self.assertEqual(code, 1, run)

        self.assertEqual([r["stale"] for r in self.record()], ["stale", "fresh", "fresh", "error"])

    def guard_verdict(self, run: dict):
        guard = run.get("guard")
        return guard.get("verdict") if isinstance(guard, dict) else guard


class RepoAcceptanceScriptTests(unittest.TestCase):

    def test_f4_repo_acceptance_script(self) -> None:
        """F4, O22: `scripts/acceptance.sh <fixture folder>` with `DEVLOOP_REPORT_DIR` set writes both XML files."""
        self.assertTrue(ACCEPTANCE_SH.is_file(), f"missing {ACCEPTANCE_SH.relative_to(REPO_ROOT)}")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(os.path.realpath(tmp))
            folder = root / "suite"
            folder.mkdir()
            (folder / "test_fixture_widgets.py").write_text(
                "import unittest\n\n\nclass FixtureWidgets(unittest.TestCase):\n"
                "    def test_breaks(self):\n        self.fail('fixture failure')\n")
            (folder / "widgets.test.mjs").write_text(
                "import test from 'node:test';\n\ntest('widgets pass', () => {});\n")
            reports = root / "reports"
            reports.mkdir()
            env = {k: v for k, v in os.environ.items() if not k.startswith(("DEVLOOP_", "GATE_"))}
            env.update(DEVLOOP_REPORT_DIR=str(reports), PYTHONDONTWRITEBYTECODE="1")
            result = subprocess.run(["bash", str(ACCEPTANCE_SH), str(folder)], cwd=REPO_ROOT, env=env,
                                    capture_output=True, text=True, timeout=600)
            tail = result.stdout[-800:] + result.stderr[-800:]
            self.assertEqual(result.returncode, 1, tail)
            python_xml = reports / "acceptance-python.xml"
            node_xml = reports / "acceptance-node.xml"
            self.assertTrue(python_xml.is_file(), f"no acceptance-python.xml: {sorted(reports.iterdir())} {tail}")
            self.assertTrue(node_xml.is_file(), f"no acceptance-node.xml: {sorted(reports.iterdir())} {tail}")
            cases = [case for case in ET.parse(python_xml).getroot().iter("testcase")
                     if "test_breaks" in case.get("name", "")]
            self.assertEqual(len(cases), 1, ET.tostring(ET.parse(python_xml).getroot())[:800])
            self.assertIsNotNone(cases[0].find("failure"), "the failing test has no failure child")
            node_names = [case.get("name", "") for case in ET.parse(node_xml).getroot().iter("testcase")]
            self.assertIn("widgets pass", node_names)


def section(text: str, number: int) -> str:
    match = re.search(rf"^## {number} .*?(?=^## )", text, re.M | re.S)
    assert match, f"no `## {number}` section"
    return match.group(0)


class KitTextTests(unittest.TestCase):

    def test_f5_kit_text_contract(self) -> None:
        """F5, O23, O24, O25: the repo commands, the SKILL.md Stage 0 and Stage 6 text, the harness refs."""
        with self.subTest(oracle=23, file="dev-loop.json"):
            config = json.loads((REPO_ROOT / "dev-loop.json").read_text())
            self.assertIn("scripts/acceptance.sh", config.get("commands", {}).get("acceptance", ""))
        with self.subTest(oracle=23, file="ci.yml"):
            ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
            job = re.search(r"^  acceptance:\n(.*?)(?=^  \S|\Z)", ci, re.M | re.S)
            self.assertIsNotNone(job, "no acceptance job in ci.yml")
            runs = re.findall(r"^\s*run:\s*(.+)$", job.group(1), re.M)
            self.assertTrue(any("scripts/acceptance.sh" in line for line in runs), runs)

        skill = SKILL.read_text()
        stage6 = section(skill, 6)
        with self.subTest(oracle=24, stage=6):
            for needle in ("devloop.py review-merge", "devloop.py acceptance", "--force-recreate"):
                self.assertIn(needle, stage6)
            self.assertTrue(all(f"`{column}`" in stage6 for column in COLUMNS)
                            or re.search(r"file\W+line\W+finding\W+class\W+verdict", stage6), "reviewer table columns")
            self.assertNotIn("Merge the tables, dedupe by file and line", stage6)
            self.assertNotIn("docker compose ps --format", stage6)
        with self.subTest(oracle=24, stage=0):
            self.assertIn("DEVLOOP_REPORT_DIR", section(skill, 0))

        claude = (REFS / "claude.md").read_text()
        codex = (REFS / "codex.md").read_text()
        with self.subTest(oracle=25):
            self.assertIn("review-merge", claude)
            self.assertIn("dev-loop/review/", claude)
            self.assertIn("review-merge", codex)


if __name__ == "__main__":
    unittest.main()
