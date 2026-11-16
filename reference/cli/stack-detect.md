# stack-detect

- `stack-detect [--repo <path>]`: read a repo tree and propose its code units from `presets.json` (the kit file beside `SKILL.md`). `--repo` default: the cwd. No command runs, no file is written; the agent shows the proposal and confirms it with the user (Stage 0, `references/bootstrap.md`).
- Output, exit 0, also when nothing matches: `{"stacks": [{"path": "."|"<folder>/", "preset": "<name>", "unit": "<unit>", "commands": {"lint", "typecheck", "test", "junit": {"command", "files", "package", "setup"}}}], "capabilities": [...], "source_dirs": [...], "unknown": [...]}`. `commands` values, `junit` keys included, are the preset values (`typecheck` can be null).
- Exit 1: `{"error": "presets.json cannot be read: <reason>"}`. Exit 2: `{"error": "stack-detect: <path> is not a folder"}`, or a usage error.

## Rules

- Candidates: `.`, each top-level folder, each child folder of `backend/`. Skipped: a name that starts with `.`, `node_modules`, the held-out suite folder, and every folder symlink. The filesystem is read, tracked or not, except that a folder git ignores is skipped as a candidate and as an `unknown` candidate (one `git check-ignore` call; a repo that is not a git work tree, or a git error, skips nothing).
- Match: a folder matches a preset when one `detect` alternative has each of its globs matching the name of a file directly in the folder. Presets are tried in file order; the first match wins (one preset per folder).
- uv workspace: the root `pyproject.toml` parses as TOML and holds `tool.uv.workspace` with `members`, a list of strings; else no workspace. The `members` and `exclude` globs (`exclude` not a list of strings: no `exclude`) resolve from the root, on folders; an excluded folder is removed. A member is kept when it holds a `pyproject.toml` file, is not `.`, passes the `source_dirs` entry rules, has no symlink on its path, and no skip rule applies (a path part that starts with `.`, `node_modules`, the held-out suite or under it, git-ignored). Each kept member, at any depth, is a unit with preset `uv`, in place of the match of that folder.
- Root with a workspace: when `.` matched a `python` family preset, it is a unit only when its `pyproject.toml` has a `[project]` table and a folder of its preset `source_dirs` or `test_dir` exists; else `.` is no unit and holds no folder for the nested member rule. A root `package.json` still counts for `ui`. A root of another family stays a unit.
- Nested member: a matched folder that is not a uv workspace member, under a matched folder of the same `family` (`.` holds every folder, a dropped root none). It gets no `stacks` entry; its path goes into `source_dirs`.
- `stacks`: the uv workspace members and the matched folders that are not nested members, `.` first, then by path. `unit`: `root` for `.`, else the path without its final `/`, each `/` as `-`.
- `capabilities`: starts from the `check_setup.py` proposal (`ui` when the root `AGENTS.md` `stacks:` line names it, `backend` when the root holds a compose file), then `ui` is added first when the `package.json` of a matched folder lists a `ui_frameworks` name in `dependencies` or `devDependencies` (a file that does not parse adds nothing). Compose files: `compose.yaml`, `compose.yml`, `docker-compose.yaml`, `docker-compose.yml`.
- `source_dirs`: each `stacks` path that is not `.`, each nested member path; for a unit at `.`: its preset `source_dirs` and `test_dir` folders that exist (a folder, not a symlink). Sorted, never the held-out suite or under it.
- `unknown`: each top-level folder (same skip rules) that holds a file at any depth (dot folders, `node_modules` and the held-out suite not walked, no link followed) and that no matched folder other than `.` and no `source_dirs` entry equals or holds. A folder that holds a claimed path is listed only when it holds a file outside every claimed path (claimed subfolders not walked). The agent asks the user about each one: a code unit, code that is not a unit, or no code.

## Callers

Stage 0 (`references/bootstrap.md`): the first setup, and a feature that adds a code unit.
