# Stage 0 Bootstrap

Read this file at Stage 0. Both harness routes use it. `SKILL.md` keeps the done check.

`old-notes-layout`: `notes-migrate` dry run first, the user confirms, then `--apply --plan <hash>`. Delivery never runs `notes-migrate`. See [references/notes-migrate.md](notes-migrate.md).
Invoke `new-repo-setup`. Apply the selected harness setup contract. Claude Code writes its `.claude/settings.json`. Codex writes its `.codex/config.toml` only when a project override is needed. Security review runs once per feature in Stage 6, not per Stop and commit.
`git init`, one commit. Invoke the route's domain-modeling skill for first terms of `GLOSSARY.md`.

## Units

Run `scripts/devloop.py stack-detect`. It matches the root, each top-level folder and each child of `backend/` against `presets.json` (12 build tools, first match wins) and proposes `stacks`, `capabilities`, `source_dirs` and the commands of each unit. Show the proposal; confirm it with the user. Then write in `dev-loop.json`: `"stacks"` (`[{"path": "." or "<folder>/", "preset": "<name>" or null}]`, these two keys only), `"capabilities"` (`ui`: web UI, `backend`: service, both, or `[]`; write the `stack-detect` proposal for `"capabilities"`, it starts from the `check_setup.py` proposal and adds `ui` from `package.json`, so there is one proposal to confirm) and `"source_dirs"` (the spec-tester wall).
Each `unknown` folder: ask the user. A code unit: a `stacks` entry (a preset or `null`) and a `source_dirs` entry. Code that is not a unit: a `source_dirs` entry only. No code: no entry.
A unit no preset fits: `"preset": null`. Read its build files, propose its lint, type check, unit test and JUnit commands, and confirm them with the user. They live in `scripts/gate.sh` only.
The `stacks:` line of the repo AGENTS.md, read by the mutation tools: `python` for a unit of the `python` family, `go` for a unit of the `go` family, `ui` when `capabilities` holds `ui`. Other families add no name.
Empty repo: the user picks a preset for each unit. Layout by `capabilities`: `ui` and `backend`: units `backend/` and `frontend/`, each with its preset `layout` in its folder (several backend languages: one unit per `backend/<service>/`). Else: one unit at the root, its preset `layout.init`, then `layout.dirs`. Acceptance tests once, at root `tests/acceptance/` (`api/` with `backend`, `e2e/` with `ui`). Root only: spec-tester writes it and cannot enter a `source_dirs` folder.
A unit added later: run `stack-detect`, then add its `stacks` entry, its `source_dirs` entry and its `scripts/gate.sh` unit in the same PR. With no `source_dirs` key, `check_setup.py` gives only the `source_dirs missing` line. The coverage line fires when `source_dirs` is present and misses a unit.

## Gate

Write `scripts/gate.sh`. `stack-detect` gives `commands` per unit, with `junit.files` and `junit.setup`. The preset `layout` (`init`, `dirs`) comes from `presets.json`, the kit file beside `SKILL.md`. Each file of `git diff --name-only HEAD` belongs to the deepest unit whose path holds it (`.` holds every file). It runs every unit with a file, each in its folder: `lint`, then `typecheck` (skip a null one), then the JUnit run: remove the files that `junit.files` matches, run `junit.command`, keep its exit, copy each match to `$DEVLOOP_REPORT_DIR/<unit>-<file name>` when `DEVLOOP_REPORT_DIR` is set. A failing step does not stop the other units: keep the worst exit over all units, and exit once, at the end, with it. `<unit>`: `root` for `.`, else the path without its final `/`, each `/` as `-`. Never E2E. It exports `core.fsmonitor=false` through `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_<n>`, `GIT_CONFIG_VALUE_<n>`, appended after the caller's entries, before its first git call (`check_setup.py` checks it, and skips it when `commands.gate` runs another command).
Add each unit's `junit.package` (a dev dependency, or the tool's install step) and make its `junit.setup` change. Also add each unit's lint, type check and test tools (the preset commands assume them) as dev dependencies.

## Services, verify, mutation, commands

`backend`: compose test profile with seeded fixtures, for Stage 6. Else skip.
Verify skill, `ui` in `capabilities`: `scripts/devloop.py verify-init --base-url <url>`, then follow [references/verify.md](verify.md): fill the skill, seed 3 to 5 map entries, prove it once. `"verify": false` in `dev-loop.json` opts out. `check_setup.py` fails on `ui` with no `verify` key.
Mutation setup per stack, as [references/mutation.md](mutation.md) section Setup states. `check_setup.py` verifies it. New repo with CI only: the nightly mutation workflow, as `mutation.md` section Nightly CI states.
Write the app CLI skeleton (acceptance run; `backend`: stack up, stack reset), mapped in `commands` of `dev-loop.json` (`commands.acceptance` required). The acceptance run writes JUnit XML into `$DEVLOOP_REPORT_DIR` (the standard `scripts/devloop.py acceptance` reads; not checked). `commands.stack_reset` must recreate the build service containers: compose keeps an unchanged one, with its old `Created` time.
New repo only: `CODING_STANDARDS.md` at the root, a copy of [references/coding-standards.md](coding-standards.md). Only the standards reviewer reads it.
Notes path: ask for it, the folder must exist. Write `{"notes": "<path>"}` to `dev-loop.local.json` in the main worktree. Add it to `.gitignore` when `git check-ignore` says it is not ignored.
