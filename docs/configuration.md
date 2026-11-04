# Configuration

## Project settings

`dev-loop.json` holds `version` and `harnesses`, and optional keys:

- `commands`: the project's own command for a step. Keys: `gate`, `stack_up`, `stack_reset`, `acceptance`, `acceptance_failed`. Each value is a non-empty string. A missing key falls back to `scripts/gate.sh`, `docker compose` and the stack's test runner. `check_setup.py` fails on any other key.
- `merge`: `auto` (the default) or `human`. With `auto`, Stage 7 merges a Track S or M pull request after green CI and the review wave, when it is a two-way door with a small blast radius. A one-way door or a Track L pull request waits for you. With `human`, every pull request waits for you. Stage 7 runs `devloop.py merge-check` to decide, and `merge_human_paths` in `dev-loop.json` lists path patterns whose changes always wait for you.
- `security_audit`: `{"images": {"<name>": "<image>"}}`, the container images the release audit sandbox may start (name: lower case letters, digits, `_` and `-`). Pull each image first; `check_setup.py` reports a stopped Docker engine or a missing image. No key: the audit runs source-only. Add `security-audit/` to the vault `.gitignore`.

## Notes folder

Feature folders (`Features/<YYYYMMDD> <Title>/` with `Framing.md`, `Spec.md`, `Plan.md`) live in a notes folder, any local folder. `skill/scripts/notes_path.py` finds it, first match wins:

1. `DEV_LOOP_NOTES` environment variable.
2. `notes` in `dev-loop.local.json` in the current worktree, then in the main worktree. This file is gitignored: each person and each machine sets its own path. A new worktree has no copy, so it uses the main worktree's file.
3. `notes` in the committed `dev-loop.json`, read from `main` (`origin/main`, else `main`): a key committed only on a branch is ignored. For a team that keeps its notes in the repository (for example `docs/dev-loop`).

A path is absolute, starts with `~`, or is relative to the repository root. The folder must exist. `init --notes=<path>` writes the local file.
