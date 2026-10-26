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

SKILL_ROOT = Path(__file__).resolve().parents[3] / "skill"
# The board tests import `board` from skill/scripts and their fixtures from board_fixtures beside this file,
# never from tests/acceptance (held out, run by the acceptance runner, not by this suite).
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
sys.path.insert(1, str(Path(__file__).resolve().parent))
from junit_report import TimedResult, case_outcomes, write_junit  # noqa: E402


def wire(name: str) -> None:
    """Load the test file `name` beside this file and take every test class it defines: one command for the suite."""
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
wire("test_skill_text")
wire("test_review_text")
wire("test_seal")
wire("test_size")
wire("test_seal_hooks")
wire("test_autonomy")
wire("test_release")
wire("test_metrics")
wire("test_ci")
wire("test_gardener")
wire("test_retro")
wire("test_feature_gate")
wire("test_upstream_watch")
wire("test_repo_rules")
wire("test_sandbox_run")
wire("test_board_robust")
wire("test_verify")
wire("test_capture")
wire("test_second_review")
wire("test_check_setup")
wire("test_setup_checks")
wire("test_notes_path")
wire("test_walls")
wire("test_git_guard")
wire("test_hooks")
wire("test_kit_contracts")
wire("test_mutation")


class JUnitReportTests(unittest.TestCase):
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
        """A tearDown error after an assertion failure keeps kind `failure` and adds its text."""
        module = "chunk_probe_" + uuid.uuid4().hex
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, f"{module}.py").write_text(
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def tearDown(self): raise RuntimeError('from tearDown')\n"
                "    def test_x(self): self.fail('from test')\n")
            sys.path.insert(0, tmp)
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    run_chunk(str(Path(tmp, "r.json")), [f"{module}.T.test_x"])
            finally:
                sys.path.remove(tmp)
                sys.modules.pop(module, None)
            case = json.loads(Path(tmp, "r.json").read_text())[f"{module}.T.test_x"]
        self.assertEqual(case["outcome"], "failure")
        self.assertIn("from test", case["text"])
        self.assertIn("from tearDown", case["text"])

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
    if os.environ.get("DEVLOOP_REPORT_DIR"):
        write_junit(Path(os.environ["DEVLOOP_REPORT_DIR"]) / "skill.xml", outcomes)
    shutil.rmtree(outcomes, ignore_errors=True)
    ran = sum(int(m.group(1)) for r in results if (m := re.search(r"^Ran (\d+) tests?", r.stderr, re.MULTILINE)))
    failed = [r for r in results if r.returncode != 0]
    for result in failed:
        print(without_summary(result.stderr), file=sys.stderr)
    ok = not failed and ran == len(ids)
    print(f"Ran {ran} of {len(ids)} tests in {time.monotonic() - start:.1f}s, {jobs} jobs: {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


def run_chunk(report: str, ids: list[str]) -> int:
    """One run_parallel process: plain unittest output on stderr, each test's outcome and time as JSON in `report`."""
    result = unittest.TextTestRunner(resultclass=TimedResult).run(unittest.TestLoader().loadTestsFromNames(ids))
    Path(report).write_text(json.dumps(case_outcomes(result)))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--chunk"]:
        sys.exit(run_chunk(sys.argv[2], sys.argv[3:]))
    # Arguments (test names, -k, -v): plain unittest. DEVLOOP_TEST_JOBS=1: one chunk process at a time.
    if len(sys.argv) > 1:
        unittest.main(verbosity=2)
    else:
        sys.exit(run_parallel(max(int(os.environ.get("DEVLOOP_TEST_JOBS") or os.cpu_count() or 1), 1)))
