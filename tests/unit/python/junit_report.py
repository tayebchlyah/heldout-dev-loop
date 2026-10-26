"""The JUnit writer of the skill suite: the parallel run (`test_dev_loop.py`) and `--discover` share it."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
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


def run_discover(folder: str, xml: str | None) -> int:
    """One serial unittest discover of `folder` (`test_*.py`), as `python3 -m unittest discover -s` runs it.
    With `xml`: the JUnit report there. For the held-out suite, `scripts/acceptance.sh`."""
    sys.path[0] = ""
    suite = unittest.TestLoader().discover(folder, pattern="test_*.py")
    result = unittest.TextTestRunner(resultclass=TimedResult).run(suite)
    if xml:
        with tempfile.TemporaryDirectory() as outcomes:
            Path(outcomes, "discover.json").write_text(json.dumps(case_outcomes(result)))
            write_junit(Path(xml), Path(outcomes), "acceptance-python")
    return 0 if result.wasSuccessful() else 1


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
