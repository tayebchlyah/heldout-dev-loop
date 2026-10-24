# AGENTS.md

## Purpose

dev-loop: stage-driven development loop skill for Claude Code and Codex, plus its installer. Tracks S, M, L. Session A: Stages 1 and 2, stops at spec approval. Session B: Stages 3 to 7 chained under the autonomy grant. Track S: no spec, no plan. `devloop.py status` detects the stage.

## Repo map

Dogfood: this repo runs dev-loop on itself, installed kit pinned to a release (`dev-loop.json` `version`). Never edit installed kit (`.agents/skills/dev-loop/`, `.claude/agents/`, `.claude/skills/dev-loop`, `.codex/agents/`, `.codex/rules/`, `.dev-loop/`): changes only by `upgrade` to the next tag, after each release. Kit source: `skill/`, `claude/`, `codex/` (installer: `bin/`, `lib/`). Everything else in the repo is edited normally.

- `bin/install.mjs`: installer entry point. Commands: `init`, `upgrade`, `uninstall-global`. No command: usage, exit 1. Flags: `--yes`, `--dry-run`, `--force`, `--harness=claude|codex|both`, `--notes=<path>` (`init`).
- `lib/`: installer internals: `args.mjs` (flag parsing), `fsops.mjs` (backup names), `merge-json.mjs`, `merge-toml.mjs` (per-format merge), `plan.mjs` (runs the merges), `kit.mjs` (project kit file list, path rewrite), `manifest.mjs` (`.dev-loop/MANIFEST.json` write and compare), `project.mjs` (`init` and `upgrade`), `uninstall.mjs` (`uninstall-global`, frozen 1.4 file and hook lists).
- `skill/SKILL.md`: the dev-loop skill payload. `skill/references/`: harness routes (`claude.md`, `codex.md`), `building.md`, `tests.md`, `spec.md`, `mutation.md`. `skill/scripts/devloop.py`: the `devloop` CLI. `skill/scripts/check_setup.py`: post-install verifier (`--hooks-trust`: one line, the Codex project hook events with an untrusted hook entry, for `session-start.sh`). `skill/scripts/notes_path.py`: notes path resolver. `skill/scripts/board/`: progress board: `store.py`, `writer.py`, `server.py`, `render.js`, `page.html`, `board-note.md`.
- `claude/`: Claude Code payload: agents, hooks, `settings.fragment.json` (merged into the project `.claude/settings.json`).
- `codex/`: Codex payload: agents (`.toml`), hooks, `config.permissions.toml`, `hooks.fragment.json`, `rules/default.rules` (kit file `.codex/rules/dev-loop.rules`).
- `tests/unit/python/`: skill suite, `test_dev_loop.py` (imports `test_devloop.py`, `test_seal.py` (SealCommandTests, AuditTests, SealCheckTests, KitChangedTests, GateSealTests, WeakenedTests), `test_seal_hooks.py` (SealHookTests, GuardTests, SpecTesterSourceWallTests, CodexWallTests, AbsolutePathWallTests, KitWallTests, CdWallTests, CodexTrustWarningTests, CanaryScriptTests), `test_board_robust.py`). `board_fixtures.py`: board fixtures shared with the held-out board oracle, which imports it; the unit suite never imports the held-out folder. `fixtures/`: test data. No test file under `skill/`.
- `tests/unit/node/*.test.mjs`: installer suite and board render unit tests (Node's built-in test runner), `helpers.mjs`.
- `tests/acceptance/`: held-out suite. Four board oracle files (`test_board.py`, `test_board_robustness.py`, `board-render.test.mjs`, `board-render-robustness.test.mjs`) plus the seal tests. Written only by `spec-tester`, through `devloop.py seal`. Run by `commands.acceptance` (CI job `acceptance`), never by the gate. Never read, list or run it as an implementer.
- `skill/scripts/seal/`: seal package. `__init__.py` (`ACCEPTANCE_DIR`, `SEAL_TRAILER`, `git_dir`, `merge_base`), `value.py` (seal value, `seal`), `audit.py` (transcript audit, taint records), `check.py` (`seal-check`), `weakened.py` (`gate` `weakened` report).
- Seal hooks: `claude/hooks/seal.sh`, `codex/hooks/seal.sh` (`stop`, `turn`, `start`), `codex/hooks/acceptance-wall.sh` (Codex PreToolUse `Bash|apply_patch`).
- `scripts/canary.sh`: live canary, this repo only (not in the kit). Run by hand before a release.
- `scripts/gate.sh`: repo gate. `dev-loop.json` maps no `commands.gate`, so the hooks and `devloop.py gate` run it: node and skill suites side by side. Exports `core.fsmonitor=false` through `GIT_CONFIG_*` before its first git call, as `check_setup.py` demands of a project `scripts/gate.sh` when `commands.gate` is unmapped. `GATE_SCOPE=changed`: any change runs the node suite, the skill suite only for `skill/`, `claude/`, `codex/`, `bin/`, `lib/`, `tests/unit/python/`, `AGENTS.md`, `package.json`, no change runs neither. `DEVLOOP_REPORT_DIR`: JUnit `node.xml`, `skill.xml`, one `FAILED <class>::<name>` line per failed test.
- `GLOSSARY.md`: domain terms (Stage 0).
- `dev-loop.json`: this repo's own project marker, kit version.

## devloop CLI

`python3 .agents/skills/dev-loop/scripts/devloop.py <command>`. One JSON object on stdout. Exit 0 success, 1 failure the JSON describes, 2 usage error.

- `status [--repo <path>] [--feature <slug>]`: `stage` (0 to 7, `shipped`, or `rebase` while a rebase is in progress), `check`, `next`, `feature`, `track`, `worktree`. `--repo` defaults to `.`. Spec frontmatter `shipped: <YYYY-MM-DD>` non-empty: `shipped`, no git check, left out of `features` (Stage 7 writes it and `pr: <url>` after the merge; not `status`: the notes vault owns it). No `--feature` on a branch without `/` (the default branch): Stage 1, plus `features`: each spec not shipped that is unapproved or has a local or origin branch `<prefix>/<slug>` or a worktree, with its `feature`, `stage`, `worktree`. An approved spec with neither is left out, stage not computed (`--feature <slug>` still resolves it). Track S: Stage 6 until `$(git rev-parse --git-dir)/dev-loop/review-done` exists. Spec frontmatter `acceptance: regression` or `none` (any case): no Stage 3 acceptance file needed, Stage 4 once the worktree exists. Stage 3 done: a file under `tests/acceptance/` named after the slug, or a commit in the history of `HEAD` that touches `tests/acceptance/` and has the message line `Acceptance: <slug>` (exact slug; a spec that only extends earlier files); no `origin/main` needed. An approved spec with no branch is `shipped` when `main` holds such a file, or `main` or `origin/main` holds such a commit. Optional `board` (`at`, `skipped`: the last board run skipped for a reason other than `busy`, `rebase`, `no-dev-loop`, `stage-0`, or crashed: `error`) and `kit_drift` (`worktree`, `main`: the worktree kit is newer than the main worktree's). Reads local state only, never fetches.
- `logged [--name <name>] -- <cmd>`: full output to `$(git rev-parse --git-dir)/dev-loop/logs/<name>-<timestamp>.log`. `<name>` defaults to the basename of the command. JSON `exit`, `log`, `seconds`, `tail`.
- `gate --summary`: `commands.gate`, else `scripts/gate.sh`. JSON `exit`, `log`, `seconds`, `failures`, `slowest` (JUnit XML in `DEVLOOP_REPORT_DIR`, else `[]` and `hint`), `seal` (the `seal-check --uncommitted` payload; the process exits 1 when `seal.ok` is false, even if the project gate passed; the JSON `exit` stays the project gate's own exit), `weakened` (list of `{path, reason}`, `reason` `skip-added` or `assertions-removed`, for test files in `git diff <base>`; report only, never changes the exit code).
- `seal --feature <slug> --message <subject> [--reverts <sha> ...]`: commits the staged change under `tests/acceptance/`. Reads the index, stages nothing. Message: subject, `This reverts commit <sha>.` per `--reverts`, `Acceptance: <slug>`, `Dev-Loop-Seal: <value>` (valid when either seal trailer matches; sha256 of one `<path> <blob>` line per staged path, sorted, blob `deleted` for a removal), `Dev-Loop-Seal-Patch: <value>` (sha256 of the `git patch-id --stable` of the `-U0` diff under `tests/acceptance/`). After the commit hooks ran, recomputes both from the commit and amends (`--no-verify`) when they or the message differ. Exit 1 with `error`: nothing staged under `tests/acceptance/`, a staged path outside it, slug not lower case words joined by `-`, a `--reverts` value that is not a commit. Exit 2: both modes or neither. JSON `commit`, `seal`, `files`. Only `spec-tester` may run it (`git-guard.sh`).
- `seal --rebase-continue`: in a rebase, value from the index into the `Dev-Loop-Seal` trailer of the rebase commit message, and `Dev-Loop-Seal-Patch` (last paragraph only), then `git rebase --continue`, no editor. Exit 1 with `error`: no rebase, a staged path outside `tests/acceptance/`, nothing staged under it, "the stopped commit is not a seal commit" (no `Dev-Loop-Seal` trailer in its last paragraph), "nothing staged under the folder", "git rebase --continue failed" (the rebase ended without continuing). JSON `seal`, `files`, `rebase` (`continued`, or `stopped` with the next conflict and `conflicts`).
- `seal-check [--uncommitted]`: checks the seal commits in `<base>..HEAD` (`<base>`: `git merge-base HEAD origin/main`, else `main`, else none: no commit checked). A seal commit is valid when its blob value or its patch value matches (a clean rebase keeps the patch). JSON `ok`, `violations` (`{reason, commit, path}`: `bad-seal`, `unsealed`, `taint-unreadable` (the taint file cannot be read; `path`), `kit-changed` (a path under `.agents/skills/dev-loop/` or `.dev-loop/`, or a `.dev-loop/MANIFEST.json` key at `<base>` or `HEAD`, differs from `<base>` and its content does not match the current MANIFEST entry (`sha256:` of the bytes, or `link:` target); fine: the MANIFEST itself, and a path gone from both the tree and the MANIFEST (an `upgrade` or `init` writes both); with `--uncommitted`, the working tree against `<base>`, untracked files under the two folders too, `__pycache__/` and `*.pyc` skipped; `seal.sh` adds a kit remedy), `changed-after-seal` (both with `commits`: the non-seal commits that touched the path), `uncommitted` only with `--uncommitted`: tracked changes, untracked paths only when named like a test file), `taint` (open records), `warnings` (closed records, audit skips). Exit 0 when `ok`, else 1; exit 2 outside a git repo.
- `audit`: SubagentStop hook JSON on stdin (`agent_id`, `agent_type`, `agent_transcript_path`, `cwd`). Compares the subagent's tool output with the files it must not read (`spec-tester`: `src/`, `app/`, `backend/`, `frontend/`; any other: `tests/acceptance/`). 2 or more distinct distinctive lines (30 characters or more): a taint record in `<git-dir>/dev-loop/taint.json`; no `<agent_id>.head`: the range starts at `<base>`, else the root. A re-stop replaces the agent's record or skip. JSON `taint` (record or null), `skipped`. Exit 0, also on a skip.
- `board [--repo <path>] [--event session-start|prompt|stop|subagent-start|subagent-stop|post-tool] [--claim <slug>] [--no-serve] [--stop-server]`: writes status notes to `<notes>/dev-loop-board/features/` (own notes in `dev-loop-board/` move there, also an own name that does not parse; the server reads both), `Dev Loop Board.md` (Dataview), and starts the server (127.0.0.1, port 7777 or `DEVLOOP_BOARD_PORT`). `--event` reads hook JSON (`cwd`, `session_id`, `agent_id`) on stdin. Claude `subagent-start` (from `gate-snapshot.sh`) and `subagent-stop` track the session's running subagents: keyed by `agent_id` with the hook's `transcript_path` (a `subagent-stop` without `agent_id` changes nothing): a `stop` while one runs (its subagent transcript, or its start, changed under 30 min ago) sets no waiting; `session-start` keeps them. `--claim`: slug must match a spec, else exit 1. A note is written only on a change: `updated` is the last real change, `notes` lists every own write-set note, written or unchanged. A Track S note (no spec) at Stage 6 or 7 stores `head` (the worktree HEAD); once its branch is gone, it is shipped when `git cherry origin/main <head>` has no `+` line. Server pidfile `~/.config/dev-loop/board-server.<port>.pid`: a `/health` pid that differs from it gives `port-busy`, no signal sent. Test only: `DEVLOOP_TEST_CLAIM_WAIT` shortens the claim wait. JSON `notes`, `removed`, `board_note`, `skipped` (`feature`, `reason`), `server` (`state`, `url`, `message`). Exit 0 also on skip or server error. Exit 1 with `error`: notes path unresolved, `--claim` unknown slug, `--claim` while the lock stays busy 10 s, any caught exception (saved best effort as `last_run` skip `error` with `message` `<Type>: <text>`). Exit 2 usage. `server.state`: `started`, `restarted`, `running`, `stopped`, `disabled`, `port-busy`, `stalled`, `error`. `--no-serve`, or a whole-run skip `no-dev-loop` or `notes-in-repo`: `disabled`. Whole-run skip `reason`: `no-dev-loop`, `stage-0`, `placeholder` (iCloud placeholder in `specs/`; seen after a write: no catch-up pass, queue kept), `notes-in-repo`, `busy` (lock held, no `--claim`), `link` (`dev-loop-board` or `dev-loop-board/features` is a symlink), `error` (`last_run` only); per feature: `rebase`, `link`, `placeholder`, `foreign`. Every busy run appends a line to `<git-common-dir>/dev-loop/board-events.jsonl` (`event` may be null), the lock holder applies it and runs catch-up passes that re-read git, worktrees, specs and claims. State files in `<git-common-dir>/dev-loop/`: `board.lock`, `board-claims.json`, `board-features.json`, `board-state.json` (features, sessions, last_run), `board-events.jsonl`. `--stop-server`: stop, write nothing. Stage 2 runs `--claim`; hooks run `--event`.
- `ci wait [--pr <n>] [--timeout <min>]`: no check registered yet: retries every 15 s within the timeout. JSON `state` (`pass`, `fail`, `timeout`, `no-pr`, or `error` with `message` when `gh pr view` fails for another reason), `failed_job`, `first_error`, `url`.

## Build and test

```bash
npm test                                    # node suite: installer and board render tests
python3 tests/unit/python/test_dev_loop.py  # skill suite
```

Held-out suite: `commands.acceptance` in `dev-loop.json`, `python3 -m unittest discover -s tests/acceptance -p 'test_*.py' && node --test tests/acceptance/*.test.mjs`. CI runs it as job `acceptance`. The gate never runs it.

Skill suite runs test ids in chunks of 5 per `test_dev_loop.py --chunk` process, `DEVLOOP_TEST_JOBS` processes at a time (default one per CPU, `1`: one at a time). `DEVLOOP_REPORT_DIR` set: writes JUnit `skill.xml` there; a chunk process that dies counts each of its ids as an error. Arguments (a test name): plain unittest, no report.

`scripts/gate.sh` runs both (the full gate).

Both must pass before any commit. `.github/workflows/ci.yml` runs jobs `test` (both suites) and `acceptance` (the held-out suite) on every PR and on `main`. Merge a PR only when both checks are green.

## Project kit (what `init` and `upgrade` write, repo-relative)

| Target | Content | How |
| --- | --- | --- |
| `dev-loop.json` | `version`, `harnesses`; optional `commands` (keys `gate`, `stack_up`, `stack_reset`, `acceptance`, `acceptance_failed`, non-empty strings), `merge` (`auto` default, or `human`) | write |
| `dev-loop.local.json` | `notes` path, gitignored (`init` adds the `.gitignore` line); optional `"board_dataview": false` (`check_setup.py` skips the Dataview check), `"board_vault_git": "shared"` (skips the vault `.gitignore` check) | write, only with a notes path |
| `.dev-loop/MANIFEST.json` | version, sha256 of each kit file | write |
| `.dev-loop/hooks/claude/`, `.dev-loop/hooks/codex/` | shell walls (Claude `deny-acceptance-to-subagents.sh`, `git-guard.sh`, `deny-path.sh`; Codex `acceptance-wall.sh`, `git-guard.sh`, `deny-path.sh`), `seal.sh` (SubagentStop `stop`, Stop `turn`, Codex SubagentStart `start`), and `board.sh` (SessionStart, UserPromptSubmit, PostToolUse: background `devloop.py board`, CLI taken from the main worktree; PostToolUse `Bash` runs it only after `task-start`, `task-done`, `sdd-workspace`, `git commit`, `git worktree add`, `gh pr create`, `gh pr merge`, Claude also on `TaskCreate|TaskUpdate`; `gate.sh` runs it on Stop and SubagentStop, Claude `gate-snapshot.sh` on SubagentStart) | kit |
| `.agents/skills/dev-loop/` | skill, harness refs, scripts; no test files (the suites live in `tests/` of this repo) | kit |
| `.claude/skills/dev-loop` | link to `../../.agents/skills/dev-loop` | link |
| `.claude/agents/`, `.codex/agents/` | subagents | kit |
| `.codex/rules/dev-loop.rules` | Codex command rules | kit |
| `.claude/settings.json`, `.codex/hooks.json`, `.codex/config.toml` | hook entries, deny rules (5 Superpowers skill denies, `.env` reads, the Claude repo contract: 3 caveman agent denies), `env` (`ENABLE_STOP_REVIEW`, `ENABLE_COMMIT_REVIEW`, `SG_PUSH_SWEEP` = `0`, `CLAUDE_CODE_ENABLE_TODO_TOOLS` = `1`, never overriding a user value), permission tables | merge |

Upgrade: `npx github:tchlyah/dev-loop#<tag> upgrade` on a branch, diff goes into a PR. Rollback: `git revert` the upgrade commit; a machine back to 1.4: `npx github:tchlyah/dev-loop#v1.4.0`. Before the first `init` on a machine: `uninstall-global`, else Claude Code runs the global skill and Codex runs neither. `uninstall-global` removes a 1.4 install from its own frozen lists (files, 1.4 and 1.5 hook commands).

Board upgrade from 2.1.0: after the kit upgrade lands on `main`, restart each in-flight Session B from the main checkout (an old-kit worktree runs the 2.1.0 writer: wrong-time duplicate in `dev-loop-board/`). Codex: trust the new PostToolUse hook with `/hooks`.

Seal upgrade: ship each in-flight feature before merging the kit upgrade. A Stage 3 commit made before has no `Dev-Loop-Seal` trailer, and `seal-check` reports it `unsealed`. Codex: trust the new hooks (`seal.sh`, `acceptance-wall.sh`) with `/hooks`. `upgrade` removes the three dead Codex profile tables (`dev-implementer`, `dev-spec-tester`, `dev-read-only`) from `.codex/config.toml` when unchanged, and refreshes dev-loop's own hook entries in place (a user field added to a dev-loop hook is lost).

MANIFEST rule: kit files are never edited in a project. Change dev-loop, then run `upgrade`. The walls deny a file-tool edit of a kit file to every caller (Claude `Edit`, `Write`, `NotebookEdit`; Codex `apply_patch`), `seal-check` reports any other route as `kit-changed`. Both walls also resolve a relative path against each folder a `cd` or `pushd` earlier in the command moves to. Codex: Codex skips an untrusted hook without a message; `session-start.sh` names each hook event with an untrusted hook entry (`<event>:<group>:<hook>` key, `check_setup.py --hooks-trust`), once `session-start.sh` itself is trusted. Accepted residual: an agent that edits the checker itself, or rewrites a MANIFEST hash with its edit, passes `seal-check`; a folder held in a variable (`d=tests; cd $d`) passes the walls' `cd` rule, the audit still records a read; only the PR diff shows the first. `upgrade` refuses on a local edit or a deleted kit file unless `--force`. A kit file gets no backup (git holds it). A merged file gets `<file>.bak-<timestamp>`. Merge never removes or reorders an entry it did not add. Exceptions: an old dev-loop SessionStart command (the 1.4 and 1.5 texts, `LEGACY_SESSION_START` in `merge-json.mjs`) is replaced in place and reported as removed; dev-loop's own hook entries are refreshed in place; the three dead Codex profile tables 2.1.1 wrote (`LEGACY_PROFILE_TABLES` in `merge-toml.mjs`) are removed when unchanged. On a `config.toml` table collision, the user's table wins and the installer prints one line naming it.

2.0.0 upgrade (breaking): from 1.5.x, `upgrade` on a branch, then the user steps. From 1.4.0: write `dev-loop.local.json`, `uninstall-global` (`--dry-run` first, keep a personal vault guard first), `init`. User steps: remove the user-level denies of `executing-plans`, Pocock `tdd`, Pocock `diagnosing-bugs` from `~/.claude/settings.json`; Codex: install Pocock `tdd`, `diagnosing-bugs`, `pr`, `prototype`; optional `CONTEXT.md` to `GLOSSARY.md`; run `check_setup.py`.

Notes path: `skill/scripts/notes_path.py` resolves it (`DEV_LOOP_NOTES`, `dev-loop.local.json` in the worktree then the main worktree, `notes` in `dev-loop.json`). No 1.4 fallback. Glossary: `GLOSSARY.md`, else `CONTEXT.md`. Unset: `check_setup.py` reports a plain top-level failure line, not under "User actions required": `missing notes path for <repo>. Write {"notes": "<path>"} to <main worktree>/dev-loop.local.json`.

## Verify after install

Applies when `dev-loop.json` exists. Without it, `check_setup.py` prints one failure line, `no dev-loop.json in <repo>. Run npx github:tchlyah/dev-loop#<tag> init`, exit 1:

```bash
python3 .agents/skills/dev-loop/scripts/check_setup.py --harness claude --repo .
python3 .agents/skills/dev-loop/scripts/check_setup.py --harness codex --repo .
```

Ruling R10: right after a correct Codex install, `check_setup.py --harness codex` still fails two things no installer can fix, printed under "User actions required":
1. Trusted project and trusted-hook-hash entries: Codex writes those only after the user trusts the project and runs `/hooks` to trust the dev-loop hook definitions.
2. Eight Pocock skills dev-loop does not ship: `grilling`, `domain-modeling`, `codebase-design`, `code-review`, `tdd`, `diagnosing-bugs`, `pr`, `prototype`.

These are expected manual steps, not install defects.

## Conventions

- Commits: gitmoji, format `<emoji>(<scope>) <message>`, scope required (e.g. `🐛(install) Back up a replaced skill outside its skills folder`).
- Changelog: `cliff.toml` (gitmoji `commit_parsers`) makes `CHANGELOG.md` and the release notes. Needs `GITHUB_TOKEN` for PR and user links. Releases are tag-driven (`vX.Y.Z`) via `.github/workflows/release.yml`: version check, `git-cliff-action`, `ncipollo/release-action`. Workflow fails when tag and `package.json` version differ.
- Release:
  1. Release PR: set `package.json` `version`, run `GITHUB_TOKEN=$(gh auth token) git cliff --tag vX.Y.Z -o CHANGELOG.md`.
  2. Run `scripts/canary.sh`, paste its lines into the release PR.
  3. Rebase the PR, no merge commit.
  4. On `main`: `git tag -a vX.Y.Z -m "dev-loop vX.Y.Z"`, `git push origin vX.Y.Z`. One tag per push: GitHub sends no push event for more than three tags.
  5. Workflow checks version, makes notes, makes the GitHub Release.
- No tracked file may contain an absolute home path or the owner's personal name. Write `~/Projects/tchlyah/dev-loop`, never the expanded home path. A guard test enforces this.
- The three merged config files (`.claude/settings.json`, `.codex/hooks.json`, `.codex/config.toml`) are always merged, never copied wholesale.

## Gotchas

- The repo is private. Nothing may assume an anonymous reader: no badges, no links that only resolve for a public repo. The `npx github:tchlyah/dev-loop` install command works only for someone whose Git or SSH credentials already reach it.

## Working principles

- Say your assumptions out loud. If two readings of a request lead to different work, ask before building. If a simpler approach exists, say so.
- Write the minimum code that solves the problem. No speculative abstractions, no unrequested flexibility, no error handling for impossible cases.
- Change only what the request needs. Match the surrounding style. Mention unrelated dead code rather than deleting it. Remove only the orphans your own change created.
