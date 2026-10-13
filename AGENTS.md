# AGENTS.md

## Purpose

dev-loop: stage-driven development loop skill for Claude Code and Codex, plus its installer. One stage per invocation: detect repo/vault state, run that one stage, stop.

## Repo map

- `bin/install.mjs` — installer entry point. Commands: `init`, `upgrade`, `uninstall-global`, `--global` (the 1.4 flow, kept for 1.5.x only). No command: usage, exit 1. Flags: `--yes`, `--dry-run`, `--force`, `--link` (only with `--global`), `--harness=claude|codex|both`, `--notes=<path>` (`init`), `--vault=<path>`. Also reads `DEV_LOOP_VAULT`.
- `lib/` — installer internals: `args.mjs` (flag parsing), `config.mjs` (config load/write), `fsops.mjs` (copy/link/backup), `merge-json.mjs`, `merge-lines.mjs`, `merge-toml.mjs` (per-format merge routing), `plan.mjs` (builds the install plan), `kit.mjs` (project kit file list, path rewrite), `manifest.mjs` (`.dev-loop/MANIFEST.json` write and compare), `project.mjs` (`init` and `upgrade`), `uninstall.mjs` (`uninstall-global`).
- `skill/SKILL.md` — the dev-loop skill payload. `skill/references/` — harness-specific reference docs (`claude.md`, `codex.md`). `skill/scripts/check_setup.py` — post-install verifier. `skill/scripts/test_dev_loop.py` — skill regression suite.
- `claude/` — Claude Code payload: agents, hooks, `settings.fragment.json` (merged into `~/.claude/settings.json`).
- `codex/` — Codex payload: agents (`.toml`), hooks, `config.permissions.toml`, `hooks.fragment.json`, `rules/default.rules` (line-merged only, never copied or linked: it holds the user's own allowlist).
- `test/*.test.mjs` — installer suite (Node's built-in test runner).

## Build and test

```bash
npm test                                # installer suite: node --test test/*.test.mjs
python3 skill/scripts/test_dev_loop.py  # skill suite
```

Both must pass before any commit. `.github/workflows/ci.yml` runs both on every PR and on `main`. Merge a PR only when its `test` check is green.

## Project kit (what `init` and `upgrade` write, repo-relative)

| Target | Content | How |
| --- | --- | --- |
| `dev-loop.json` | `version`, `harnesses` | write |
| `dev-loop.local.json` | `notes` path, gitignored (`init` adds the `.gitignore` line) | write, only with a notes path |
| `.dev-loop/MANIFEST.json` | version, sha256 of each kit file | write |
| `.dev-loop/hooks/claude/`, `.dev-loop/hooks/codex/` | shell walls | kit |
| `.agents/skills/dev-loop/` | skill, harness refs, scripts | kit |
| `.claude/skills/dev-loop` | link to `../../.agents/skills/dev-loop` | link |
| `.claude/agents/`, `.codex/agents/` | subagents | kit |
| `.codex/rules/dev-loop.rules` | Codex command rules | kit |
| `.claude/settings.json`, `.codex/hooks.json`, `.codex/config.toml` | hook entries, deny rules, permission tables | merge |

MANIFEST rule: kit files are never edited in a project. Change dev-loop, then run `upgrade`. `upgrade` refuses on a local edit or a deleted kit file unless `--force`. A kit file gets no backup (git holds it). A merged file gets `<file>.bak-<timestamp>`.

## Global install targets (`--global`, the 1.4 flow, 1.5.x only)

| Target | Content | How |
| --- | --- | --- |
| `~/.agents/skills/dev-loop/` | skill, harness refs, scripts | copy or link |
| `~/.agents/dev-loop.config.json` | vault path (nullable), user name, harnesses | write |
| `~/.claude/skills/dev-loop` | link to the skill | link |
| `~/.claude/agents/`, `~/.claude/hooks/` | subagents, shell walls | copy or link |
| `~/.claude/settings.json` | hook entries + 3 deny rules | merge |
| `~/.codex/agents/` | Codex agent definitions | copy always; Paseo rejects linked agent TOMLs |
| `~/.codex/hooks/` | Codex hooks | copy or link |
| `~/.codex/hooks.json`, `~/.codex/config.toml` | hook entries, permission tables | merge |
| `~/.codex/rules/default.rules` | line merge only | merge, never copy/link |

Backups: every file the installer replaces goes to `<file>.bak-<timestamp>`. A replaced skill goes to `<root>/backups/skills/<name>.bak-<timestamp>` instead (for example `~/.agents/backups/skills/`), because a backup inside a skills folder is discovered as a second skill. It never removes or reorders an entry it did not add. On a `config.toml` table collision (user already declares a table the fragment also declares), the user's table wins and the installer prints one line naming it.

Vault: may be unset. Then `~/.agents/dev-loop.config.json` has `vault: null`, the installer says so, and `check_setup.py` reports it as a plain top-level failure line, not under "User actions required": `missing vault in dev-loop config: <path>. Rerun npx github:tchlyah/dev-loop --vault=<path>`.

## Verify after install

Project mode (applies when `dev-loop.json` exists):

```bash
python3 .agents/skills/dev-loop/scripts/check_setup.py --harness claude --repo .
python3 .agents/skills/dev-loop/scripts/check_setup.py --harness codex --repo .
```

Global mode (1.4):

```bash
python3 ~/.agents/skills/dev-loop/scripts/check_setup.py --harness claude --repo .
python3 ~/.agents/skills/dev-loop/scripts/check_setup.py --harness codex --repo .
```

Ruling R10: right after a correct Codex install, `check_setup.py --harness codex` still fails two things no installer can fix, printed under "User actions required":
1. Five trusted-hook-hash entries — Codex writes those only after the user runs `/hooks` and trusts the dev-loop hook definitions.
2. Six third-party skills dev-loop does not ship: `domain-modeling`, `grilling`, `codebase-design`, `code-review`, `research`, `handoff`.

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
- `rules/default.rules` and the three merged config files (`~/.claude/settings.json`, `~/.codex/hooks.json`, `~/.codex/config.toml`) are always merged, never copied or linked wholesale.

## Gotchas

- The repo is private. Nothing may assume an anonymous reader: no badges, no links that only resolve for a public repo. The `npx github:tchlyah/dev-loop` install command works only for someone whose Git or SSH credentials already reach it.
- `--link` mode links skill and hooks. Codex agent TOMLs stay copied for Paseo compatibility. Agent or fragment edit needs rerun.

## Working principles

- Say your assumptions out loud. If two readings of a request lead to different work, ask before building. If a simpler approach exists, say so.
- Write the minimum code that solves the problem. No speculative abstractions, no unrequested flexibility, no error handling for impossible cases.
- Change only what the request needs. Match the surrounding style. Mention unrelated dead code rather than deleting it. Remove only the orphans your own change created.
