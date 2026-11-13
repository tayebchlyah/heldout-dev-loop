#!/usr/bin/env python3
"""Held-out oracle tests for mutation adapters (spec: 20261113 Mutation Adapters), the `check_setup.py` seam.

Seam 2: the `check_setup.py --harness claude` process of the kit source on a fixture repo, its stdout and exit code.
One test for flow F4, plus the setup oracle lines no flow holds; each docstring names the `## Acceptance oracle`
lines (O<n>) it checks.

Fixture choices the spec leaves open:
- The fixture repo is the Stack Model setup project (`stack_model_support.SetupProject`): installed, on `main`.
  Each case writes `dev-loop.json` and its files, then commits them on `main`, so the checkout and the main ref agree.
- A "mutation line" is a stripped stdout line that starts with `- mutation `. The proposal of the `mutation missing`
  line is the JSON value right after `add "mutation": `; words may follow it.
- Version pins sit in the entry `package.json` `devDependencies` (Stryker) and the entry `pyproject.toml` (mutmut).
"""

from __future__ import annotations

import json
import shutil
import unittest

from stack_model_support import SetupProject, commit_all, put, stripped

INVALID = "- mutation in dev-loop.json is invalid: "
MISSING = '- mutation missing in dev-loop.json: add "mutation": '
WORKFLOW = "name: nightly\non: workflow_dispatch\njobs:\n  mutate:\n    runs-on: ubuntu-latest\n    steps:\n" \
           "      - run: python3 .agents/skills/dev-loop/scripts/select_mutants.py --run\n"
MUTMUT_TOOL = '[tool.mutmut]\nsource_paths = ["src/"]\npytest_add_cli_args_test_selection = ["tests/unit"]\n'


def mutation_lines(output: str) -> list[str]:
    return [line for line in stripped(output) if line.startswith("- mutation ")]


def entry_lines(output: str, path: str) -> list[str]:
    return [line for line in stripped(output) if line.startswith(f"- mutation entry {path}: ")]


def proposal(output: str) -> object:
    found = [line for line in stripped(output) if line.startswith(MISSING)]
    if len(found) != 1:
        raise AssertionError(f"not one `mutation missing` line:\n{output[-3000:]}")
    value, _ = json.JSONDecoder().raw_decode(found[0][len(MISSING):])
    return value


class MutationSetup(SetupProject):
    def apply(self, files: dict[str, str | None] | None = None, drop: tuple[str, ...] = (),
              **changes: object) -> tuple[int, str]:
        """Write the files (None: remove) and the base config with `changes`, commit on `main`, run the check."""
        for name, text in (files or {}).items():
            path = self.project / name
            if text is None:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            else:
                put(path, text)
        self.write_config(self.config_with(drop=drop, **changes))
        commit_all(self.project, self.env, "Change the setup")
        return self.check()


class UpgradeFlowTests(MutationSetup):
    def test_f4_o28_o29_o30_upgrade_without_mutation_key(self) -> None:
        """F4, O30: no `mutation` key, no `stacks` key, no `stacks:` line, no tool config: the proposal is `false`.
        O28: `.github/workflows/m.yml` that names `select_mutants.py`: the line with `devloop.py mutation`. O29:
        `stacks` `[{"path": "backend/", "preset": "uv"}]` and `backend/pyproject.toml` `only_mutate =
        ["src/p/core/*"]`: the proposal is the `backend/` mutmut entry with that `critical`."""
        put(self.project / "AGENTS.md", "# AGENTS.md\n\nKettle works: boils water on request.\n")
        code, output = self.apply()
        with self.subTest(oracle=30):
            self.assertEqual(code, 1, output)
            self.assertIs(proposal(output), False, output)

        code, output = self.apply({".github/workflows/m.yml": WORKFLOW})
        with self.subTest(oracle=28):
            self.assertEqual(code, 1, output)
            lines = [line for line in stripped(output) if "select_mutants.py" in line and "m.yml" in line]
            self.assertTrue(lines, output[-3000:])
            self.assertTrue(all("devloop.py mutation" in line for line in lines), lines)

        pyproject = '[project]\nname = "p"\nversion = "0.1.0"\n\n[tool.mutmut]\nonly_mutate = ["src/p/core/*"]\n'
        code, output = self.apply({"backend/pyproject.toml": pyproject, "backend/uv.lock": "version = 1\n",
                                   "backend/src/p/core/m.py": "X = 1\n"},
                                  stacks=[{"path": "backend/", "preset": "uv"}], source_dirs=["src/", "backend/"])
        with self.subTest(oracle=29):
            self.assertEqual(proposal(output), [{"path": "backend/", "adapter": "mutmut",
                                                 "critical": ["src/p/core/*"]}], output[-3000:])


class RecordShapeTests(MutationSetup):
    def test_o23_invalid_mutation_value(self) -> None:
        """O23: `mutation` `{}`; path `../x/`; `critical` `[]`; `critical` `["!a"]`; both `adapter` and `command`;
        two entries with path `a/`: exit 1, the `mutation in dev-loop.json is invalid` line."""
        put(self.project / "a" / "core" / "m.py", "X = 1\n")
        good = {"path": "a/", "command": "sh x", "critical": ["core/**"]}
        cases = (("object", {}),
                 ("outside path", [{**good, "path": "../x/"}]),
                 ("empty critical", [{**good, "critical": []}]),
                 ("only negated critical", [{**good, "critical": ["!a"]}]),
                 ("adapter and command", [{**good, "adapter": "mutmut"}]),
                 ("duplicate path", [good, dict(good)]))
        for name, value in cases:
            with self.subTest(oracle=23, case=name):
                code, output = self.apply(mutation=value)
                self.assertEqual(code, 1, output)
                self.assertTrue([line for line in stripped(output) if line.startswith(INVALID)], output[-3000:])

    def test_o24_o25_adapter_command_and_glob(self) -> None:
        """O24: adapter `pit`: the `unknown adapter pit` line; `command` `./nope --x` with no `a/nope`: the
        `command not found: ./nope` line; `command` `sh x`: no such line. O25: critical `core/**` with no tracked
        file under `b/core/`: the `matches no tracked file` line."""
        files = {"a/core/m.py": "X = 1\n", "b/other.py": "Y = 1\n"}
        with self.subTest(oracle=24, adapter="pit"):
            code, output = self.apply(files, mutation=[{"path": "a/", "adapter": "pit", "critical": ["core/**"]}])
            self.assertEqual(code, 1, output)
            self.assertIn("- mutation entry a/: unknown adapter pit", stripped(output), output[-3000:])
        with self.subTest(oracle=24, command="./nope --x"):
            code, output = self.apply(mutation=[{"path": "a/", "command": "./nope --x", "critical": ["core/**"]}])
            self.assertEqual(code, 1, output)
            self.assertIn("- mutation entry a/: command not found: ./nope", stripped(output), output[-3000:])
        with self.subTest(oracle=24, command="sh x"):
            code, output = self.apply(mutation=[{"path": "a/", "command": "sh x", "critical": ["core/**"]}])
            self.assertEqual([line for line in mutation_lines(output) if "command not found" in line], [],
                             output[-3000:])
        with self.subTest(oracle=25):
            code, output = self.apply(mutation=[{"path": "b/", "command": "sh x", "critical": ["core/**"]}])
            self.assertEqual(code, 1, output)
            self.assertIn("- mutation entry b/: critical glob core/** matches no tracked file", stripped(output),
                          output[-3000:])


class ToolSetupTests(MutationSetup):
    def test_o26_o27_mutmut_pin_and_stryker_versions(self) -> None:
        """O26: adapter `mutmut`, `a/pyproject.toml` with `[tool.mutmut]` and no pin: a line that names `mutmut==`;
        a Poetry group `mutmut = "3.8.1"`: no such line. O27: adapter `stryker`, core `10.1.1` and
        `@stryker-mutator/jest-runner` `10.1.0`: a line that names both; the same version: no such line;
        `testRunner` `command`: a line that names `command`."""
        put(self.project / "a" / "src" / "m.py", "X = 1\n")
        mutmut = [{"path": "a/", "adapter": "mutmut", "critical": ["src/*"]}]
        unpinned = '[project]\nname = "a"\nversion = "0.1.0"\n\n' + MUTMUT_TOOL
        with self.subTest(oracle=26, pin="none"):
            code, output = self.apply({"a/pyproject.toml": unpinned}, mutation=mutmut)
            self.assertEqual(code, 1, output)
            self.assertTrue([line for line in entry_lines(output, "a/") if "mutmut==" in line], output[-3000:])
        poetry = ('[tool.poetry]\nname = "a"\nversion = "0.1.0"\n\n[tool.poetry.group.dev.dependencies]\n'
                  'mutmut = "3.8.1"\n\n' + MUTMUT_TOOL)
        with self.subTest(oracle=26, pin="poetry"):
            code, output = self.apply({"a/pyproject.toml": poetry, "a/poetry.lock": "# lock\n"}, mutation=mutmut)
            self.assertEqual([line for line in stripped(output) if "mutmut==" in line], [], output[-3000:])

        files = {"a/pyproject.toml": None, "a/poetry.lock": None, "a/src/m.py": None, "a/src/m.ts": "export {}\n"}
        stryker = [{"path": "a/", "adapter": "stryker", "critical": ["src/*"]}]
        for runner_version, runner in (("10.1.0", "jest"), ("10.1.1", "jest"), ("10.1.1", "command")):
            package = {"name": "a", "devDependencies": {"@stryker-mutator/core": "10.1.1",
                                                        "@stryker-mutator/jest-runner": runner_version}}
            files.update({"a/package.json": json.dumps(package) + "\n",
                          "a/stryker.config.json": json.dumps({"testRunner": runner, "mutate": ["src/*.ts"]}) + "\n"})
            code, output = self.apply(files, mutation=stryker)
            both = [line for line in entry_lines(output, "a/") if "core" in line and "jest-runner" in line]
            with self.subTest(oracle=27, runner=runner, version=runner_version):
                if runner == "command":
                    self.assertEqual(code, 1, output)
                    self.assertTrue([line for line in entry_lines(output, "a/") if "command" in line],
                                    output[-3000:])
                elif runner_version == "10.1.0":
                    self.assertEqual(code, 1, output)
                    self.assertTrue(both, output[-3000:])
                else:
                    self.assertEqual(both, [], output[-3000:])


if __name__ == "__main__":
    unittest.main()
