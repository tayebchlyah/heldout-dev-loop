# Test rules

Read before writing a test.

1. One home file per module. A function's tests live in one file, found by name.
2. Public behaviour only. No test reads private state or calls a private helper.
3. One table per function. Cases are rows, not new test functions.
4. No change-detector tests. A test that only mirrors the code fails on every edit and proves nothing.
5. One golden per output. A rendered or serialised output has one golden file.
6. Cost markers: `proof` for a test that proves a claim, `slow` for a test over the cheap budget.
7. A mutation fix test proves the kill: commit it only when a rerun on the listed functions (`select_mutants.py --only`) shows it kills one or more listed mutants.
8. A test budget per plan task: the plan states the number of tests. Stay inside it.
9. Synthetic fixtures for moved acceptance details. A detail moved out of `tests/acceptance` gets a small made-up fixture in its unit test, never a copy of production data.
10. A growth report on each PR.

Plus: tests only at the seams the spec names.

## Seams

Source: Pocock skills v1.3.2 `codebase-design` (`skills/engineering/codebase-design/SKILL.md` and `DEEPENING.md`, commit `d1478fc`).

11. The interface is the test surface. Callers and tests cross the same seam. A test that must reach past the interface means the module has the wrong shape.
12. Replace old tests, do not layer new ones. Once tests exist at the deepened interface, delete the old tests on the shallow modules.
13. One adapter is a hypothetical seam, two adapters are a real one. Add a seam only when something varies across it, typically production and test.

## Verification budget

The kit enforces no time cap: a hook the harness kills is a non-blocking error, so a hard cap could let a red gate pass. Each project caps T0 in its own `gate.sh`.

| Tier | When | Budget | Content |
|---|---|---|---|
| T0 | Stop hook, each turn | 60 s or less, never falls back to the full gate | lint, types, impacted tests without `proof` or `slow` markers |
| T1 | End of a stage, direct `gate.sh` | 4 min or less | full fast tier, writes the green record |
| T2 | CI on each PR | 8 min or less, jobs split | fast tier, proof tier, UI |
| T3 | CI, feature gate | API 11 min, Playwright 9 min per shard | held-out acceptance, flows only |
| T4 | Nightly | about 60 min | mutation on critical modules, test-value audit, flake statistics |
