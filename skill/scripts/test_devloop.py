#!/usr/bin/env python3
"""Tests for devloop.py: status, logged, gate --summary, ci wait."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DEVLOOP = Path(__file__).resolve().parent / "devloop.py"



def load_devloop():
    spec = importlib.util.spec_from_file_location("devloop", DEVLOOP)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load devloop module: {DEVLOOP}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

GIT = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "init.defaultBranch=main",
       "-c", "commit.gpgsign=false"]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def put(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def run_cli(*args: str, cwd: Path, env: dict[str, str] | None = None, timeout: float = 60) -> tuple[int, dict]:
    result = subprocess.run(
        [sys.executable, str(DEVLOOP), *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout
    )
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        raise AssertionError(f"stdout is not one JSON object: {result.stdout!r} {result.stderr!r}")
    return result.returncode, payload


def frontmatter(**fields: str) -> str:
    return "---\n" + "".join(f"{k}: {v}\n" for k, v in fields.items()) + "---\n\n# Spec\n"


PLAN = "---\nspec: 20261001 Add Widget.md\n---\n\n# Plan\n\n### Task 1: one\n\n## Task 2: two\n"


class Sandbox(unittest.TestCase):
    """A main checkout with a bare origin, a notes folder, and DEV_LOOP_NOTES set."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.notes = self.root / "notes"
        (self.notes / "specs").mkdir(parents=True)
        (self.notes / "plans").mkdir()
        self.origin = self.root / "origin.git"
        git(self.root, "init", "-q", "--bare", str(self.origin))
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        put(self.repo / "GLOSSARY.md", "# Glossary\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        git(self.repo, "remote", "add", "origin", str(self.origin))
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "origin")
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = {**os.environ, "HOME": str(self.home), "DEV_LOOP_NOTES": str(self.notes)}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def status(self, *args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> dict:
        code, payload = run_cli("status", *args, cwd=cwd or self.repo, env=env or self.env)
        self.assertEqual(code, 0, payload)
        return payload

    def commit(self, cwd: Path, path: str, text: str) -> None:
        put(cwd / path, text)
        git(cwd, "add", "-A")
        git(cwd, "commit", "-q", "-m", path)


class StatusTests(Sandbox):
    def spec(self, review: str = "approved", name: str = "20261001 Add Widget.md", **extra: str) -> None:
        fields = {"feature": "widget", "track": "M", "review_status": review, **extra}
        put(self.notes / "specs" / name, frontmatter(**fields))

    def worktree(self) -> Path:
        path = self.root / "wt-widget"
        git(self.repo, "worktree", "add", "-q", "-b", "feat/widget", str(path), "origin/main")
        return path.resolve()

    def acceptance(self, wt: Path) -> None:
        put(wt / "tests" / "acceptance" / "api" / "test_widget_flow.py", "def test_flow():\n    pass\n")

    def ledger(self, wt: Path, text: str) -> None:
        put(wt / ".superpowers" / "sdd" / "20261002 Widget Plan" / "progress.md", text)

    def built(self) -> Path:
        self.spec()
        wt = self.worktree()
        self.acceptance(wt)
        put(self.notes / "plans" / "20261002 Widget Plan.md", PLAN)
        return wt

    def test_stage_0_without_a_glossary(self) -> None:
        git(self.repo, "rm", "-q", "GLOSSARY.md")
        out = self.status()
        self.assertEqual(out["stage"], 0)
        self.assertIn("glossary", out["check"].lower())

    def test_stage_0_without_a_notes_path(self) -> None:
        env = {k: v for k, v in self.env.items() if k != "DEV_LOOP_NOTES"}
        out = self.status(env=env)
        self.assertEqual(out["stage"], 0)
        self.assertTrue(out["next"].startswith(f"no notes path for {self.repo}"), out)
        self.assertIn("dev-loop.local.json", out["next"])

    def test_stage_0_without_a_commit(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        git(empty, "init", "-q")
        put(empty / "GLOSSARY.md")
        self.assertEqual(self.status(cwd=empty)["stage"], 0)

    def test_stage_1_on_main_with_no_open_feature(self) -> None:
        out = self.status()
        self.assertEqual(out["stage"], 1)
        self.assertEqual(out["features"], [])
        self.assertIsNone(out["feature"])

    def test_track_s_branch_is_build_then_ship_after_a_commit(self) -> None:
        git(self.repo, "switch", "-q", "-c", "fix/tiny")
        out = self.status()
        self.assertEqual((out["stage"], out["feature"], out["track"]), (5, "tiny", "S"))
        self.assertNotIn("features", out)
        self.commit(self.repo, "a.txt", "a\n")
        self.assertEqual(self.status()["stage"], 7)

    def test_stage_2_unapproved_spec(self) -> None:
        self.spec(review="draft")
        out = self.status("--feature", "widget")
        self.assertEqual((out["stage"], out["feature"], out["track"]), (2, "widget", "M"))

    def test_stage_3_no_acceptance_file(self) -> None:
        self.spec()
        wt = self.worktree()
        out = self.status(cwd=wt)
        self.assertEqual((out["stage"], out["worktree"]), (3, str(wt)))

    def test_stage_4_no_plan_naming_the_spec(self) -> None:
        self.spec()
        wt = self.worktree()
        self.acceptance(wt)
        put(self.notes / "plans" / "20261002 Other Plan.md", "---\nspec: 20260901 Other.md\n---\n")
        self.assertEqual(self.status(cwd=wt)["stage"], 4)

    def test_stage_5_ledger_missing_or_incomplete(self) -> None:
        wt = self.built()
        self.assertEqual(self.status(cwd=wt)["stage"], 5)
        self.ledger(wt, "# Ledger\nTask 1: complete\n")
        out = self.status(cwd=wt)
        self.assertEqual(out["stage"], 5)
        self.assertIn("2", out["check"])

    def test_stage_6_no_gate_line(self) -> None:
        wt = self.built()
        self.ledger(wt, "Task 1: complete\nTask 2: complete (abc123)\n")
        self.assertEqual(self.status(cwd=wt)["stage"], 6)

    def test_stage_7_unmerged_then_shipped_after_a_rebase_merge(self) -> None:
        wt = self.built()
        self.ledger(wt, "Task 1: complete\nTask 2: complete\nFeature gate: green at abc123\n")
        self.commit(wt, "widget.py", "WIDGET = 1\n")
        self.assertEqual(self.status(cwd=wt)["stage"], 7)
        # A rebase merge: same patch, new commit, on origin/main.
        self.commit(self.repo, "other.txt", "other\n")
        git(self.repo, "cherry-pick", "feat/widget")
        git(self.repo, "push", "-q", "origin", "main")
        out = self.status(cwd=wt)
        self.assertEqual(out["stage"], "shipped")

    def test_stage_7_without_origin_main_says_fetch(self) -> None:
        wt = self.built()
        self.ledger(wt, "Task 1: complete\nTask 2: complete\nFeature gate: green at abc123\n")
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        out = self.status(cwd=wt)
        self.assertEqual(out["stage"], 7)
        self.assertIn("git fetch origin", out["next"])

    def test_failed_cherry_says_fetch_and_never_ships(self) -> None:
        wt = self.built()
        self.ledger(wt, "Task 1: complete\nTask 2: complete\nFeature gate: green at abc123\n")
        tree = git(self.repo, "rev-parse", "HEAD^{tree}").strip()
        git(self.repo, "update-ref", "refs/remotes/origin/main", tree)
        out = self.status(cwd=wt)
        self.assertEqual(out["stage"], 7)
        self.assertIn("git fetch origin", out["next"])
        git(self.repo, "switch", "-q", "-c", "fix/tiny")
        out = self.status()
        self.assertEqual(out["stage"], 5)
        self.assertIn("git fetch origin", out["next"])

    def test_approved_spec_with_no_branch_and_acceptance_on_main_is_shipped(self) -> None:
        self.spec()
        self.commit(self.repo, "tests/acceptance/api/test_widget_flow.py", "def test_flow():\n    pass\n")
        self.assertEqual(self.status("--feature", "widget")["stage"], "shipped")
        self.assertEqual(self.status()["features"], [])

    def test_remote_feature_branch_keeps_it_open(self) -> None:
        self.spec()
        self.commit(self.repo, "tests/acceptance/api/test_widget_flow.py", "def test_flow():\n    pass\n")
        git(self.repo, "update-ref", "refs/remotes/origin/feat/widget", "HEAD")
        self.assertEqual(self.status("--feature", "widget")["stage"], 3)

    def test_main_lists_open_features(self) -> None:
        self.spec(review="draft")
        self.spec(name="20261003 Gadget.md", feature="gadget")
        out = self.status()
        self.assertEqual(out["stage"], 1)
        self.assertEqual(
            out["features"],
            [{"feature": "widget", "stage": 2, "worktree": None}, {"feature": "gadget", "stage": 3, "worktree": None}],
        )

    def test_slug_from_title(self) -> None:
        self.assertEqual(load_devloop().slug_from_title("20250901 Fix: the API, v2.md"), "fix-the-api-v2")

    def test_frontmatter_feature_wins_over_title(self) -> None:
        put(self.notes / "specs" / "20250901 Fix: the API, v2.md", frontmatter(feature="gizmo", review_status="draft"))
        self.assertEqual(self.status("--feature", "gizmo")["stage"], 2)
        self.assertEqual(self.status("--feature", "fix-the-api-v2")["stage"], 1)

    def test_spec_without_feature_resolves_by_its_title(self) -> None:
        put(self.notes / "specs" / "20250901 Fix: the API, v2.md", frontmatter(review_status="draft"))
        self.assertEqual(self.status("--feature", "fix-the-api-v2")["stage"], 2)


class LoggedTests(Sandbox):
    SCRIPT = "import sys\nfor i in range(30): print(f'line {i}')\nprint('to stderr', file=sys.stderr)\nsys.exit(int(sys.argv[1]))\n"

    def logged(self, code: int) -> tuple[int, dict]:
        put(self.root / "noisy.py", self.SCRIPT)
        return run_cli("logged", "--name", "noisy", "--", sys.executable, str(self.root / "noisy.py"), str(code),
                       cwd=self.repo, env=self.env)

    def test_full_output_on_disk_and_tail(self) -> None:
        code, out = self.logged(0)
        self.assertEqual((code, out["exit"]), (0, 0))
        log = Path(out["log"])
        self.assertEqual(log.parent, (self.repo / ".git" / "dev-loop" / "logs").resolve())
        self.assertRegex(log.name, r"^noisy-\d{8}T\d{6}\.log$")
        text = log.read_text()
        self.assertIn("line 0\n", text)
        self.assertIn("to stderr", text)
        self.assertEqual(out["tail"], text.splitlines()[-20:])
        self.assertEqual(len(out["tail"]), 20)
        self.assertIsInstance(out["seconds"], float)

    def test_log_names_within_one_second_are_unique(self) -> None:
        module = load_devloop()
        first, second = module.log_path(self.repo, "same"), module.log_path(self.repo, "same")
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_file() and second.is_file())

    def test_a_failing_command_exits_1(self) -> None:
        code, out = self.logged(3)
        self.assertEqual((code, out["exit"]), (1, 3))


JUNIT = "<testsuites><testsuite name='s'>{}</testsuite></testsuites>".format(
    "".join(f"<testcase classname='tests.test_m' name='test_{i}' time='{i / 10}'/>" for i in range(12))
)
FAILING_GATE = """#!/bin/sh
echo "ruff ok"
echo "FAILED tests/unit/test_a.py::test_one - AssertionError"
echo "ERROR tests/unit/test_b.py - ImportError"
echo "--- FAIL: TestThing (0.00s)"
echo " FAIL  src/score.test.ts > score > rounds"
echo "FAILED tests/unit/test_a.py::test_one - AssertionError"
echo "1 failed"
exit 1
"""


class GateTests(Sandbox):
    def gate(self, script: str) -> None:
        put(self.repo / "scripts" / "gate.sh", script)
        (self.repo / "scripts" / "gate.sh").chmod(0o755)

    def run_gate(self) -> tuple[int, dict]:
        return run_cli("gate", "--summary", cwd=self.repo, env=self.env)

    def test_passing_gate(self) -> None:
        self.gate("#!/bin/sh\necho all good\n")
        code, out = self.run_gate()
        self.assertEqual((code, out["exit"], out["failures"]), (0, 0, []))
        self.assertIn("all good", Path(out["log"]).read_text())

    def test_failing_gate_lists_failure_lines(self) -> None:
        self.gate(FAILING_GATE)
        code, out = self.run_gate()
        self.assertEqual((code, out["exit"]), (1, 1))
        self.assertEqual(
            out["failures"],
            [
                "FAILED tests/unit/test_a.py::test_one - AssertionError",
                "ERROR tests/unit/test_b.py - ImportError",
                "--- FAIL: TestThing (0.00s)",
                "FAIL  src/score.test.ts > score > rounds",
            ],
        )

    def test_slowest_from_junit_xml(self) -> None:
        put(self.root / "junit.xml", JUNIT)
        self.gate(f'#!/bin/sh\ncp "{self.root / "junit.xml"}" "$DEVLOOP_REPORT_DIR/pytest.xml"\n')
        _, out = self.run_gate()
        self.assertEqual(len(out["slowest"]), 10)
        self.assertEqual(out["slowest"][0], {"name": "tests.test_m::test_11", "seconds": 1.1})
        self.assertEqual([s["seconds"] for s in out["slowest"]], sorted((i / 10 for i in range(2, 12)), reverse=True))
        self.assertNotIn("hint", out)

    def test_slowest_skips_a_non_numeric_time(self) -> None:
        put(self.root / "r" / "a.xml", "<testsuite><testcase name='bad' time='n/a'/><testcase name='ok' time='0.5'/></testsuite>")
        self.assertEqual(load_devloop().slowest(self.root / "r"), [{"name": "ok", "seconds": 0.5}])

    def test_no_xml_gives_empty_slowest_and_a_hint(self) -> None:
        self.gate("#!/bin/sh\necho ok\n")
        _, out = self.run_gate()
        self.assertEqual(out["slowest"], [])
        self.assertIn("DEVLOOP_REPORT_DIR", out["hint"])

    def test_mapped_gate_command_wins(self) -> None:
        self.gate("#!/bin/sh\necho from-script\n")
        put(self.repo / "dev-loop.json", json.dumps({"commands": {"gate": "echo from-map && exit 0"}}))
        code, out = self.run_gate()
        text = Path(out["log"]).read_text()
        self.assertEqual(code, 0)
        self.assertIn("from-map", text)
        self.assertNotIn("from-script", text)

    def test_missing_gate_exits_127(self) -> None:
        code, out = self.run_gate()
        self.assertEqual((code, out["exit"]), (1, 127))
        self.assertTrue(Path(out["log"]).is_file())


FAKE_GH = """#!{python}
import json, os, sys, time
mode = os.environ["FAKE_GH"]
args = sys.argv[1:]
if args[:2] == ["pr", "view"]:
    if mode == "nopr":
        print("no pull requests found for branch", file=sys.stderr)
        sys.exit(1)
    if mode == "autherror":
        print("HTTP 401: Bad credentials", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({{"number": 7, "url": "https://github.com/o/r/pull/7"}}))
elif args[:2] == ["pr", "checks"] and "--watch" in args:
    assert args[2] == "7", args
    if mode == "timeout":
        time.sleep(30)
    sys.exit(0 if mode == "green" else 1)
elif args[:2] == ["pr", "checks"]:
    print(json.dumps([
        {{"name": "lint", "state": "SUCCESS", "link": "https://github.com/o/r/actions/runs/41/job/1"}},
        {{"name": "test", "state": "FAILURE", "link": "https://github.com/o/r/actions/runs/42/job/9"}},
    ]))
    sys.exit(1)
elif args[:3] == ["run", "view", "42"] and "--log-failed" in args:
    print("test\\tSetup\\tinstalling")
    print("test\\tRun\\tERROR: test_widget failed")
    for i in range(40):
        print(f"test\\tRun\\tdetail {{i}}")
else:
    sys.exit(f"unexpected gh call: {{args}}")
"""


class CiWaitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        gh = self.root / "bin" / "gh"
        put(gh, FAKE_GH.format(python=sys.executable))
        gh.chmod(0o755)
        self.bin = gh.parent

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def wait(self, mode: str, *args: str) -> tuple[int, dict]:
        env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "FAKE_GH": mode}
        return run_cli("ci", "wait", *args, cwd=self.root, env=env)

    def test_green(self) -> None:
        code, out = self.wait("green")
        self.assertEqual((code, out["state"], out["url"]), (0, "pass", "https://github.com/o/r/pull/7"))

    def test_red_names_the_failed_job_and_its_first_error(self) -> None:
        code, out = self.wait("red", "--pr", "7")
        self.assertEqual((code, out["state"], out["failed_job"]), (1, "fail", "test"))
        lines = out["first_error"].splitlines()
        self.assertEqual(lines[0], "test\tRun\tERROR: test_widget failed")
        self.assertEqual(len(lines), 30)
        self.assertEqual(out["url"], "https://github.com/o/r/actions/runs/42/job/9")

    def test_timeout(self) -> None:
        code, out = self.wait("timeout", "--timeout", "0.02")
        self.assertEqual((code, out["state"]), (1, "timeout"))

    def test_no_pr(self) -> None:
        code, out = self.wait("nopr")
        self.assertEqual((code, out["state"]), (1, "no-pr"))

    def test_other_pr_view_failure_is_an_error(self) -> None:
        code, out = self.wait("autherror")
        self.assertEqual((code, out["state"]), (1, "error"))
        self.assertIn("Bad credentials", out["message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
