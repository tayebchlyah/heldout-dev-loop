# dev-loop

Stage-driven development loop for Claude Code and Codex. Each piece of work is sized as Track S, M or L. A Track M or L feature runs in one session by default, in two parts: Shaping (Stages 1 and 2, at high effort) frames it and writes the spec and stops at the spec approval; Delivery (Stages 3 to 7, at medium effort) chains acceptance tests, plan, build, feature gate, ship under an autonomy grant. A Track S change has no spec and no plan: it is built, goes through the review wave of the feature gate (no acceptance tests), then ships.

## Install

```bash
npx github:tchlyah/dev-loop#<tag> init
```

The repository is private, so this works only once your Git or SSH credentials reach it (`git ls-remote git@github.com:tchlyah/dev-loop.git` should succeed). Otherwise npx fails to fetch it.

Flags: `--yes`, `--dry-run`, `--force`, `--harness=claude|codex|both`, `--notes=<path>` (`init`).

`check_setup.py` reports a repo with no notes path as a top-level failure: `missing notes path for <repo>. Write {"notes": "<path>"} to <main worktree>/dev-loop.local.json`.

## Commands

Run each with a release tag. Run `init` and `upgrade` from the top level of a git repository; `uninstall-global` runs from anywhere:

```bash
npx github:tchlyah/dev-loop#<tag> init              # install the kit into this repository
npx github:tchlyah/dev-loop#<tag> upgrade           # update the kit to the tag's version
npx github:tchlyah/dev-loop#<tag> uninstall-global  # remove the 1.4 global install
```

`init` writes `dev-loop.json`, `.dev-loop/MANIFEST.json`, the skill in `.agents/skills/dev-loop/`, the `pause`, `continue`, `release-audit` and `garden` skills in `.agents/skills/<name>/` and the files of the chosen harnesses (`--harness=claude|codex|both`) under `.claude/`, `.codex/` and `.dev-loop/hooks/`. `--notes=<path>` sets the notes folder in the gitignored `dev-loop.local.json`. Commit the result. Kit files are never edited in a project: change dev-loop, then run `upgrade`. `upgrade` refuses on a local edit unless `--force`. A call with no command prints the usage and exits 1.

## Project settings

`dev-loop.json` holds `version` and `harnesses`, and optional keys:

- `commands`: the project's own command for a step. Keys: `gate`, `stack_up`, `stack_reset`, `acceptance`, `acceptance_failed`. Each value is a non-empty string. A missing key falls back to `scripts/gate.sh`, `docker compose` and the stack's test runner. `check_setup.py` fails on any other key.
- `merge`: `auto` (the default) or `human`. With `auto`, Stage 7 merges a Track S or M pull request after green CI and the review wave, when it is a two-way door with a small blast radius. A one-way door or a Track L pull request waits for you. With `human`, every pull request waits for you. Stage 7 runs `devloop.py merge-check` to decide, and `merge_human_paths` in `dev-loop.json` lists path patterns whose changes always wait for you.
- `security_audit`: `{"images": {"<name>": "<image>"}}`, the container images the release audit sandbox may start (name: lower case letters, digits, `_` and `-`). Pull each image first; `check_setup.py` reports a stopped Docker engine or a missing image. No key: the audit runs source-only. Add `security-audit/` to the vault `.gitignore`.

## Verify skill

A project with `ui` in `stacks:` holds its own verify skill in `.agents/skills/<skill>/`: how an agent launches, checks, drives and cleans up the real app, and a feature map, one file per feature, with the routes and the accessible names a user sees. Stage 0 writes it with `devloop.py verify-init`, then `skill/references/verify.md`. Stage 6 runs `devloop.py drive` on the routes the branch changed, and one subagent checks the layout on the screenshots. Stage 7 adds the before and after screens to the PR body. `"verify": false` in `dev-loop.json` opts out.

## The devloop command

`skill/scripts/devloop.py` does the deterministic work. In a project it runs as `python3 .agents/skills/dev-loop/scripts/devloop.py <command>`. Each command prints one JSON object. Exit 0 on success, 1 on a failure the JSON describes, 2 on a usage error.

- `status`: the current stage of the work, with the check that chose it and the next action. On the default branch it lists the open features.
- `logged -- <cmd>`: runs a long command and writes its full output to a log file under the git folder.
- `gate --summary`: runs `commands.gate`, else `scripts/gate.sh`, and reports the exit code, the log, the failure lines and the slowest tests.
- `size-check [--uncommitted | --all]`: fails a file above 40,000 bytes that is new on the branch or grew on it, and names a line to split it at. `--all` lists every file above the limit. `gate --summary` runs it and `seal` refuses a big acceptance file. `file_size_exempt` in `dev-loop.json` lists paths it skips.
- `board`: writes a status note per feature to the `dev-loop-board` folder of the notes folder, writes `Dev Loop Board.md` (needs the Obsidian Dataview plugin with JavaScript queries on), and runs a local page at `http://127.0.0.1:7777/` (`DEVLOOP_BOARD_PORT` sets the port). Hooks run it; `--claim <slug>` shows a new spec at Stage 2. Add `dev-loop-board/` to the vault `.gitignore`: `check_setup.py` reports both.
- `verify-init --base-url <url>`: writes the skeleton of the project's verify skill and its feature map, and the `verify` key.
- `drive`: drives each feature map route the branch changed in Chromium (needs Playwright in the project), checks the status, the accessible names and the console errors, and saves one screenshot per route. `--routes-from` drives the same routes on the base branch (the before run).
- `capture --pr <n>`: puts a before and an after screenshot of each driven route (a WebM for a journey) in the PR body, uploaded with `gh pr edit --attach` (gh 2.99.0 or later).
- `release-plan`, `release-record`, `release-check`: the release security audit. Before a `v*` tag, run `/release-audit` (Codex `$release-audit`) in the main session on `main`: it runs the Cloudflare `security-audit` skill, then `release-record` writes the confirmed and needs-validation findings into `## Deferred` of the project note and one line into `## Release audits`. `release-check --sha HEAD` says whether the commit may get the tag; the git guard runs it on a tag push and `gh release create`. A run takes hours and costs tens of dollars. Target code runs only in the container sandbox `sandbox-run.py` (Docker).
- `sessions --since <days>`: lists the Claude Code and Codex sessions of every worktree of the repo, largest first, for the weekly `/retro` review. `status` on the default branch gives `review_due` when the last `/garden` run is 7 days old or older, and Stage 1 then refuses new work until the review is done.
- `review-merge <reviewer>=<file> ...`: merges the Stage 6 reviewer tables (columns `file`, `line`, `finding`, `class`, `verdict`) into one row per file and line, and lists the rows with a security finding.
- `acceptance [--failed]`: runs `commands.acceptance` (or `commands.acceptance_failed`) with `DEVLOOP_REPORT_DIR` set, reads the failing tests from the JUnit XML there, checks that no compose build container is older than the newest code commit, records the run, and gives the progress guard verdict (`continue`, `stop`, `green` or `unknown`).
- `ci wait`: gives one verdict for the pull request of the current branch: conflicts, open review threads, a change request, failed checks (with the failed job and its first error), a merge block, or pass.

## Before the first `init` on a machine

Remove the 1.4 global install first: `npx github:tchlyah/dev-loop#<tag> uninstall-global` (try `--dry-run` first). With a global `dev-loop` skill still in place, Claude Code runs the global skill instead of the project one, and Codex runs neither. `check_setup.py` fails while a user-level `dev-loop` skill exists. `uninstall-global` refuses when no hook of yours runs a `git-guard.sh` outside the dev-loop install, because a vault guard that lived there would go too (`--force` skips this check).

## Upgrade and rollback

- **Upgrade:** on a branch, run `npx github:tchlyah/dev-loop#<new tag> upgrade`, commit the diff, open a PR. Each person gets the new version with `git pull`.
- **Rollback of a project:** `git revert` the upgrade commit.
- **Rollback of a machine to 1.4:** `npx github:tchlyah/dev-loop#v1.4.0` reinstalls the global install. Do not keep it on a machine whose projects use the kit.

Upgrade notes per version (board, seal, 2.0.0): [`reference/kit.md`](reference/kit.md) `## Upgrade`.

## Upgrade to 2.0.0

2.0.0 breaks these:

- Stages chain under an autonomy grant. "One stage per invocation" is gone.
- Tracks S, M and L. A Track M or L spec needs `feature:` and `track:` in its frontmatter (a 1.x spec still resolves by its title).
- Stage 1 uses Pocock `grilling` only. Superpowers `brainstorming`, `test-driven-development`, `systematic-debugging` and `finishing-a-development-branch` are denied in the project. Pocock `tdd`, `diagnosing-bugs` and `pr` replace them.
- The feature gate has three reviewers. `simplify`, the Codex `simplifier` agent and the mutation step are gone. Mutation is a nightly job.
- The 1.4 `vault_notes:` notes fallback, the `vault_notes:` SessionStart detection and the `--global` install are gone.
- User settings: remove the user-level denies of `executing-plans`, Pocock `tdd` and Pocock `diagnosing-bugs` from `~/.claude/settings.json`. Codex: install Pocock `tdd`, `diagnosing-bugs`, `pr` and `prototype`.

From 1.4.0: write `dev-loop.local.json` with your notes path, run `npx github:tchlyah/dev-loop#v2.0.0 uninstall-global` (try `--dry-run` first, and keep a personal vault guard first), then `init`, change the user deny list, install the Codex skills, rename `CONTEXT.md` to `GLOSSARY.md` if you want, and run `check_setup.py`.

From 1.5.x: run `upgrade` on a branch, then the user steps above.

Rollback: `git revert` the upgrade commit, then `npx github:tchlyah/dev-loop#v1.4.0` on each machine for a 1.4 project.

## Notes folder

Specs and plans live in a notes folder, any local folder. `skill/scripts/notes_path.py` finds it, first match wins:

1. `DEV_LOOP_NOTES` environment variable.
2. `notes` in `dev-loop.local.json` in the current worktree, then in the main worktree. This file is gitignored: each person and each machine sets its own path. A new worktree has no copy, so it uses the main worktree's file.
3. `notes` in the committed `dev-loop.json`, for a team that keeps its notes in the repository (for example `docs/dev-loop`).

A path is absolute, starts with `~`, or is relative to the repository root. The folder must exist. `init --notes=<path>` writes the local file.

## Backups

Every merged file the installer replaces (`.claude/settings.json`, `.codex/hooks.json`, `.codex/config.toml`) gets a backup at `<file>.bak-<timestamp>`. It never removes or reorders an entry it did not add. If your `.codex/config.toml` already declares a table the fragment also declares, your table wins and the installer prints one line naming it.

## After installing

`check_setup.py` can report three things under "User actions required" that no installer can fix. All are expected, not a broken install:

1. **Trusted Codex hooks.** Codex only trusts hook definitions after you run `/hooks` inside Codex and approve the dev-loop hooks. Until you trust the project and its hooks, the trusted-project and trusted-hook-hash checks fail.
2. **Third-party skills.** dev-loop does not ship the Codex skills `grilling`, `domain-modeling`, `codebase-design`, `code-review`, `tdd`, `diagnosing-bugs`, `pr` and `prototype` (Matt Pocock's skills). Install them separately from their own source.
3. **The security-audit skill.** For both harnesses, the Stage 6 security verifier reads its prompt from Cloudflare's `security-audit` skill, pinned to one commit. Install it with `npx skills add https://github.com/cloudflare/security-audit-skill/tree/c1c8a8c1471069fb0e188eeaff69b8e8db6564a8 --skill security-audit --global --agent claude-code codex`.

## Develop

```bash
git clone git@github.com:tchlyah/dev-loop.git ~/Projects/tchlyah/dev-loop
cd ~/Projects/tchlyah/dev-loop
```

To try a change in a project, run `node ~/Projects/tchlyah/dev-loop/bin/install.mjs init` (or `upgrade`) from the project's top level.

```bash
npm ci && npx playwright install chromium   # once per clone or worktree
npm test                                    # node suite: installer, board render and drive tests
python3 tests/unit/python/test_dev_loop.py  # skill suite
scripts/acceptance.sh                       # held-out acceptance suite (CI job `acceptance`), JUnit with DEVLOOP_REPORT_DIR
```

## Verify

In a project with `dev-loop.json`:

```bash
python3 .agents/skills/dev-loop/scripts/check_setup.py --harness claude --repo .
python3 .agents/skills/dev-loop/scripts/check_setup.py --harness codex --repo .
```

