# Upgrade

## Before the first `init` on a machine

Remove the 1.4 global install first: `npx github:tayebchlyah/heldout-dev-loop#<tag> uninstall-global` (try `--dry-run` first). With a global `dev-loop` skill still in place, Claude Code runs the global skill instead of the project one, and Codex runs neither. `check_setup.py` fails while a user-level `dev-loop` skill exists. `uninstall-global` refuses when no hook of yours runs a `git-guard.sh` outside the dev-loop install, because a vault guard that lived there would go too (`--force` skips this check).

## Upgrade and rollback

- **Upgrade:** on a branch, run `npx github:tayebchlyah/heldout-dev-loop#<new tag> upgrade`, commit the diff, open a PR. Each person gets the new version with `git pull`.
- **After `upgrade`:** run `check_setup.py`. A `source_dirs missing` line under "User actions required" lists the top-level folders of the repo: add the `source_dirs` key to `dev-loop.json` in the upgrade PR (see `docs/configuration.md`). `upgrade` writes no key. That PR runs CI and waits for a human merge, because the key is a trusted key. Until it reaches `main`, the spec-tester wall keeps `src/ app/ backend/ frontend/`.
- **Rollback of a project:** `git revert` the upgrade commit.
- **Rollback of a machine to 1.4:** `npx github:tayebchlyah/heldout-dev-loop#v1.4.0` reinstalls the global install. Do not keep it on a machine whose projects use the kit.

Upgrade notes per version (board, seal, 2.0.0): [`reference/kit.md`](../reference/kit.md) `## Upgrade`.

## Upgrade to 2.0.0

2.0.0 breaks these:

- Stages chain under an autonomy grant. "One stage per invocation" is gone.
- Tracks S, M and L. A Track M or L spec needs `feature:` and `track:` in its frontmatter (a 1.x spec still resolves by its title).
- Stage 1 uses Pocock `grilling` only. Superpowers `brainstorming`, `test-driven-development`, `systematic-debugging` and `finishing-a-development-branch` are denied in the project. Pocock `tdd`, `diagnosing-bugs` and `pr` replace them.
- The feature gate has three reviewers. `simplify`, the Codex `simplifier` agent and the mutation step are gone. Mutation is a nightly job.
- The 1.4 `vault_notes:` notes fallback, the `vault_notes:` SessionStart detection and the `--global` install are gone.
- User settings: remove the user-level denies of `executing-plans`, Pocock `tdd` and Pocock `diagnosing-bugs` from `~/.claude/settings.json`. Codex: install Pocock `tdd`, `diagnosing-bugs`, `pr` and `prototype`.

From 1.4.0: write `dev-loop.local.json` with your notes path, run `npx github:tayebchlyah/heldout-dev-loop#v2.0.0 uninstall-global` (try `--dry-run` first, and keep a personal vault guard first), then `init`, change the user deny list, install the Codex skills, rename `CONTEXT.md` to `GLOSSARY.md` if you want, and run `check_setup.py`.

From 1.5.x: run `upgrade` on a branch, then the user steps above.

Rollback: `git revert` the upgrade commit, then `npx github:tayebchlyah/heldout-dev-loop#v1.4.0` on each machine for a 1.4 project.
