# Project kit (what `init` and `upgrade` write, repo-relative)

| Target | Content | How |
| --- | --- | --- |
| `dev-loop.json` | `version`, `harnesses`; optional `commands` (keys `gate`, `stack_up`, `stack_reset`, `acceptance`, `acceptance_failed`, non-empty strings), `merge` (`auto` default, or `human`), `merge_human_paths` (list of strings, each non-empty after strip, no trailing `/` (write `src/auth/*`), `fnmatchcase` patterns on the repo-relative path, `*` matches `/`: a changed path that matches makes the PR wait; `check_setup.py` fails another shape), `file_size_exempt` (same shape as `merge_human_paths`, paths `size-check` skips; `check_setup.py` fails another shape; `init` and `upgrade` never write it), `verify` (`false`, or `skill`, `base_url`, optional `setup`, `storage_state`; `ui` in `stacks:` and no `verify`: `check_setup.py` fails), `second_review` (JSON boolean, the project lock of `second-review`, read from `<main-ref>` only; `init` and `upgrade` never write it), `security_audit` (`{"images": {<name>: <image>}}`, name `^[a-z0-9][a-z0-9_-]*$`, the images `sandbox-run.py` may start; another shape: `check_setup.py` fails; images set and `docker info` failing, or an image not pulled: a "User actions required" line), `release_notes` (`"cliff"` or `"written"`, no key: `cliff`; another value or type: `check_setup.py` fails), `version_files` (list of repo-relative paths, each a non-empty string with no leading `/` and no `..` segment; no key or `[]`: the version comes from the tag only; `check_setup.py` fails another shape and a listed path that does not exist; `/release` proposes it on the first run), `commands.release_prepare`, `commands.release_check`, `commands.release_verify` (optional non-empty strings; `release_prepare` runs in the release branch before the release commit, `release_check` after it, `release_verify` in the main checkout after the GitHub Release is published; each gets `RELEASE_VERSION` (`X.Y.Z` or `X.Y.Z-rc.N`) and `RELEASE_TAG` in its environment; non-zero exit stops `/release`); `init` and `upgrade` never write these keys | write |
| `dev-loop.local.json` | `notes` path, gitignored (`init` adds the `.gitignore` line); optional `"board_dataview": false` (`check_setup.py` skips the Dataview check), `"board_vault_git": "shared"` (skips the vault `.gitignore` check), `second_review` (the user lock: `false`, or `{"codex": ..., "claude": ...}`, each `false` or `{"model": ..., "effort": ...}`; Shaping writes it, `init` and `upgrade` never do) | write, only with a notes path |
| `.gitignore` | `init` adds the `dev-loop.local.json` line; `init` and `upgrade` add `.claude/worktrees/` (the Claude feature worktrees) when `claude` is a harness. A line already there is kept; a missing final newline is fixed; never rewritten otherwise | append |
| `.dev-loop/MANIFEST.json` | version, sha256 of each kit file | write |
| `.dev-loop/hooks/claude/`, `.dev-loop/hooks/codex/` | shell walls (Claude `deny-acceptance-to-subagents.sh`, `git-guard.sh` and its checker `git-guard.py`, `deny-path.sh`, `deny-ask.sh` (PreToolUse `AskUserQuestion`: deny in a feature worktree, the autonomy grant), `worktree-wall.sh` (PreToolUse `Edit|Write|NotebookEdit`: deny an edit into a feature worktree from a session whose cwd is another checkout); Codex `acceptance-wall.sh`, `git-guard.sh` and `git-guard.py`, `deny-path.sh`), Claude `pause.sh` (StopFailure `rate_limit`: `devloop.py pause --event`, CLI of the cwd top level), `seal.sh` (SubagentStop `stop`, Stop `turn`, Codex SubagentStart `start`), and `board.sh` (SessionStart, UserPromptSubmit, PostToolUse: background `devloop.py board`, CLI taken from the main worktree; PostToolUse `Bash` runs it only after `task-start`, `task-done`, `sdd-workspace`, `git commit`, `git worktree add`, `gh pr create`, `gh pr merge`, Claude also on `TaskCreate|TaskUpdate`; `gate.sh` runs it on Stop and SubagentStop, Claude `gate-snapshot.sh` on SubagentStart) | kit |
| `.agents/skills/dev-loop/` | skill, harness refs, scripts; no test files (the suites live in `tests/` of this repo) | kit |
| `.claude/skills/dev-loop` | link to `../../.agents/skills/dev-loop` | link |
| `.agents/skills/pause/`, `.agents/skills/continue/`, `.agents/skills/release-audit/`, `.agents/skills/garden/`, `.agents/skills/release/`, `.agents/skills/release-notes/` | from `skills/<name>/` (`KIT_SKILLS` in `lib/kit.mjs`), every harness: `pause` (user-invoked only: Claude `disable-model-invocation: true`, Codex `agents/openai.yaml` `allow_implicit_invocation: false`) writes the pause note; `continue` wakes stopped subagents or takes over from the note; `release-audit` (same user-invoked rule) runs the full security audit and records it; `garden` (same user-invoked rule) runs the weekly `## Gardener` triage; `release` (same user-invoked rule) runs a full release or release candidate; `release-notes` (same user-invoked rule) drafts or rewrites the written `CHANGELOG.md` section | kit |
| `.claude/skills/pause`, `.claude/skills/continue`, `.claude/skills/release-audit`, `.claude/skills/garden`, `.claude/skills/release`, `.claude/skills/release-notes` | links to `../../.agents/skills/<name>`, with `claude` in `harnesses` | link |
| `.claude/agents/`, `.codex/agents/` | subagents (Codex `planner.toml`: the Stage 4 plan writer) | kit |
| `.codex/rules/dev-loop.rules` | Codex command rules | kit |
| `.claude/settings.json`, `.codex/hooks.json`, `.codex/config.toml` | hook entries, deny rules (5 Superpowers skill denies, `.env` reads, the Claude repo contract: 3 caveman agent denies), `env` (`ENABLE_STOP_REVIEW`, `ENABLE_COMMIT_REVIEW`, `SG_PUSH_SWEEP` = `0`, `CLAUDE_CODE_ENABLE_TODO_TOOLS` = `1`, never overriding a user value), permission tables; top-level Claude keys `effortLevel` `high` and `autoCompactWindow` `400000` (`autoCompactWindow` a safety net), and the Codex root key `model_reasoning_effort = "high"`, each added only when absent (a user overrides a Claude value in `.claude/settings.local.json`) | merge |

MANIFEST rule: kit files are never edited in a project. Change dev-loop, then run `upgrade`. The walls deny a file-tool edit of a kit file to every caller (Claude `Edit`, `Write`, `NotebookEdit`; Codex `apply_patch`), `seal-check` reports any other route as `kit-changed`. Both walls also resolve a relative path against each folder a `cd` or `pushd` earlier in the command moves to. Accepted residual: an agent that edits the checker itself, or rewrites a MANIFEST hash with its edit, passes `seal-check`; a folder held in a variable (`d=tests; cd $d`) passes the walls' `cd` rule, the audit still records a read; only the PR diff shows the first. `upgrade` refuses on a local edit or a deleted kit file unless `--force`. `init`, and `upgrade` for a kit file or link path the old MANIFEST does not list, refuse a file of the user at a kit file path (other content), or a link with another target at a kit link path, unless `--force`. `--force` moves the file to a `.bak-` backup beside it, the link to `.claude/backups/skills/<name>.bak-<stamp>`. A file or folder at a kit link path is refused even with `--force`. A kit file gets no other backup (git holds it). A merged file gets `<file>.bak-<timestamp>`. Merge never removes or reorders an entry it did not add. Exceptions: an old dev-loop SessionStart command (the 1.4 and 1.5 texts, `LEGACY_SESSION_START` in `merge-json.mjs`) is replaced in place and reported as removed; dev-loop's own hook entries are refreshed in place; the three dead Codex profile tables 2.1.1 wrote (`LEGACY_PROFILE_TABLES` in `merge-toml.mjs`) are removed when unchanged. On a `config.toml` table collision, the user's table wins and the installer prints one line naming it.

Codex hook timeout and trust: Codex runs the tool command when a PreToolUse hook fails or times out (a run is `Failed`, only a deny is `Blocked`; no setting blocks instead, unlike Claude `onFailure: block`), so the two Codex walls `git-guard.sh` and `acceptance-wall.sh` get `timeout` 30. Codex skips an untrusted hook without a message, and a hook whose entry changed (its trust hash covers the whole entry, `timeout` too) until `/hooks` trusts it. `check_setup.py --hooks-trust` and `session-start.sh` name each hook event (`<event>:<group>:<hook>` key) with a missing or changed trust hash, once `session-start.sh` itself is trusted. `init` and `upgrade` print a `/hooks` line when they add or change a Codex hook entry.

Codex `dev-workspace` and local sockets: the profile runs its network through the Codex managed proxy (`[features] network_proxy = true`, `mode = "full"`, `allow_local_binding = true`) and allows only the hosts of `[permissions.dev-workspace.network.domains]` (GitHub, npm, PyPI, Go modules, the Claude API, the Playwright downloads). A sandboxed command reaches no local unix socket, so a container engine cannot read the denied `.env` files or write outside the workspace through it. Docker work (`$release-audit`, a compose test stack) uses the `dev-docker` profile: it extends `dev-workspace` and opens every local unix socket (a full bypass); start the session with `codex -c default_permissions=dev-docker`. Limits: host loopback TCP stays reachable, so a Docker engine on loopback TCP (an exposed daemon port, a Testcontainers Cloud context) is still a bypass; keys of the user layer (`~/.codex/config.toml`) that the project does not set still apply; verified on macOS with codex-cli 0.165.0 only. An SSH key held only in an agent needs the agent socket in `[permissions.dev-workspace.network.unix_sockets]` (`"<socket path>" = "allow"`); git over SSH with a key file works through the proxy.

Notes path: `skill/scripts/notes_path.py` resolves it, for every `devloop` command: `DEV_LOOP_NOTES` (relative to the `--repo` worktree), then `dev-loop.local.json` of the main worktree, then `notes` in `dev-loop.json` read from the main ref (`refs/remotes/origin/main`, else `refs/heads/main`, via `git show`, never the disk copy: a branch checked out in the main worktree cannot change it; no main ref or no file on it counts as no `notes` key; a folder outside git has no main ref, so its `dev-loop.json` gives no notes path). `dev-loop.local.json` is read from disk. A feature worktree's own two files are never read. A main worktree `dev-loop.local.json` that git tracks: error naming `tracked` and `git rm --cached dev-loop.local.json`, unless `DEV_LOOP_NOTES` is set. No 1.4 fallback. Glossary: `GLOSSARY.md`, else `CONTEXT.md`. Unset: `check_setup.py` reports a plain top-level failure line, not under "User actions required": `missing notes path for <repo>. Write {"notes": "<path>"} to <main worktree>/dev-loop.local.json`.

## Verify after install

Applies when `dev-loop.json` exists. Without it, `check_setup.py` prints one failure line, `no dev-loop.json in <repo>. Run npx github:tchlyah/dev-loop#<tag> init`, exit 1:

```bash
python3 .agents/skills/dev-loop/scripts/check_setup.py --harness claude --repo .
python3 .agents/skills/dev-loop/scripts/check_setup.py --harness codex --repo .
```

Ruling R10: right after a correct install, `check_setup.py` still fails things no installer can fix, printed under "User actions required" (items 1 and 2: `--harness codex`; items 3 and 4: both harnesses):
1. Trusted project and trusted-hook-hash entries: Codex writes those only after the user trusts the project and runs `/hooks` to trust the dev-loop hook definitions.
2. Ten Pocock skills dev-loop does not ship: `grilling`, `domain-modeling`, `codebase-design`, `code-review`, `tdd`, `diagnosing-bugs`, `pr`, `prototype`, `wayfinder`, `retro`.
3. The Cloudflare `security-audit` skill (the Stage 6 security verifier's prompt), pinned at tree `ccbc33ed119b28c3ef7b44a7e2081ab191ad63af` in `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/security-audit` (Claude) or `~/.agents/skills/security-audit` (Codex): `npx skills add https://github.com/cloudflare/security-audit-skill/tree/c1c8a8c1471069fb0e188eeaff69b8e8db6564a8 --skill security-audit --global --agent claude-code codex`.
4. Agent shell options (#165). Claude Code and Codex copy the interactive shell config into their shell snapshots. `noclobber` makes `>` refuse an existing file (an empty issue body, a stale gate JSON); an `-i` alias of `rm`, `cp`, `mv` or `ln` holds a command on a prompt. `check_setup.py` runs `$SHELL -ic` with no terminal on stdin and stdout, as the snapshot does, and names each one it finds. No auto-fix. The fix keeps both in an interactive terminal and turns them off with no terminal. Proven on Prezto 2026-11-01 for Claude Code (a fresh `claude -p` session: `noclobber` off, no `-i` aliases):
   - `~/.zpreztorc`: `[[ ! -t 0 && ! -t 1 ]] && zstyle ':prezto:module:utility' safe-ops 'no'`
   - `~/.zshrc`, after the Prezto `source`: `[[ ! -t 0 && ! -t 1 ]] && setopt CLOBBER`

   Other frameworks: the same test around their own option.

These are expected manual steps, not install defects.

## Release

Two modes, `dev-loop.json` `release_notes`: `cliff` (default: `CHANGELOG.md` and the release body come from `git cliff`) and `written` (the `CHANGELOG.md` section of the tag is written by hand, `/release-notes`; the release body is that section).

`written`: the step of a project `release.yml` that takes the release body from the changelog:

```bash
python3 .agents/skills/dev-loop/scripts/devloop.py release-notes --tag "$GITHUB_REF_NAME" --output CHANGES.md
```

CI skip job. A release PR or a kit upgrade PR changes no code; job `changes` in a project `ci.yml` lets the other jobs skip. It checks out the PR head with `fetch-depth: 0` and the base into `.devloop-base`, and runs the `ci-skip` of the base kit, never the kit of the PR. A kit upgrade PR skips only in the kit-source repo (dev-loop itself), where the base commit's installer builds the reference kit; in any other project it runs CI and the next release audit covers the new kit copies. A base without `ci_skip.py` writes `skip=false`, and so does a PR whose base is not the default branch. A retarget is a `pull_request` `edited` event. List `edited` in the `pull_request` types, with no job-level condition on it: a retarget then runs `changes` again on the new base, and every other job unless `changes` skips them for that base. A push skips only to the default branch.

```yaml
  changes:
    runs-on: ubuntu-latest
    outputs:
      skip: ${{ steps.skip.outputs.skip }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ github.event.pull_request.head.sha || github.sha }}
          fetch-depth: 0
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ github.event.pull_request.base.sha || github.event.before }}
          path: .devloop-base
      - id: skip
        env:
          TARGET: ${{ github.base_ref || github.ref_name }}
          DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}
        run: |
          if [ "$TARGET" != "$DEFAULT_BRANCH" ] || [ ! -f .devloop-base/.agents/skills/dev-loop/scripts/ci_skip.py ]; then
            echo "skip=false" >> "$GITHUB_OUTPUT"
          else
            python3 .devloop-base/.agents/skills/dev-loop/scripts/devloop.py ci-skip --repo . --base "${{ github.event.pull_request.base.sha || github.event.before }}" --github-output
          fi
```

Each other job:

```yaml
    needs: changes
    if: ${{ !cancelled() && needs.changes.outputs.skip != 'true' }}
```

## Upgrade

Upgrade: `npx github:tchlyah/dev-loop#<tag> upgrade` on a branch, diff goes into a PR. Rollback: `git revert` the upgrade commit; a machine back to 1.4: `npx github:tchlyah/dev-loop#v1.4.0`. Before the first `init` on a machine: `uninstall-global`, else Claude Code runs the global skill and Codex runs neither. `uninstall-global` removes a 1.4 install from its own frozen lists (files, 1.4 and 1.5 hook commands).

Notes layout upgrade: `notes-migrate` moves a notes folder in the old layout to the feature folder layout (`reference/cli/notes-migrate.md`). Every machine sharing the notes folder upgrades the kit first, then one runs it.

Board upgrade from 2.1.0: after the kit upgrade lands on `main`, restart each in-flight Delivery from the main checkout (an old-kit worktree runs the 2.1.0 writer: wrong-time duplicate in `dev-loop-board/`). Codex: trust the new PostToolUse hook with `/hooks`.

Seal upgrade: ship each in-flight feature before merging the kit upgrade. A Stage 3 commit made before has no `Dev-Loop-Seal` trailer, and `seal-check` reports it `unsealed`. Codex: trust the new hooks (`seal.sh`, `acceptance-wall.sh`) with `/hooks`. `upgrade` removes the three dead Codex profile tables (`dev-implementer`, `dev-spec-tester`, `dev-read-only`) from `.codex/config.toml` when unchanged, and refreshes dev-loop's own hook entries in place (a user field added to a dev-loop hook is lost).

Codex wall timeout upgrade: `upgrade` sets `timeout` 30 on the Codex `git-guard.sh` and `acceptance-wall.sh` entries. Codex skips both walls until `/hooks` trusts the changed entries again, so `upgrade` prints the `/hooks` line.

Tool walls upgrade (#97): `upgrade` replaces the `deny-acceptance-to-subagents.sh` and `acceptance-wall.sh` entries by command, with the matchers that add `LSP`, `Monitor`, the MCP tools and Codex `view_image`, and writes `disallowedTools` into `.claude/agents/implementer.md` and `spec-tester.md`. Codex: the new matcher changes the hook `trusted_hash`, so Codex skips `acceptance-wall.sh` until `/hooks` trusts it again; until then `check_setup.py --harness codex` reports `pre_tool_use` untrusted.

Git guard on Monitor (#154): `upgrade` moves the Claude `git-guard.sh` entry from matcher `Bash` to `Bash|Monitor`, so a `Monitor` command gets the same push, `gh`, reset and seal decisions as a Bash command. No user step.

Hook git config pin (#155): every kit hook script (Claude and Codex) sources `git-pin.sh` before its first git or CLI call. It appends to the `GIT_CONFIG_*` entries, as `scripts/gate.sh` does: `core.fsmonitor=false`, `log.showSignature=false`, and for each filter driver in the config, `clean` and `process` empty and `required=false`. A hook that moves to another repo directory calls `git_pin_repo <dir>` for it. A `core.fsmonitor` program, a clean or process filter, and a `gpg.program` on a signed commit no longer run from a hook. Per-call flags cover the rest: `--no-textconv` and `--no-ext-diff` on the tree hash diff and the audit patch ids, `--no-filters` on the tree hash `hash-object`. `core.pager` needs no pin (no tty) and `diff.external` needs none (`--no-ext-diff`). The Stop gate hooks treat a failed `git status` as a dirty tree and run the gate. `git-pin.sh` is a new kit file, so `upgrade` adds it. No user step; Codex hook entries are unchanged, so no `/hooks` trust.

Effort upgrade: `upgrade` adds `effortLevel`, `autoCompactWindow`, the Codex root `model_reasoning_effort` and the `worktree-wall.sh` entry, writes `.codex/agents/planner.toml`, and keeps each value the user set.

Codex network scope upgrade: Update Codex to 0.165.0 first (`check_setup.py` fails below it). `upgrade` merges `[features]`, `[permissions.dev-workspace.network]` and `[permissions.dev-workspace.network.domains]` key by key: each missing kit key is added, each project value is kept and named in a `kept your own <table> <key>` line. It adds `network_proxy`, `mode`, `allow_local_binding`, the domains and the `dev-docker` tables. Other tables keep the table-level merge.

3.0.0 upgrade (breaking): from 2.1.x, in this order.

1. Ship each in-flight feature first (Seal upgrade).
2. Update the CLIs to the minimums `check_setup.py` checks (`MIN_VERSIONS`): Claude Code 2.1.295 (`onFailure`), codex-cli 0.165.0 (`network_proxy`, hook `agent_type`). Older CLIs cannot run the new hooks.
3. `npx github:tchlyah/dev-loop#v3.0.0 upgrade` on a branch, one PR, merged to `main`. The output lists each section of `CHANGELOG.md` after the installed version. Make the branch, commit, PR and merge from Claude Code or by hand, not from Codex: once `upgrade` writes `.codex/hooks.json`, Codex skips the changed `git-guard.sh` entry and the new walls until `/hooks`, so a Codex agent would run them with no guard.
4. After the merge, on each machine that runs Codex on the repo: run `/hooks` and trust the new and changed dev-loop hooks. The list is in Seal upgrade and Codex wall timeout upgrade, and for a project still on 2.1.0 also Board upgrade from 2.1.0 (the PostToolUse `board.sh` hook is new from 2.1.0). `upgrade` prints this line.
5. Run `check_setup.py --harness claude|codex --repo .` and act on each line (Verify after install):
   - a `ui` project: `devloop.py verify-init --base-url <url>`, or `"verify": false` in `dev-loop.json`;
   - `old-notes-layout`: `notes-migrate` (Notes layout upgrade);
   - Codex: the Pocock skills `wayfinder` and `retro`;
   - GitHub: "allow rebase merging" on (`ci wait`);
   - `security_audit` set: its images pulled and Docker running;
   - the vault `.gitignore` lines for `Dev Loop Board/` (skipped when `dev-loop.local.json` has `"board_vault_git": "shared"`) and `Security Audit/`, and the Cloudflare `security-audit` skill.
6. After the merge, end each open session and start a new one (a feature in Delivery resumes with `/continue`). The 2.1.x kit has no `kit_drift` `main-newer`, so an old session never stops by itself. From 3.0.0 on, a later upgrade shows `kit_drift` `main-newer` at the next Stage 3, 6 or 7 start, after `git fetch origin`.

What changes for the user after the upgrade:

- The gate also fails on `seal-check` (held-out test commits without their trailers, kit file changes, taint records) and on the file-size ratchet (a new or grown file over 40,000 bytes; `file_size_exempt` lists the files it skips). It reports `weakened` tests.
- An agent's push of a `v*` tag or `gh release create` (the kit guard) runs `release-check`: it needs a current `/release-audit` record and no open security item in `## Deferred`. Claude asks with the verdict, Codex denies on a refusal; reasons in `reference/cli/release-check.md`. A tag pushed from the user's own terminal is not checked. `/release` runs the whole release, audit included. The guard also asks (Claude main session; a subagent and Codex are denied) for gh writes: `gh api` with a method other than `GET` or `HEAD`, `--input`, or fields (GraphQL: a `mutation` or an `@` file); `gh release` other than `list`, `view`, `download`, `verify`, `verify-asset`; the writes of `gh alias`, `ruleset`, `repo`, `workflow`, `secret`, `variable`; `gh pr merge --admin`; `gh run rerun`, `cancel` and `delete` (a rerun of the tag-driven release workflow publishes a release); `gh release create` with `-R`, `--repo` or `GH_REPO`, which the guard cannot check; `gh extension exec`, `gh copilot`, an alias or extension word, and a gh word it cannot read. For pushes it reads abbreviated options (`--mi`, `--ta`), refspec forms (`heads/main`, `tags/v1`, `refs/remotes/`, `HEAD` as destination), `remote.<r>.push`, a `branch.<b>.merge` off a feature branch, and `push.followTags`. Not mapped: a script the agent writes and runs, `git` or `gh` run by another program (`make`, a test runner), `git` behind a wrapper outside its list (`stdbuf`; every `gh` word is read, except after `echo`, `grep`, `ls` and the like), a default branch other than `main` or `master`, and, tracked in #98, `gh auth token` with an HTTP client, `gh ssh-key add`, `gh agent-task create` and the GitHub MCP tools. The decisive control for `main` and `v*` is server side: rulesets with no bypass for the agent's token, the owner's to set and check.
- One session per feature: Shaping (Stages 1, 2), then Delivery (Stages 3 to 7) after the effort hand-off. `upgrade` sets the effort defaults only where the user set none (Effort upgrade).

2.0.0 upgrade (breaking): from 1.5.x, `upgrade` on a branch, then the user steps. From 1.4.0: write `dev-loop.local.json`, `uninstall-global` (`--dry-run` first, keep a personal vault guard first), `init`. User steps: remove the user-level denies of `executing-plans`, Pocock `tdd`, Pocock `diagnosing-bugs` from `~/.claude/settings.json`; Codex: install Pocock `tdd`, `diagnosing-bugs`, `pr`, `prototype`; optional `CONTEXT.md` to `GLOSSARY.md`; run `check_setup.py`.
