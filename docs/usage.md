# Usage

## Install

```bash
npx github:tayebchlyah/heldout-dev-loop#<tag> init
```

Flags: `--yes`, `--dry-run`, `--force`, `--harness=claude|codex|both`, `--notes=<path>` (`init`).

`check_setup.py` reports a repo with no notes path as a top-level failure: `missing notes path for <repo>. Write {"notes": "<path>"} to <main worktree>/dev-loop.local.json`.

## Commands

Run each with a release tag. Run `init` and `upgrade` from the top level of a git repository; `uninstall-global` runs from anywhere:

```bash
npx github:tayebchlyah/heldout-dev-loop#<tag> init              # install the kit into this repository
npx github:tayebchlyah/heldout-dev-loop#<tag> upgrade           # update the kit to the tag's version
npx github:tayebchlyah/heldout-dev-loop#<tag> uninstall-global  # remove the 1.4 global install
```

`init` writes `dev-loop.json`, `.dev-loop/MANIFEST.json`, the skill in `.agents/skills/dev-loop/`, the `pause`, `continue`, `release-audit`, `garden`, `release` and `release-notes` skills in `.agents/skills/<name>/` and the files of the chosen harnesses (`--harness=claude|codex|both`) under `.claude/`, `.codex/` and `.dev-loop/hooks/`. `--notes=<path>` sets the notes folder in the gitignored `dev-loop.local.json`. Commit the result. Kit files are never edited in a project: change dev-loop, then run `upgrade`. `upgrade` refuses on a local edit unless `--force`. A call with no command prints the usage and exits 1.

## After installing

`check_setup.py` can report three things under "User actions required" that no installer can fix. All are expected, not a broken install:

1. **Trusted Codex hooks.** Codex only trusts hook definitions after you run `/hooks` inside Codex and approve the dev-loop hooks. Until you trust the project and its hooks, the trusted-project and trusted-hook-hash checks fail.
2. **Third-party skills.** dev-loop does not ship the Codex skills `grilling`, `domain-modeling`, `codebase-design`, `code-review`, `tdd`, `diagnosing-bugs`, `pr` and `prototype` (Matt Pocock's skills). Install them separately from their own source.
3. **The security-audit skill.** For both harnesses, the Stage 6 security verifier reads its prompt from Cloudflare's `security-audit` skill, pinned to one commit. Install it with `npx skills add https://github.com/cloudflare/security-audit-skill/tree/c1c8a8c1471069fb0e188eeaff69b8e8db6564a8 --skill security-audit --global --agent claude-code codex`.

## Verify

In a project with `dev-loop.json`:

```bash
python3 .agents/skills/dev-loop/scripts/check_setup.py --harness claude --repo .
python3 .agents/skills/dev-loop/scripts/check_setup.py --harness codex --repo .
```

## Verify skill

A project with `ui` in `stacks:` holds its own verify skill in `.agents/skills/<skill>/`: how an agent launches, checks, drives and cleans up the real app, and a feature map, one file per feature, with the routes and the accessible names a user sees. Stage 0 writes it with `devloop.py verify-init`, then `skill/references/verify.md`. Stage 6 runs `devloop.py drive` on the routes the branch changed, and one subagent checks the layout on the screenshots. Stage 7 adds the before and after screens to the PR body. `"verify": false` in `dev-loop.json` opts out.

## Backups

Every merged file the installer replaces (`.claude/settings.json`, `.codex/hooks.json`, `.codex/config.toml`) gets a backup at `<file>.bak-<timestamp>`. It never removes or reorders an entry it did not add. If your `.codex/config.toml` already declares a table the fragment also declares, your table wins and the installer prints one line naming it.
