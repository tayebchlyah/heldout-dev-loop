#!/usr/bin/env python3
"""Tests for devloop.py: status, logged, gate --summary, ci wait."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

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
        out = self.status()
        self.assertEqual(out["stage"], 6)
        self.assertIn("review-done", out["next"])
        self.assertIn("fix wave, then write the review-done marker", out["next"])
        marker = self.repo / ".git" / "dev-loop" / "review-done"
        put(marker, git(self.repo, "rev-parse", "HEAD"))
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

    def test_acceptance_commit_on_the_branch_ends_stage_3(self) -> None:
        """Stage 3 extends earlier acceptance files only, no file named after the slug: the branch commit counts."""
        self.spec()
        self.commit(self.repo, "tests/acceptance/api/test_gadget.py", "def test_gadget():\n    pass\n")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "origin")
        wt = self.worktree()
        self.assertEqual(self.status(cwd=wt)["stage"], 3)
        self.commit(wt, "tests/acceptance/api/test_gadget.py", "def test_gadget():\n    assert True\n")
        self.assertEqual(self.status(cwd=wt)["stage"], 4)

    def test_acceptance_regression_or_none_skips_stage_3(self) -> None:
        wt = self.worktree()
        for value in ["regression", "none", "None"]:
            with self.subTest(value=value):
                self.spec(acceptance=value)
                self.assertEqual(self.status(cwd=wt)["stage"], 4)

    def test_acceptance_new_or_unknown_keeps_stage_3(self) -> None:
        wt = self.worktree()
        for value in ["", "new", "skip"]:
            with self.subTest(value=value):
                self.spec(acceptance=value)
                self.assertEqual(self.status(cwd=wt)["stage"], 3)

    def test_acceptance_skip_still_needs_the_worktree(self) -> None:
        self.spec(acceptance="none")
        self.assertEqual(self.status("--feature", "widget")["stage"], 3)

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

    def test_shipped_field_wins_over_an_open_branch(self) -> None:
        self.spec(shipped="2026-10-01", pr="https://example.invalid/pr/1")
        wt = self.worktree()
        self.commit(wt, "widget.py", "WIDGET = 1\n")
        out = self.status(cwd=wt)
        self.assertEqual(out["stage"], "shipped")
        self.assertIn("shipped: 2026-10-01", out["check"])
        self.assertEqual(self.status()["features"], [])

    def test_empty_shipped_field_keeps_it_open(self) -> None:
        self.spec(shipped="", pr="")
        self.worktree()
        self.assertEqual(self.status("--feature", "widget")["stage"], 3)

    def test_remote_feature_branch_keeps_it_open(self) -> None:
        self.spec()
        self.commit(self.repo, "tests/acceptance/api/test_widget_flow.py", "def test_flow():\n    pass\n")
        git(self.repo, "update-ref", "refs/remotes/origin/feat/widget", "HEAD")
        self.assertEqual(self.status("--feature", "widget")["stage"], 3)

    def test_slug_matches_a_whole_token_only(self) -> None:
        holds = load_devloop().holds_slug
        self.assertTrue(holds("api/test_add_widget.py", "add-widget"))
        self.assertTrue(holds("e2e/add-widget.spec.ts", "add-widget"))
        self.assertFalse(holds("api/test_oauth_login.py", "auth"))
        self.spec(feature="auth")
        self.commit(self.repo, "tests/acceptance/api/test_oauth_login.py", "def test_flow():\n    pass\n")
        self.assertEqual(self.status("--feature", "auth")["stage"], 3)

    def test_merged_branch_without_worktree_is_shipped(self) -> None:
        self.spec()
        git(self.repo, "switch", "-q", "-c", "feat/widget")
        self.commit(self.repo, "widget.py", "WIDGET = 1\n")
        git(self.repo, "switch", "-q", "main")
        self.commit(self.repo, "other.txt", "other\n")
        git(self.repo, "cherry-pick", "feat/widget")
        git(self.repo, "push", "-q", "origin", "main")
        self.assertEqual(self.status("--feature", "widget")["stage"], "shipped")
        # Stale remote-tracking ref, local branch deleted after the merge.
        git(self.repo, "update-ref", "refs/remotes/origin/feat/widget", "feat/widget")
        git(self.repo, "branch", "-q", "-D", "feat/widget")
        self.assertEqual(self.status("--feature", "widget")["stage"], "shipped")

    def test_rebase_in_progress_is_its_own_stage(self) -> None:
        git(self.repo, "switch", "-q", "-c", "fix/tiny")
        self.commit(self.repo, "a.txt", "mine\n")
        git(self.repo, "switch", "-q", "main")
        self.commit(self.repo, "a.txt", "theirs\n")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "switch", "-q", "fix/tiny")
        subprocess.run([*GIT, "rebase", "origin/main"], cwd=self.repo, capture_output=True)
        for args in [(), ("--feature", "tiny")]:
            out = self.status(*args)
            with self.subTest(args=args):
                self.assertEqual((out["stage"], out["feature"]), ("rebase", "tiny"))
                self.assertIn("finish or abort the rebase", out["next"])

    def test_main_lists_open_features(self) -> None:
        self.spec(review="draft")
        self.spec(name="20261003 Gadget.md", feature="gadget")
        git(self.repo, "branch", "feat/gadget")
        out = self.status()
        self.assertEqual(out["stage"], 1)
        self.assertEqual(
            out["features"],
            [{"feature": "widget", "stage": 2, "worktree": None}, {"feature": "gadget", "stage": 3, "worktree": None}],
        )

    def test_main_leaves_out_an_approved_spec_with_no_branch_and_no_worktree(self) -> None:
        self.spec(name="20250101 Old Feature.md", feature="F1")
        self.spec(name="20261003 Draft.md", feature="draft", review="draft")
        self.spec(name="20261004 Remote.md", feature="remote")
        git(self.repo, "update-ref", "refs/remotes/origin/feat/remote", "HEAD")
        self.spec()
        wt = self.worktree()
        self.assertEqual(
            self.status()["features"],
            [{"feature": "widget", "stage": 3, "worktree": str(wt)},
             {"feature": "draft", "stage": 2, "worktree": None},
             {"feature": "remote", "stage": 3, "worktree": None}],
        )
        self.assertEqual(self.status("--feature", "F1")["stage"], 3)

    def test_slug_comparison_ignores_case(self) -> None:
        self.spec(feature="F1", review="draft")
        self.assertEqual(self.status("--feature", "f1")["stage"], 2)
        self.spec(feature="F1")
        path = self.root / "wt-f1"
        git(self.repo, "worktree", "add", "-q", "-b", "feat/f1", str(path), "origin/main")
        out = self.status(cwd=path)
        self.assertEqual((out["stage"], out["feature"], out["track"]), (3, "f1", "M"))
        put(path / "tests" / "acceptance" / "api" / "test_f1.py", "def test_flow():\n    pass\n")
        self.assertEqual(self.status("--feature", "F1")["stage"], 4)
        self.assertEqual(self.status()["features"], [{"feature": "f1", "stage": 4, "worktree": str(path.resolve())}])

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
    if mode == "nochecks" or (mode == "late" and not os.path.exists(os.path.join(os.environ["FAKE_GH_DIR"], "seen"))):
        if mode == "late":
            open(os.path.join(os.environ["FAKE_GH_DIR"], "seen"), "w").close()
        print("no checks reported on the 'feat/x' branch", file=sys.stderr)
        sys.exit(1)
    if mode == "late":
        sys.exit(0)
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

    def test_no_checks_yet_retries_until_checks_exist(self) -> None:
        module = load_devloop()
        setattr(module, "CHECKS_RETRY_SECONDS", 0.05)
        env = {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "FAKE_GH": "late", "FAKE_GH_DIR": str(self.root)}
        args = argparse.Namespace(pr=None, timeout=1.0)
        with mock.patch.dict(os.environ, env), redirect_stdout(io.StringIO()) as out:
            code = module.cmd_ci_wait(args)
        self.assertEqual((code, json.loads(out.getvalue())["state"]), (0, "pass"))
        env["FAKE_GH"] = "nochecks"
        args.timeout = 0.005
        with mock.patch.dict(os.environ, env), redirect_stdout(io.StringIO()) as out:
            code = module.cmd_ci_wait(args)
        self.assertEqual((code, json.loads(out.getvalue())["state"]), (1, "timeout"))

    def test_other_pr_view_failure_is_an_error(self) -> None:
        code, out = self.wait("autherror")
        self.assertEqual((code, out["state"]), (1, "error"))
        self.assertIn("Bad credentials", out["message"])


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_ROOT.parent
TEN_RULES = [
    "one home file per module",
    "public behaviour only",
    "one table per function",
    "no change-detector tests",
    "one golden per output",
    "cost markers",
    "a mutation fix test proves the kill",
    "a test budget per plan task",
    "synthetic fixtures for moved acceptance details",
    "a growth report on each pr",
    "tests only at the seams the spec names",
]
IMPLEMENTERS = [REPO_ROOT / "claude" / "agents" / "implementer.md", REPO_ROOT / "codex" / "agents" / "implementer.toml"]
SPEC_TESTERS = [REPO_ROOT / "claude" / "agents" / "spec-tester.md", REPO_ROOT / "codex" / "agents" / "spec-tester.toml"]


class ProjectConfigShapeTests(unittest.TestCase):
    def test_non_object_config_is_a_failure_line(self) -> None:
        check_setup = load_check_setup()
        for raw in ["[]", '"x"', "3"]:
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as tmp:
                put(Path(tmp) / "dev-loop.json", raw)
                self.assertEqual(check_setup.check_project_config(Path(tmp)), ["dev-loop.json must hold a JSON object"])


class InstructionContractTests(unittest.TestCase):
    def test_building_md_holds_ponytail_and_its_notice(self) -> None:
        text = (SKILL_ROOT / "references" / "building.md").read_text()
        for value in ["Does it need to exist?", "MIT License", "DietrichGebert"]:
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_tests_md_holds_ten_rules(self) -> None:
        text = (SKILL_ROOT / "references" / "tests.md").read_text().lower()
        for rule in TEN_RULES:
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_implementers_read_building_and_tests(self) -> None:
        for path in IMPLEMENTERS:
            text = path.read_text()
            for value in ["references/building.md", "references/tests.md"]:
                with self.subTest(path=path.name, value=value):
                    self.assertIn(value, text)

    def test_implementers_have_contradictory_test_exit(self) -> None:
        for path in IMPLEMENTERS:
            with self.subTest(path=path.name):
                self.assertIn("CONTRADICTORY_TEST: <test> <spec line>", path.read_text())

    def test_spec_testers_write_one_test_per_flow(self) -> None:
        for path in SPEC_TESTERS:
            with self.subTest(path=path.name):
                self.assertIn("one test per acceptance flow", path.read_text())

    def test_spec_testers_name_files_after_the_slug(self) -> None:
        for path in SPEC_TESTERS:
            with self.subTest(path=path.name):
                text = path.read_text()
                self.assertIn("tests/acceptance/api/test_<slug>.py", text)
                self.assertIn("tests/acceptance/e2e/<slug>.spec.ts", text)
        self.assertIn("feature slug", stage_text(3))

    def test_no_double_check_in_kit_prompts(self) -> None:
        banned = ["double-check", "double check", "use a subagent to verify", "narration"]
        for root in [SKILL_ROOT, REPO_ROOT / "claude", REPO_ROOT / "codex"]:
            for path in root.rglob("*"):
                if path.suffix not in {".md", ".toml", ".json"} or "__pycache__" in path.parts:
                    continue
                text = path.read_text().lower()
                for value in banned:
                    with self.subTest(path=str(path.relative_to(REPO_ROOT)), value=value):
                        self.assertNotIn(value, text)

    def test_mutation_runs_nightly(self) -> None:
        text = (SKILL_ROOT / "references" / "mutation.md").read_text()
        self.assertIn("--head main --run", text)
        self.assertNotIn("GATE_SHA", text)


REFERENCES = SKILL_ROOT / "references"
REPLACED_SKILLS = [
    "superpowers:brainstorming",
    "superpowers:test-driven-development",
    "superpowers:systematic-debugging",
    "superpowers:finishing-a-development-branch",
]
PROJECT_SKILL_DENIES = [
    "Skill(superpowers:verification-before-completion)",
    "Skill(superpowers:test-driven-development)",
    "Skill(superpowers:systematic-debugging)",
    "Skill(superpowers:brainstorming)",
    "Skill(superpowers:finishing-a-development-branch)",
]


def load_check_setup():
    spec = importlib.util.spec_from_file_location("check_setup_for_stack", SKILL_ROOT / "scripts" / "check_setup.py")
    if spec is None or spec.loader is None:
        raise ImportError("cannot load check_setup module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stage_text(number: int) -> str:
    skill = (SKILL_ROOT / "SKILL.md").read_text()
    start = skill.index(f"\n## {number} ")
    end = skill.find("\n## ", start + 1)
    return skill[start:] if end == -1 else skill[start:end]


class SkillStackContractTests(unittest.TestCase):
    """D16, D18, D22 and the 2.0.0 pipeline: the skill each stage names."""

    def setUp(self) -> None:
        self.claude = (REFERENCES / "claude.md").read_text()
        self.codex = (REFERENCES / "codex.md").read_text()

    def assert_only_denied(self, name: str) -> None:
        for path in [SKILL_ROOT / "SKILL.md", *sorted(REFERENCES.glob("*.md"))]:
            for line in path.read_text().splitlines():
                if name in line:
                    with self.subTest(path=path.name, line=line):
                        if not line.lstrip("- ").startswith("Never invoke"):
                            self.assertIn(f"Skill({name})", line)

    def test_codex_skill_list(self) -> None:
        self.assertEqual(
            load_check_setup().CODEX_SKILLS,
            ["grilling", "domain-modeling", "codebase-design", "code-review", "tdd", "diagnosing-bugs", "pr", "prototype"],
        )

    def test_claude_project_check_requires_the_skill_denies(self) -> None:
        check_setup = load_check_setup()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"permissions": {"deny": ["Read(./.env)", "Read(./.env.*)"]}}))
            failures: list[str] = []
            check_setup.check_claude_settings(path, failures)
            for rule in PROJECT_SKILL_DENIES:
                with self.subTest(rule=rule):
                    self.assertIn(f"missing Claude deny: {rule}", failures)

    def test_stage_1_uses_grilling_only(self) -> None:
        self.assert_only_denied("superpowers:brainstorming")
        stage = stage_text(1)
        for value in ["grilling", "prototype", "/wayfinder", "Track S", "one-line reason"]:
            with self.subTest(value=value):
                self.assertIn(value, stage)
        for value in ["mattpocock-skills:grilling", "mattpocock-skills:prototype"]:
            self.assertIn(value, self.claude)
        for value in ["`grilling`", "`prototype`"]:
            self.assertIn(value, self.codex)

    def test_spec_reference_holds_builder_check_and_notices(self) -> None:
        spec = (REFERENCES / "spec.md").read_text()
        for value in [
            "feature:", "track:", "review_status:", "\nshipped:\npr:\n", "## Acceptance oracle", "Supersedes", "## Assumptions",
            "the ideal number is one",
            "You've been handed a design document",
            "nothing important left",
            "Copyright (c) 2025 Jesse Vincent",
            "Run STRIDE over each boundary",
            "Adversarial review. Find what is wrong with this artifact.",
            "Copyright (c) 2025 Addy Osmani",
            "MIT License",
        ]:
            with self.subTest(value=value):
                self.assertIn(value, spec)
        self.assertIn("references/spec.md", stage_text(2))

    def test_stage_4_writes_plans(self) -> None:
        self.assertIn("superpowers:writing-plans", stage_text(4))

    def test_stage_5_executor_by_track(self) -> None:
        stage = stage_text(5)
        for value in [
            "Track M: `superpowers:executing-plans`",
            "Track L: `superpowers:subagent-driven-development`",
            "`tdd`", "`diagnosing-bugs`", "references/building.md", "references/tests.md",
        ]:
            with self.subTest(value=value):
                self.assertIn(value, stage)
        for name in ["superpowers:test-driven-development", "superpowers:systematic-debugging"]:
            self.assert_only_denied(name)
        for value in ["superpowers:executing-plans", "mattpocock-skills:tdd", "mattpocock-skills:diagnosing-bugs"]:
            self.assertIn(value, self.claude)
        for value in ["superpowers:executing-plans", "`tdd`", "`diagnosing-bugs`"]:
            self.assertIn(value, self.codex)

    def test_stage_7_is_dev_loop_own_with_pr(self) -> None:
        stage = stage_text(7)
        self.assert_only_denied("superpowers:finishing-a-development-branch")
        for value in ["`pr`", "git rebase origin/main", "gh pr merge --rebase"]:
            with self.subTest(value=value):
                self.assertIn(value, stage)
        self.assertIn("mattpocock-skills:pr", self.claude)
        self.assertIn("`pr`", self.codex)



def section(path: Path, heading: str) -> str:
    text = path.read_text()
    start = text.index(f"\n{heading}")
    end = text.find("\n## ", start + 1)
    return text[start:] if end == -1 else text[start:end]


class ReviewWaveContractTests(unittest.TestCase):
    """D12, D13, D14, D6 in Stage 6: one review wave of three reviewers."""

    def setUp(self) -> None:
        self.stage = stage_text(6)
        self.claude = section(REFERENCES / "claude.md", "## Feature gate")
        self.codex = section(REFERENCES / "codex.md", "## Feature gate")

    def test_review_wave_has_three_reviewers_per_route(self) -> None:
        for value in [
            "`general-purpose`", "`model: opus`", "one message, four Agent calls",
            "skill `code-review`, args `high <feature branch>`", "skill `security-review`",
            "`git diff main...GATE_SHA` from the feature worktree, not the working tree",
            "Pocock spec desk", "Pocock standards desk", "`mattpocock-skills:code-review` SKILL.md",
            "no nested agents", "reports after its parent hands back", "Pocock = two desks",
            "works in the feature worktree on `main...GATE_SHA`",
        ]:
            with self.subTest(route="claude", value=value):
                self.assertIn(value, self.claude)
        self.assertNotIn("three Agent calls", self.claude)
        for value in [
            "`final_reviewer`", "skill `code-review` with fixed point `main`", "`security_reviewer`",
            "works in the feature worktree on `main...GATE_SHA`",
        ]:
            with self.subTest(route="codex", value=value):
                self.assertIn(value, self.codex)

    def test_no_simplify_or_mutation_in_gate(self) -> None:
        for name, text in [("SKILL.md", self.stage), ("claude.md", self.claude), ("codex.md", self.codex)]:
            for value in ["simplif", "mutation", "mutant"]:
                with self.subTest(path=name, value=value):
                    self.assertNotIn(value, text.lower())
        self.assertFalse((REPO_ROOT / "codex" / "agents" / "simplifier.toml").exists())
        self.assertNotIn("simplifier", (REFERENCES / "codex.md").read_text())
        self.assertNotIn("simplifier.toml", (SKILL_ROOT / "scripts" / "check_setup.py").read_text())

    def test_gate_uses_devloop_and_commands_map(self) -> None:
        for value in [
            "git fetch origin", "`commands.stack_reset`", "docker compose --profile test up -d --build --wait",
            "`GATE_SHA`", "scripts/devloop.py gate --summary", "`commands.acceptance`", "`commands.acceptance_failed`",
            "product, test or environment", "Never rerun to green", "Progress guard", "Stale image rule",
            "## Gardener", "at most 20 open items", "lint candidate", "at most 5",
        ]:
            with self.subTest(value=value):
                self.assertIn(value, self.stage)

    def test_every_defect_is_fixed(self) -> None:
        self.assertNotIn("Defect past the cap", self.stage)
        self.assertIn("Every defect is fixed, no cap", self.stage)
        self.assertIn("More than 5 defects: fix all, say so in the final report", self.stage)
        self.assertIn("at most 5 optional non-defect items", self.stage)
        self.assertIn("passes when its defects are fixed", self.stage)
        for name in ["claude.md", "codex.md"]:
            with self.subTest(path=name):
                self.assertIn("Act On list: every defect", (REFERENCES / name).read_text())

    def test_track_s_marker_comes_after_the_fix_wave(self) -> None:
        line = next(line for line in self.stage.splitlines() if line.startswith("Track S:"))
        self.assertIn("After the fix wave: `mkdir -p", line)
        self.assertLess(line.index("fix wave only"), line.index("review-done"))

    def test_gate_writes_ledger_line(self) -> None:
        self.assertIn("`Feature gate: green at <sha>`", self.stage)
        self.assertIn("progress.md", self.stage)


class TracksContractTests(unittest.TestCase):
    """D7, D10, D11, D14, D15: tracks, chained sessions, status detection, the merge rule."""

    def setUp(self) -> None:
        self.skill = (SKILL_ROOT / "SKILL.md").read_text()
        self.header = self.skill[: self.skill.index("\n## 0 ")]

    def test_skill_chains_stages_under_the_grant(self) -> None:
        self.assertNotIn("Run one stage per invocation", self.skill)
        self.assertNotIn("Never chain stages", self.skill)
        description = next(line for line in self.skill.splitlines() if line.startswith("description:"))
        self.assertIn("dev-loop.json", description)
        self.assertNotIn("vault_notes", self.skill)
        for value in [
            "Track S", "Track M", "Track L", "Session A", "Session B", "Stages 3 to 7",
            "## Assumptions", "three fix rounds", "one-way door", "deletes data", "reaches production",
            "usage limit", "Supersedes", "Monitor", "two minutes", "| T0 |", "| T4 |",
        ]:
            with self.subTest(value=value):
                self.assertIn(value, self.header)
        stage = stage_text(3)
        self.assertNotIn("Tell the user to review", stage)
        self.assertNotIn("/clear", stage)

    def test_skill_detects_by_devloop_status(self) -> None:
        self.assertIn("scripts/devloop.py status", self.header)
        self.assertNotIn("| Check | Stage |", self.skill)

    def test_stage_4_smart_zone_rule(self) -> None:
        stage = stage_text(4)
        for value in ["smart zone", "names each file", "no ruling from another task", "split", "`spec: <spec file name>`"]:
            with self.subTest(value=value):
                self.assertIn(value, stage)

    def test_stage_7_merge_rule(self) -> None:
        stage = stage_text(7)
        for value in [
            "git fetch origin", "git rebase origin/main", "scripts/devloop.py gate --summary",
            "scripts/devloop.py ci wait", "`merge: auto`", "`merge: human`", "two-way door", "blast radius",
            "Track L", "gh pr merge --rebase", "security item in `## Deferred`",
            "Right after the merge: write `shipped: <merge date>`", "`pr: <PR url>`",
            "into the spec frontmatter in the notes folder", "Track S: no spec, skip",
        ]:
            with self.subTest(value=value):
                self.assertIn(value, stage)

    def test_new_repo_files_and_the_deferred_cap(self) -> None:
        for value in ["`CODING_STANDARDS.md`", "nightly"]:
            with self.subTest(value=value):
                self.assertIn(value, stage_text(0))
        self.assertIn("`## Deferred` holds 20 open items", stage_text(1))

    def test_track_s_has_no_spec_and_no_plan(self) -> None:
        self.assertIn("Track S: no spec, no plan", stage_text(1))
        self.assertIn("Track S: no plan, no executor", stage_text(5))
        self.assertIn("then the Stage 6 review wave, triage and fix wave, then Stage 7", stage_text(5))
        self.assertIn("Stage 1, Stage 5, Stage 6 review wave, triage and fix wave only, Stage 7", self.header)
        self.assertIn("after green CI and the Stage 6 review wave", stage_text(7))
        self.assertNotIn("Tracks S and M", self.skill)
        self.assertNotIn("Tracks S, M", (REFERENCES / "claude.md").read_text())
        self.assertNotIn("Tracks S, M", (REFERENCES / "codex.md").read_text())

    def test_codex_never_invoke_list_names_plain_skills(self) -> None:
        codex = (REFERENCES / "codex.md").read_text()
        self.assertNotIn("Skill(superpowers:", codex)
        line = next(line for line in codex.splitlines() if line.lstrip("- ").startswith("Never invoke"))
        for name in REPLACED_SKILLS:
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", line)


class SpecGapContractTests(unittest.TestCase):
    """D6, D9, the pipeline table, the devloop CLI contract in AGENTS.md."""

    def test_mutation_md_describes_the_nightly_ci_workflow(self) -> None:
        nightly = section(REFERENCES / "mutation.md", "## Nightly CI")
        for value in ["schedule", "cron", "once a day", "`main`", "--head main --run", "artifact",
                      "functions that need one unit test", "Stage 0", "new repo"]:
            with self.subTest(value=value):
                self.assertIn(value, nightly)

    def test_stage_0_writes_the_app_cli_skeleton(self) -> None:
        self.assertIn("app CLI skeleton (stack up, stack reset, acceptance run), mapped in `commands`", stage_text(0))

    def test_stack_up_and_stack_reset_are_read(self) -> None:
        self.assertIn("`commands.stack_up`, else `docker compose --profile test up -d --wait`", stage_text(5))
        self.assertIn("Fresh stack: `commands.stack_reset`", stage_text(6))

    def test_agents_md_documents_the_cli_as_built(self) -> None:
        agents = (REPO_ROOT / "AGENTS.md").read_text()
        for value in ["`status [--repo <path>] [--feature <slug>]`", "`logged [--name <name>] -- <cmd>`",
                      "`error` with `message`", "`features`: each spec not shipped",
                      "An approved spec with neither is left out", "`shipped: <YYYY-MM-DD>`", "`pr: <url>`"]:
            with self.subTest(value=value):
                self.assertIn(value, agents)


if __name__ == "__main__":
    unittest.main(verbosity=2)
