# stack-detect

- `stack-detect [--repo <path>]`: read a repo tree and propose its code units from `presets.json` (the kit file beside `SKILL.md`). `--repo` default: the cwd. No command runs, no file is written; the agent shows the proposal and confirms it with the user (Stage 0, `references/bootstrap.md`).
- Output, exit 0, also when nothing matches: `{"stacks": [{"path": "."|"<folder>/", "preset": "<name>", "unit": "<unit>", "commands": {"lint", "typecheck", "test", "junit": {"command", "package"}}}], "capabilities": [...], "source_dirs": [...], "unknown": [...]}`. `commands` values are the preset values (`typecheck` can be null).
- Exit 1: `{"error": "presets.json cannot be read: <reason>"}`. Exit 2: `{"error": "stack-detect: <path> is not a folder"}`, or a usage error.

## Rules

- Candidates: `.`, each top-level folder, each child folder of `backend/`. Skipped: a name that starts with `.`, `node_modules`, the held-out suite folder, and every folder symlink. The filesystem is read, tracked or not.
- Match: a folder matches a preset when one `detect` alternative has each of its globs matching the name of a file directly in the folder. Presets are tried in file order; the first match wins (one preset per folder).
- Workspace member: a matched folder under a matched folder of the same `family` (`.` holds every folder). It gets no `stacks` entry; its path goes into `source_dirs`.
- `stacks`: the matched folders that are not members, `.` first, then by path. `unit`: `root` for `.`, else the path without its final `/`, each `/` as `-`.
- `capabilities`: `ui` when the `package.json` of a matched folder lists a `ui_frameworks` name in `dependencies` or `devDependencies` (a file that does not parse adds nothing); `backend` when the root holds a compose file (`compose.yaml`, `compose.yml`, `docker-compose.yaml`, `docker-compose.yml`).
- `source_dirs`: each matched path that is not `.`, members included; for a unit at `.`: its preset `source_dirs` and `test_dir`. Sorted, never the held-out suite or under it.
- `unknown`: each top-level folder (same skip rules) that holds a file at any depth (dot folders, `node_modules` and the held-out suite not walked, no link followed) and that no matched folder other than `.` and no `source_dirs` entry equals, holds or lies under. The agent asks the user about each one: a code unit, code that is not a unit, or no code.

## Callers

Stage 0 (`references/bootstrap.md`): the first setup, and a feature that adds a code unit.
