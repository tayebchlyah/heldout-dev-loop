# AGENTS.md

## Purpose

dev-loop: stage-driven development loop skill for Claude Code and Codex, plus its installer. Tracks S, M, L. Shaping: Stages 1, 2, to spec approval. Delivery: Stages 3 to 7, autonomy grant. Same session. Track S: no spec, no plan. `status` detects stage.

## Repo map

Dogfood: repo runs dev-loop on itself, installed kit pinned to the latest tag, release or pre-release (`dev-loop.json` `version`). Never edit installed kit (`.agents/skills/` and `.claude/skills/` `dev-loop`, `pause`, `continue`; `.claude/agents/`, `.codex/agents/`, `.codex/rules/`, `.dev-loop/`): changes only by `upgrade` to the next tag. Kit source: `skill/`, `skills/`, `claude/`, `codex/` (installer: `bin/`, `lib/`). Else edit normally.

- `bin/install.mjs`: installer entry point (`init`, `upgrade`, `uninstall-global`).
- `lib/`: installer internals: flag parsing, per-format merges, kit file list, MANIFEST, `init`, `upgrade`, `uninstall-global`.
- `skills/`: kit skills `pause`, `continue`, `release-audit`, `garden`, `release`, `release-notes` (user-invoked only, except `continue`).
- `skill/SKILL.md`: the dev-loop skill payload.
- `skill/references/`: harness routes (`claude.md`, `codex.md`), `building.md`, `coding-standards.md` (the `CODING_STANDARDS.md` template), `tests.md`, `spec.md`, `mutation.md`, `benchmark.md`, `maps.md`, `verify.md`.
- `skill/references/second-review.md`: second reviewer prompt.
- `skill/scripts/devloop.py`: `devloop` CLI: parser, dispatch, shared git helpers. Each command's code: the module of its line below.
- `skill/scripts/status.py`: `status`.
- `skill/scripts/merge_check.py`: `merge-check`.
- `skill/scripts/gate.py`: `logged`, `gate`.
- `skill/scripts/check_setup.py`: post-install verifier.
- `skill/scripts/notes_path.py`: notes path resolver.
- `skill/scripts/autonomy.py`: `decide`, `pause`, the pause note in `status`.
- `skill/scripts/verify.py`, `skill/scripts/drive.mjs`, `skill/scripts/capture.py`: `verify-init`, `drive`, `capture`.
- `skill/scripts/metrics.py`: `metrics`.
- `skill/scripts/ci.py`: `ci wait`, the GitHub settings check.
- `skill/scripts/ci_skip.py`: `ci-skip`, the rules `release-check` reuses.
- `skill/scripts/retro.py`: `status` `review_due`, `sessions`.
- `skill/scripts/release.py`: `release-check`, `release-plan`, `release-record`, `release-notes`.
- `skill/scripts/sandbox-run.py`: audit sandbox runner (Docker).
- `skill/scripts/feature_gate.py`: `review-merge`, `acceptance`.
- `skill/scripts/second_review.py`: `second-review`.
- `skill/scripts/size.py`: `size-check`, the `seal` size refusal.
- `skill/scripts/notes_migrate/`: `notes-migrate`: `links`, `plan`, `texts`, `apply`.
- `skill/scripts/seal/`: seal package: `seal`, `audit`, `seal-check`, the `gate` `weakened` report.
- `skill/scripts/board/`: progress board writer, server and page; `board/cli.py`: `board`.
- `claude/`: Claude Code payload: agents, hooks, `settings.fragment.json`.
- `codex/`: Codex payload: agents, hooks, `config.permissions.toml`, `hooks.fragment.json`, `rules/default.rules`.
- `tests/unit/python/`: skill suite, one file per subject:
  - `test_dev_loop.py`: the entry: loads the files below, in chunks.
  - `test_devloop.py`: `logged`, `gate`, the project config shape, CLI test helpers.
  - `test_status.py`, `test_merge_check.py`, `test_merge_binding.py`: `status`, `merge-check`.
  - `test_skill_text.py`, `test_review_text.py`, `test_kit_contracts.py`: kit text contracts.
  - `test_check_setup.py`, `test_setup_checks.py`: `check_setup.py`.
  - `test_notes_path.py`, `test_mutation.py`: `notes_path.py`; `select_mutants.py`, mutation setup and contract.
  - `test_hooks.py`, `test_seal.py`: board hook launch, Claude gate hook; `seal`, `audit`.
  - `test_seal_check.py`: `seal-check`, `kit-changed`, `weakened`.
  - `test_size.py`: `size-check`, seams, size in `gate`, `seal`.
  - `test_seal_hooks.py`: seal hooks, guard, spec-tester, Codex walls.
  - `test_kit_walls.py`: path, kit, `cd` walls, canary script, Codex trust warning.
  - `test_walls.py`, `test_git_guard.py`, `test_push_spellings.py`, `test_gh_guard.py`: deny walls, Codex hooks, git and gh guard.
  - `test_board_robust.py`: board robustness.
  - `test_notes_migrate.py`, `test_notes_migrate_apply.py`: `notes-migrate`.
  - `test_board_layout.py`, `test_board_tasks.py`: board folders, names; task state, subagents, hook filter.
  - `test_verify.py`, `test_capture.py`: `verify-init`, `drive`, verify checks; `capture`.
  - `test_second_review.py`: `second-review`.
  - `test_release.py`, `test_release_record.py`: `release-check`, `release-plan`, `release-notes`; `release-record`.
  - `test_release_guard.py`: release guards, setup, kit, text.
  - `test_sandbox_run.py`: `sandbox-run.py` (real Docker; none: skip, `DEVLOOP_REQUIRE_DOCKER=1` fails, set in CI).
  - `test_metrics.py`, `test_ci.py`, `test_ci_skip.py`: `metrics`; `ci wait` (stub `gh` on `PATH`), `ci-skip`.
  - `test_kit_reference.py`: `kit_reference`, `kit_matches`, kit-source flows.
  - `test_gardener.py`: `garden` skill, kit text, harness names.
  - `test_retro.py`, `test_feature_gate.py`: `review_due`, `sessions`; `review-merge`, `acceptance`.
  - `test_upstream_watch.py`, `test_autonomy.py`: upstream watch; `decide`, `pause`, autonomy hooks.
  - `test_repo_rules.py`: `repo_rules.py`, `AGENTS.md` budget, CLI index, gate scope.
  - `junit_report.py`: the one JUnit writer.
  - `board_fixtures.py`: board fixtures shared with the held-out oracle.
  - `support.py`: shared constants and helpers of this suite.
  - `fixtures/`: test data. No test file under `skill/`.
- `tests/unit/node/`: installer suite (`project-init`, `project-upgrade`, `project-hooks`, shared `helpers.mjs`), board render and `drive.mjs` tests (Node test runner, real Chromium).
- `tests/acceptance/`: held-out suite. Written only by `spec-tester`, through `devloop.py seal`. Run by `commands.acceptance` (CI job `acceptance`), never the gate. Implementers never read, list or run it.
- `scripts/gate.sh`: repo gate, node and skill suites side by side in an empty `TMPDIR`, a leftover fails it; `GATE_SCOPE=changed` picks the suites by path.
- `scripts/acceptance.sh [<folder>]`: held-out suite runner (`commands.acceptance`).
- `scripts/canary.sh`: live canary before a release (this repo only).
- `scripts/release-check.sh`, `scripts/release-verify.sh`: `commands.release_check` (canary, `promoted --sha`), `release_verify` (`npx` upgrade of `main`, no diff).
- `scripts/repo_rules.py`: repo checks `comments` (allowed comments), `subjects` (gitmoji), `promoted` (final tag, or `--sha` commit, ships last rc kit source), this repo only.
- `scripts/upstream_watch.py`, `upstream.json`: weekly watch of outside sources dev-loop copies or invokes (this repo only). `reference/upstream.md`
- `reference/cli/<command>.md`: full contract per `devloop` command.
- `reference/kit.md`: what `init` and `upgrade` write, MANIFEST rule, notes path, verify after install, upgrade notes.
- `reference/release.md`: the release steps.
- `GLOSSARY.md`: domain terms (Stage 0).
- `CODING_STANDARDS.md`: this repo's standards, `## Comments` equal to `skill/references/coding-standards.md` (guard test).
- `dev-loop.json`: this repo's project marker, kit version.

## devloop CLI

`python3 .agents/skills/dev-loop/scripts/devloop.py <command>`. One JSON object on stdout. Exit 0 success, 1 failure the JSON describes, 2 usage error.

Read `reference/cli/<command>.md` before you change, test or debug a command, or use an option its index line does not name.

- `status [--repo <path>] [--feature <slug>]`: Detect the stage of a feature, or list the open features on `main`, with the next action. `reference/cli/status.md`
- `logged [--name <name>] -- <cmd>`: Run a command with its full output in a log file; give its exit, log path, time and tail. `reference/cli/logged.md`
- `gate --summary`: Run the project gate; report failures, slowest tests, seal check, weakened tests, size check. `reference/cli/gate.md`
- `seal --feature <slug> --message <subject> [--reverts <sha> ...]`: Commit the staged held-out tests with the seal trailers (`spec-tester` only); `seal --rebase-continue` in a rebase. `reference/cli/seal.md`
- `seal-check [--uncommitted]`: Check the branch seal commits, kit file changes and taint records. `reference/cli/seal-check.md`
- `size-check [--uncommitted | --all]`: File-size ratchet: a new or grown file over 40,000 bytes fails, with a split seam. `reference/cli/size-check.md`
- `verify-init --base-url <url> [--name <skill>]`: Write the project verify skill, feature map README and `verify` key. `reference/cli/verify-init.md`
- `drive [--all | --since <sha> | --routes-from <file>]`: Drive the verify map routes the branch touches in Chromium; save evidence. `reference/cli/drive.md`
- `capture --write-after <file>`: Save the after evidence to a file, no gh; `--pr <n>` puts before and after shots and videos in the PR. `reference/cli/capture.md`
- `audit`: SubagentStop hook: record a taint when a subagent read files it must not read. `reference/cli/audit.md`
- `board [--repo <path>] [--event session-start|prompt|stop|subagent-start|subagent-stop|post-tool] [--claim <slug>] [--no-serve] [--stop-server]`: Write the board notes, run the board server. `reference/cli/board.md`
- `decide --kind decision|discovery --what <text> --why <text> --evidence <text> [--result <text>] [--risk] [--feature <slug>] [--repo <path>]`: Append a row to the decision log. `reference/cli/decide.md`
- `pause [--event] [--intent <t>] [--verified <t>] [--open <t>] [--next <t>] [--agent <id>=<task> ...] [--run <log> ...] [--repo <path>]`: Write the worktree pause note; `--clear` removes it. `reference/cli/pause.md`
- `second-review --harness claude|codex [--timeout <minutes>]`: Run the other family's CLI as second reviewer, correctness and security only. `reference/cli/second-review.md`
- `review-merge <reviewer>=<file> [...]`: Merge the Stage 6 reviewer tables by file and line. `reference/cli/review-merge.md`
- `acceptance [--failed]`: Run the held-out suite; read its JUnit, check stale images, apply progress guard. `reference/cli/acceptance.md`
- `ci wait [--pr <n>] [--timeout <min>]`: Wait for the PR verdict of local `HEAD`: pass, fail, conflicts, threads, blocked, timeout. `reference/cli/ci.md`
- `merge-check [--repo <path>] [--feature <slug>]`: The Stage 7 merge decision, `auto` or `human`, with its reasons. `reference/cli/merge-check.md`
- `metrics [--repo <path>] [--feature <slug>] [--write]`: Token cost per stage from Claude transcripts; `--write` adds the Feature Metrics row. `reference/cli/metrics.md`
- `sessions --since <days> [--repo <path>]`: List the Claude Code and Codex sessions of every worktree, for retro. `reference/cli/sessions.md`
- `release-check [--repo <path>] (--tag <name> | --sha <rev>)`: May a commit get a release tag: a current audit record and no open security item. `reference/cli/release-check.md`
- `ci-skip --base <rev> [--head <rev>] [--repo <path>] [--github-output]`: May CI skip this diff: only a release (version files, `CHANGELOG.md`) or a kit upgrade. `reference/cli/ci-skip.md`
- `release-notes --tag <tag> [--repo <path>] [--output <file>]`: Write the `CHANGELOG.md` section of a tag to `--output`: the GitHub Release body. `reference/cli/release-notes.md`
- `release-plan [--repo <path>] [--harness claude|codex] [--profile quick|standard|deep] [--budget <n>]`: Plan a release security audit run: folder, profile, budget, scope, sandbox. `reference/cli/release-plan.md`
- `notes-migrate [--repo <path>] [--apply [--plan <hash>]]`: Move an old notes layout to feature folders; dry run, then `--apply --plan <hash>`. `reference/cli/notes-migrate.md`
- `release-record --run <folder> [--base <sha>] [--harness claude|codex] [--repo <path>]`: Write a release audit result into the project note. `reference/cli/release-record.md`

## Build and test

```bash
npm ci && npx playwright install chromium   # once per worktree (Chromium once per machine): the node suite drives a real browser
npm test                                    # node suite: installer, board render and drive.mjs tests
python3 tests/unit/python/test_dev_loop.py  # skill suite
```

Skill suite: ids in chunks of 5 per `test_dev_loop.py --chunk` process, `DEVLOOP_TEST_JOBS` at once (default per CPU, `1`: serial). `DEVLOOP_REPORT_DIR` set: JUnit `skill.xml` there; a dead chunk errors its ids. Argument (test name): plain unittest, no report.

`scripts/gate.sh` (full gate, both suites) fails with `run npm ci && npx playwright install chromium` when `node_modules/playwright` is missing; a new feature worktree (Stage 3 start) runs it first. Both must pass before commit. `.github/workflows/ci.yml`: job `changes` (`ci-skip`: a release or kit upgrade PR skips the rest), `test` (both suites), `acceptance` (held-out suite), on every PR and `main`. Merge only when both are green or skipped.

## Project kit

- Kit files are never edited in a project: change the source here, then run `upgrade`.
- The walls deny a file-tool edit of a kit file; `seal-check` reports any other route as `kit-changed`.
- Kit table, MANIFEST rule, notes path, verify after install (R10), upgrade notes: `reference/kit.md`.

## Conventions

- Data rule: no smaller wave, no removal of the Track L threat model and no second review pass until 5 features have a complete section in Review Yield.md (`$NOTES/Review Yield.md`).
- Cost data rule: no process cut for cost (drop a reviewer, no `tdd`, shorter spec stages) until 5 features have a row with no `?` stage cell in `$NOTES/Feature Metrics.md` (`metrics --write`, Stage 7).
- Commits: gitmoji, format `<emoji>(<scope>) #<issue>: <message>`, scope required (e.g. `🐛(install) #12: Back up a replaced skill outside its skills folder`).
- Issues: each feature or fix starts from a GitHub issue, opened before the branch; each commit subject names it (`#<issue>:`). PR body: `Closes #<issue>`.
- Changelog: `release_notes` `written`: prose `CHANGELOG.md` by `/release-notes`. `cliff.toml` kept for `repo_rules.py subjects`. Releases tag-driven (`vX.Y.Z`, `vX.Y.Z-rc.N`: pre-release) via `.github/workflows/release.yml`: version check (tag equals `package.json`), `promoted`, `release-notes`, `ncipollo/release-action`.
- Release: `reference/release.md`.
- Stage 7 after a merge: report line `rc: now|later: <reason>`; rule in `reference/release.md`.
- Comments: `scripts/repo_rules.py comments` fails a new comment line of a kind `CODING_STANDARDS.md` disallows; mark one on behaviour an outside dependency forces with `allow-comment: dependency`.
- Commit subjects: `scripts/repo_rules.py subjects` fails on a bad `HEAD` subject (fix: `git commit --amend`). `warnings` (older commits): reword in the Stage 7 rebase, before push.
- No tracked file holds an absolute home path or the owner's personal name. Write `~/Projects/tchlyah/dev-loop`, never its expansion (guard test).
- No tracked file names an internal client or project (employer, its customers, private projects). `tests/unit/node/payload.test.mjs` holds only `sha256` (first 16 hex) of the lowercased name words joined by one space. New name: add hash to `INTERNAL_NAME_HASHES`, never the name.
- The 3 merged config files (`.claude/settings.json`, `.codex/hooks.json`, `.codex/config.toml`): merged, never copied whole.

## Gotchas

- Repo private. No anonymous reader: no badges, no links needing a public repo. `npx github:tchlyah/dev-loop` install needs Git or SSH credentials that reach it.

## Working principles

- Say your assumptions out loud. If two readings of a request lead to different work, ask before building. If a simpler approach exists, say so.
- Write the minimum code that solves the problem. No speculative abstractions, no unrequested flexibility, no error handling for impossible cases.
- Change only what the request needs. Match the surrounding style. Mention unrelated dead code rather than deleting it. Remove only the orphans your own change created.
