# Notes migration (Stage 0)

The `check_setup.py` `old-notes-layout` line belongs to Stage 0. Command: `python3 .agents/skills/dev-loop/scripts/devloop.py notes-migrate [--repo <path>] [--apply --plan <hash>]`. One JSON object on stdout.

1. Every machine that shares the notes folder upgrades the kit first.
2. Dry run first: `notes-migrate` changes nothing. Show the user `moves`, `outside_links`, `repo_links`, `git_case`, `gitignore` and `blocked`. A `blocked` item (exit 1) changes nothing: fix its cause, run the dry run again.
3. `notes-migrate --apply --plan <plan_hash>` only after the user confirms. A plan that changed since the dry run: `plan-changed`, nothing changed; run the dry run again.
4. Delivery never runs `notes-migrate`: it moves user files.
5. After `--apply`, fix each `outside_links` and `repo_links` item only after the user confirms it.
6. Give the user the `git_case` commands to run from `$NOTES`. The kit never runs a git write in the notes repo.

## Recovery

A run that finds the journal `$NOTES/.notes-migrate.json` resumes it and needs no `--plan`. A journal of another host, or one that fails its checks: `locked` or `io`, nothing changes. The manual fix is in the error `message`: run `--apply` on the host it names, or check the journal, delete it (and the lock, once that host is idle) and start a new dry run.
