#!/usr/bin/env python3
"""Tests for devloop.py: `logged`, `gate --summary`, the project config shape and the CLI test helpers."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

# CI runners have no global git identity: every CLI or hook subprocess that commits needs one from the environment.
for _key, _value in (("GIT_AUTHOR_NAME", "dev-loop test"), ("GIT_AUTHOR_EMAIL", "test@example.invalid"),
                     ("GIT_COMMITTER_NAME", "dev-loop test"), ("GIT_COMMITTER_EMAIL", "test@example.invalid")):
    os.environ.setdefault(_key, _value)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from support import ROOT, SKILL_ROOT  # noqa: E402

DEVLOOP = ROOT / "skill" / "scripts" / "devloop.py"


def load_devloop():
    spec = importlib.util.spec_from_file_location("devloop", DEVLOOP)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load devloop module: {DEVLOOP}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_script(name: str):
    path = DEVLOOP.parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} module: {path}")
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

    def commit(self, cwd: Path, path: str, text: str, message: str | None = None) -> None:
        put(cwd / path, text)
        git(cwd, "add", "-A")
        git(cwd, "commit", "-q", "-m", message or path)


class LoggedTests(Sandbox):
    SCRIPT = "import sys\nfor i in range(30): print(f'line {i}')\nprint('to stderr', file=sys.stderr)\nsys.exit(int(sys.argv[1]))\n"

    def logged(self, code: int) -> tuple[int, dict]:
        put(self.root / "noisy.py", self.SCRIPT)
        return run_cli("logged", "--name", "noisy", "--", sys.executable, str(self.root / "noisy.py"), str(code),
                       cwd=self.repo, env=self.env)

    def test_main_of_an_imported_copy_does_not_register_the_caller_as_devloop(self) -> None:
        module = load_devloop()
        out = io.StringIO()
        with mock.patch.dict(sys.modules), mock.patch.object(sys, "argv", ["devloop.py", "status", "--repo", str(self.repo)]):
            sys.modules.pop("devloop", None)
            sys.modules.pop("status", None)
            with contextlib.redirect_stdout(out):
                module.main()
            self.assertIsNot(sys.modules.get("devloop"), sys.modules["__main__"])
        self.assertIsInstance(json.loads(out.getvalue()), dict)

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
        module = load_script("gate")
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

    def test_failing_gate_lists_junit_failures(self) -> None:
        mixed = ("<testsuite><testcase classname='m.T' name='test_ok'/>"
                 "<testcase classname='m.T' name='test_bad'><failure/></testcase>"
                 "<testcase name='crash'><error/></testcase>"
                 "<testcase classname='m.T' name='test_skip'><skipped/></testcase></testsuite>")
        rows = [
            ([mixed], ["FAIL: test_bad (m.T.test_bad)", "FAILED (failures=2)"],
             ["FAILED crash", "FAILED m.T::test_bad"]),
            (["<testsuite><testcase name='cut'", "<testsuite><testcase name='bad'><failure/></testcase></testsuite>"],
             ["FAILED lint.py - E501"], ["FAILED bad"]),
            (["<testsuite><testcase name='ok'/></testsuite>"], ["FAILED lint.py - E501"], ["FAILED lint.py - E501"]),
        ]
        for reports, log_lines, expected in rows:
            with self.subTest(expected=expected):
                copies = "".join(f'cp "{self.root / f"r{i}.xml"}" "$DEVLOOP_REPORT_DIR/r{i}.xml"\n'
                                 for i in range(len(reports)))
                for i, xml in enumerate(reports):
                    put(self.root / f"r{i}.xml", xml)
                echoes = "".join(f'echo "{line}"\n' for line in log_lines)
                self.gate(f"#!/bin/sh\n{copies}{echoes}exit 1\n")
                _, out = self.run_gate()
                self.assertEqual(out["failures"], expected)

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
        self.assertEqual(load_script("gate").slowest(self.root / "r"), [{"name": "ok", "seconds": 0.5}])

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


class LayoutLookupTests(Sandbox):
    def test_specs_lists_feature_folders(self) -> None:
        dl = load_devloop()
        put(self.notes / "Features" / "20261101 Demo Work" / "Spec.md", frontmatter(feature="demo-work"))
        path = self.notes / "Features" / "20261101 Demo Work" / "Spec.md"
        self.assertEqual(dl.specs(self.notes), [("demo-work", path)])
        put(path, frontmatter(track="M"))
        self.assertEqual(dl.specs(self.notes), [("demo-work", path)])

    def test_specs_reads_old_layout_during_transition(self) -> None:
        dl = load_devloop()
        put(self.notes / "Features" / "20261101 Demo Work" / "Spec.md", frontmatter(feature="demo-work"))
        put(self.notes / "specs" / "20261020 Old.md", frontmatter(feature="old"))
        self.assertEqual({slug for slug, _ in dl.specs(self.notes)}, {"demo-work", "old"})

    def test_find_plan_in_folder_and_old_layout(self) -> None:
        dl = load_devloop()
        folder = self.notes / "Features" / "20261101 Demo Work"
        put(folder / "Spec.md", frontmatter(feature="demo-work"))
        put(folder / "Plan.md", "# Plan\n")
        self.assertEqual(dl.find_plan(self.notes, folder / "Spec.md"), folder / "Plan.md")
        old = self.notes / "specs" / "20261020 Old.md"
        put(old, frontmatter(feature="old"))
        put(self.notes / "plans" / "x.md", "---\nspec: 20261020 Old.md\n---\n")
        self.assertEqual(dl.find_plan(self.notes, old), self.notes / "plans" / "x.md")

    def test_ledger_by_marker_real_path(self) -> None:
        dl = load_devloop()
        top = self.root / "top"
        plan_dir = self.root / "plans-real"
        put(plan_dir / "Plan.md", "# Plan\n")
        link = self.root / "plans-link"
        link.symlink_to(plan_dir)
        sdd = top / ".superpowers" / "sdd"
        put(sdd / "Plan" / "plan-path", str(self.root / "other" / "Plan.md") + "\n")
        put(sdd / "Plan" / "progress.md", "other\n")
        put(sdd / "Plan-x" / "plan-path", str(link / "Plan.md") + "\n")
        put(sdd / "Plan-x" / "progress.md", "mine\n")
        self.assertEqual(dl.ledger(top, plan_dir / "Plan.md"), sdd / "Plan-x" / "progress.md")
        self.assertIsNone(dl.ledger(top, self.root / "nowhere" / "Plan.md"))

    def test_old_layout_exact_names(self) -> None:
        dl = load_devloop()
        for name in ("Answers", "Maps", "specs"):
            (self.notes / name).mkdir(exist_ok=True)
        put(self.notes / ".notes-migrate.json", "{}")
        (self.notes / "plans").rmdir()
        self.assertEqual(dl.old_layout(self.notes), [".notes-migrate.json", "specs"])
        self.assertEqual(dl.old_layout(self.root / "missing"), [])


REPO_ROOT = ROOT


class ProjectConfigShapeTests(unittest.TestCase):
    def test_non_object_config_is_a_failure_line(self) -> None:
        check_setup = load_check_setup()
        for raw in ["[]", '"x"', "3"]:
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as tmp:
                put(Path(tmp) / "dev-loop.json", raw)
                self.assertEqual(check_setup.check_project_config(Path(tmp)), ["dev-loop.json must hold a JSON object"])


def load_check_setup():
    spec = importlib.util.spec_from_file_location("check_setup_for_stack", SKILL_ROOT / "scripts" / "check_setup.py")
    if spec is None or spec.loader is None:
        raise ImportError("cannot load check_setup module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def section(path: Path, heading: str) -> str:
    text = path.read_text()
    start = text.index(f"\n{heading}")
    end = text.find("\n## ", start + 1)
    return text[start:] if end == -1 else text[start:end]


if __name__ == "__main__":
    unittest.main(verbosity=2)
