# dev-loop

Stage-driven development loop for Claude Code and Codex. One stage per invocation: detect, run, stop.

## Install

```bash
npx github:tchlyah/dev-loop
```

Flags: `--yes`, `--dry-run`, `--harness=claude|codex|both`, `--vault=<path>`, `--link`.

The vault can also come from `DEV_LOOP_VAULT`. If no vault is set, `~/.agents/dev-loop.config.json` gets `vault: null`, the installer says so, and `check_setup.py` reports it as a top-level failure: `missing vault in dev-loop config: <path>. Rerun npx github:tchlyah/dev-loop --vault=<path>`.

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

Link mode points `~/.agents` and the harness homes at this clone. An edit applies to the next session. A change to a fragment file needs a new run.

```bash
npm test                                # installer suite
python3 skill/scripts/test_dev_loop.py  # skill suite
```

## Verify

```bash
python3 ~/.agents/skills/dev-loop/scripts/check_setup.py --harness claude --repo .
python3 ~/.agents/skills/dev-loop/scripts/check_setup.py --harness codex --repo .
```
