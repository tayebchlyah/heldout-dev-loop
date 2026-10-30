# Codex route

Read this file only in Codex. Use Codex names and walls, not Claude paths or slash commands.

## Setup contract

- Project agents: `.codex/agents/implementer.toml`, `.codex/agents/spec-tester.toml`, `.codex/agents/task-reviewer.toml`, `.codex/agents/planner.toml`.
- Review agents: `.codex/agents/final-reviewer.toml`, `.codex/agents/security-reviewer.toml`.
- Project hooks: `.codex/hooks.json`, plus scripts in `.dev-loop/hooks/codex/`: `gate.sh`, `deny-path.sh`, `git-guard.sh`, `acceptance-wall.sh`, `seal.sh`, `session-start.sh`, and `board.sh`. `PostToolUse` runs `board.sh post-tool` (matcher `Bash`). No `update_plan`: Codex 0.162.1 has no plan tool, the ledger is the record.
- `features.hooks = true`. Trust exact hook definitions through `/hooks` before relying on them. Codex skips an untrusted hook without a message: once `session-start.sh` is trusted, it names each project hook event with a hook entry that has no trusted hash (`check_setup.py --hooks-trust`). Tell the user to run `/hooks`.
- The active `dev-workspace` permission profile extends `:workspace` and denies environment files. It walls no agent: the walls are hooks. No legacy `sandbox_mode` key can remain in a loaded config layer.
- Walls: `acceptance-wall.sh` (PreToolUse `Bash|apply_patch`) reads `agent_type`. `spec_tester` cannot name `src/`, `app/`, `backend/` or `frontend/`. Any other subagent cannot name `tests/acceptance`. The main session cannot `apply_patch` a file under `tests/acceptance/`. No caller can `apply_patch` an installed kit file (`.dev-loop/`, `.codex/rules/dev-loop.rules`, the kit agents, the kit skill folders `.agents/skills/<name>/` and `.claude/skills/<name>` of `dev-loop`, `pause`, `continue`, `release-audit`, `garden`, `release` and `release-notes`): kit files change only by `upgrade`. A relative path resolves against the cwd and each folder a `cd` or `pushd` of the command moves to. For `apply_patch` the paths are the file headers, never the patch body. Search rule: `acceptance-wall.sh` denies the `Bash` of every subagent but `spec_tester` a recursive search (`grep -r`, `rg`, `git grep`, `find`) whose root is `tests/acceptance` or a folder above it, unless it excludes the suite; the block names the scope. Engine: `devloop.py search-wall` of the kit at the cwd top, skipped when none; a CLI error blocks. Not walled: wrappers (`xargs`, `env`, `sh -c`, `eval`) and `ls -R`, `tree`, `fd`, `ag`, `ack`, `git ls-files`; the audit path rule (a search output line that starts with a held-out file path) backs up the search tools. A hook denies with exit 2. `seal.sh start|stop|turn`: `start` records the subagent's start HEAD in `<git-dir>/dev-loop-subagents/<agent_id>.head`; the audit and the seal check at SubagentStop and Stop, a taint check at Stop. `stop_hook_active` true skips the block. `git-guard.sh`: only `spec_tester` runs `devloop.py seal`, and it cannot run a plain `git commit`. Minimum codex-cli 0.162.1.
- Gaps, written down, not closed: a script that copies the seal value method; a script that reads a protected file and prints only a summary; a hook timeout; a rollout in Legacy mode (no audit); acceptance failure text that the controller gives to an implementer in a Stage 6 fix round; a tainted change squashed into another commit (its patch-id changes). Found in the build: a wrapped run (`env`, `uv run`, `python3 -I`, `command git commit`, `timeout 5 git commit`) passes the command-position seal rule, the seal check still catches the commit; `git patch-id` includes context lines, so a revert made after neighbouring edits may not close a taint record, and a rebase over changed context can close one; `seal.sh` exits 0 when `jq` is missing (a Stop hook cannot fail closed), `devloop.py gate` still checks; a variable that holds the script name (`D=devloop.py; python3 $D seal` across two segments) passes the seal-run rule, the seal check still catches the commit; a trailer key set through git config (`git -c trailer.<x>.key=Dev-Loop-Seal ... --trailer`) or a message file (`git commit -F file`) passes the typed-trailer rule, the seal check still rejects the forged value; a tainted agent can steer which of its commits the audit records by printing a commit line (D24 attribution), the audit and the PR diff still show the change; the kit in the worktree (`.agents/skills/dev-loop/`, `.dev-loop/hooks/`) is editable by an agent through the shell and the seal check runs from it: `seal-check` reports a kit file whose content does not match its MANIFEST entry (`kit-changed`), but an agent that edits the checker itself, or rewrites the MANIFEST hash too, passes it, only the PR diff shows that; a folder held in a variable (`d=tests; cd $d`) passes the `cd` rule of the walls, the audit still records the read.
- Command rules prompt for direct pushes, every `git -C` form, and every `gh api` and `gh release` command (reads too: a prefix rule cannot see the method or the subcommand). They forbid direct hard resets and `rm -rf /`. The trusted Git hook provides defense in depth for wrapped commands.
- Install with `npx github:tchlyah/dev-loop#<tag> init`. Repair or update with `npx github:tchlyah/dev-loop#<tag> upgrade`. Validate with `python3 .agents/skills/dev-loop/scripts/check_setup.py --harness codex --repo .`.

## Delivery autonomy

- Skills `$pause` and `$continue` (`.agents/skills/pause/`, `.agents/skills/continue/`). `$pause` is user-invoked only (`agents/openai.yaml` `allow_implicit_invocation: false`).
- Decision log: the controller runs `devloop.py decide`; the log is in the spec in the notes folder. With the notes folder outside the writable roots, each `decide` asks for approval: add the notes folder as a writable root in the user config. Implementers never run it: they end their final report with a `Discoveries` list.
- Wake (same session): `send_input` (v1) or `followup_task` (v2) to each subagent that stopped during its work; a closed one: `resume_agent` first. A wake that fails: a fresh agent, full brief, plus "the uncommitted work in the worktree is yours, continue it".
- `$pause`: `interrupt_agent` (v2), else `close_agent` (v1), on each running subagent; background runs stay alive.
- No ask tool outside plan mode: no ask wall needed.
- Hand-off at spec approval (SKILL.md Stage 2), each line in its own code block: 1 `/reasoning` with the medium level, 2 `/compact`, 3 `Start Delivery of <slug>`. Track S (Stage 1): `/reasoning` medium, then `Accept Track S`.
- Gaps, written down, not closed: no failure event (Codex `Stop` has no error field), so only `$pause` writes a pause note, a usage limit writes none; the wake of an `errored` agent and the `$pause`, `$continue` entries of the `/` menu are unconfirmed until a live check. Confirmed on 0.162.1: a shell command sees `CODEX_THREAD_ID`, the id of the session file `rollout-<time>-<id>.jsonl`.

## Skill and agent names

- Matt Pocock skills from the local installation: `grilling`, `domain-modeling`, `codebase-design`, `code-review`, `tdd`, `diagnosing-bugs`, `pr`, `prototype`, `wayfinder`, `retro`.
- `codebase-design`: reference only. Never invoke it alone. Never run its design-it-twice step unless the user asks for it.
- Core loop: Stage 1 `grilling`, `prototype`; map trigger: read `~/.agents/skills/wayfinder/SKILL.md` (user-invoked only). Stage 4 `superpowers:writing-plans`. Stage 5 `superpowers:executing-plans` (Track M), `superpowers:subagent-driven-development` (Track L), `tdd`, `diagnosing-bugs`. Stage 7 `pr`, then `scripts/devloop.py capture` (`references/verify.md` section Stage 7).
- Stage 7 metrics: a Codex session is not counted (`devloop.py metrics` reads Claude transcripts only). Skip the SKILL.md Stage 7 metrics step.
- Stage 7 log audit: the main transcript is the file `find "${CODEX_HOME:-$HOME/.codex}/sessions" -name "rollout-*-$CODEX_THREAD_ID.jsonl"` prints. The controller resolves it and passes the path.
- Never invoke these Superpowers skills (Codex has no skill deny list): `superpowers:brainstorming`, `superpowers:test-driven-development`, `superpowers:systematic-debugging`, `superpowers:finishing-a-development-branch`.
- Acceptance author: custom agent `spec_tester`.
- SDD implementers: custom agent `implementer`.
- Task reviewers and re-reviewers: custom read-only agent `task_reviewer`.
- Explicitly request each custom role. A generic worker does not provide the held-out-test wall: the wall keys on the custom agent name.

## Stage 4 plan

Stage 4 planner: custom agent `planner` (`model_reasoning_effort = "high"`), with the spec path. It invokes `superpowers:writing-plans` and returns the plan path. No worktree wall in Codex: the session cwd stays where Codex started it. The main thread reads the plan, applies the SKILL.md Stage 4 checks, then runs Stage 5.

## Feature gate

Five phases, in order. First rebase onto `origin/main`, fresh stack, `GATE_SHA` = HEAD (SKILL.md Stage 6).

1. **Review wave, once.** First the second review (SKILL.md Stage 6), a background run watched to its end: `python3 .agents/skills/dev-loop/scripts/devloop.py logged --name second-review -- python3 .agents/skills/dev-loop/scripts/devloop.py second-review --harness codex` (reviewer `claude`). Then three reviewers, in parallel where the session allows it, otherwise in sequence. Each works in the feature worktree on `main...GATE_SHA`: whole branch, report only, and returns its table (SKILL.md Stage 6 table contract). The reviewers are read-only: the controller writes each table to `<git-dir>/dev-loop/review/<reviewer>.md` of the feature worktree (`<reviewer>`: `final-reviewer`, `code-review`, `security-reviewer`).
   - Custom read-only agent `final_reviewer`. In the CLI, Codex `/review` is the interactive equivalent.
   - Invoke skill `code-review` with fixed point `main`.
   - Custom read-only agent `security_reviewer`.
   - Both custom reviewers run at `model_reasoning_effort = "high"` (their agent files).
   - The Claude reviewer runs inside `dev-workspace` (network on). It cannot write its config folder and does not need to.
2. **Triage, controller.** SKILL.md Stage 6. Read `seal`, `size` and `weakened` from the gate JSON: a `seal` or `size` violation is red. Merge: `python3 .agents/skills/dev-loop/scripts/devloop.py review-merge final-reviewer=<file> code-review=<file> security-reviewer=<file> [second-review:claude=<record>/output.md]`. Security verifier: a fresh `security_reviewer` per security finding. Prompt: the candidate-verifier prompt from `~/.agents/skills/security-audit/VALIDATION-AND-REPORTING.md` (Phase 3, "Candidate-verifier prompt"), plus the finding.
3. **Fix wave.** A new custom agent `implementer` per round, one file cluster at a time, for the Act On list: every defect, plus optional items the lead picks. Each passes `scripts/devloop.py gate --summary`: the process exits 0 (the project gate, `seal` and `size`).
4. **Drive.** SKILL.md Stage 6, `references/verify.md` section Stage 6. `scripts/devloop.py drive`, then the layout check: one fresh generic worker with the `drive.json` path. After the final full acceptance run: the closing drive (`references/verify.md` Stage 6 step 7).
5. **Acceptance loop.** SKILL.md Stage 6. Spec `acceptance: none`: skipped. `commands.acceptance` mapped: `python3 .agents/skills/dev-loop/scripts/devloop.py logged --name acceptance-run -- python3 .agents/skills/dev-loop/scripts/devloop.py acceptance [--failed]`, a background run watched to its end; read `guard` and `stale` from the JSON. `commands.acceptance` absent: runner lines from `stacks:`. Python: `pytest tests/acceptance -q --tb=no`. Go: `go test ./tests/acceptance/...`. UI: `npx playwright test tests/acceptance`. `commands.acceptance` absent: failures only by `--lf` or `--last-failed`.

Do not substitute Claude-only review, security, or verification slash commands in the Codex branch. Do not invoke `superpowers:verification-before-completion`. The Stop gate owns routine verification.

## Release audit

User-invoked only: `$release-audit` (kit skill `release-audit`), main session, at the commit to tag on `main`. Never in Delivery, never in a subagent: the `security-audit` skill starts its own agents. Target-controlled execution goes only through `sandbox-run.py` (OS-enforced container, no network). No container engine or image: source-only run, no target code. It needs the Docker engine reachable from the `dev-workspace` profile: `release-plan` `sandbox.engine` false means a source-only run. Cost: a run takes hours and costs tens of dollars (trial: 30 min, 19 USD, 2 subsystems, `quick`): the skill says so before it starts. Steps and CLI: the skill's `SKILL.md`.

`/release` (Codex: `$release`, kit skill `release`) runs the whole release and the audit steps inside it.

## Garden

User-invoked only: `$garden` (kit skill `garden`), the weekly `## Gardener` triage, main checkout on `main`. Steps: the skill's `SKILL.md`.

Retro skill: `$retro` (Pocock, user-invoked only), the weekly review before `$garden` (SKILL.md Stage 1).
