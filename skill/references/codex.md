# Codex route

Read this file only in Codex. Use Codex names and walls, not Claude paths or slash commands.

## Setup contract

- Project agents: `.codex/agents/implementer.toml`, `.codex/agents/spec-tester.toml`, `.codex/agents/task-reviewer.toml`.
- Review agents: `.codex/agents/final-reviewer.toml`, `.codex/agents/security-reviewer.toml`.
- Project hooks: `.codex/hooks.json`, plus scripts in `.dev-loop/hooks/codex/`: `gate.sh`, `deny-path.sh`, `git-guard.sh`, `acceptance-wall.sh`, `seal.sh`, `session-start.sh`, and `board.sh`. `PostToolUse` runs `board.sh post-tool` (matcher `Bash`). No `update_plan`: Codex 0.162.1 has no plan tool, the ledger is the record.
- `features.hooks = true`. Trust exact hook definitions through `/hooks` before relying on them. Codex skips an untrusted hook without a message: once `session-start.sh` is trusted, it names each project hook event with a hook entry that has no trusted hash (`check_setup.py --hooks-trust`). Tell the user to run `/hooks`.
- The active `dev-workspace` permission profile extends `:workspace` and denies environment files. It walls no agent: the walls are hooks. No legacy `sandbox_mode` key can remain in a loaded config layer.
- Walls: `acceptance-wall.sh` (PreToolUse `Bash|apply_patch`) reads `agent_type`. `spec_tester` cannot name `src/`, `app/`, `backend/` or `frontend/`. Any other subagent cannot name `tests/acceptance`. The main session cannot `apply_patch` a file under `tests/acceptance/`. No caller can `apply_patch` an installed kit file (`.agents/skills/dev-loop/`, `.dev-loop/`, `.claude/skills/dev-loop`, `.codex/rules/dev-loop.rules`, the kit agents): kit files change only by `upgrade`. A relative path resolves against the cwd and each folder a `cd` or `pushd` of the command moves to. For `apply_patch` the paths are the file headers, never the patch body. A hook denies with exit 2. `seal.sh start|stop|turn`: `start` records the subagent's start HEAD in `<git-dir>/dev-loop-subagents/<agent_id>.head`; the audit and the seal check at SubagentStop and Stop, a taint check at Stop. `stop_hook_active` true skips the block. `git-guard.sh`: only `spec_tester` runs `devloop.py seal`, and it cannot run a plain `git commit`. Minimum codex-cli 0.162.1.
- Gaps, written down, not closed: a script that copies the seal value method; a script that reads a protected file and prints only a summary; a hook timeout; a rollout in Legacy mode (no audit); acceptance failure text that the controller gives to an implementer in a Stage 6 fix round; a tainted change squashed into another commit (its patch-id changes). Found in the build: a wrapped run (`env`, `uv run`, `python3 -I`, `command git commit`, `timeout 5 git commit`) passes the command-position seal rule, the seal check still catches the commit; `git patch-id` includes context lines, so a revert made after neighbouring edits may not close a taint record, and a rebase over changed context can close one; `seal.sh` exits 0 when `jq` is missing (a Stop hook cannot fail closed), `devloop.py gate` still checks; a variable that holds the script name (`D=devloop.py; python3 $D seal` across two segments) passes the seal-run rule, the seal check still catches the commit; a trailer key set through git config (`git -c trailer.<x>.key=Dev-Loop-Seal ... --trailer`) or a message file (`git commit -F file`) passes the typed-trailer rule, the seal check still rejects the forged value; a tainted agent can steer which of its commits the audit records by printing a commit line (D24 attribution), the audit and the PR diff still show the change; the kit in the worktree (`.agents/skills/dev-loop/`, `.dev-loop/hooks/`) is editable by an agent through the shell and the seal check runs from it: `seal-check` reports a kit file whose content does not match its MANIFEST entry (`kit-changed`), but an agent that edits the checker itself, or rewrites the MANIFEST hash too, passes it, only the PR diff shows that; a folder held in a variable (`d=tests; cd $d`) passes the `cd` rule of the walls, the audit still records the read.
- Command rules prompt for direct pushes and every `git -C` form. They forbid direct hard resets and `rm -rf /`. The trusted Git hook provides defense in depth for wrapped commands.
- Install with `npx github:tchlyah/dev-loop#<tag> init`. Repair or update with `npx github:tchlyah/dev-loop#<tag> upgrade`. Validate with `python3 .agents/skills/dev-loop/scripts/check_setup.py --harness codex --repo .`.

## Skill and agent names

- Matt Pocock skills from the local installation: `grilling`, `domain-modeling`, `codebase-design`, `code-review`, `tdd`, `diagnosing-bugs`, `pr`, `prototype`.
- Core loop: Stage 1 `grilling`, `prototype`. Stage 4 `superpowers:writing-plans`. Stage 5 `superpowers:executing-plans` (Track M), `superpowers:subagent-driven-development` (Track L), `tdd`, `diagnosing-bugs`. Stage 7 `pr`.
- Never invoke these Superpowers skills (Codex has no skill deny list): `superpowers:brainstorming`, `superpowers:test-driven-development`, `superpowers:systematic-debugging`, `superpowers:finishing-a-development-branch`.
- Acceptance author: custom agent `spec_tester`.
- SDD implementers: custom agent `implementer`.
- Task reviewers and re-reviewers: custom read-only agent `task_reviewer`.
- Explicitly request each custom role. A generic worker does not provide the held-out-test wall: the wall keys on the custom agent name.

## Feature gate

Five phases, in order. First rebase onto `origin/main`, fresh stack, `GATE_SHA` = HEAD (SKILL.md Stage 6).

1. **Review wave, once.** Three reviewers, in parallel where the session allows it, otherwise in sequence. Each works in the feature worktree on `main...GATE_SHA`: whole branch, report only, and returns a table: file, line, finding, verdict or severity, reviewer.
   - Custom read-only agent `final_reviewer`. In the CLI, Codex `/review` is the interactive equivalent.
   - Invoke skill `code-review` with fixed point `main`.
   - Custom read-only agent `security_reviewer`.
   - Both custom reviewers run at `model_reasoning_effort = "high"` (their agent files).
2. **Triage, controller.** SKILL.md Stage 6. Read `seal` and `weakened` from the gate JSON. Security verifier: a fresh `security_reviewer` per security finding. Prompt: the candidate-verifier prompt from `~/.agents/skills/security-audit/VALIDATION-AND-REPORTING.md` (Phase 3, "Candidate-verifier prompt"), plus the finding.
3. **Fix wave.** A new custom agent `implementer` per round, one file cluster at a time, for the Act On list: every defect, plus optional items the lead picks. Each passes `scripts/devloop.py gate --summary`.
4. **Drive.** SKILL.md Stage 6, `references/verify.md` section Stage 6. `scripts/devloop.py drive`, then the layout check: one fresh generic worker with the `drive.json` path.
5. **Acceptance loop.** SKILL.md Stage 6. Spec `acceptance: none`: skipped. Runner lines when `commands.acceptance` is absent, from `stacks:`. Python: `pytest tests/acceptance -q --tb=no`. Go: `go test ./tests/acceptance/...`. UI: `npx playwright test tests/acceptance`. Failures only when `commands.acceptance_failed` is absent: `--lf` or `--last-failed`.

Do not substitute Claude-only review, security, or verification slash commands in the Codex branch. Do not invoke `superpowers:verification-before-completion`. The Stop gate owns routine verification.
