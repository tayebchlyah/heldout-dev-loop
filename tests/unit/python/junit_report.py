"""The JUnit writer of the skill suite: the parallel run (`test_dev_loop.py`) and `--discover` share it."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zlib
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


class TimedResult(unittest.TextTestResult):
    """Keeps each test's time by test id, for the JUnit report."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.seconds: dict[str, float] = {}

    def addDuration(self, test, elapsed) -> None:
        super().addDuration(test, elapsed)
        self.seconds[test.id()] = elapsed


def case_outcomes(result: TimedResult) -> dict:
    """Each test's outcome and time by test id. A test with two outcomes keeps the first kind, all texts."""
    cases = {test_id: {"seconds": seconds, "outcome": "pass", "text": ""} for test_id, seconds in result.seconds.items()}
    for kind, found in (("skipped", result.skipped), ("failure", result.failures), ("error", result.errors)):
        for test, text in found:
            case = cases.setdefault(test.id(), {"seconds": 0.0, "outcome": "pass", "text": ""})
            if case["outcome"] == "pass":
                case.update(outcome=kind, text=text)
            else:
                case["text"] += text
    return cases


def flat(suite: unittest.TestSuite):
    for test in suite:
        yield from flat(test) if isinstance(test, unittest.TestSuite) else [test]


def in_shard(keys: list[str]) -> list[str]:
    value = os.environ.get("DEVLOOP_TEST_SHARD")
    if not value:
        return keys
    index, _, total = value.partition("/")
    if not (index.isdigit() and total.isdigit() and 1 <= int(index) <= int(total)):
        sys.exit(f"DEVLOOP_TEST_SHARD must be <index>/<total> with 1 <= index <= total, not {value!r}")
    return [key for key in keys if zlib.crc32(key.encode()) % int(total) == int(index) - 1]


def chunk_report(outcomes: Path, chunk: list[str]) -> Path:
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


def run_chunks(command: list[str], chunks: list[list[str]], ids: list[str], jobs: int, cwd: Path,
               xml: Path | None, name: str, outcomes: Path) -> int:
    def run(chunk: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run([*command, str(chunk_report(outcomes, chunk)), *chunk],
                              cwd=cwd, capture_output=True, text=True)
    start = time.monotonic()
    with ThreadPoolExecutor(jobs) as pool:
        results = list(pool.map(run, chunks))
    record_dead_chunks(outcomes, chunks, [r.stderr for r in results])
    cases = {}
    for report in outcomes.glob("*.json"):
        cases.update(json.loads(report.read_text()))
    if xml:
        write_junit(xml, outcomes, name)
    ran = sum(test_id in cases for test_id in ids)
    failed = [r for r in results if r.returncode != 0]
    for result in failed:
        print(result.stdout, end="")
        print(without_summary(result.stderr), file=sys.stderr)
    ok = not failed and ran == len(ids) and not any(case["outcome"] in ("failure", "error") for case in cases.values())
    print(f"Ran {ran} of {len(ids)} tests in {time.monotonic() - start:.1f}s, {jobs} jobs: {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


def run_chunk(report: str, ids: list[str]) -> int:
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


def set_discover_path(folder: str) -> None:
    sys.path[0] = ""
    sys.path.insert(0, str(Path(folder).resolve()))


def run_discover(runner: str, folder: str, xml: str | None, jobs: int) -> int:
    set_discover_path(folder)
    tests = list(flat(unittest.TestLoader().discover(folder, pattern="test_*.py")))
    loader_errors = [test.id() for test in tests if test.id().startswith("unittest.loader.")]
    classes = in_shard(list(dict.fromkeys(test.id().rpartition(".")[0] for test in tests
                                          if test.id() not in loader_errors)))
    kept = set(in_shard(loader_errors)) | set(classes)
    tests = [test for test in tests if test.id() in kept or test.id().rpartition(".")[0] in kept]
    broken = [test for test in tests if test.id() in loader_errors]
    outcomes = Path(tempfile.mkdtemp(dir=os.environ.get("DEVLOOP_RUN_DIR")))
    try:
        if broken:
            result = unittest.TextTestRunner(resultclass=TimedResult).run(unittest.TestSuite(broken))
            Path(outcomes, "broken.json").write_text(json.dumps(case_outcomes(result)))
        ids = [test.id() for test in tests]
        return run_chunks([sys.executable, runner, "--discover-chunk", folder], [[name] for name in classes], ids,
                          jobs, Path.cwd(), Path(xml) if xml else None, "acceptance-python", outcomes)
    finally:
        shutil.rmtree(outcomes, ignore_errors=True)


def write_junit(path: Path, outcomes: Path, name: str = "skill") -> None:
    """JUnit XML of every chunk's outcomes, for `devloop.py gate --summary`."""
    cases = {}
    for report in outcomes.glob("*.json"):
        cases.update(json.loads(report.read_text()))
    suite = ET.Element("testsuite", name=name, tests=str(len(cases)))
    for test_id, case in sorted(cases.items()):
        head = test_id.partition(" ")[0]
        if "." in head:
            classname = head.rpartition(".")[0]
            name = test_id[len(classname) + 1:]
        else:  # a fixture error: `setUpClass (mod.Foo)`
            classname, name = test_id.partition(" ")[2].strip("()"), head
        element = ET.SubElement(suite, "testcase", classname=classname, name=name,
                                time=f"{case['seconds']:.3f}")
        if case["outcome"] != "pass":
            lines = case["text"].strip().splitlines() or [""]
            ET.SubElement(element, case["outcome"], message=lines[-1]).text = case["text"]
    root = ET.Element("testsuites")
    root.append(suite)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
