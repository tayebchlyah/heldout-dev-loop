# dev-loop

Stage-driven development loop for Claude Code and Codex. Each piece of work is sized as Track S, M or L. A Track M or L feature runs in two sessions: Session A frames it and writes the spec (Stages 1 and 2) and stops at the spec approval. Session B chains Stages 3 to 7 (acceptance tests, plan, build, feature gate, ship) under an autonomy grant. A Track S change has no spec and no plan: it is built, goes through the review wave of the feature gate (no acceptance tests), then ships.

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

`init` writes `dev-loop.json`, `.dev-loop/MANIFEST.json`, the skill in `.agents/skills/dev-loop/` and the files of the chosen harnesses (`--harness=claude|codex|both`) under `.claude/`, `.codex/` and `.dev-loop/hooks/`. `--notes=<path>` sets the notes folder in the gitignored `dev-loop.local.json`. Commit the result. Kit files are never edited in a project: change dev-loop, then run `upgrade`. `upgrade` refuses on a local edit unless `--force`. A call with no command prints the usage and exits 1.

## Project settings

`dev-loop.json` holds `version` and `harnesses`, and two optional keys:

- `commands`: the project's own command for a step. Keys: `gate`, `stack_up`, `stack_reset`, `acceptance`, `acceptance_failed`. Each value is a non-empty string. A missing key falls back to `scripts/gate.sh`, `docker compose` and the stack's test runner. `check_setup.py` fails on any other key.
- `merge`: `auto` (the default) or `human`. With `auto`, Stage 7 merges a Track S or M pull request after green CI and the review wave, when it is a two-way door with a small blast radius. A one-way door or a Track L pull request waits for you. With `human`, every pull request waits for you.

## The devloop command

`skill/scripts/devloop.py` does the deterministic work. In a project it runs as `python3 .agents/skills/dev-loop/scripts/devloop.py <command>`. Each command prints one JSON object. Exit 0 on success, 1 on a failure the JSON describes, 2 on a usage error.

- `status`: the current stage of the work, with the check that chose it and the next action. On the default branch it lists the open features.
- `logged -- <cmd>`: runs a long command and writes its full output to a log file under the git folder.
- `gate --summary`: runs `commands.gate`, else `scripts/gate.sh`, and reports the exit code, the log, the failure lines and the slowest tests.
- `ci wait`: waits for the checks of the current branch's pull request, and on a failure names the failed job and its first error.

## Before the first `init` on a machine

Remove the 1.4 global install first: `npx github:tchlyah/dev-loop#<tag> uninstall-global` (try `--dry-run` first). With a global `dev-loop` skill still in place, Claude Code runs the global skill instead of the project one, and Codex runs neither. `check_setup.py` fails while a user-level `dev-loop` skill exists. `uninstall-global` refuses when no hook of yours runs a `git-guard.sh` outside the dev-loop install, because a vault guard that lived there would go too (`--force` skips this check).

## Upgrade and rollback

- **Upgrade:** on a branch, run `npx github:tchlyah/dev-loop#<new tag> upgrade`, commit the diff, open a PR. Each person gets the new version with `git pull`.
- **Rollback of a project:** `git revert` the upgrade commit.
- **Rollback of a machine to 1.4:** `npx github:tchlyah/dev-loop#v1.4.0` reinstalls the global install. Do not keep it on a machine whose projects use the kit.

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

`check_setup.py` can report two things under "User actions required" that no installer can fix. Both are expected, not a broken install:

1. **Trusted Codex hooks.** Codex only trusts hook definitions after you run `/hooks` inside Codex and approve the dev-loop hooks. Until you trust the project and its hooks, the trusted-project and trusted-hook-hash checks fail.
2. **Third-party skills.** dev-loop does not ship the Codex skills `grilling`, `domain-modeling`, `codebase-design`, `code-review`, `tdd`, `diagnosing-bugs`, `pr` and `prototype` (Matt Pocock's skills). Install them separately from their own source.

## Develop

```bash
git clone git@github.com:tchlyah/dev-loop.git ~/Projects/tchlyah/dev-loop
cd ~/Projects/tchlyah/dev-loop
```

To try a change in a project, run `node ~/Projects/tchlyah/dev-loop/bin/install.mjs init` (or `upgrade`) from the project's top level.

```bash
npm test                                # installer suite
python3 skill/scripts/test_dev_loop.py  # skill suite
```

## Verify

In a project with `dev-loop.json`:

```bash
python3 .agents/skills/dev-loop/scripts/check_setup.py --harness claude --repo .
python3 .agents/skills/dev-loop/scripts/check_setup.py --harness codex --repo .
```

