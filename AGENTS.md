# AGENTS.md

## Purpose

dev-loop: stage-driven development loop skill for Claude Code and Codex, plus its installer. Tracks S, M, L. Session A: Stages 1 and 2, stops at spec approval. Session B: Stages 3 to 7 chained under the autonomy grant. Track S: no spec, no plan. `devloop.py status` detects the stage.

## Repo map

Dogfood: this repo runs dev-loop on itself, installed kit pinned to the latest tag, release or pre-release (`dev-loop.json` `version`). Never edit installed kit (`.agents/skills/dev-loop/`, `.agents/skills/pause/`, `.agents/skills/continue/`, `.claude/agents/`, `.claude/skills/dev-loop`, `.claude/skills/pause`, `.claude/skills/continue`, `.codex/agents/`, `.codex/rules/`, `.dev-loop/`): changes only by `upgrade` to the next tag. Kit source: `skill/`, `skills/`, `claude/`, `codex/` (installer: `bin/`, `lib/`). Everything else in the repo is edited normally.

- `bin/install.mjs`: installer entry point (`init`, `upgrade`, `uninstall-global`).
- `lib/`: installer internals: flag parsing, per-format merges, kit file list, MANIFEST, `init`, `upgrade`, `uninstall-global`.
- `skills/`: the `pause`, `continue`, `release-audit` and `garden` kit skills (user-invoked only, except `continue`).
- `skill/SKILL.md`: the dev-loop skill payload.
- `skill/references/`: harness routes (`claude.md`, `codex.md`), `building.md`, `coding-standards.md` (the `CODING_STANDARDS.md` template: the comment rule), `tests.md`, `spec.md`, `mutation.md`, `benchmark.md`, `maps.md`, `verify.md`.
- `skill/references/second-review.md`: the second reviewer's prompt.
- `skill/scripts/devloop.py`: the `devloop` CLI: parser, dispatch, shared git helpers. Each command's code lives in the module of its line below.
- `skill/scripts/status.py`: `status`.
- `skill/scripts/merge_check.py`: `merge-check`.
- `skill/scripts/gate.py`: `logged`, `gate`.
- `skill/scripts/check_setup.py`: post-install verifier.
- `skill/scripts/notes_path.py`: notes path resolver.
- `skill/scripts/autonomy.py`: `decide`, `pause`, the pause note in `status`.
- `skill/scripts/verify.py`, `skill/scripts/drive.mjs`, `skill/scripts/capture.py`: `verify-init`, `drive`, `capture`.
- `skill/scripts/metrics.py`: `metrics`.
- `skill/scripts/ci.py`: `ci wait`, the GitHub settings check.
- `skill/scripts/retro.py`: `status` `review_due`, `sessions`.
- `skill/scripts/release.py`: `release-check`, `release-plan`, `release-record`.
- `skill/scripts/sandbox-run.py`: the audit sandbox runner (Docker).
- `skill/scripts/feature_gate.py`: `review-merge`, `acceptance`.
- `skill/scripts/second_review.py`: `second-review`.
- `skill/scripts/size.py`: `size-check`, the `seal` size refusal.
- `skill/scripts/seal/`: seal package: `seal`, `audit`, `seal-check`, the `gate` `weakened` report.
- `skill/scripts/board/`: progress board writer, server and page; `board/cli.py`: `board`.
- `claude/`: Claude Code payload: agents, hooks, `settings.fragment.json`.
- `codex/`: Codex payload: agents, hooks, `config.permissions.toml`, `hooks.fragment.json`, `rules/default.rules`.
- `tests/unit/python/`: skill suite, one file per subject:
  - `test_dev_loop.py`: the entry, loads every test class of each test file below; runs in parallel chunks.
  - `test_devloop.py`: `logged`, `gate`, the project config shape, CLI test helpers.
  - `test_status.py`, `test_merge_check.py`: `status`, `merge-check`.
  - `test_skill_text.py`, `test_review_text.py`, `test_kit_contracts.py`: kit text contracts.
  - `test_check_setup.py`, `test_setup_checks.py`: `check_setup.py`.
  - `test_notes_path.py`: `notes_path.py`.
  - `test_mutation.py`: `select_mutants.py`, mutation setup and contract.
  - `test_hooks.py`: board hook launch, Claude gate hook.
  - `test_seal.py`: `seal`, `audit`.
  - `test_seal_check.py`: `seal-check`, `kit-changed`, `weakened`.
  - `test_size.py`: `size-check`, seams, size in `gate` and `seal`.
  - `test_seal_hooks.py`: seal hooks, guard, spec-tester and Codex walls.
  - `test_kit_walls.py`: absolute path, kit and `cd` walls, canary script, Codex trust warning.
  - `test_walls.py`, `test_git_guard.py`: deny walls, Codex hooks, git guard.
  - `test_board_robust.py`: board robustness.
  - `test_board_tasks.py`: board task state, subagents, hook filter.
  - `test_verify.py`: `verify-init`, `drive`, verify checks.
  - `test_capture.py`: `capture`.
  - `test_second_review.py`: `second-review`.
  - `test_release.py`: `release-check`, `release-plan`.
  - `test_release_record.py`: `release-record`.
  - `test_release_guard.py`: release guards, setup, kit, text.
  - `test_sandbox_run.py`: `sandbox-run.py` (real Docker; skipped without it, a failure with `DEVLOOP_REQUIRE_DOCKER=1`, set in CI).
  - `test_metrics.py`: `metrics`.
  - `test_ci.py`: `ci wait` (stub `gh` on `PATH`).
  - `test_gardener.py`: the `garden` skill, kit text, harness names.
  - `test_retro.py`: `review_due`, `sessions`.
  - `test_feature_gate.py`: `review-merge`, `acceptance`.
  - `test_upstream_watch.py`: the upstream watch.
  - `test_autonomy.py`: `decide`, `pause`, autonomy hooks.
  - `test_repo_rules.py`: `scripts/repo_rules.py`, the `AGENTS.md` budget and CLI index, the gate scope.
  - `junit_report.py`: the one JUnit writer.
  - `board_fixtures.py`: board fixtures shared with the held-out board oracle.
  - `support.py`: shared constants and helpers of the skill suite.
  - `fixtures/`: test data. No test file under `skill/`.
- `tests/unit/node/`: installer suite (`project-init`, `project-upgrade`, `project-hooks`, shared `helpers.mjs`), board render and `drive.mjs` tests (Node test runner, real Chromium).
- `tests/acceptance/`: held-out suite. Written only by `spec-tester`, through `devloop.py seal`. Run by `commands.acceptance` (CI job `acceptance`), never by the gate. Never read, list or run it as an implementer.
- `scripts/gate.sh`: repo gate, node and skill suites side by side; `GATE_SCOPE=changed` picks the suites by path.
- `scripts/acceptance.sh [<folder>]`: held-out suite runner (`commands.acceptance`).
- `scripts/canary.sh`: live canary, run by hand before a release (this repo only).
- `scripts/repo_rules.py`: repo checks `comments` (allowed comment kinds) and `subjects` (gitmoji subjects), `promoted` (final tag ships its last rc kit source), this repo only.
- `scripts/upstream_watch.py`, `upstream.json`: weekly upstream watch of the outside sources dev-loop copies or invokes (this repo only). `reference/upstream.md`
- `reference/cli/<command>.md`: the full contract of each `devloop` command.
- `reference/kit.md`: what `init` and `upgrade` write, MANIFEST rule, notes path, verify after install, upgrade notes.
- `reference/release.md`: the release steps.
- `GLOSSARY.md`: domain terms (Stage 0).
- `CODING_STANDARDS.md`: this repo's coding standards, the `## Comments` section equal to `skill/references/coding-standards.md` (a guard test checks it).
- `dev-loop.json`: this repo's own project marker, kit version.

## devloop CLI

`python3 .agents/skills/dev-loop/scripts/devloop.py <command>`. One JSON object on stdout. Exit 0 success, 1 failure the JSON describes, 2 usage error.

Read `reference/cli/<command>.md` before you change, test or debug a command, or use an option that its index line does not name.

- `status [--repo <path>] [--feature <slug>]`: Detect the stage of a feature, or list the open features on `main`, with the next action. `reference/cli/status.md`
- `logged [--name <name>] -- <cmd>`: Run a command with its full output in a log file; give its exit, log path, time and tail. `reference/cli/logged.md`
- `gate --summary`: Run the project gate; report failures, slowest tests, the seal check, weakened tests and the size check. `reference/cli/gate.md`
- `seal --feature <slug> --message <subject> [--reverts <sha> ...]`: Commit the staged held-out tests with the seal trailers (`spec-tester` only); `seal --rebase-continue` in a rebase. `reference/cli/seal.md`
- `seal-check [--uncommitted]`: Check the seal commits of the branch, kit file changes and taint records. `reference/cli/seal-check.md`
- `size-check [--uncommitted | --all]`: File-size ratchet: a new or grown file over 40,000 bytes fails, with a seam to split at. `reference/cli/size-check.md`
- `verify-init --base-url <url> [--name <skill>]`: Write the project verify skill, the feature map README and the `verify` key. `reference/cli/verify-init.md`
- `drive [--all | --since <sha> | --routes-from <file>]`: Drive the verify map routes the branch touches in Chromium; save screenshots and evidence. `reference/cli/drive.md`
- `capture --write-after <file>`: Save the evidence after set to a file, no gh; `capture --pr <n>` puts the before and after shots and videos in the PR. `reference/cli/capture.md`
- `audit`: SubagentStop hook: record a taint when a subagent read files it must not read. `reference/cli/audit.md`
- `board [--repo <path>] [--event session-start|prompt|stop|subagent-start|subagent-stop|post-tool] [--claim <slug>] [--no-serve] [--stop-server]`: Write the progress board notes and run the local board server. `reference/cli/board.md`
- `decide --kind decision|discovery --what <text> --why <text> --evidence <text> [--result <text>] [--risk] [--feature <slug>] [--repo <path>]`: Append one row to the decision log of the feature. `reference/cli/decide.md`
- `pause [--event] [--intent <t>] [--verified <t>] [--open <t>] [--next <t>] [--agent <id>=<task> ...] [--run <log> ...] [--repo <path>]`: Write the pause note of the worktree; `pause --clear` removes it. `reference/cli/pause.md`
- `second-review --harness claude|codex [--timeout <minutes>]`: Run the other family's CLI as a second reviewer of the branch, correctness and security only. `reference/cli/second-review.md`
- `review-merge <reviewer>=<file> [...]`: Merge the Stage 6 reviewer tables by file and line. `reference/cli/review-merge.md`
- `acceptance [--failed]`: Run the held-out suite; read its JUnit, check stale images, apply the progress guard. `reference/cli/acceptance.md`
- `ci wait [--pr <n>] [--timeout <min>]`: Wait for one PR verdict for the local `HEAD`: pass, fail, conflicts, threads, blocked or timeout. `reference/cli/ci.md`
- `merge-check [--repo <path>] [--feature <slug>]`: The Stage 7 merge decision, `auto` or `human`, with its reasons. `reference/cli/merge-check.md`
- `metrics [--repo <path>] [--feature <slug>] [--write]`: Token cost of each stage of a feature from Claude transcripts; `--write` adds the Feature Metrics row. `reference/cli/metrics.md`
- `sessions --since <days> [--repo <path>]`: List the Claude Code and Codex sessions of every worktree, for retro. `reference/cli/sessions.md`
- `release-check [--repo <path>] (--tag <name> | --sha <rev>)`: May a commit get a release tag: a current audit record and no open security item. `reference/cli/release-check.md`
- `release-plan [--repo <path>] [--harness claude|codex] [--profile quick|standard|deep] [--budget <n>]`: Plan a release security audit run: run folder, profile, budget, scope, sandbox. `reference/cli/release-plan.md`
- `release-record --run <folder> [--base <sha>] [--harness claude|codex] [--repo <path>]`: Write a release audit result into the project note. `reference/cli/release-record.md`

## Build and test

```bash
npm ci && npx playwright install chromium   # once per worktree (Chromium once per machine): the node suite drives a real browser
npm test                                    # node suite: installer, board render and drive.mjs tests
python3 tests/unit/python/test_dev_loop.py  # skill suite
```

Held-out suite: `commands.acceptance` in `dev-loop.json`, `scripts/acceptance.sh`. CI runs it as job `acceptance`. The gate never runs it.

Skill suite runs test ids in chunks of 5 per `test_dev_loop.py --chunk` process, `DEVLOOP_TEST_JOBS` processes at a time (default one per CPU, `1`: one at a time). `DEVLOOP_REPORT_DIR` set: writes JUnit `skill.xml` there; a chunk process that dies counts each of its ids as an error. Arguments (a test name): plain unittest, no report.

`scripts/gate.sh` runs both (the full gate).

`scripts/gate.sh` fails with `run npm ci && npx playwright install chromium` when `node_modules/playwright` is missing; a new feature worktree (Stage 3 start) runs that command first. Both must pass before any commit. `.github/workflows/ci.yml` runs jobs `test` (both suites) and `acceptance` (the held-out suite) on every PR and on `main`. Merge a PR only when both checks are green.

## Project kit

- Kit files are never edited in a project: change the source here, then run `upgrade`.
- The walls deny a file-tool edit of a kit file; `seal-check` reports any other route as `kit-changed`.
- Kit table, MANIFEST rule, notes path, verify after install (Ruling R10), upgrade notes: `reference/kit.md`.

## Conventions

- Data rule: no smaller wave, no removal of the Track L threat model and no second review pass until 5 features have a complete section in Review Yield.md (the project notes, `$NOTES/Review Yield.md`).
- Cost data rule: no process cut based on cost (drop a reviewer, no `tdd` for a task, shorter spec stages) until 5 features have a row with stage cells that are not `?` in Feature Metrics.md (`$NOTES/Feature Metrics.md`, written by `metrics --write` at Stage 7).
- Commits: gitmoji, format `<emoji>(<scope>) #<issue>: <message>`, scope required (e.g. `🐛(install) #12: Back up a replaced skill outside its skills folder`).
- Issues: every new feature or fix starts from a GitHub issue, opened before the branch. Each of its commits names it in the subject (`#<issue>:`). PR body: `Closes #<issue>`.
- Changelog: `cliff.toml` (gitmoji `commit_parsers`) makes `CHANGELOG.md` and the release notes. Needs `GITHUB_TOKEN` for PR and user links. Releases are tag-driven (`vX.Y.Z`) via `.github/workflows/release.yml`: version check, `git-cliff-action`, `ncipollo/release-action`. Workflow fails when tag and `package.json` version differ.
- Release: follow `reference/release.md`.
- Stage 7 after a merge: report line `rc: now|later: <reason>`; rule in `reference/release.md`.
- Comments: `scripts/repo_rules.py comments` fails a new comment line of a kind `CODING_STANDARDS.md` does not allow; mark one about behaviour an outside dependency forces with `allow-comment: dependency`.
- Commit subjects: `scripts/repo_rules.py subjects` fails on a bad `HEAD` subject (fix: `git commit --amend`). Its `warnings` (older commits) are reworded in the Stage 7 rebase, before the push.
- No tracked file may contain an absolute home path or the owner's personal name. Write `~/Projects/tchlyah/dev-loop`, never the expanded home path. A guard test enforces this.
- No tracked file may name an internal client or project (employer, its customers, private projects). The node test in `tests/unit/node/payload.test.mjs` holds only `sha256` of the lowercased name words joined by one space, first 16 hex chars. New name: add its hash to `INTERNAL_NAME_HASHES`, never the name.
- The three merged config files (`.claude/settings.json`, `.codex/hooks.json`, `.codex/config.toml`) are always merged, never copied wholesale.

## Gotchas

- The repo is private. Nothing may assume an anonymous reader: no badges, no links that only resolve for a public repo. The `npx github:tchlyah/dev-loop` install command works only for someone whose Git or SSH credentials already reach it.

## Working principles

- Say your assumptions out loud. If two readings of a request lead to different work, ask before building. If a simpler approach exists, say so.
- Write the minimum code that solves the problem. No speculative abstractions, no unrequested flexibility, no error handling for impossible cases.
- Change only what the request needs. Match the surrounding style. Mention unrelated dead code rather than deleting it. Remove only the orphans your own change created.
