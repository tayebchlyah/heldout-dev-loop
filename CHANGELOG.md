# Changelog

## [3.0.0-rc.2] - 2026-11-01

### Highlights

**One session per feature.** A feature now runs in one session: Shaping (Stages 1 and 2, high effort), then Delivery (Stages 3 to 7, medium effort). At spec approval the agent gives you three lines to copy: `/effort medium`, `/compact`, `Start Delivery of <slug>`. The work that needs high effort (spec-tester, the Stage 4 planner, the reviewers) runs in subagents that set their own level, and `status` tells you when your level differs from the one the stage wants.

**Feature folders in the notes.** All notes of one feature live in one folder, `Features/<YYYYMMDD> <Title>/`, with `Framing.md`, `Spec.md` and `Plan.md`. Every folder the kit writes has a Title Case name (`Answers/`, `Maps/`, `Security Audit/`, `Dev Loop Board/`). The new `devloop.py notes-migrate` command moves an old layout and rewrites the links, after you check a dry run.

**Held-out tests stay held out.** Only `spec-tester` can change `tests/acceptance/`: every commit to it carries a seal, walls on Claude Code and Codex stop other agents, and a transcript audit records a read the walls miss. The walls also cover LSP, MCP tools, Monitor and Codex `view_image`: walled agents cannot use LSP or MCP, and a Monitor command is checked as a Bash command. The gate fails on an unsealed commit or a changed installed kit file, and reports each change that makes a test weaker. Codex warns at session start when it skips an untrusted dev-loop hook.

**Release security audit.** `/release-audit` runs a full security audit of the changes since the last audit, in a Docker sandbox with no network, and writes the result into the project note. An agent's push of a `v*` tag, or `gh release create`, runs `release-check` first and shows you why a release is not ready. `/release` runs the whole release: audit, version, notes, release PR, tag and a check of the published release. Each release gets one audit and at most one fix wave: a confirmed `low` or `informational` finding goes to `## Gardener` and does not block, you rule each other item (`fix #n`, `accept`, `close`), and `release-record --fix-wave` records the fixes so they need no second audit.

**Written release notes.** A project can set `release_notes: written` in `dev-loop.json` to get short prose sections in `CHANGELOG.md` in place of the git-cliff commit list. `/release-notes` drafts or rewrites a section from the specs, PRs and commits. CI skips its jobs on a release PR or a kit upgrade PR (`ci-skip`), since they change no code.

**Release candidates.** dev-loop now has pre-release tags `vX.Y.Z-rc.N`, and `init` and `upgrade` accept them. When `main` has a newer kit than your worktree, `status` reports `kit_drift` `main-newer` and the agent asks for a new session, so an old kit never runs a stage.

**Safer merges without you.** `devloop.py merge-check` decides at Stage 7 whether a PR may merge on its own (`auto`) or waits for you (`human`), with its reasons: Track L, paths you list in `merge_human_paths`, or code that changed after the review. Review triage drops noise with five named filters, a fresh verifier checks each security finding, and `Review Yield.md` records which reviewer found each fixed defect. The PR body now ends with a merge danger proof.

**Verify a UI change in a real browser.** `devloop.py verify-init` gives a UI project its own verify skill and feature map. In Stage 6, `devloop.py drive` opens each route the branch changed in Chromium and saves screenshots; a model checks only the layout. At Stage 7 the PR body shows a before and an after screenshot of each route (and a video for a journey).

**Delivery runs without you.** Delivery runs what can answer a question instead of asking, and writes each decision and surprise to one decision log (`devloop.py decide`). A usage limit, an account switch or a harness switch no longer loses the work: `/pause` saves where it stopped, and `/continue` resumes it. Delivery cannot use `AskUserQuestion` in a feature worktree, and it ends with one full report.

**Second reviewer from the other model family.** When the project and you both opt in, `devloop.py second-review` adds a Codex reviewer to a Claude Code review wave, or a Claude reviewer to a Codex one. It looks only for correctness and security defects, never sees the held-out tests or the agent instruction files, and records what was sent to which vendor.

**Simpler Stage 6 and Stage 7.** `devloop.py review-merge` merges the reviewer tables by file and line. `devloop.py acceptance` runs the held-out suite, reads its JUnit, flags a run on a stale container and gives the progress guard verdict. `devloop.py ci wait` gives one verdict for the PR of your local `HEAD`: conflicts, open threads, blocked, fail or pass.

**Codex network through the managed proxy.** The Codex `dev-workspace` profile now sends its network through the Codex managed proxy, to a list of allowed domains, with no local unix socket: a Codex agent cannot reach Docker, so a container cannot read `.env` or write outside the workspace. Loopback and git still work. Docker work (`$release-audit`, a compose test stack) uses the new `dev-docker` profile, which you choose at session start with `codex -c default_permissions=dev-docker`.

**Lease push after a rebase.** The git guards now let the main session push a rebased feature branch with `--force-with-lease --force-if-includes` to its own remote. Every other force push still asks (Claude) or is denied (Codex). `check_setup.py` tells you when the GitHub repo does not allow rebase merges.

**File-size ratchet.** A new or grown file over 40,000 bytes fails the gate, and `seal` refuses such a test file. Each failure names a line where the file can be split, and `file_size_exempt` in `dev-loop.json` lists the files it skips. Big files cost tokens each time an agent reads them.

**Stage cost per feature.** `devloop.py metrics` gives the token cost of each stage of a feature, with sessions, active hours, gate runs, tests added and effort per stage, from the local Claude transcripts. Stage 7 writes one row to `Feature Metrics.md`, so you can see where a feature spends its budget.

**Weekly review.** When the last `/garden` run is 7 days old, `status` on `main` reports `review_due` and Stage 1 waits for the review: `/retro` on the week's sessions (`devloop.py sessions`), then `/garden`. The new `/garden` skill gives each `## Gardener` item a verdict and turns a repeated finding into an automatic check. Stage 0 now writes a `CODING_STANDARDS.md` with a comment rule template.

**Shorter, clearer specs.** A spec now has `## Sources`, at least one `## Non-goals` line, and Catches and Misses lines per seam. Stage 1 can answer a question with no code change in an answer note, and plans work larger than one feature as a map. Coder agents get a short principles index and a benchmark checklist.

**Board ledger.** The progress board folds by project, with your own features first. Each feature, in progress or shipped, is one row with its track and total time; open it to see the time per stage, a time bar per task and the decisions made without you. Each project keeps its 5 latest shipped features, and a time of a day or more shows days and hours.

### Fixes

- The board shows one doing task when the build writes every task brief up front.
- The board no longer says "Needs you" while a background subagent runs.
- Stage 3 ends on the acceptance commit with an `Acceptance: <slug>` line, also when spec-tester only extends earlier test files.
- The board shows no Stage 5 time while a planned feature has no ledger.
- The board server log moves to `dev-loop-board.log.1` when it is over 64 KB, so it no longer grows without a bound.
- Board times use one duration rule and never show a negative span.
- The gate and its test repos run git with `core.fsmonitor` off, so they no longer leave `git fsmonitor--daemon` processes behind.
- The Codex walls (`git-guard.sh`, `acceptance-wall.sh`) get a 30 s timeout: Codex runs the command when a hook times out.
- The Codex trust check reports a dev-loop hook whose entry changed since you trusted it, checks each hook entry (not each event), and skips a hook that is not a command hook.
- `init` and `upgrade` print the `/hooks` step when they add or change a Codex hook.
- `upgrade` keeps your own hook groups, and refuses to overwrite a file of yours at a new kit path unless `--force`.
- The project kit no longer holds the skill test files.
- `git-guard` asks for every `gh` write (`gh api` writes, `gh release` writes, `gh pr merge --admin`, `gh` aliases) and for every spelling of a push that reaches `main`, a tag or a mirror: abbreviated options, refspecs, `push.followTags`, the push remote git picks. Codex prompts for `gh api` and `gh release`.
- `git-guard.sh` and the Stop gate hooks run Python with `-I`, so a module file in the session folder cannot run in the hook. The guard hides a heredoc body only when the shell surely does not run it.
- Every kit skill is walled, and a subagent search (`grep`, `rg`, `git grep`, `find`, Grep, Glob) that reaches `tests/acceptance/` with no effective exclusion is denied. The transcript audit also records a search output line that names a held-out file.
- `merge-check` binds to its own worktree and to one branch for the slug, counts each reviewed patch per occurrence, and counts CI files, configuration outside test folders and `commands` changes as code.
- `acceptance` runs only the command that `main` holds, needs an executed test for green, and reports `short` when a run has fewer distinct tests than an earlier full run of the same suite.
- `ci-skip`, `release-check` and `release-plan` compare kit copies with the kit that the trusted commit's own installer builds. A PR's own MANIFEST is no longer trusted.
- `notes-migrate` refuses a move onto a name that differs only by case or Unicode form, in the plan and again at apply.
- `git-guard` keeps a redirect (`2>&1`, `>/dev/null`, `&>`, `>&`, `>|`) out of the git arguments, so a push with a redirect runs, and a redirect never hides a later push argument.
- A Claude feature worktree is created under `.claude/worktrees/`, where `EnterWorktree` does not ask, and `init` and `upgrade` add that folder to `.gitignore`.
- `upgrade` merges the Codex config by TOML meaning (quote styles, quoted headers, inline tables, multi-line values, dotted keys) and keeps each of your values in the `[features]` and network tables.
- `upgrade` backs up a link of yours at a kit link path outside the skills folder, and refuses it unless `--force`.
- CI skips its jobs only for a PR or a push to the default branch.
- `gate --summary` lists the failed JUnit cases, and the gate ignores the Python `tempfile` probe file a killed test leaves.
- The held-out suite runs in an empty `TMPDIR`, and `scripts/acceptance.sh` fails on a temp leak or a run with no held-out test.
- `metrics` counts only the gate runs of the feature and keeps escaped pipes in a row.
- The board keeps a feature you own until it ships, also when a run does not see it, and writes the kit version only from a valid MANIFEST.
- `scripts/release-verify.sh` never waits on a git credential prompt.
- The audit sandbox copy-back drops `.git` parts and adds `.txt` to every other name, so no target repository or note reaches your machine. The runner waits up to 10 s for leftover processes and lists them.

### Upgrade notes

3.0.0 is a breaking upgrade. From 2.1.x, follow these steps in this order:

1. Ship each in-flight feature first. A Stage 3 commit made before the upgrade has no seal trailer, and `seal-check` reports it `unsealed`.
2. Update the CLIs to the minimums `check_setup.py` checks: Claude Code 2.1.295 and codex-cli 0.165.0. Older CLIs cannot run the new hooks.
3. Run `npx github:tchlyah/dev-loop#v3.0.0-rc.2 upgrade` on a branch, in one PR, merged to `main`. The output lists each `CHANGELOG.md` section after your installed version. Make the branch, commit, PR and merge from Claude Code or by hand, not from Codex: after `upgrade` writes `.codex/hooks.json`, Codex skips the changed `git-guard.sh` and the new walls until `/hooks`, so a Codex agent would run with no guard.
4. After the merge, on each machine that runs Codex on the repo: run `/hooks` and trust the new and changed dev-loop hooks (`seal.sh`, `acceptance-wall.sh`, `git-guard.sh`; for a project still on 2.1.0, also the PostToolUse `board.sh`). `upgrade` prints this line.
5. Run `check_setup.py --harness claude|codex --repo .` and act on each line:
   - a UI project: `devloop.py verify-init --base-url <url>`, or `"verify": false` in `dev-loop.json`;
   - `old-notes-layout`: run `notes-migrate` (see the notes layout item below);
   - Codex: install the Pocock skills `wayfinder` and `retro`;
   - GitHub: turn on "allow rebase merging" (`ci wait`);
   - `security_audit` set: pull its images and keep Docker running;
   - the vault `.gitignore` lines for `Dev Loop Board/` (skipped with `"board_vault_git": "shared"` in `dev-loop.local.json`) and `Security Audit/`, and the Cloudflare `security-audit` skill;
   - Codex network without the managed proxy: `upgrade` adds `features.network_proxy`, the domain list and `dev-docker`; a profile you changed by hand may need the same keys;
   - `scripts/gate.sh` lacks the `core.fsmonitor` guard: export `core.fsmonitor=false` through `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_<n>` and `GIT_CONFIG_VALUE_<n>` before its first git call.
6. After the merge, end each open session and start a new one. A feature in Delivery resumes with `/continue`. The 2.1.x kit has no `kit_drift` `main-newer`, so an old session never stops by itself. From 3.0.0 on, a later upgrade shows `kit_drift` `main-newer` at the next Stage 3, 6 or 7 start, after `git fetch origin`.

- Notes layout: every machine that shares the notes folder upgrades the kit first. Then one machine runs `devloop.py notes-migrate` (a dry run), checks the moves, and runs `devloop.py notes-migrate --apply --plan <hash>`. Until then `check_setup.py` fails `old-notes-layout`, the kit reads both layouts and writes only the new one, and `release-plan`, `release-record` and `release-check` stop while `security-audit/` exists. Delivery never migrates. There is no automatic undo: the log note lists each move.
- The gate now also fails on `seal-check` (held-out test commits without their trailers, changed kit files, taint records) and on the file-size ratchet. It reports `weakened` tests.
- An agent's push of a `v*` tag, or `gh release create`, runs `release-check`: it needs a current `/release-audit` record and no open security item in `## Deferred`. Claude asks with the verdict, Codex denies on a refusal. A tag you push from your own terminal is not checked.
- Session A and Session B are now Shaping and Delivery. `upgrade` sets `effortLevel: high` and `autoCompactWindow` in the project Claude settings, `model_reasoning_effort` in the Codex config, and the new `worktree-wall.sh` entry, only where you set no value. The project `effortLevel` overrides your user setting: change it in `.claude/settings.local.json`.
- A Claude Code edit into a feature worktree from another folder is now denied: run `EnterWorktree <path>` first.
- Codex `dev-workspace` has no local unix socket and allows only the listed domains. An agent that hits `Network access to "<host>" was blocked` stops and names the host: add it to `[permissions.dev-workspace.network.domains]`. For Docker, start the session with `codex -c default_permissions=dev-docker`.
- The notes path comes from `DEV_LOOP_NOTES`, else the main worktree's `dev-loop.local.json`, then `dev-loop.json`. A feature worktree's own `dev-loop.local.json` no longer counts, and a `dev-loop.local.json` that git tracks fails.
- To change `commands.acceptance`, merge that change to `main` first: `acceptance` refuses a branch whose command differs from the one on `main`.
- `upgrade` removes the unused Codex profile tables `dev-implementer`, `dev-spec-tester` and `dev-read-only` from `.codex/config.toml` when unchanged. It refreshes dev-loop's own hook entries in place, so a field you added to a dev-loop hook is lost.

Full changes: https://github.com/tchlyah/dev-loop/compare/v2.1.1...v3.0.0-rc.2

## [2.1.1] - 2026-10-16

### Highlights

**Board shows true progress.** Stage and task times now come from files on disk (the ledger, the task briefs, the test logs, the commits), so the board stays right with no action by you or the model. More than one task can be doing at once, each task shows its plan number, and backfilled times are marked as estimated.

**Board refresh after each step.** A new PostToolUse hook refreshes the board after a task starts or ends, after a commit, a new worktree, a PR create or merge, and a task list change.

**Status notes in a subfolder.** The status notes now go to `dev-loop-board/features/`, so the board note is easy to find. The board keeps its state in `board-state.json` in the git folder, and rewrites an own note that Obsidian changed and that no longer parses.

**More in `devloop status`.** `status` reports why the board skipped a run, and a worktree kit newer than the one on `main` (`kit_drift`).

### Fixes

- A busy board run is always queued, and each catch-up pass reads git, worktrees, specs and claims again.
- A board note's `updated` time moves only when a board value changes, not on a repair.
- A crashed board run is reported, an old note that does not parse is moved aside, and claims survive a spec that iCloud evicted.

### Upgrade notes

- After the kit upgrade lands on `main`, restart each in-flight Session B from the main checkout. A worktree on the old kit runs the 2.1.0 board writer and writes a duplicate note with wrong times in `dev-loop-board/`.
- Codex: trust the new PostToolUse hook with `/hooks`.

Full changes: https://github.com/tchlyah/dev-loop/compare/v2.1.0...v2.1.1

## [2.1.0] - 2026-10-16

### Highlights

**Progress board.** One page shows every dev-loop feature in progress on all your machines: the stage, the time per stage, the build tasks and their times, the time left and what waits for you. Open it at `http://127.0.0.1:7777/` on a desktop, or as the Obsidian note `Dev Loop Board.md` on a phone. Hooks write it with a script, so no agent spends tokens on it.

**Skip Stage 3 for a slice with no flow.** A spec with `acceptance: regression` (refactor, performance) or `acceptance: none` (tests, CI, tooling, docs) skips the held-out tests at Stage 3. `regression` still runs the existing suite in Stage 6, `none` skips the acceptance loop, and the board leaves Stage 3 out.

**Checklist in the native plan tool.** The agent keeps one item per stage and per plan task in the Claude or Codex plan tool, so you see the progress in the harness. `init` and `upgrade` set `CLAUDE_CODE_ENABLE_TODO_TOOLS=1` in the project Claude settings.

**Mutation fix wave you can check.** The mutant report prints its base and head, and `select_mutants.py --only` reruns the listed functions. A fix-wave test is committed only when the rerun shows it kills a listed mutant.

### Fixes

- A Track S feature ships on the board once its recorded head is on `main`, even after its branch is gone.
- The board server restarts with the kit version, queues busy events, skips a FIFO note, waits for a refused port and trusts a pid only from its pidfile.
- Board times: no Waiting time in "Needs you", zero-length tasks count in the time left, tasks split at Stage 5 only.
- `check_setup.py` reports a board note at the kit version whose body is not the kit template, honours the board vault opt-outs, and orders prerelease versions.

### Upgrade notes

- Install the Obsidian Dataview plugin with JavaScript queries on (or set `"board_dataview": false` in `dev-loop.local.json`).
- Add `dev-loop-board/` to the vault `.gitignore` (or set `"board_vault_git": "shared"` in `dev-loop.local.json`). `check_setup.py` reports both.
- Codex: trust the new `board.sh` hooks with `/hooks`.
- A project that set `CLAUDE_CODE_ENABLE_TODO_TOOLS` to another value now fails `check_setup.py`.

Full changes: https://github.com/tchlyah/dev-loop/compare/v2.0.0...v2.1.0

## [2.0.0] - 2026-10-14

### Highlights

**Tracks S, M and L.** Each piece of work gets a size. Track S has no spec and no plan; a Track M feature asks you twice: at spec approval and, with `merge: human`, at the merge.

**Stages chain in sessions.** "One stage per invocation" is gone. Session A runs Stages 1 and 2 and stops at the spec approval; Session B runs Stages 3 to 7 under an autonomy grant.

**The `devloop` CLI.** `devloop.py status` detects the stage of a feature as JSON, `logged` keeps a command's full output in a log, `gate --summary` runs the gate and reports the failures, and `ci wait` waits for the PR checks. `dev-loop.json` takes an optional `commands` map and `merge: auto|human`.

**One review wave.** The gate runs one wave of reviewers: native `code-review` at high effort, the Pocock spec and standards desks, and `security-review`. `simplify` and the gate mutation step are gone; mutation testing runs as a nightly CI job.

**New skill stack.** Stage 1 uses Pocock `grilling`; Pocock `tdd`, `diagnosing-bugs` and `pr` replace the Superpowers skills. Implementers get measured rules against over-building and a bounded test count, and Stage 7 marks a spec shipped with a `shipped:` field.

### Fixes

- `status` ships a merged feature with no branch, lists only open specs on `main`, and compares feature slugs without case.
- The Stop and SubagentStop gate runs the mapped `commands.gate`.
- Every reviewer runs in the feature worktree, and Track S goes through the review wave.
- The nightly mutation job falls back to the root commit when `main` has no commit 24 hours old.

### Upgrade notes

- From 1.5.x: `npx github:tchlyah/dev-loop#v2.0.0 upgrade` on a branch, then the steps below.
- From 1.4.0: write `dev-loop.local.json` with the notes path (keep a personal vault guard outside dev-loop first), run `uninstall-global --dry-run`, then `uninstall-global`, then `init` on a branch.
- Remove the user-level denies of `superpowers:executing-plans`, `mattpocock-skills:tdd` and `mattpocock-skills:diagnosing-bugs` from `~/.claude/settings.json`: a user deny wins over a project allow.
- Codex: install the Pocock skills `tdd`, `diagnosing-bugs`, `pr` and `prototype`.
- A Track M or L spec needs `feature:` and `track:` in its frontmatter. Acceptance files are named after the feature slug.
- The project denies the Superpowers skills `brainstorming`, `test-driven-development`, `systematic-debugging` and `finishing-a-development-branch`.
- The 1.4 fallbacks are gone: `vault_notes:`, `--global` and `--link`. `check_setup.py` needs `dev-loop.json`. Optional: rename `CONTEXT.md` to `GLOSSARY.md`.
- Run `check_setup.py --harness claude|codex --repo .`. Rollback: `git revert` the upgrade commit.

Full changes: https://github.com/tchlyah/dev-loop/compare/v1.5.0...v2.0.0

## [1.5.0] - 2026-10-13

### Highlights

**dev-loop in each project.** `npx github:tchlyah/dev-loop#<tag> init` writes the kit into the repository for Claude Code and Codex, so the team shares one version through git. `upgrade` replaces the kit and prints the release notes, and `uninstall-global` removes the 1.4 global install. `.dev-loop/MANIFEST.json` records each kit file, so `upgrade` and `check_setup.py` catch a local edit.

**Notes folder per person.** The notes folder now comes from `DEV_LOOP_NOTES`, the gitignored `dev-loop.local.json`, or `notes` in `dev-loop.json`. The kit has no vault rule, and the SessionStart hooks find dev-loop by `dev-loop.json`.

**Walls that never wait for you.** The git guard checks only what the shell runs: it strips heredoc bodies and reads quoted strings only where a shell runs them. A subagent never gets a prompt: its blocked push is denied with a reason. This removed almost every needless prompt.

**Glossary and sizes.** The skill reads `GLOSSARY.md`, else `CONTEXT.md`, and every Superpowers 7 brainstorming size goes on to the spec.

### Fixes

- The guard follows `cd` through assignments, subshells and variables, checks a push against every repo it may reach, and asks when it cannot be sure.
- `init` refuses symlinked paths, `upgrade` deletes only kit files the old manifest names, and a file of yours at a kit path is backed up, never overwritten.
- `upgrade` removes the hook entries of dropped kit scripts, and names only the Codex tables that differ from dev-loop's.
- `init` writes the Claude repo contract into the project settings, so a fresh project passes `check_setup.py`.
- A `$NAME` in the legacy `vault_notes:` value is read from the environment.
- `check_setup.py` writes no `__pycache__` into the skill folder.

### Upgrade notes

- Before the first `init` on a machine, run `npx github:tchlyah/dev-loop#v1.5.0 uninstall-global` (`--dry-run` first). With the global skill still in place, Claude Code runs it instead of the project one, and Codex runs neither.
- `--vault`, `DEV_LOOP_VAULT` and the vault prompt are gone. Set the notes folder with `init --notes=<path>` or in `dev-loop.local.json`.
- The vault guard is no longer in the kit: keep your own if you need one.
- Upgrade later with `upgrade` on a branch and a PR. Rollback: `git revert` the upgrade commit, or `npx github:tchlyah/dev-loop#v1.4.0` to put a machine back on the global install.

Full changes: https://github.com/tchlyah/dev-loop/compare/v1.4.0...v1.5.0

## [1.4.0] - 2026-10-12

### Highlights

**A clear push rule.** A plain push of a feature branch runs; a push that rewrites history, deletes, pushes a tag or reaches `main` asks. The Codex guard has the same rule and denies where Claude asks, since Codex cannot ask.

**A stricter push check.** The guard parses a push as the shell does and fails closed: quotes, combined flags, `git -c`, aliases, remote config, shell expansion and arguments fed by `xargs` or `find` all ask. It finds `git` anywhere in a command.

### Fixes

- The vault write check judges each git call on its own folder.
- A deny wall matches a path on whole segments only.
- A replaced skill is backed up outside its skills folder, so Codex no longer lists dev-loop three times.

Full changes: https://github.com/tchlyah/dev-loop/compare/v1.3.0...v1.4.0

## [1.3.0] - 2026-10-03

### Highlights

**Faster gate from the hooks.** The hooks run the gate with `GATE_SCOPE=changed`, so a project gate can run only what the diff reaches. The gate no longer runs after a read-only subagent or one that changed nothing, and a stage that changed the repo ends on one full gate.

**Leaner acceptance tests.** `spec-tester` proves each flow once and leaves details to unit tests.

### Fixes

- The acceptance wall resolves a subagent's relative paths against its working folder.

### Upgrade notes

- Run the installer again (or use link mode) so `~/.claude/settings.json` gets the new `SubagentStart` hook.

Full changes: https://github.com/tchlyah/dev-loop/compare/v1.2.0...v1.3.0

## [1.2.0] - 2026-10-01

### Highlights

**One worktree per feature.** Stages 3 to 7 run in the feature's own worktree and branch, which the agent finds or creates; you never change folder. A spec lists the earlier acceptance lines it replaces, and `spec-tester` updates exactly those tests.

**Smaller mutation runs.** The gate mutates only the functions a feature changed, in the modules you mark critical, and asks for one unit test per listed function. On one real feature this cut 4,108 mutants to 1,794.

**Reviews in parallel.** The gate runs its reviews once, side by side, and the acceptance suite last.

### Fixes

- A flaky test is a race to name and fix, never a rerun to green.
- Subagent models are named by alias only, so they follow the newest model.

### Upgrade notes

- `check_setup.py` now needs the mutation setup: a `[tool.mutmut]` `only_mutate` list and a JSON Stryker config with a `mutate` list.

Full changes: https://github.com/tchlyah/dev-loop/compare/v1.1.0...v1.2.0

## [1.1.0] - 2026-09-18

### Fixes

- The installer removes a legacy root `sandbox_mode` from `~/.codex/config.toml`.
- Link mode copies the Codex agents.
- The skill tells unplanned deployment and infrastructure work apart from planned work.

Full changes: https://github.com/tchlyah/dev-loop/compare/v1.0.0...v1.1.0

## [1.0.0] - 2026-09-14

### Highlights

**dev-loop for Claude Code and Codex.** `npx github:tchlyah/dev-loop` installs the stage-driven development loop skill, its subagents and its hooks for both harnesses, by copy or by link. Every replaced file gets a timestamped backup.

**Merges that keep your config.** The installer merges its hook entries, deny rules, Codex profiles and Codex rules into your files and never touches an entry it did not add.

**Walls around the held-out tests.** Deny walls keep subagents out of the acceptance suite, and `check_setup.py` checks the install and the vault path.

### Fixes

- A malformed hook group or an unreadable folder entry no longer stops the install, and the error names the path.
- The Codex config merge never duplicates a key or table, and a re-run keeps the configured vault.
- The deny walls match path fields only, with no glob hole.
