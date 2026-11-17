# Test rules

Read before writing a test.

1. One home file per module. A function's tests live in one file, found by name.
2. Public behaviour only. No test reads private state or calls a private helper.
3. One table per function. Cases are rows, not new test functions. Table rows call the function in-process, at its seam. A behaviour keeps at most 3 end-to-end rows (CLI or hook): one per distinct wiring, then more on one wiring up to 3 in total; a behaviour with more than 3 wirings keeps one per wiring. A wiring is one entry point (a hook file or a CLI) run by one harness and one caller kind.
4. No change-detector tests. A test that only mirrors the code fails on every edit and proves nothing.
5. One golden per output. A rendered or serialised output has one golden file.
6. Cost markers: `proof` for a test that proves a claim; the slow mark, the word `slow` in the test id (case ignored, a whole word between non-letters: `test_slow_import`, `slow: import`), for a test over the cheap budget. Time ratchet of `gate --summary` (limits `test_time.gate` in `dev-loop.json`): a new test over 2 s without the mark, a new test over 10 s with it, a new or grown test over 30 s, or suite growth over 3%: red. Baseline: a record of a `main` tree, written by a clean full gate in this clone only: a new clone or machine gets `no-baseline` until a Stage 7 gate of a merged branch writes one.
7. A mutation fix test proves the kill: commit it only when a rerun on the listed units (`devloop.py mutation --entry <path> --only <unit> ...`) lists fewer mutants under its unit and no more inconclusive mutants.
8. A test budget per plan task: the plan states the number of tests. Stay inside it.
9. Synthetic fixtures for moved acceptance details. A detail moved out of `tests/acceptance` gets a small made-up fixture in its unit test, never a copy of production data.
10. A growth report on each PR.
11. No oracle copy. A unit test does not repeat a held-out test. The implementer cannot see the held-out suite, so the Stage 6 oracle copy step removes the copies (section Oracle copy step).
12. One stub per process. A test writes each stub (a fake executable) once per process and reuses it, never one new executable per row. On macOS you may turn on the Developer Tools exemption for the terminal and the agent app (System Settings, Privacy and Security, Developer Tools): without it, the first exec of each new file costs 0.3 to 0.8 s. The trade-off: processes the listed apps start skip the macOS Gatekeeper and XProtect first-run checks, so a binary the agent downloads or builds runs unscanned. One stub per process already avoids most of the cost.

Plus: tests only at the seams the spec names.

URL host checks: in a test, check a URL host with a prefix that ends in `/`, a regex, or a parsed host. A bare prefix such as `https://example.com` also matches `https://example.com.evil.net`, and CodeQL flags it.

## Seams

Source: Pocock skills v1.3.2 `codebase-design` (`skills/engineering/codebase-design/SKILL.md` and `DEEPENING.md`, commit `d1478fc`). License: MIT, [spec.md](spec.md#licenses).

13. The interface is the test surface. Callers and tests cross the same seam. A test that must reach past the interface means the module has the wrong shape.
14. Replace old tests, do not layer new ones. Once tests exist at the deepened interface, delete the old tests on the shallow modules.
15. One adapter is a hypothetical seam, two adapters are a real one. Add a seam only when something varies across it, typically production and test.

## Verification budget

The kit enforces no time cap: a hook the harness kills is a non-blocking error, so a hard cap could let a red gate pass. Each project caps T0 in its own `gate.sh`.

| Tier | When | Budget | Content |
|---|---|---|---|
| T0 | Stop hook, each turn | 60 s or less, never falls back to the full gate | lint, types, impacted tests without `proof` or `slow` markers |
| T1 | End of a stage, direct `gate.sh` | 4 min or less | full fast tier, writes the green record |
| T2 | CI on each PR | 8 min or less, jobs split | fast tier, proof tier, UI |
| T3 | CI, feature gate | API 11 min, Playwright 9 min per shard | held-out acceptance, flows only |
| T4 | Nightly | about 60 min | mutation on critical modules, test-value audit, flake statistics |

## Oracle copy step

Stage 6, Tracks M and L with spec `acceptance` empty or `regression`: the last step, after the acceptance loop is green and after the closing drive, before the `End:` ledger line. Spec `acceptance: none`: skipped, line `oracle copies: skipped: acceptance none`. Track S: no step, no line. The step changes only unit test files, never a product or mapped path: no new acceptance run and no new drive after it. `Feature gate: green at <sha>` names HEAD after the step. A cleanup, never a stop item and never a fix round.

1. Scope. The unit tests new on the branch: the test functions and table rows that `git diff origin/main...HEAD` of the unit test files adds and that do not exist at the merge-base. A test the branch only edits stays out. None: line `oracle copies: skipped: no new unit test`.
2. Compare, controller. Read the new unit tests and the held-out suite. A unit row is an oracle copy only when one held-out test calls the same entry point with the same input and asserts the same outcome, at least as strict as the unit assertion, and the unit row reaches no branch the held-out suite does not reach. A weaker held-out assertion (`assertIn` where the unit test compares the exact value, fewer fields, no message text) keeps the unit row. A doubtful match is no copy: the row stays.
3. Log. Per copy, one `scripts/devloop.py decide --kind decision` row: `--what` the unit test id (and the row input for a table row), `--why` oracle copy, `--evidence` the held-out `file::test`. No copy: no `implementer`, no commit, line `oracle copies: 0 removed`.
4. Remove. One fresh `implementer`. Its brief names the unit test ids and the row inputs as the unit file writes them, never a held-out path, test name or content. A row it cannot remove alone (a fixture or helper other tests use) stays; it names that row in its `Discoveries`. Its commit body names the removed unit tests and points to the spec `## Decision log` by path, without quoting its rows (the `implementer` does not read the log). A new `weakened` item the removal causes (`assertions-removed`) gets a quoted reason in the commit body: the removed rows are oracle copies, as triage does. Then `scripts/devloop.py gate --summary` exits 0.
5. Gate red after the removal: the `implementer` puts back the rows the failure names and runs the gate again. The put-back is a new commit. Still red: the controller runs `git revert` of the put-back commit and of the removal commit, newest first, then `scripts/devloop.py gate --summary` must exit 0 before the controller logs a `decide --kind discovery` row; the line reads `oracle copies: skipped: gate red`.

Report line, the seventh, after `drive:`: `oracle copies: <n> removed` (`<n>`: the rows removed at the end), or `oracle copies: skipped: <reason>` (`acceptance none`, `no new unit test`, `gate red`). Never a fail line: a copy left in place is no defect.
