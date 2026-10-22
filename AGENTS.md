# AGENTS.md

## Purpose

dev-loop: stage-driven development loop skill for Claude Code and Codex, plus its installer. Tracks S, M, L. Session A: Stages 1 and 2, stops at spec approval. Session B: Stages 3 to 7 chained under the autonomy grant. Track S: no spec, no plan. `devloop.py status` detects the stage.

## Repo map

Dogfood: this repo runs dev-loop on itself, installed kit pinned to a release (`dev-loop.json` `version`). Never edit installed kit (`.agents/skills/dev-loop/`, `.claude/agents/`, `.claude/skills/dev-loop`, `.codex/agents/`, `.codex/rules/`, `.dev-loop/`): changes only by `upgrade` to the next tag, after each release. Kit source: `skill/`, `claude/`, `codex/` (installer: `bin/`, `lib/`). Everything else in the repo is edited normally.

- `bin/install.mjs`: installer entry point. Commands: `init`, `upgrade`, `uninstall-global`. No command: usage, exit 1. Flags: `--yes`, `--dry-run`, `--force`, `--harness=claude|codex|both`, `--notes=<path>` (`init`).
- `lib/`: installer internals: `args.mjs` (flag parsing), `fsops.mjs` (backup names), `merge-json.mjs`, `merge-toml.mjs` (per-format merge), `plan.mjs` (runs the merges), `kit.mjs` (project kit file list, path rewrite), `manifest.mjs` (`.dev-loop/MANIFEST.json` write and compare), `project.mjs` (`init` and `upgrade`), `uninstall.mjs` (`uninstall-global`, frozen 1.4 file and hook lists).
- `skill/SKILL.md`: the dev-loop skill payload. `skill/references/`: harness routes (`claude.md`, `codex.md`), `building.md`, `tests.md`, `spec.md`, `mutation.md`. `skill/scripts/devloop.py`: the `devloop` CLI. `skill/scripts/check_setup.py`: post-install verifier. `skill/scripts/notes_path.py`: notes path resolver. `skill/scripts/test_dev_loop.py`: skill suite (imports `test_devloop.py`). `skill/scripts/board/`: progress board: `store.py`, `writer.py`, `server.py`, `render.js`, `page.html`, `board-note.md`. Tests: `skill/scripts/test_board.py`, `skill/scripts/test_board_robust.py`, `test/board-render.test.mjs`, `test/board-render-robust.test.mjs`.
- `claude/`: Claude Code payload: agents, hooks, `settings.fragment.json` (merged into the project `.claude/settings.json`).
- `codex/`: Codex payload: agents (`.toml`), hooks, `config.permissions.toml`, `hooks.fragment.json`, `rules/default.rules` (kit file `.codex/rules/dev-loop.rules`).
- `test/*.test.mjs`: installer suite (Node's built-in test runner).
- `scripts/gate.sh`: repo gate. `dev-loop.json` maps no `commands.gate`, so the hooks and `devloop.py gate` run it: node and skill suites side by side. `GATE_SCOPE=changed`: any change runs the node suite, the skill suite only for `skill/`, `claude/`, `codex/`, `bin/`, `lib/`, `AGENTS.md`, `package.json`, no change runs neither. `DEVLOOP_REPORT_DIR`: JUnit `node.xml`, `skill.xml`, one `FAILED <class>::<name>` line per failed test.
- `GLOSSARY.md`: domain terms (Stage 0).
- `dev-loop.json`: this repo's own project marker, kit version.

## devloop CLI

`python3 .agents/skills/dev-loop/scripts/devloop.py <command>`. One JSON object on stdout. Exit 0 success, 1 failure the JSON describes, 2 usage error.

- `status [--repo <path>] [--feature <slug>]`: `stage` (0 to 7, `shipped`, or `rebase` while a rebase is in progress), `check`, `next`, `feature`, `track`, `worktree`. `--repo` defaults to `.`. Spec frontmatter `shipped: <YYYY-MM-DD>` non-empty: `shipped`, no git check, left out of `features` (Stage 7 writes it and `pr: <url>` after the merge; not `status`: the notes vault owns it). No `--feature` on a branch without `/` (the default branch): Stage 1, plus `features`: each spec not shipped that is unapproved or has a local or origin branch `<prefix>/<slug>` or a worktree, with its `feature`, `stage`, `worktree`. An approved spec with neither is left out, stage not computed (`--feature <slug>` still resolves it). Track S: Stage 6 until `$(git rev-parse --git-dir)/dev-loop/review-done` exists. Spec frontmatter `acceptance: regression` or `none` (any case): no Stage 3 acceptance file needed, Stage 4 once the worktree exists. Stage 3 done: a file under `tests/acceptance/` named after the slug, or a branch commit not on `origin/main` that touches `tests/acceptance/` (a spec that only extends earlier files). Optional `board` (`at`, `skipped`: the last board run skipped for a reason other than `busy`, `rebase`, `no-dev-loop`, `stage-0`, or crashed: `error`) and `kit_drift` (`worktree`, `main`: the worktree kit is newer than the main worktree's). Reads local state only, never fetches.
- `logged [--name <name>] -- <cmd>`: full output to `$(git rev-parse --git-dir)/dev-loop/logs/<name>-<timestamp>.log`. `<name>` defaults to the basename of the command. JSON `exit`, `log`, `seconds`, `tail`.
- `gate --summary`: `commands.gate`, else `scripts/gate.sh`. JSON `exit`, `log`, `seconds`, `failures`, `slowest` (JUnit XML in `DEVLOOP_REPORT_DIR`, else `[]` and `hint`).
- `board [--repo <path>] [--event session-start|prompt|stop|subagent-stop|post-tool] [--claim <slug>] [--no-serve] [--stop-server]`: writes status notes to `<notes>/dev-loop-board/features/` (own notes in `dev-loop-board/` move there, also an own name that does not parse; the server reads both), `Dev Loop Board.md` (Dataview), and starts the server (127.0.0.1, port 7777 or `DEVLOOP_BOARD_PORT`). `--event` reads hook JSON (`cwd`, `session_id`) on stdin. `--claim`: slug must match a spec, else exit 1. A note is written only on a change: `updated` is the last real change, `notes` lists every own write-set note, written or unchanged. A Track S note (no spec) at Stage 6 or 7 stores `head` (the worktree HEAD); once its branch is gone, it is shipped when `git cherry origin/main <head>` has no `+` line. Server pidfile `~/.config/dev-loop/board-server.<port>.pid`: a `/health` pid that differs from it gives `port-busy`, no signal sent. Test only: `DEVLOOP_TEST_CLAIM_WAIT` shortens the claim wait. JSON `notes`, `removed`, `board_note`, `skipped` (`feature`, `reason`), `server` (`state`, `url`, `message`). Exit 0 also on skip or server error. Exit 1 with `error`: notes path unresolved, `--claim` unknown slug, `--claim` while the lock stays busy 10 s, any caught exception (saved best effort as `last_run` skip `error` with `message` `<Type>: <text>`). Exit 2 usage. `server.state`: `started`, `restarted`, `running`, `stopped`, `disabled`, `port-busy`, `stalled`, `error`. `--no-serve`, or a whole-run skip `no-dev-loop` or `notes-in-repo`: `disabled`. Whole-run skip `reason`: `no-dev-loop`, `stage-0`, `placeholder` (iCloud placeholder in `specs/`; seen after a write: no catch-up pass, queue kept), `notes-in-repo`, `busy` (lock held, no `--claim`), `link` (`dev-loop-board` or `dev-loop-board/features` is a symlink), `error` (`last_run` only); per feature: `rebase`, `link`, `placeholder`, `foreign`. Every busy run appends a line to `<git-common-dir>/dev-loop/board-events.jsonl` (`event` may be null), the lock holder applies it and runs catch-up passes that re-read git, worktrees, specs and claims. State files in `<git-common-dir>/dev-loop/`: `board.lock`, `board-claims.json`, `board-features.json`, `board-state.json` (features, sessions, last_run), `board-events.jsonl`. `--stop-server`: stop, write nothing. Stage 2 runs `--claim`; hooks run `--event`.
- `ci wait [--pr <n>] [--timeout <min>]`: no check registered yet: retries every 15 s within the timeout. JSON `state` (`pass`, `fail`, `timeout`, `no-pr`, or `error` with `message` when `gh pr view` fails for another reason), `failed_job`, `first_error`, `url`.

## Build and test

```bash
npm test                                # installer suite: node --test test/*.test.mjs
python3 skill/scripts/test_dev_loop.py  # skill suite
```

Skill suite runs test ids in chunks of 5 per `test_dev_loop.py --chunk` process, `DEVLOOP_TEST_JOBS` processes at a time (default one per CPU, `1`: one at a time). `DEVLOOP_REPORT_DIR` set: writes JUnit `skill.xml` there; a chunk process that dies counts each of its ids as an error. Arguments (a test name): plain unittest, no report.

`scripts/gate.sh` runs both (the full gate).

Both must pass before any commit. `.github/workflows/ci.yml` runs both on every PR and on `main`. Merge a PR only when its `test` check is green.

## Project kit (what `init` and `upgrade` write, repo-relative)

| Target | Content | How |
| --- | --- | --- |
| `dev-loop.json` | `version`, `harnesses`; optional `commands` (keys `gate`, `stack_up`, `stack_reset`, `acceptance`, `acceptance_failed`, non-empty strings), `merge` (`auto` default, or `human`) | write |
| `dev-loop.local.json` | `notes` path, gitignored (`init` adds the `.gitignore` line); optional `"board_dataview": false` (`check_setup.py` skips the Dataview check), `"board_vault_git": "shared"` (skips the vault `.gitignore` check) | write, only with a notes path |
| `.dev-loop/MANIFEST.json` | version, sha256 of each kit file | write |
| `.dev-loop/hooks/claude/`, `.dev-loop/hooks/codex/` | shell walls, and `board.sh` (SessionStart, UserPromptSubmit, PostToolUse: background `devloop.py board`, CLI taken from the main worktree; PostToolUse `Bash` runs it only after `task-start`, `task-done`, `sdd-workspace`, `git commit`, `git worktree add`, `gh pr create`, `gh pr merge`, Claude also on `TaskCreate|TaskUpdate`; `gate.sh` runs it on Stop and SubagentStop) | kit |
| `.agents/skills/dev-loop/` | skill, harness refs, scripts; no `test_*.py` (the skill suite runs in this repo only) | kit |
| `.claude/skills/dev-loop` | link to `../../.agents/skills/dev-loop` | link |
| `.claude/agents/`, `.codex/agents/` | subagents | kit |
| `.codex/rules/dev-loop.rules` | Codex command rules | kit |
| `.claude/settings.json`, `.codex/hooks.json`, `.codex/config.toml` | hook entries, deny rules (5 Superpowers skill denies, `.env` reads, the Claude repo contract: 3 caveman agent denies), `env` (`ENABLE_STOP_REVIEW`, `ENABLE_COMMIT_REVIEW`, `SG_PUSH_SWEEP` = `0`, `CLAUDE_CODE_ENABLE_TODO_TOOLS` = `1`, never overriding a user value), permission tables | merge |

Upgrade: `npx github:tchlyah/dev-loop#<tag> upgrade` on a branch, diff goes into a PR. Rollback: `git revert` the upgrade commit; a machine back to 1.4: `npx github:tchlyah/dev-loop#v1.4.0`. Before the first `init` on a machine: `uninstall-global`, else Claude Code runs the global skill and Codex runs neither. `uninstall-global` removes a 1.4 install from its own frozen lists (files, 1.4 and 1.5 hook commands).

Board upgrade from 2.1.0: after the kit upgrade lands on `main`, restart each in-flight Session B from the main checkout (an old-kit worktree runs the 2.1.0 writer: wrong-time duplicate in `dev-loop-board/`). Codex: trust the new PostToolUse hook with `/hooks`.

MANIFEST rule: kit files are never edited in a project. Change dev-loop, then run `upgrade`. `upgrade` refuses on a local edit or a deleted kit file unless `--force`. A kit file gets no backup (git holds it). A merged file gets `<file>.bak-<timestamp>`. Merge never removes or reorders an entry it did not add. One exception: an old dev-loop SessionStart command (the 1.4 and 1.5 texts, `LEGACY_SESSION_START` in `merge-json.mjs`) is replaced in place and reported as removed. On a `config.toml` table collision, the user's table wins and the installer prints one line naming it.

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
  2. Rebase the PR, no merge commit.
  3. On `main`: `git tag -a vX.Y.Z -m "dev-loop vX.Y.Z"`, `git push origin vX.Y.Z`. One tag per push: GitHub sends no push event for more than three tags.
  4. Workflow checks version, makes notes, makes the GitHub Release.
- No tracked file may contain an absolute home path or the owner's personal name. Write `~/Projects/tchlyah/dev-loop`, never the expanded home path. A guard test enforces this.
- The three merged config files (`.claude/settings.json`, `.codex/hooks.json`, `.codex/config.toml`) are always merged, never copied wholesale.

## Gotchas

- The repo is private. Nothing may assume an anonymous reader: no badges, no links that only resolve for a public repo. The `npx github:tchlyah/dev-loop` install command works only for someone whose Git or SSH credentials already reach it.

## Working principles

- Say your assumptions out loud. If two readings of a request lead to different work, ask before building. If a simpler approach exists, say so.
- Write the minimum code that solves the problem. No speculative abstractions, no unrequested flexibility, no error handling for impossible cases.
- Change only what the request needs. Match the surrounding style. Mention unrelated dead code rather than deleting it. Remove only the orphans your own change created.
