#!/usr/bin/env python3
"""Regression and setup tests for the shared Claude/Codex dev-loop skill."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
import shutil
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if __name__ == "__main__" and sys.argv[1:2] == ["--discover"]:
    from junit_report import run_discover

    sys.exit(run_discover(sys.argv[2], (sys.argv[3:4] or [None])[0]))

# core.fsmonitor in a user or repo config starts a daemon in every throwaway repo these tests make.
# The override wins over every config file, appends to the caller's GIT_CONFIG_* entries, skips if already set.
_GIT_CONFIG_COUNT = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
if not any(
    os.environ.get(f"GIT_CONFIG_KEY_{i}") == "core.fsmonitor" and os.environ.get(f"GIT_CONFIG_VALUE_{i}") == "false"
    for i in range(_GIT_CONFIG_COUNT)
):
    os.environ.update({
        f"GIT_CONFIG_KEY_{_GIT_CONFIG_COUNT}": "core.fsmonitor",
        f"GIT_CONFIG_VALUE_{_GIT_CONFIG_COUNT}": "false",
        "GIT_CONFIG_COUNT": str(_GIT_CONFIG_COUNT + 1),
    })
# A hook test finds the kit CLI through CLAUDE_PROJECT_DIR too (#91): a session's own project must not leak in.
os.environ.pop("CLAUDE_PROJECT_DIR", None)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import SKILL_ROOT  # noqa: E402
# The board tests import `board` from skill/scripts and their fixtures from board_fixtures beside this file,
# never from tests/acceptance (held out, run by the acceptance runner, not by this suite).
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
from junit_report import TimedResult, case_outcomes, write_junit  # noqa: E402


def wire(name: str) -> None:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    for value in vars(module).values():
        if isinstance(value, type) and issubclass(value, unittest.TestCase) and value.__module__ == name:
            globals()[value.__name__] = value


wire("test_devloop")
wire("test_status")
wire("test_merge_check")
wire("test_merge_binding")
wire("test_skill_text")
wire("test_review_text")
wire("test_seal")
wire("test_seal_check")
wire("test_search_wall")
wire("test_search_hooks")
wire("test_size")
wire("test_seal_hooks")
wire("test_kit_walls")
wire("test_autonomy")
wire("test_release")
wire("test_release_record")
wire("test_release_carry")
wire("test_release_guard")
wire("test_metrics")
wire("test_metrics_cli")
wire("test_ci")
wire("test_ci_skip")
wire("test_kit_reference")
wire("test_gardener")
wire("test_retro")
wire("test_feature_gate")
wire("test_case_floor")
wire("test_upstream_watch")
wire("test_repo_rules")
wire("test_repo_docs")
wire("test_suite_lock")
wire("test_sandbox_run")
wire("test_board_robust")
wire("test_board_layout")
wire("test_board_tasks")
wire("test_board_shipped")
wire("test_board_links")
wire("test_verify")
wire("test_capture")
wire("test_second_review")
wire("test_check_setup")
wire("test_setup_checks")
wire("test_notes_path")
wire("test_codex_fill")
wire("test_notes_migrate")
wire("test_notes_migrate_apply")
wire("test_walls")
wire("test_frontmatter_walls")
wire("test_git_guard")
wire("test_gh_guard")
wire("test_push_spellings")
wire("test_hooks")
wire("test_codex_gate_launcher")
wire("test_macos_hook")
wire("test_codex_trusted_checks")
wire("test_kit_contracts")
wire("test_mutation")
wire("test_audit_common")
wire("test_audit_seed")
wire("test_audit_units")
wire("test_audit_skilltext")
wire("test_audit_brief")
wire("test_audit_check")
wire("test_audit_promote")
wire("test_audit_fold")
wire("test_audit_fold_review")
wire("test_audit_close")
wire("test_audit_report")
wire("test_audit_text")


class JUnitReportTests(unittest.TestCase):
    def test_discover_keeps_flow_modules_separate_from_unit_helpers(self) -> None:
        """A discovered module can load unit helpers without hiding a later flow module."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "test_bootstrap.py").write_text(
                "import sys\n"
                f"sys.path.insert(0, {str(Path(__file__).parent)!r})\n"
                "import test_dev_loop\n")
            (folder / "test_codex_hook_sandbox.py").write_text(
                "import unittest\nclass Flow(unittest.TestCase):\n"
                "    def test_flow(self): self.assertEqual(2 + 2, 4)\n")
            report = folder / "flow.xml"
            result = subprocess.run([sys.executable, "-B", __file__, "--discover", str(folder), str(report)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            cases = list(ET.parse(report).getroot().iter("testcase"))
            self.assertEqual([(c.get("classname"), c.get("name")) for c in cases],
                             [("test_codex_hook_sandbox.Flow", "test_flow")])

    def test_write_junit_names_each_case_with_its_time_and_failure(self) -> None:
        """The parallel run's JUnit report: one testcase per id, its time, a failure element with the last line."""
        with tempfile.TemporaryDirectory() as tmp:
            outcomes, xml = Path(tmp, "outcomes"), Path(tmp, "skill.xml")
            outcomes.mkdir()
            (outcomes / "a.json").write_text(json.dumps({
                "test_board.T.test_ok": {"seconds": 1.5, "outcome": "pass", "text": ""},
                "test_board.T.test_bad (x=1)": {"seconds": 0.25, "outcome": "failure",
                                                "text": "Traceback\nAssertionError: 1 != 2\n"}}))
            write_junit(xml, outcomes)
            cases = {c.get("name"): c for c in ET.parse(xml).getroot().iter("testcase")}
        self.assertEqual(set(cases), {"test_ok", "test_bad (x=1)"})
        self.assertEqual({c.get("classname") for c in cases.values()}, {"test_board.T"})
        self.assertEqual(cases["test_ok"].get("time"), "1.500")
        self.assertIsNone(cases["test_ok"].find("failure"))
        self.assertEqual(cases["test_bad (x=1)"].find("failure").get("message"), "AssertionError: 1 != 2")

    def test_write_junit_splits_a_fixture_error_into_class_and_fixture_name(self) -> None:
        """A failing setUpClass has id `setUpClass (mod.Foo)`: classname `mod.Foo`, name `setUpClass`."""
        with tempfile.TemporaryDirectory() as tmp:
            outcomes, xml = Path(tmp, "outcomes"), Path(tmp, "skill.xml")
            outcomes.mkdir()
            (outcomes / "a.json").write_text(json.dumps({
                "setUpClass (test_dev_loop.Foo)": {"seconds": 0.0, "outcome": "error", "text": "Boom\n"}}))
            write_junit(xml, outcomes)
            case = next(ET.parse(xml).getroot().iter("testcase"))
        self.assertEqual((case.get("classname"), case.get("name")), ("test_dev_loop.Foo", "setUpClass"))

    def test_run_chunk_keeps_the_first_failure_kind_and_appends_later_texts(self) -> None:
        """Chunk reports keep selected skips, fixture errors, and the first of multiple failure kinds."""
        for fixture, action, expected, exit_code in [
            ("class", "skip", {"T.test_a": "skipped", "T.test_b": "skipped", "U.test_c": "pass"}, 0),
            ("module", "skip", {"T.test_a": "skipped", "T.test_b": "skipped", "U.test_c": "skipped"}, 0),
            ("class", "error", {"fixture": "error", "U.test_c": "pass"}, 1),
            ("module", "error", {"fixture": "error"}, 1),
            ("class", "teardown-error", {"T.test_a": "pass", "T.test_b": "pass", "fixture": "error", "U.test_c": "pass"}, 1),
            ("method", "skip", {"T.test_a": "skipped", "T.test_b": "pass", "U.test_c": "pass"}, 0),
            ("method", "failure", {"T.test_a": "failure", "T.test_b": "error", "U.test_c": "pass"}, 1),
            ("method", "subtest", {"T.test_a": "pass", "T.test_a (n=1)": "failure", "T.test_b": "pass", "U.test_c": "pass"}, 1),
        ]:
            with self.subTest(fixture=fixture, action=action), tempfile.TemporaryDirectory() as tmp:
                module = "chunk_probe_" + uuid.uuid4().hex
                raised = "unittest.SkipTest('no Docker engine')" if action == "skip" else "AssertionError('required Docker unavailable')"
                body = "import unittest\n"
                if fixture == "module":
                    body += f"def setUpModule(): raise {raised}\n"
                body += "class T(unittest.TestCase):\n"
                if fixture == "class":
                    setup = "tearDownClass" if action == "teardown-error" else "setUpClass"
                    body += f"    @classmethod\n    def {setup}(cls): raise {raised}\n"
                if action == "failure":
                    body += "    def tearDown(self): raise RuntimeError('from tearDown')\n"
                method = "raise " + raised if fixture == "method" and action == "skip" else "self.fail('from test')" if action == "failure" else "pass"
                if action == "subtest":
                    method = "\n        with self.subTest(n=1): self.fail('from subtest')"
                body += (f"    def test_a(self): {method}\n    def test_b(self): pass\n"
                         "class U(unittest.TestCase):\n    def test_c(self): pass\n")
                Path(tmp, f"{module}.py").write_text(body)
                ids = [f"{module}.{name}" for name in ("T.test_a", "T.test_b", "U.test_c")]
                sys.path.insert(0, tmp)
                try:
                    with contextlib.redirect_stderr(io.StringIO()):
                        result = run_chunk(str(Path(tmp, "r.json")), ids)
                finally:
                    sys.path.remove(tmp)
                    sys.modules.pop(module, None)
                cases = json.loads(Path(tmp, "r.json").read_text())
                actual = {key.removeprefix(module + ".") if not key.startswith(("setUp", "tearDown")) else "fixture": case["outcome"]
                          for key, case in cases.items()}
                self.assertEqual(result, exit_code)
                self.assertEqual(actual, expected)
                if action == "failure":
                    self.assertIn("from test", cases[ids[0]]["text"])
                    self.assertIn("from tearDown", cases[ids[0]]["text"])

    def test_run_parallel_counts_skips_and_rejects_incomplete_child_reports(self) -> None:
        """Every selected id needs an outcome, including when a successful child loses report data."""
        for damage, expected in [("none", 0), ("partial", 1), ("missing", 1)]:
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as tmp:
                module = "parallel_probe_" + uuid.uuid4().hex
                Path(tmp, f"{module}.py").write_text(
                    "import unittest\nclass T(unittest.TestCase):\n"
                    " @classmethod\n def setUpClass(cls): raise unittest.SkipTest('fixture unavailable')\n"
                    " def test_a(self): pass\n def test_b(self): pass\n"
                    "class U(unittest.TestCase):\n def test_c(self): pass\n")
                code = ("import json,sys,unittest; from pathlib import Path; import test_dev_loop as runner\n"
                        f"import {module} as fixture\n"
                        "for name,value in list(vars(runner).items()):\n"
                        " if isinstance(value,type) and issubclass(value,unittest.TestCase): delattr(runner,name)\n"
                        "runner.T=fixture.T;runner.U=fixture.U\n"
                        "original=runner.subprocess.run\n"
                        "def child(*args,**kwargs):\n"
                        " result=original(*args,**kwargs)\n"
                        " report=Path(args[0][args[0].index('--chunk')+1])\n"
                        f" if {damage!r}=='missing': report.unlink()\n"
                        f" if {damage!r}=='partial':\n"
                        "  cases=json.loads(report.read_text());cases.pop(next(iter(cases)));report.write_text(json.dumps(cases))\n"
                        " return result\n"
                        "runner.subprocess.run=child\nraise SystemExit(runner.run_parallel(1))\n")
                env = {**os.environ, "PYTHONPATH": os.pathsep.join((tmp, str(Path(__file__).parent)))}
                result = subprocess.run([sys.executable, "-B", "-c", code], cwd=tmp, env=env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                if damage == "none":
                    self.assertIn("Ran 3 of 3 tests", result.stdout)

    def test_record_dead_chunks_makes_an_error_of_each_id_without_a_report(self) -> None:
        """A chunk process that died before writing its JSON: each of its ids becomes an error with its stderr tail."""
        with tempfile.TemporaryDirectory() as tmp:
            outcomes, xml = Path(tmp), Path(tmp, "skill.xml")
            chunks = [["m.T.a", "m.T.b"], ["m.T.c"]]
            (outcomes / chunk_report(outcomes, chunks[1]).name).write_text(
                json.dumps({"m.T.c": {"seconds": 1.0, "outcome": "pass", "text": ""}}))
            stderr = [f"line {i}" for i in range(100)]
            record_dead_chunks(outcomes, chunks, ["\n".join(stderr), ""])
            write_junit(xml, outcomes)
            cases = {c.get("name"): c for c in ET.parse(xml).getroot().iter("testcase")}
        self.assertEqual(set(cases), {"a", "b", "c"})
        self.assertIsNone(cases["c"].find("error"))
        self.assertIn("line 99", cases["a"].find("error").text)
        self.assertNotIn("line 0\n", cases["a"].find("error").text)

    def test_unittest_summary_line_is_dropped_from_echoed_stderr(self) -> None:
        """`FAILED (failures=2)` would be listed by devloop.py as a failure; the runner's own line is not touched."""
        text = "boom\nFAILED (failures=2)\nFAILED (errors=1, skipped=3)\nRan 5 of 9 tests in 1.0s: FAILED\n"
        self.assertEqual(without_summary(text), "boom\nRan 5 of 9 tests in 1.0s: FAILED\n")


def flat(suite: unittest.TestSuite):
    for test in suite:
        yield from flat(test) if isinstance(test, unittest.TestSuite) else [test]


def chunk_report(outcomes: Path, chunk: list[str]) -> Path:
    """Where the `--chunk` process of `chunk` writes its outcomes."""
    return outcomes / f"{chunk[0]}.json"


def record_dead_chunks(outcomes: Path, chunks: list[list[str]], stderrs: list[str]) -> None:
    """Every id of a chunk whose process wrote no report becomes an error carrying the tail of its stderr."""
    for chunk, stderr in zip(chunks, stderrs):
        if not chunk_report(outcomes, chunk).exists():
            text = "\n".join(stderr.splitlines()[-20:])
            cases = {test_id: {"seconds": 0.0, "outcome": "error", "text": text} for test_id in chunk}
            chunk_report(outcomes, chunk).write_text(json.dumps(cases))


def without_summary(stderr: str) -> str:
    """`stderr` minus unittest's own `FAILED (failures=N)` lines, which `devloop.py gate` would list as failures."""
    return "".join(line for line in stderr.splitlines(keepends=True) if not re.fullmatch(r"FAILED \(.*\)\n?", line))


def run_parallel(jobs: int) -> int:
    """Every test id of this suite, five per `--chunk` process of this file, `jobs` processes at a time.

    The tests start processes (git, devloop.py, hooks) and wait on them, so the suite is bound by process
    start time, not CPU: parallel processes cut the wall time. Each test owns its temp dirs and port."""
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    if loader.errors:
        print("\n".join(map(str, loader.errors)), file=sys.stderr)
        return 1
    ids = list(dict.fromkeys(test.id().replace("__main__.", "test_dev_loop.", 1) for test in flat(suite)))
    chunks = [ids[i:i + 5] for i in range(0, len(ids), 5)]

    outcomes = Path(tempfile.mkdtemp())

    def run(chunk: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, __file__, "--chunk", str(chunk_report(outcomes, chunk)), *chunk],
                              cwd=Path(__file__).parent, capture_output=True, text=True)

    start = time.monotonic()
    with ThreadPoolExecutor(jobs) as pool:
        results = list(pool.map(run, chunks))
    record_dead_chunks(outcomes, chunks, [r.stderr for r in results])
    cases = {}
    for report in outcomes.glob("*.json"):
        cases.update(json.loads(report.read_text()))
    if os.environ.get("DEVLOOP_REPORT_DIR"):
        write_junit(Path(os.environ["DEVLOOP_REPORT_DIR"]) / "skill.xml", outcomes)
    shutil.rmtree(outcomes, ignore_errors=True)
    ran = sum(test_id in cases for test_id in ids)
    failed = [r for r in results if r.returncode != 0]
    for result in failed:
        print(without_summary(result.stderr), file=sys.stderr)
    ok = not failed and ran == len(ids) and not any(case["outcome"] in ("failure", "error") for case in cases.values())
    print(f"Ran {ran} of {len(ids)} tests in {time.monotonic() - start:.1f}s, {jobs} jobs: {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


def run_chunk(report: str, ids: list[str]) -> int:
    """One run_parallel process: plain unittest output on stderr, each test's outcome and time as JSON in `report`."""
    suite = unittest.TestLoader().loadTestsFromNames(ids)
    tests = list(flat(suite))
    result = unittest.TextTestRunner(resultclass=TimedResult).run(suite)
    cases = case_outcomes(result)
    fixtures = set()
    for test in tests:
        cls = type(test)
        for setup, scope in (("setUpClass", cls.__module__ + "." + cls.__qualname__), ("setUpModule", cls.__module__)):
            fixture = f"{setup} ({scope})"
            if fixture in cases and cases[fixture]["outcome"] == "skipped":
                cases.setdefault(test.id(), dict(cases[fixture]))
                fixtures.add(fixture)
    for fixture in fixtures:
        del cases[fixture]
    Path(report).write_text(json.dumps(cases))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--chunk"]:
        sys.exit(run_chunk(sys.argv[2], sys.argv[3:]))
    # Arguments (test names, -k, -v): plain unittest. DEVLOOP_TEST_JOBS=1: one chunk process at a time.
    if len(sys.argv) > 1:
        unittest.main(verbosity=2)
    else:
        sys.exit(run_parallel(max(int(os.environ.get("DEVLOOP_TEST_JOBS") or os.cpu_count() or 1), 1)))
