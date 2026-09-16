# dev-loop

Stage-driven development loop for Claude Code and Codex. One stage per invocation: detect, run, stop.

## Install

```bash
npx github:tchlyah/dev-loop
```

The repository is private, so this works only once your Git or SSH credentials reach it (`git ls-remote git@github.com:tchlyah/dev-loop.git` should succeed). Otherwise npx fails to fetch it.

Flags: `--yes`, `--dry-run`, `--harness=claude|codex|both`, `--vault=<path>`, `--link`.

The vault can also come from `DEV_LOOP_VAULT`. A re-run keeps the vault already in `~/.agents/dev-loop.config.json`, so only an explicit new value replaces it; `--vault=` with an empty value is that explicit answer for "no vault" and clears it. If no vault is set, `~/.agents/dev-loop.config.json` gets `vault: null`, the installer says so, and `check_setup.py` reports it as a top-level failure: `missing vault in dev-loop config: <path>. Rerun npx github:tchlyah/dev-loop --vault=<path>`.

## What it installs

| Target | Content |
| --- | --- |
| `~/.agents/skills/dev-loop/` | the skill, its harness references and its scripts |
| `~/.agents/dev-loop.config.json` | vault path, user name, harnesses |
| `~/.claude/skills/dev-loop` | link to the skill |
| `~/.claude/agents/`, `~/.claude/hooks/` | the subagents and the shell walls |
| `~/.claude/settings.json` | hook entries and three deny rules, merged |
| `~/.codex/agents/`, `~/.codex/hooks/`, `~/.codex/rules/` | the Codex equivalents |
| `~/.codex/hooks.json`, `~/.codex/config.toml` | merged |

`~/.codex/rules/default.rules` is a line merge only, never a copy and never a link, because it holds the user's own allowlist. The same is true of the three merged configuration files above.

The installer backs up every file it replaces to `<file>.bak-<timestamp>`. It never removes or reorders an entry it did not add. If your `~/.codex/config.toml` already declares a table the fragment also declares, your table wins and the installer prints one line naming it.

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

```bash
python3 ~/.agents/skills/dev-loop/scripts/check_setup.py --harness claude --repo .
python3 ~/.agents/skills/dev-loop/scripts/check_setup.py --harness codex --repo .
```
