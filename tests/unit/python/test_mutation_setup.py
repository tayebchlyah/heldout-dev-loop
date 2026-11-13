#!/usr/bin/env python3
"""Tests for `mutation/setup.py`: the `mutation` lines of check_setup.py and the proposal when mutation is off."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import CHECKER, ROOT, checker_env, make_git_repo, write

from mutation import setup

MUTMUT = '[tool.mutmut]\nsource_paths = ["src"]\npytest_add_cli_args_test_selection = ["tests/unit"]\n'
PINNED = '[dependency-groups]\ndev = ["mutmut==3.8.1"]\n\n' + MUTMUT
CORE = "@stryker-mutator/core"
JEST = "@stryker-mutator/jest-runner"


def config(mutation: object = None, **extra: object) -> str:
    data: dict = {"version": "3.0.0", **extra}
    if mutation is not None:
        data["mutation"] = mutation
    return json.dumps(data)


def entry(path: str = "a/", critical: list[str] | None = None, **kind: str) -> dict:
    return {"path": path, **(kind or {"command": "sh x"}), "critical": ["*.py"] if critical is None else critical}


def mutation_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if "mutation" in line]


class MutationSetup(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.count = 0

    def repo(self, files: dict[str, str]) -> Path:
        self.count += 1
        return make_git_repo(self.root / str(self.count), files)

    def failures(self, mutation: object, files: dict[str, str] | None = None) -> list[str]:
        return setup.lines(self.repo({"dev-loop.json": config(mutation), "a/m.py": "x = 1\n", **(files or {})}))[0]

    def test_invalid(self) -> None:
        for value in ({}, [entry("../x/")], [entry(critical=[])], [entry(critical=["!a"])],
                      [{**entry(), "adapter": "mutmut"}], [entry(), entry()]):
            with self.subTest(value=value):
                found = self.failures(value)
                self.assertEqual(len(found), 1, found)
                self.assertTrue(found[0].startswith("mutation in dev-loop.json is invalid: "), found)

    def test_adapter_and_command(self) -> None:
        self.assertIn("mutation entry a/: unknown adapter pit", self.failures([entry(adapter="pit")]))
        self.assertIn("mutation entry a/: command not found: ./nope", self.failures([entry(command="./nope --x")]))
        self.assertEqual(self.failures([entry(command="sh x")]), [])
        self.assertEqual(self.failures([entry(command="./adapt")], {"a/adapt": "#!/bin/sh\n"}), [])

    def test_glob_matches_no_tracked_file(self) -> None:
        found = self.failures([entry(critical=["core/**", "*.py"])])
        self.assertEqual(found, ["mutation entry a/: critical glob core/** matches no tracked file"])

    def test_mutmut_setup(self) -> None:
        mutmut = [entry(adapter="mutmut")]
        ignore = {".gitignore": "mutants/\n"}
        self.assertEqual(self.failures(mutmut, {"a/pyproject.toml": PINNED, **ignore}), [])
        unpinned = self.failures(mutmut, {"a/pyproject.toml": MUTMUT, **ignore})
        self.assertEqual(len(unpinned), 1, unpinned)
        self.assertIn("mutmut==", unpinned[0])
        poetry = '[tool.poetry.group.dev.dependencies]\nmutmut = "3.8.1"\n\n' + MUTMUT
        self.assertEqual(self.failures(mutmut, {"a/pyproject.toml": poetry, **ignore}), [])
        for broken in (PINNED.replace('source_paths = ["src"]\n', ""), PINNED.replace("tests/unit", "tests/acceptance")):
            with self.subTest(broken=broken):
                self.assertEqual(len(self.failures(mutmut, {"a/pyproject.toml": broken, **ignore})), 1)
        not_ignored = self.failures(mutmut, {"a/pyproject.toml": PINNED})
        self.assertEqual(len(not_ignored), 1, not_ignored)
        self.assertIn("mutants/", not_ignored[0])

    def test_stryker_setup(self) -> None:
        stryker = [entry(adapter="stryker", critical=["*.ts"])]
        ignore = ".stryker-tmp/\nreports/\n"

        def files(core: str = "10.1.1", runner: str = "10.1.1", gitignore: str = ignore, **settings: object) -> dict:
            return {"a/m.ts": "x\n", ".gitignore": gitignore,
                    "a/stryker.config.json": json.dumps({"testRunner": "jest", **settings}),
                    "a/package.json": json.dumps({"devDependencies": {CORE: core, JEST: runner}})}

        self.assertEqual(self.failures(stryker, files()), [])
        apart = self.failures(stryker, files(runner="10.1.0"))
        self.assertEqual(len(apart), 1, apart)
        self.assertTrue(CORE in apart[0] and JEST in apart[0], apart)
        for label, case, word in [("command", files(testRunner="command"), "command"),
                                  ("two configs", {**files(), "a/stryker.conf.mjs": "export default {}\n"}, "stryker"),
                                  ("tmp", files(gitignore="reports/\n"), ".stryker-tmp/"),
                                  ("reports", files(gitignore=".stryker-tmp/\n"), "reports/"),
                                  ("incremental", files(incremental=True), "incremental"),
                                  ("coverage off", files(coverageAnalysis="off"), "coverageAnalysis")]:
            with self.subTest(label=label):
                found = self.failures(stryker, case)
                self.assertEqual(len(found), 1, found)
                self.assertIn(word, found[0])

    def test_mutago_setup(self) -> None:
        mutago = [entry(adapter="mutago", critical=["*.go"])]
        module = {"a/m.go": "package a\n", "a/go.mod": "module a\n"}
        bins = self.root / "bins"
        stub = bins / "go"

        def run(found: str | None, files: dict[str, str] = module) -> list[str]:
            stub.unlink(missing_ok=True)
            if found:
                write(stub, f"#!/bin/sh\necho 'go version {found} darwin/arm64'\n")
                stub.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": f"{bins}{os.pathsep}/usr/bin{os.pathsep}/bin"}):
                return self.failures(mutago, files)

        bins.mkdir()
        self.assertEqual(len(run("go1.26.6", {"a/m.go": "package a\n"})), 1)
        self.assertEqual(len(run("go1.26.5")), 1)
        self.assertEqual(len(run(None)), 1)
        self.assertEqual(run("go1.26.6"), [])
        self.assertEqual(run("go1.27.0"), [])

    def test_workflow_names_select_mutants(self) -> None:
        flow = {".github/workflows/m.yml": "run: python3 select_mutants.py --head HEAD\n"}
        line = ".github/workflows/m.yml runs select_mutants.py: use devloop.py mutation"
        for mutation in (None, [entry()]):
            with self.subTest(mutation=mutation):
                self.assertEqual(self.failures(mutation, flow), [line])

    def test_proposal(self) -> None:
        def notices(files: dict[str, str], mutation: object = None, **extra: object) -> tuple[list[str], list]:
            return setup.lines(self.repo({"dev-loop.json": config(mutation, **extra), **files}))

        def offer(value: list[dict]) -> str:
            return f'mutation is off: to turn it on, add "mutation": {json.dumps(value)} to dev-loop.json'

        backend = {"backend/pyproject.toml": MUTMUT + 'only_mutate = ["src/p/core/*"]\n'}
        self.assertEqual(notices(backend, stacks=[{"path": "backend/", "preset": "uv"}]),
                         ([], [(offer([{"path": "backend/", "adapter": "mutmut", "critical": ["src/p/core/*"]}]), [])]))
        web = {"web/stryker.config.json": json.dumps({"mutate": ["src/a.ts:1-5", "src/b/**/*.ts"]})}
        self.assertEqual(notices(web),
                         ([], [(offer([{"path": "web/", "adapter": "stryker", "critical": ["src/a.ts", "src/b/**/*.ts"]}]), [])]))
        go = {"api/go.mod": "module api\n", "AGENTS.md": "stacks: go\n"}
        self.assertEqual(notices(go),
                         ([], [(offer([{"path": "api/", "adapter": "mutago", "critical": []}]), ["list the critical files"])]))
        self.assertEqual(notices({"api/go.mod": "module api\n"}), ([], []))
        self.assertEqual(notices({"README.md": "x\n"}), ([], []))
        self.assertEqual(notices(backend, False), ([], []))

    def test_check_setup_process(self) -> None:
        tool = {"backend/pyproject.toml": MUTMUT + 'only_mutate = ["src/p/core/*"]\n'}
        offer = ('- mutation is off: to turn it on, add "mutation": [{"path": "backend/", "adapter": "mutmut", '
                 '"critical": ["src/p/core/*"]}] to dev-loop.json')

        def check(files: dict[str, str]) -> subprocess.CompletedProcess[str]:
            repo = self.repo({"dev-loop.json": config(), "README.md": "x\n", **files})
            return subprocess.run([sys.executable, str(CHECKER), "--harness", "claude", "--home", str(self.root / "home"),
                                   "--repo", str(repo)], capture_output=True, text=True, check=False,
                                  env=checker_env(self.root / "bin"))

        offered, plain = check(tool), check({})
        out = offered.stdout.splitlines()
        self.assertIn(offer, out)
        self.assertGreater(out.index(offer), out.index("User actions required (expected manual steps, not install defects):"))
        self.assertEqual(offered.returncode, plain.returncode)
        self.assertEqual(mutation_lines(plain.stdout.splitlines()), [])
        self.assertEqual(setup.lines(ROOT), ([], []))


if __name__ == "__main__":
    unittest.main()
