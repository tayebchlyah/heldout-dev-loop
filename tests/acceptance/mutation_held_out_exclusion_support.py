"""Shared fixture of the held-out tests for mutation held-out exclusion (spec: 20261116 Mutation Held-Out Exclusion).

Seam 1 (spec `## Interfaces and seams`): the `check_setup.py --harness claude` process of the kit source on the
Stack Model setup project (`stack_model_support.SetupProject`), its `mutation` lines. Seam 2: the
`devloop.py mutation --run` process of the kit source on a light fixture repo (`RunRepo`), with a stub
`node_modules/.bin/stryker` and a stub `mutmut` on `PATH`. Every name in the fixture repos is invented.

Fixture choices the spec leaves open:
- A `RunRepo` has a `main` branch with one commit (the base: `dev-loop.json`, the tool config, the code, a unit
  test and a held-out test under the root `tests/acceptance/`), then a branch `feat` with one commit that changes one
  line of each critical file, checked out; runs pass `--head feat`. `main`, `--head` and the working tree then agree
  on the `mutation` key and the tool config (M9). `.gitignore` ignores `node_modules/`, `.stryker-tmp/`, `reports/`
  and `mutants/`.
- The stub `stryker` sits at the repo root `node_modules/.bin/stryker`. Each call appends `{"cwd", "argv"}` to a log
  outside the repo (`cwd` relative to the repo root, `.` for the root) and writes the report a control file outside
  the repo sets for that `cwd` to `reports/mutation/mutation.json` (no report for a null value), then exits 0.
- The stub report holds one `Survived` mutant, covered by a test, on the changed line 2 of `src/core/price.ts`
  (`total > 100` to `total >= 100`), and, when given, a `testFiles` object with one test per listed key.
- The stub `mutmut` is first on `PATH` (no lock file, so the adapter runs `mutmut` from `PATH`); each call appends
  its argv to a log outside the repo, and it exits 0.
- The value of `--ignorePatterns` is the argument after it, or the text after `--ignorePatterns=`.
- A "failure line" is a stripped `check_setup.py` stdout line that starts with `- ` and comes before the
  `User actions required` heading; a `mutation` failure line starts with `- mutation `. "The line `<text>`" of the
  spec is a failure line `- mutation entry <path>: <declared text>`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from stack_model_support import DEVLOOP, SetupProject, clean_env, commit_all, git, init_repo, put, stripped

PRICE_BASE = ("export function discount(total: number): number {\n"
              "  if (total > 50) {\n"
              "    return 10;\n"
              "  }\n"
              "  return 0;\n"
              "}\n")
PRICE_HEAD = PRICE_BASE.replace("total > 50", "total > 100")
KETTLE_BASE = "def boil(level):\n    if level > 50:\n        return 100\n    return 0\n"
KETTLE_HEAD = KETTLE_BASE.replace("level > 50", "level > 80")
IGNORED = "node_modules/\n.stryker-tmp/\nreports/\nmutants/\n"
PACKAGE = json.dumps({"name": "kettle-web", "private": True, "type": "module",
                      "devDependencies": {"@stryker-mutator/core": "10.1.1",
                                          "@stryker-mutator/vitest-runner": "10.1.1", "vitest": "4.1.12"}}) + "\n"
PYPROJECT_HEAD = ('[tool.poetry]\nname = "kettle"\nversion = "0.1.0"\n\n'
                  '[tool.poetry.group.dev.dependencies]\nmutmut = "3.8.1"\n\n')

REACHES = "[tool.mutmut] test selection reaches tests/acceptance: run unit tests only"
NO_PATH = "[tool.mutmut] test selection names no test path: name the unit test folder"
CLI_ARGS = "[tool.mutmut] pytest_add_cli_args reaches tests/acceptance: run unit tests only"
IN_PLACE = 'stryker.config.json sets "inPlace": true: the held-out suite then reaches the run'
NOT_LIST = "stryker.config.json ignorePatterns is not a list of strings"
COMMA = "stryker.config.json ignorePatterns entry has a comma: "

STRYKER_STUB = '''#!{python}
import json
import os
import pathlib
import sys

REPO = {repo!r}
LOG = {log!r}
CONTROL = {control!r}
cwd = os.path.relpath(os.path.realpath(os.getcwd()), os.path.realpath(REPO))
with open(LOG, "a") as handle:
    handle.write(json.dumps({{"cwd": cwd, "argv": sys.argv[1:]}}) + "\\n")
with open(CONTROL) as handle:
    report = json.load(handle).get(cwd)
if report is not None:
    path = pathlib.Path("reports/mutation/mutation.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report))
sys.exit(0)
'''

MUTMUT_STUB = '''#!{python}
import json
import sys

with open({log!r}, "a") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
sys.exit(0)
'''


def stryker_report(test_files: list[str] | None = None) -> dict:
    """A Stryker JSON report of `src/core/price.ts` at the head; `testFiles` only when `test_files` is given."""
    mutant = {"id": "1", "mutatorName": "EqualityOperator", "replacement": "total >= 100", "status": "Survived",
              "coveredBy": ["0"], "killedBy": [], "testsCompleted": 1,
              "location": {"start": {"line": 2, "column": 7}, "end": {"line": 2, "column": 18}}}
    report: dict = {"schemaVersion": "1", "thresholds": {"high": 80, "low": 60, "break": None},
                    "files": {"src/core/price.ts": {"language": "typescript", "source": PRICE_HEAD,
                                                    "mutants": [mutant]}}}
    if test_files is not None:
        report["testFiles"] = {name: {"tests": [{"id": str(n), "name": f"discount case {n}"}]}
                               for n, name in enumerate(test_files)}
    return report


def stryker_folder(prefix: str, config: dict) -> dict[str, str]:
    """The files of one Stryker entry folder (`prefix` `""` for the root): config, package, critical source."""
    return {f"{prefix}stryker.config.json": json.dumps(config) + "\n", f"{prefix}package.json": PACKAGE,
            f"{prefix}src/core/price.ts": PRICE_BASE}


def mutmut_pyproject(selection: list[str], cli_args: list[str] | None = None) -> str:
    text = PYPROJECT_HEAD + '[tool.mutmut]\nsource_paths = ["src/"]\n'
    text += f"pytest_add_cli_args_test_selection = {json.dumps(selection)}\n"
    if cli_args is not None:
        text += f"pytest_add_cli_args = {json.dumps(cli_args)}\n"
    return text


def python_folder(prefix: str, selection: list[str], cli_args: list[str] | None = None) -> dict[str, str]:
    """The files of one mutmut entry folder (`prefix` `""` for the root): pyproject, critical source, unit tests."""
    return {f"{prefix}pyproject.toml": mutmut_pyproject(selection, cli_args), f"{prefix}src/kettle.py": KETTLE_BASE,
            f"{prefix}tests/unit/test_kettle.py": "def test_boil():\n    assert True\n",
            f"{prefix}tests/unit/test_slow.py": "def test_t():\n    assert True\n"}


HELD_OUT_FILES = {"tests/acceptance/test_x.py": "def test_a():\n    assert True\n",
                  "tests/acceptance/a.test.ts": "export {}\n", "tests/unit/b.test.ts": "export {}\n"}


def option_values(argv: list[str], option: str = "--ignorePatterns") -> list[str]:
    values = []
    for n, item in enumerate(argv):
        if item == option and n + 1 < len(argv):
            values.append(argv[n + 1])
        elif item.startswith(option + "="):
            values.append(item[len(option) + 1:])
    return values


class RunRepo:
    """A light fixture repo for `devloop.py mutation --run`, with the stub tools."""

    def __init__(self, case: "HeldOutCase | SetupRepo", name: str) -> None:
        self.case = case
        self.path = case.root / name
        self.env = dict(case.env)
        self.stryker_log = case.root / f"{name}-stryker.jsonl"
        self.stryker_control = case.root / f"{name}-stryker-control.json"
        self.mutmut_log = case.root / f"{name}-mutmut.jsonl"

    def build(self, mutation: list[dict], files: dict[str, str], changes: dict[str, str]) -> "RunRepo":
        init_repo(self.path, self.env)
        for name, text in {**HELD_OUT_FILES, ".gitignore": IGNORED, **files}.items():
            put(self.path / name, text)
        put(self.path / "dev-loop.json", json.dumps({"version": "0.0.0", "mutation": mutation}, indent=2) + "\n")
        commit_all(self.path, self.env, "Base")
        git(self.path, self.env, "checkout", "-q", "-b", "feat")
        for name, text in changes.items():
            put(self.path / name, text)
        commit_all(self.path, self.env, "Change")
        return self

    def stryker(self, reports: dict[str, dict | None]) -> None:
        """Plant the stub `stryker`; `reports`: the report it writes for each `cwd` (relative to the repo)."""
        put(self.stryker_control, json.dumps(reports))
        stub = self.path / "node_modules" / ".bin" / "stryker"
        put(stub, STRYKER_STUB.format(python=sys.executable, repo=str(self.path), log=str(self.stryker_log),
                                      control=str(self.stryker_control)))
        stub.chmod(0o755)

    def mutmut(self) -> None:
        """Put the stub `mutmut` first on `PATH`."""
        stub = self.case.root / f"{self.path.name}-bin" / "mutmut"
        put(stub, MUTMUT_STUB.format(python=sys.executable, log=str(self.mutmut_log)))
        stub.chmod(0o755)
        self.env["PATH"] = f"{stub.parent}{os.pathsep}{self.env.get('PATH', '')}"

    def stryker_calls(self) -> list[dict]:
        if not self.stryker_log.exists():
            return []
        return [json.loads(line) for line in self.stryker_log.read_text().splitlines()]

    def mutmut_calls(self) -> list[list[str]]:
        if not self.mutmut_log.exists():
            return []
        return [json.loads(line) for line in self.mutmut_log.read_text().splitlines()]

    def run(self) -> tuple[int, dict, str]:
        """`devloop.py mutation --repo <repo> --head feat --run`: exit, the stdout JSON object, the output text."""
        result = subprocess.run([sys.executable, str(DEVLOOP), "mutation", "--repo", str(self.path),
                                 "--head", "feat", "--run"], cwd=self.path, env=self.env, capture_output=True,
                                text=True, timeout=300)
        text = f"exit {result.returncode}\nstdout:\n{result.stdout[-3000:]}\nstderr:\n{result.stderr[-3000:]}"
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise AssertionError(f"stdout is not one JSON object:\n{text}")
        return result.returncode, payload, text


def entry_of(payload: dict, path: str) -> dict:
    found = [item for item in payload.get("entries") or [] if item.get("path") == path]
    if len(found) != 1:
        raise AssertionError(f"not one entry {path!r} in {json.dumps(payload)[-3000:]}")
    return found[0]


class HeldOutCase(unittest.TestCase):
    """A scratch folder (removed after the test) with a home and a clean environment."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="held-out-exclusion-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = clean_env(self.home)


def failure_lines(output: str) -> list[str]:
    lines = output.splitlines()
    heads = [n for n, line in enumerate(lines) if line.startswith("User actions required")]
    return [line for line in stripped("\n".join(lines[:heads[0]] if heads else lines)) if line.startswith("- ")]


def mutation_failures(output: str) -> list[str]:
    return [line for line in failure_lines(output) if line.startswith("- mutation ")]


def declared(output: str, text: str) -> list[str]:
    """The failure lines `- mutation entry <path>: <text>` (`text` ending in `: `: that prefix of the text)."""
    return [line for line in mutation_failures(output) if line.startswith("- mutation entry ")
            and (f": {text}" in line if text.endswith(": ") else line.endswith(f": {text}"))]


class SetupRepo(SetupProject):
    """The installed setup project; each `apply` writes files and `mutation`, commits on `main`, runs the check."""

    def setUp(self) -> None:
        super().setUp()
        self.runs = 0
        ignore = self.project / ".gitignore"
        put(ignore, (ignore.read_text() if ignore.exists() else "") + "\n" + IGNORED)
        for name, text in HELD_OUT_FILES.items():
            put(self.project / name, text)
        commit_all(self.project, self.env, "Held-out suite")

    def apply(self, files: dict[str, str], mutation: list[dict]) -> tuple[int, str]:
        for name, text in files.items():
            put(self.project / name, text)
        self.write_config(self.config_with(mutation=mutation))
        commit_all(self.project, self.env, "Change the setup")
        return self.check()

    def run_repo(self) -> RunRepo:
        """A new light run repo in the scratch folder, sharing this case's environment."""
        self.runs += 1
        return RunRepo(self, f"run-{self.runs}")
