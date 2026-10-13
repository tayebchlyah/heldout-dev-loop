# dev-loop

Stage-driven development loop for Claude Code and Codex. One stage per invocation: detect, run, stop.

## Install

```bash
npx github:tchlyah/dev-loop#<tag> init
```

The repository is private, so this works only once your Git or SSH credentials reach it (`git ls-remote git@github.com:tchlyah/dev-loop.git` should succeed). Otherwise npx fails to fetch it.

Flags: `--yes`, `--dry-run`, `--force`, `--harness=claude|codex|both`, `--notes=<path>` (`init`), `--link` (works only with `--global`).

`check_setup.py` reports a repo with no notes path as a top-level failure: `missing notes path for <repo>. Write {"notes": "<path>"} to <main worktree>/dev-loop.local.json`.

## Commands

Run each with a release tag. Run `init` and `upgrade` from the top level of a git repository; `uninstall-global` runs from anywhere:

```bash
npx github:tchlyah/dev-loop#<tag> init              # install the kit into this repository
npx github:tchlyah/dev-loop#<tag> upgrade           # update the kit to the tag's version
npx github:tchlyah/dev-loop#<tag> uninstall-global  # remove the 1.4 global install
npx github:tchlyah/dev-loop#<tag> --global          # run the 1.4 global install (1.5.x only)
```

`init` writes `dev-loop.json`, `.dev-loop/MANIFEST.json`, the skill in `.agents/skills/dev-loop/` and the files of the chosen harnesses (`--harness=claude|codex|both`) under `.claude/`, `.codex/` and `.dev-loop/hooks/`. `--notes=<path>` sets the notes folder in the gitignored `dev-loop.local.json`. Commit the result. Kit files are never edited in a project: change dev-loop, then run `upgrade`. `upgrade` refuses on a local edit unless `--force`. `--link` works only with `--global`. A call with no command prints the usage and exits 1.

The sections below describe the `--global` flow.

## What it installs

| Target | Content |
| --- | --- |
| `~/.agents/skills/dev-loop/` | the skill, its harness references and its scripts |
| `~/.agents/dev-loop.config.json` | user name, harnesses |
| `~/.claude/skills/dev-loop` | link to the skill |
| `~/.claude/agents/`, `~/.claude/hooks/` | the subagents and the shell walls |
| `~/.claude/settings.json` | hook entries and three deny rules, merged |
| `~/.codex/agents/`, `~/.codex/hooks/`, `~/.codex/rules/` | the Codex equivalents |
| `~/.codex/hooks.json`, `~/.codex/config.toml` | merged |

`~/.codex/rules/default.rules` is a line merge only, never a copy and never a link, because it holds the user's own allowlist. The same is true of the three merged configuration files above.

The installer backs up every file it replaces to `<file>.bak-<timestamp>`. A replaced skill goes to `<root>/backups/skills/<name>.bak-<timestamp>` instead, so no harness discovers the backup as a second skill. It never removes or reorders an entry it did not add. If your `~/.codex/config.toml` already declares a table the fragment also declares, your table wins and the installer prints one line naming it.

## After installing

`check_setup.py` can report two things under "User actions required" that no installer can fix. Both are expected, not a broken install:

1. **Trusted Codex hooks.** Codex only trusts hook definitions after you run `/hooks` inside Codex and approve the dev-loop hooks. Until you do, five trusted-hook-hash checks fail.
2. **Third-party skills.** dev-loop does not ship `domain-modeling`, `grilling`, `codebase-design`, `code-review`, `research`, or `handoff`. Install these separately from their own source if you want them.

## Develop

```bash
git clone git@github.com:tchlyah/dev-loop.git ~/Projects/tchlyah/dev-loop
cd ~/Projects/tchlyah/dev-loop
node bin/install.mjs --link
```

Link mode links skill and hooks into this clone. Codex agent TOMLs stay regular copied files because Paseo rejects linked definitions at dispatch. Re-run after a Codex agent or fragment edit.

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

With `--global`:

```bash
python3 ~/.agents/skills/dev-loop/scripts/check_setup.py --harness claude --repo .
python3 ~/.agents/skills/dev-loop/scripts/check_setup.py --harness codex --repo .
```
