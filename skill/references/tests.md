# Test rules

Read before writing a test. Source: Dev Process Review, "Test Strategy".

1. One home file per module. A function's tests live in one file, found by name.
2. Public behaviour only. No test reads private state or calls a private helper.
3. One table per function. Cases are rows, not new test functions.
4. No change-detector tests. A test that only mirrors the code fails on every edit and proves nothing.
5. One golden per output. A rendered or serialised output has one golden file.
6. Cost markers: `proof` for a test that proves a claim, `slow` for a test over the cheap budget. The gate runs the unmarked set first.
7. A mutation fix test proves the kill: commit it only when a rerun shows the mutant killed.
8. A test budget per plan task: the plan states the number of tests. Stay inside it.
9. Synthetic fixtures for moved acceptance details. A detail moved out of `tests/acceptance` gets a small made-up fixture in its unit test, never a copy of production data.
10. A growth report on each PR: tests added, tests removed, run time change.

Plus: tests only at the seams the spec names.
