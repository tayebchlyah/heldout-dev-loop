# AGENTS.md

## Purpose

dev-loop: stage-driven development loop skill for Claude Code and Codex, plus installer. Tracks S, M, L. Shaping: Stages 1, 2, to spec approval. Delivery: Stages 3 to 7, autonomy grant, same session. Track S: no spec, no plan. `status`: stage.

## Repo map

Dogfood: repo runs dev-loop on itself, installed kit pinned to the latest tag, release or pre-release (`dev-loop.json` `version`). Never edit installed kit (`.agents/skills/` and `.claude/skills/` `dev-loop`, `pause`, `continue`; `.claude/agents/`, `.codex/agents/`, `.codex/rules/`, `.dev-loop/`): changes only by `upgrade` to the next tag. Kit source: `skill/`, `skills/`, `claude/`, `codex/` (installer: `bin/`, `lib/`).

- `bin/install.mjs`: installer entry point (`init`, `upgrade`, `uninstall-global`).
- `lib/`: installer internals; `lib/codex_fill.py`: fill, check of Codex kit copy, in each Codex hook.
- `skills/`: kit skills `pause`, `continue`, `release-audit`, `garden`, `release`, `release-notes` (user-invoked only, except `continue`).
- `skill/SKILL.md`: the dev-loop skill payload.
- `skill/references/`: harness routes (`claude.md`, `codex.md`), `bootstrap.md` (Stage 0), `building.md`, `coding-standards.md` (template), `tests.md`, `spec.md`, `mutation.md`, `benchmark.md`, `maps.md`, `verify.md`, `deferred.md`; `skill/references/second-review.md`: second reviewer prompt.
- `skill/scripts/devloop.py`: `devloop` CLI: parser, dispatch, shared git helpers.
- `skill/scripts/status.py`: `status`; `merge_check.py`: `merge-check`; `cleanup.py`: `cleanup`; `gate.py`: `logged`, `gate`; `ratchet.py`: time ratchet.
- `skill/scripts/check_setup.py`: post-install verifier; `security_skill.py`: its security-audit skill check; `capabilities.py`: its `capabilities` and `commands.acceptance` lines; `notes_path.py`: notes path resolver.
- `skill/scripts/source_dirs.py`: `source_dirs` key: validator, matcher, main ref read, setup lines; `source_wall.py`: `source-wall`; `stacks.py`: `stack-detect`, the `stacks` lines; `skill/presets.json`: build tool presets.
- `skill/scripts/autonomy.py`: `decide`, `pause`, pause note in `status`; `verify.py`, `drive.mjs`, `capture.py`: `verify-init`, `drive`, `capture`.
- `skill/scripts/ci.py`: `ci wait`, the GitHub settings check; `ci_skip.py`: `ci-skip`.
- `skill/scripts/metrics.py`: `metrics`; `retro.py`: `status` `review_due`, `sessions`.
- `skill/scripts/release.py`: `release-check`, `release-plan`, `release-record`, `release-notes`; `release_carry.py`: the `accept` carry; `release_rulings.py`: `## Deferred` parser; `sandbox-run.py`: audit sandbox (Docker).
- `skill/scripts/audit/`: `audit-run` package: one module per step, shared `common`, `skilltext`, `safefs`.
- `skill/scripts/mutation/`: `mutation`: `run.py` (selection, protocol, checks, report), built-in adapters `mutmut.py`, `stryker.py`, `mutago.py` (`adapter.py` shared), `setup.py` (its `check_setup.py` lines), `heldout.py` (held-out folder rules `setup.py` and adapters share).
- `skill/scripts/feature_gate.py`: `review-merge`, `review-save`, `acceptance`; `skill/scripts/second_review.py`: `second-review`.
- `skill/scripts/size.py`: `size-check`, the `seal` size refusal; `notes_migrate/`: `notes-migrate`: `links`, `plan`, `texts`, `apply`.
- `skill/scripts/seal/`: `seal`, `audit`, `seal-check`, `search-wall`, `skill-wall`, the `gate` `weakened` report; `board/`: progress board (`board/cli.py`: `board`).
- `claude/`: Claude Code payload: agents, hooks, `settings.fragment.json`.
- `codex/`: Codex payload: agents, hooks, `config.permissions.toml`, `hooks.fragment.json`, `rules/default.rules`.
- `tests/unit/python/`: skill suite, one file per subject. File index: `tests/unit/python/README.md` (new test file: its line there). No test file under `skill/`.
- `tests/unit/node/`: installer suite (`project-init`, `project-upgrade`, `project-hooks`, shared `helpers.mjs`), board render and `drive.mjs` tests.
- `tests/acceptance/`: held-out suite. Written only by `spec-tester`, through `devloop.py seal`. Run by `commands.acceptance` (CI job `acceptance`), never the gate. Implementers never read, list or run it.
- `scripts/gate.sh`: repo gate, node and skill suites at once; `GATE_SCOPE=changed`: `impact_map.py select` modules (`tests/impact-map.json`).
- `scripts/temp_leak.sh`, `scripts/suite_lock.sh`: leak rule of `gate.sh`, `acceptance.sh` (empty `TMPDIR`, leftover fails); one suite run at a time.
- `scripts/acceptance.sh [<folder>]`: held-out suite runner (`commands.acceptance`), leak guard.
- `scripts/canary.sh`: live canary before a release (this repo only). `scripts/mutation_canary.sh`, `mutation-canary.yml`: weekly adapter canary.
- `scripts/release-check.sh`, `scripts/release-verify.sh`: `commands.release_check` (canary, `promoted --sha`), `release_verify` (`npx` upgrade of `main`, no diff).
- `scripts/repo_rules.py`: repo checks `comments`, `subjects` (gitmoji), `promoted` (final tag, or `--sha` commit, ships last rc kit source, `package.json` beyond `version` included; no rc: skips).
- `scripts/upstream_watch.py`, `upstream.json`: weekly watch of outside sources dev-loop copies or invokes. `reference/upstream.md`
- `reference/cli/<command>.md`: full contract per `devloop` command.
- `reference/kit.md`: what `init` and `upgrade` write, MANIFEST rule, notes path, verify after install, upgrade notes.
- `reference/release.md`: the release steps.
- `GLOSSARY.md`: domain terms (Stage 0).
- `CODING_STANDARDS.md`: this repo's standards, `## Comments` equal to `skill/references/coding-standards.md`.
- `dev-loop.json`: this repo's marker, kit version.
- `assets/`: README logo, icon, loop diagram (`the-loop.archify.json` source).
- `docs/`: human guides `usage.md`, `configuration.md`, `commands.md`, `upgrade.md`.
- `LICENSE`: MIT; `skill/LICENSE.txt` identical kit copy. `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `.github/` issue and PR templates: community files.
- `.github/rulesets/`: `gh api` ruleset bodies, contexts `changes`, `test`, `acceptance`.

## devloop CLI

`python3 .agents/skills/dev-loop/scripts/devloop.py <command>`; a command or option newer than the installed tag (`invalid choice`, `unrecognized arguments`): `python3 skill/scripts/devloop.py <command>`. One JSON object on stdout. Exit 0 success, 1 failure JSON describes, 2 usage error.

Read `reference/cli/<command>.md` before you change, test or debug a command, or run a command or option whose usage you do not know: file holds usage, contract.

- `status`: `reference/cli/status.md`
- `logged`: `reference/cli/logged.md`
- `gate`: `reference/cli/gate.md`
- `seal`: `reference/cli/seal.md`
- `seal-check`: `reference/cli/seal-check.md`
- `search-wall`: `reference/cli/search-wall.md`
- `skill-wall`: `reference/cli/skill-wall.md`
- `source-wall`: `reference/cli/source-wall.md`
- `stack-detect`: `reference/cli/stack-detect.md`
- `mutation`: `reference/cli/mutation.md`
- `size-check`: `reference/cli/size-check.md`
- `verify-init`: `reference/cli/verify-init.md`
- `drive`: `reference/cli/drive.md`
- `capture`: `reference/cli/capture.md`
- `audit`: `reference/cli/audit.md`
- `board`: `reference/cli/board.md`
- `decide`: `reference/cli/decide.md`
- `pause`: `reference/cli/pause.md`
- `second-review`: `reference/cli/second-review.md`
- `review-merge`: `reference/cli/review-merge.md`
- `review-save`: `reference/cli/review-save.md`
- `acceptance`: `reference/cli/acceptance.md`
- `ci`: `reference/cli/ci.md`
- `merge-check`: `reference/cli/merge-check.md`
- `cleanup`: `reference/cli/cleanup.md`
- `metrics`: `reference/cli/metrics.md`
- `sessions`: `reference/cli/sessions.md`
- `release-check`: `reference/cli/release-check.md`
- `ci-skip`: `reference/cli/ci-skip.md`
- `release-notes`: `reference/cli/release-notes.md`
- `release-plan`: `reference/cli/release-plan.md`
- `notes-migrate`: `reference/cli/notes-migrate.md`
- `release-record`: `reference/cli/release-record.md`
- `audit-run`: `reference/cli/audit-run.md`

## Build and test

```bash
npm ci && npx playwright install chromium   # once per worktree (Chromium once per machine): the node suite drives a real browser
npm test                                    # node suite: installer, board render and drive.mjs tests
python3 tests/unit/python/test_dev_loop.py  # skill suite
```

Skill suite: ids in chunks of 5 per `test_dev_loop.py --chunk` process, `DEVLOOP_TEST_JOBS` at once (default per CPU; `1`: serial). `DEVLOOP_REPORT_DIR`: JUnit `skill.xml` there; dead chunk errors its ids. Test name arg: plain unittest, no report. `DEVLOOP_TEST_MODULES`: those modules (comma/space list).

`scripts/gate.sh` (both suites) fails with `run npm ci && npx playwright install chromium` without `node_modules/playwright`; new worktree (Stage 3) runs it first. Both pass before commit. `.github/workflows/ci.yml`: job `changes` (`ci-skip`: release, kit upgrade, inert-path PR skips suites), `hygiene` (on a skip), `test` (both suites; aggregates shards, `hygiene`), `acceptance` (held-out suite), on each PR and `main`. Merge only both green/skipped.

## Project kit

- Kit files never edited in a project: change source here, run `upgrade`.
- The walls deny a file-tool edit of a kit file; `seal-check` reports any other route as `kit-changed`.
- Kit table, MANIFEST rule, notes path, verify after install (R10), upgrade notes: `reference/kit.md`.

## Conventions

- Data rule: no smaller wave, no removal of the Track L threat model and no second review pass until 5 features have a complete section in Review Yield.md (`$NOTES/Review Yield.md`).
- Cost data rule: no process cut for cost (drop reviewer, no `tdd`, shorter spec stages) until 5 features have a row with no `?` stage cell in `$NOTES/Feature Metrics.md` (`metrics --write`, Stage 7).
- Commits: gitmoji, format `<emoji>(<scope>) #<issue>: <message>`, scope required.
- Issues: feature or fix starts from a GitHub issue, opened before the branch; commit subjects name it (`#<issue>:`). PR body: `Closes #<issue>`.
- Changelog: `release_notes` `written`: prose `CHANGELOG.md` by `/release-notes`. `cliff.toml`: kept for `repo_rules.py subjects`. Releases tag-driven (`vX.Y.Z`; `-rc.N`: pre-release) via `.github/workflows/release.yml`: version check (tag = `package.json`), `promoted`, `release-notes`, `ncipollo/release-action`.
- Release: `reference/release.md`. Stage 7 after merge: report line `rc: now|later: <reason>`.
- Comments: `scripts/repo_rules.py comments` fails a new comment line, private docstring or trailing `.sh` comment `CODING_STANDARDS.md` disallows; mark one an outside dependency forces with `allow-comment: dependency`.
- Commit subjects: `scripts/repo_rules.py subjects` fails on a bad `HEAD` subject (fix: `git commit --amend`). `warnings` (older commits): reword at Stage 7 rebase.
- No tracked file holds an absolute home path: write a `~` path, never its expansion. Owner's name only as handle `tayebchlyah` (guard test, case-sensitive).
- No tracked file names an internal client or project (employer, customers, private ones): notes folder `Internal Names.md`, one `- <name>` line each; `tests/unit/node/payload.test.mjs` reads it (skipped: no notes path/note). New name: there only.
- The 3 merged config files (`.claude/settings.json`, `.codex/hooks.json`, `.codex/config.toml`): merged, never copied whole.

## Gotchas

- Repo `tayebchlyah/heldout-dev-loop`, public from 3.0.0: badges and links allowed. Kit names stay `dev-loop` (`dev-loop.json`, the skill, `.dev-loop/`, `devloop.py`).

## Working principles

- Say your assumptions out loud. If two readings of a request lead to different work, ask before building. If a simpler approach exists, say so.
- Write the minimum code that solves the problem. No speculative abstractions, no unrequested flexibility, no error handling for impossible cases.
- Change only what the request needs. Match the surrounding style. Mention unrelated dead code rather than deleting it. Remove only the orphans your own change created.
