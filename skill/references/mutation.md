# Mutation step

Read this file when the nightly mutation job runs, or when Stage 0 sets up mutation testing. Both harness routes use it. The full command contract: `reference/cli/mutation.md` of the dev-loop repository.

## Run

Mutation is a nightly job on `main`, not a gate step. From the repository root, run one command:

```
BASE=$(git rev-list -1 --before='24 hours ago' main); python3 .agents/skills/dev-loop/scripts/devloop.py mutation --base "${BASE:-$(git rev-list --max-parents=0 main | tail -n 1)}" --head main --run
```

Base: `main` 24 h ago. No commit that old (a repo younger than a day): the root commit (the oldest when `main` has several: one base, one value).

The command computes the selection from the diff `<base>..main`, runs each entry's adapter on that selection only, checks the output, and prints one JSON object whose `report` is the report text. The same diff always gives the same selection, so the report is comparable between runs.

Mutation is opt in. No `mutation` key in `dev-loop.json` means off: the report is `mutation: off` and the status `pass`. `check_setup.py` prints a `mutation is off` line under `User actions required` with a proposed value, never a failure. The proposal comes from the `stacks` key of `dev-loop.json` when present, else from the `stacks:` line of `AGENTS.md` and tool config (`[tool.mutmut]`, `stryker.config.json`, `go.mod`) in the repo root, top-level folders or `backend/<x>/`. A `mutation` value that is not valid gives status `setup`.

The `mutation` value is a list of entries, one per code unit: `{"path", "adapter", "critical"}` or `{"path", "command", "critical"}`. `adapter` is `mutmut`, `stryker` or `mutago`. `command` runs any other tool through the protocol below. `critical` lists globs relative to `path`.

## Protocol

Each entry with a selected file runs in its folder, one after the other: a built-in as the kit's Python on its adapter script, a `command` through `sh -c`. The kit writes stdin and closes it (an adapter may leave it unread). Stdout holds one JSON object and nothing else. Tool output goes to stderr, which the kit streams. No timeout. An adapter never reuses the results of an earlier run.

Stdin: `{"protocol": 1, "run", "base", "head", "files", "only"}`.

- `files`: each selected file, relative to the entry folder, with its changed line ranges `[[start, end], ...]` (1-based, inclusive).
- `run`: false means run no tool and return only `units`. The kit sends it before every `--only` rerun, to learn the units.
- `only`: null, or the units to mutate.

Stdout: `{"units": [<unit>], "mutants": [...], "not_covered": [...]}`.

- `units`: each unit of the selection, before `only`, computed from stdin and the head tree only. A unit is a non-empty string with no control character and no leading `-`.
- `mutants`: each has `unit` (one of `units`; with `only`, one of `only`), `file` (a key of `files`), `line` (integer, 1 or more), `status` (`killed`, `survived`, `no-tests` or `inconclusive`), `diff` (null, or rows joined by a newline, each row starting with `-`, `+` or a space, header rows removed) and an optional `detail` string (for example `timeout`).
- `not_covered`: each has `file` (a key of `files`), `start`, `end` (integers, `start <= end`) and a non-empty `reason`. Use it for a selected line the tool cannot mutate, and for "no mutable code".
- With `run` true, every file of `files` is named by a mutant or a `not_covered` entry. With `run` false, `mutants` and `not_covered` may be absent.
- Exit `0`: done. Exit `2`: setup missing or broken (the message on stderr). Any other exit: the tool failed.
- Forward rule: an adapter ignores a stdin key it does not know, and the kit ignores an output key it does not know. A new key never changes the meaning of an old one; a change that does goes with `protocol` 2. An adapter exits `2` on a `protocol` value it does not speak.

The kit checks the output. A broken rule makes the entry `setup`, with the fault as `cause`.

A project adapter is a script that follows this protocol, started through `command`. The integrity check watches the first word of the `command` and not the files the adapter reads (helper scripts, `conftest.py`): `sh ./adapt` and `cd x && ./adapt` leave the real script unwatched. The review of the fix PR is the backstop.

The kit compares `dev-loop.json` `mutation`, the adapter script and the tool config between the main ref, `--head` and the working tree, before each adapter call and after each entry. A difference makes the entry `failed`. A fix branch cannot weaken the run it is judged by.

## What the selection holds

Mutation runs only in **critical files**: decision logic whose unit tests a regression could slip past. Stage 0 lists them in `critical`, see [Setup](#setup). A changed file outside them is reported under "outside the critical modules" and gets no mutants.

- A changed file belongs to the deepest entry whose path holds it. It is selected with its changed line ranges (a deleted line counts as a change of the line above it) when a `critical` glob without `!` matches it and no `!` glob does.
- Globs are case-sensitive. `*` and `?` stay in one path segment. `**` matches zero or more segments.
- A line the adapter cannot mutate is listed under "not covered by the selection", with file, lines and a reason. Triage reads them as context and writes no mutation test for them.
- mutmut: a function or a method of a top-level class whose lines the diff touches. A leftover `only_mutate` in `[tool.mutmut]` still narrows the files. A changed file outside `only_mutate`, in `do_not_mutate`, outside `source_paths` or not `.py` is listed as not covered with that reason. Module-level lines, class-body lines outside a method, decorated functions (except one with a single `@staticmethod` or `@classmethod`) and `__getattribute__`, `__setattr__`, `__new__` are not covered.
- Stryker: a mutant that lies fully inside a changed range. A mutant that spans past a changed line is not run. The unit is the file.
- mutago: a Go function or method whose lines the diff touches.

## Report and statuses

The report starts with `base:` and `head:`, then one block per entry: `entry <path>: <adapter> <status>`, mutants run, units that need one unit test, message-only survivors, inconclusive, not covered by the selection, then the files outside the critical modules, then one `mutation: none for <path>` line for each `stacks` path that no entry holds.

The kit classes each survivor from its diff, the same way on every run.

- **Decision survivor.** The mutant changes what the code decides or returns: a comparison, a condition, a boolean, a number, a returned value, or a string that code reads, such as a dict key or a compared value (a string inside `[ ]`, or an operand of `==`, `!=`, `in`, `not in` or `is`). The report lists its unit.
- **No tests.** No unit test reaches the mutant. The report lists its unit.
- **Message survivor.** The mutant changes only text a person reads: Python, a string in a `raise`, an `assert` message, a logging call (`print(`, or a method of a receiver named `logging`, `logger`, `log`, `_logger`, `_log`, `LOG` or `LOGGER`; `math.log(` is not one), or a message-named argument or variable (`detail`, `message`, `msg`, `description`, `help`, `reason`, `summary`); JS and TS, a string in a `throw`, a `new Error(`, a `console` call or a message-named property; Go, a string in `errors.New`, `fmt.Errorf`, `panic` or a call of the `log` or `slog` package, including a `%w` to `%v` change in a `fmt.Errorf` row that differs only in a string literal. That change can break `errors.Is`: review such a Deferred item. A message survivor goes to `## Deferred` and gets no test. A surviving Stryker `StringLiteral` is no longer always a message: the classification decides.
- **Inconclusive.** `timeout`, `suspicious`, `segfault`, `not checked`, Stryker `Timeout`, `RuntimeError`, `CompileError` and a mutant with no test completed (`testsCompleted` 0, detail `no test completed`), mutago `errored` and `skipped`, and any status the kit adapter does not map. Reported, never counted as killed, never a test. A mutago timeout counts as killed: mutago reports no separate timeout status, so a hang that mutago stops is a kill.

Entry status: `setup` for exit 2, 126 or 127, or a broken output; `failed` for any other failure or an integrity difference; `survivors` when a unit has a `no-tests` mutant or a decision survivor; else `pass`. Message survivors and inconclusive mutants do not count. The run status is the worst entry status. Exit `0` for `pass` and `survivors`, `1` for `setup` and `failed`.

The nightly job fails on any run status but `pass`. A `setup` or `failed` status gets the `cause` of its entry in the record of the failed step. Stage 0 owns the repair of a `setup`, see [Setup](#setup).

## Fix

A nightly survivor is a Track S change. The report lists each unit that needs one unit test, with the mutants that justify it.

- Write **one unit test per listed unit**, not one per mutant. The test pins the unit's outputs, so it kills its decision survivors together. When a diff does not show the gap, read the mutant with the tool (`mutmut show <name>`).
- The test extends the home test file of the unit's module. It calls the module's public entry, never a private helper: for a private function, the public function that reaches it.
- Group the listed units and write the tests in parallel, one `implementer` per group. Each changes no product code and runs only its test file.
- Then rerun the entry on the listed units only, from the fix branch, with the `base:` of the report:

  ```
  python3 .agents/skills/dev-loop/scripts/devloop.py mutation --base <report base> --head HEAD --run --entry <path> --only <unit> ...
  ```

  Each `<unit>` is one name of the report list: a mutmut function such as `pkg.core.score.x_score`, a Stryker file such as `src/logic/score.ts`, a mutago `<file>:<function>` such as `core/score.go:Score`. `--entry` is the `path` of the entry. A name outside the selection exits `2`. Delete `mutants/` in the mutmut root first, so mutmut reuses no earlier result.
- Commit a test only when the rerun lists fewer mutants under its unit and no more inconclusive mutants. A test that kills no listed mutant is not committed.
- Run each tool once per night on the whole selection. The fix rerun covers the listed units only.

## Setup

Stage 0 owns this setup, after the user says yes to mutation. `scripts/check_setup.py` verifies each built-in setup and fails until it holds. The nightly job uses the setup as it finds it.

**mutmut.**

- Pin mutmut to an exact version in the dev dependency group: `mutmut==<version>`, in the entry's `pyproject.toml` or the repo root `pyproject.toml` (a uv workspace). The adapter runs mutmut in the entry folder through `uv run` or `poetry run` when `uv.lock` or `poetry.lock` is in the entry folder, else in the repo root; else as plain `mutmut`.
- Put `[tool.mutmut]` in the `pyproject.toml` of the package the tests import. Single stack: the root file. Two stacks: `backend/pyproject.toml`. mutmut names each mutant from the file path relative to the directory it runs in, and matches that name against the imported module at run time, so this directory is the only one that works.

  ```toml
  [tool.mutmut]
  source_paths = ["src"]
  pytest_add_cli_args_test_selection = ["tests/unit"]
  ```

  The config holds only what stays true across runs. The `critical` globs of the `mutation` entry choose the files; the job adds the selection at run time.
- Set `process_isolation = "forkserver"` when the unit tests import a C extension that starts threads, such as a database client SDK. Under the default `fork`, those workers crash and mutmut labels the mutant `segfault`. The adapter stops with exit code `2` when more than half of the selected mutants crash. On macOS it also sets `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` for mutmut.
- List in `do_not_mutate` every module that defines a `@runtime_checkable` Protocol. mutmut adds its trampoline attributes to the class body, they become Protocol members, and every `isinstance` check against the Protocol fails.
- Git-ignore `mutants/` in that package directory.
- Keep unit tests runnable from mutmut's copy at `<package>/mutants/`. A test finds a data file from its own `__file__` inside the package, not from the working directory and not through `parents[N]` up to the repository root. Deselect timing-sensitive tests with `--deselect` in `pytest_add_cli_args_test_selection`, because the trampolines slow every call.

**Stryker.**

- Pin `@stryker-mutator/core` and the runner plugin of the test runner to the same exact version in the entry's `devDependencies` (StrykerJS 10.1.1 is the tested pin). Run `npm ci` first. The adapter runs `node_modules/.bin/stryker` of the entry folder, else of the first parent folder up to the repo root that has it (a workspace root), never `npx`, and names `stryker.config.json` on its command line.
- Exactly one `stryker.config.json` in the entry folder, with: `testRunner` set to a runner but not `command`; `incremental` not `true`; `coverageAnalysis` not `"off"`. Vitest browser mode is not supported: use the Node Vitest config. A `thresholds.break` setting still reports the survivors.
- A file path with `{`, `}`, `*`, `?`, `,`, `!` or `@(`, `+(` is not mutated: it is listed as not covered with the reason `path not expressible in --mutate`. `[` and `]` in a route path work.
- Git-ignore `.stryker-tmp/` and `reports/`.

**mutago.**

- Go 1.26.6 or later. The first run installs mutago v2.10.42 with the checksum database on (the `go env` file is ignored for that install).
- The adapter refuses to run when `report.json`, `mutago-summary.json` or `mutago-agentic.json` already exists in the entry folder: mutago would overwrite it. Remove or rename the file.
- A mutago timeout counts as killed (see Report and statuses).

## Nightly CI

Stage 0 writes this workflow for a new repo with CI only, for example `.github/workflows/mutation.yml`, after a yes to mutation. An existing repo gets none.

- Trigger: a schedule, cron once a day, on `main`.
- One job. Check out `main` with full history. One toolchain step for each adapter in use: uv or Poetry (mutmut, with the dev dependencies), Node 22 with `npm ci` (Stryker), Go 1.26.6 (mutago). Then run the command of [Run](#run): `BASE=$(git rev-list -1 --before='24 hours ago' main); python3 .agents/skills/dev-loop/scripts/devloop.py mutation --base "${BASE:-$(git rev-list --max-parents=0 main | tail -n 1)}" --head main --run`.
- Save the `report` of its stdout to a file. Upload that report as an artifact, pass or fail.
- `survivors` exits `0`, so the job reads `.status` from the JSON and fails on any status but `pass`: `survivors` (a report line `<path> units that need one unit test: <n>` with `n` above 0), `setup` or `failed`.
