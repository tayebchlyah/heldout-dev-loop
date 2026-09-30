# Mutation step

Read this file when the feature gate reaches its mutation step, or when Stage 0 sets up mutation testing. Both harness routes use it.

## Run

In the worktree detached to `GATE_SHA`, from the repository root, run one command:

```
python3 ~/.agents/skills/dev-loop/scripts/select_mutants.py --head GATE_SHA --run
```

The script computes the selection from the diff `merge-base(main, GATE_SHA)..GATE_SHA`, runs the tools on that selection only, and prints the report. Return the report as the step's finding table. The same diff always gives the same selection, so the report is comparable between runs.

Exit codes:

- `0`: the run finished. The report lists the functions that need a unit test. "0 mutants selected", "hold no mutable code" and a feature that touches no critical module are passes.
- `2`: the mutation setup is missing or broken: no config, unit tests that fail inside mutmut's copy, forked workers that crash, or most mutants reaching no unit test. The message names the failure. Stop the step and record it as failed with that message. Stage 0 owns the repair, see [Setup](#setup).
- Any other code: the tool failed. Record the step as failed with the tool output.

## What the selection holds

Mutation runs only in **critical modules**: decision logic whose unit tests a regression could slip past. Stage 0 lists them, see [Setup](#setup). A changed file outside them is reported under "outside the critical modules" and gets no mutants.

- **Python, mutmut.** Every mutant inside a top-level function, or inside a method of a top-level class, whose lines the diff touches, in a file `only_mutate` matches and `do_not_mutate` does not. A deleted line counts as a touch of the line above it.
- **UI, Stryker.** Every mutant on a changed line of a non-test file the `mutate` list of `stryker.config.json` matches. Stryker has no function-level mode, so the unit is the changed line range.

## What the selection does not cover

The report lists these under "not covered by the selection", with file and lines. They are reviewer territory. Triage reads them as context and writes no mutation test for them.

- Module-level lines: constants, imports, top-level statements.
- Class-body lines outside a method, including every method of a nested class.
- Decorator lines, and whole decorated functions. mutmut skips every decorated function except one with a single `@staticmethod` or `@classmethod`.
- `__getattribute__`, `__setattr__` and `__new__`, which mutmut never mutates.
- UI only: unchanged lines inside a changed function.

## Report

The script classes every survivor from its diff, the same way on every run.

- **Decision survivor.** The mutant changes what the code decides or returns: a comparison, a condition, a boolean, a number, a returned value, or a string that code reads, such as a dict key or a compared value. The report lists its function.
- **No tests.** No unit test reaches the mutant. The report lists its function.
- **Message survivor.** The mutant changes only text a person reads: a string in a `raise`, an `assert` message, a logging call, or a message-named argument or variable (`detail`, `message`, `msg`, `description`, `help`, `reason`, `summary`). It goes to `## Deferred` and gets no test. Stryker: a surviving `StringLiteral`.
- **Inconclusive.** `timeout`, `suspicious`, `segfault`, `not checked`, and Stryker `Timeout` and `RuntimeError`. Reported, never counted as killed, never a test.

## Fix

The report ends with "functions that need one unit test", each with the mutants that justify it.

- Write **one unit test per listed function**, not one per mutant. The test pins the function's outputs, so it kills its decision survivors together. When a mutant's name does not show the gap, read its change with `mutmut show <name>`.
- Dispatch all the mutation tests at once, in parallel, one `implementer` per group of listed functions. Each implementer writes only its own new test file, changes no product code, and runs only that file.
- Run mutmut once per feature. A test written in the fix wave is accepted when its own file passes; mutmut does not run again to confirm the kill.

## Setup

Stage 0 owns this setup. `scripts/check_setup.py` verifies it and fails until it holds, so a repository bootstrapped before this rule gets it at its next Stage 0 run. The gate uses the setup as it finds it.

**Python stack.**

- Pin mutmut to an exact version in the dev dependency group: `mutmut==<version>`.
- Put `[tool.mutmut]` in the `pyproject.toml` of the package the tests import. Single stack: the root file. Two stacks: `backend/pyproject.toml`. mutmut names each mutant from the file path relative to the directory it runs in, and matches that name against the imported module at run time, so this directory is the only one that works.

  ```toml
  [tool.mutmut]
  source_paths = ["src"]
  only_mutate = ["src/<pkg>/analysis/*", "src/<pkg>/ingestion/facts.py"]
  pytest_add_cli_args_test_selection = ["tests/unit"]
  ```

  `only_mutate` lists the critical modules, as `fnmatch` globs relative to that directory: detectors, evaluators, verifiers, scoring, parsers, window and coverage logic. Leave out wiring, routes, persistence adapters, formatting and logging.

  The config holds only what stays true across features. The gate adds the per-feature selection at run time.
- Set `process_isolation = "forkserver"` when the unit tests import a C extension that starts threads, such as the Couchbase Python SDK. Under the default `fork`, those workers crash and mutmut labels the mutant `segfault`. The script stops with exit code `2` when more than half of the selected mutants crash. On macOS it also sets `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` for mutmut, because macOS aborts forked children otherwise.
- List in `do_not_mutate` every module that defines a `@runtime_checkable` Protocol. mutmut adds its trampoline attributes to the class body, they become Protocol members, and every `isinstance` check against the Protocol fails.
- Git-ignore `mutants/` in that package directory.
- Keep unit tests runnable from mutmut's copy at `<package>/mutants/`. A test finds a data file from its own `__file__` inside the package, not from the working directory and not through `parents[N]` up to the repository root. Deselect timing-sensitive tests with `--deselect` in `pytest_add_cli_args_test_selection`, because the trampolines slow every call.

**UI stack.**

- Pin `@stryker-mutator/core` and `@stryker-mutator/vitest-runner` to exact versions in the frontend `devDependencies`.
- Put one `stryker.config.json` in the frontend root, with `testRunner: "vitest"`, the Node Vitest config, and a `mutate` list of the critical files only: logic helpers such as value formatting, never screens, which the E2E tests cover. Stryker cannot run Vitest browser mode. The script reads that `mutate` list, so the file is JSON.
- Git-ignore `.stryker-tmp/` and `reports/`.
