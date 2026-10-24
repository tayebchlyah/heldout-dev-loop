# Claude Code route

Read this file only in Claude Code. Preserve these names and walls.

## Setup contract

- Project agents: `.claude/agents/implementer.md`, `.claude/agents/spec-tester.md`.
- Project hooks: `.dev-loop/hooks/claude/gate.sh`, `gate-snapshot.sh`, `tree-hash.sh`, `deny-path.sh`, `deny-acceptance-to-subagents.sh`, `git-guard.sh`, `seal.sh`, `board.sh`. `PostToolUse` runs `board.sh post-tool` (matchers `Bash`, `TaskCreate|TaskUpdate`).
- Walls and seal: `deny-acceptance-to-subagents.sh` denies `Edit`, `Write` and `NotebookEdit` under `tests/acceptance/` to the main session and every subagent but `spec-tester`; reads stay open. It denies the same tools on an installed kit file to every caller (kit files change only by `upgrade`), and resolves a relative path against the cwd and each folder a `cd` or `pushd` of the command moves to. It also denies `spec-tester` any path that names `src/`, `app/`, `backend/` or `frontend/`: the `deny-path.sh` hook in its frontmatter holds the same rule, but did not fire in Claude Code 2.1.309. `git-guard.sh`: only `spec-tester` runs `devloop.py seal` (it cannot run a plain `git commit`). Every guard entry carries `onFailure: "block"` and exits 2 on an internal error. `seal.sh stop` (SubagentStop) runs the audit and the seal check, `seal.sh turn` (Stop) the seal check and the taint check. `stop_hook_active` true skips the block, as `gate.sh` does. `gate-snapshot.sh` (SubagentStart) writes `<git-dir>/dev-loop-subagents/<agent_id>.head`. Minimum Claude Code 2.1.295.
- Gaps (full list in `codex.md`): a wrapped run (`env`, `uv run`) passes the command-position seal rule, the seal check still catches the commit; `git patch-id` includes context lines, so a revert after neighbouring edits may not close a taint record; `seal.sh` exits 0 when `jq` is missing (a Stop hook cannot fail closed), `devloop.py gate` still checks; a variable that holds the script name passes the seal-run rule, and a trailer key set through git config or `git commit -F file` passes the typed-trailer rule, the seal check catches both; a tainted agent can steer the audit's commit attribution by printing a commit line, and an agent can edit the kit in the worktree through the shell: `seal-check` reports it (`kit-changed`) unless the agent also edits the checker or rewrites the MANIFEST hash, the PR diff shows it.
- Repo settings: `.claude/settings.json` denies `Agent(caveman:cavecrew-reviewer)`, `Agent(caveman:cavecrew-builder)`, and `Agent(caveman:cavecrew-investigator)`. It sets `ENABLE_STOP_REVIEW=0`, `ENABLE_COMMIT_REVIEW=0`, `SG_PUSH_SWEEP=0`, and `CLAUDE_CODE_ENABLE_TODO_TOOLS=1` (the `TaskCreate`/`TaskUpdate` checklist).
- User deny list keeps `Skill(superpowers:verification-before-completion)`, `Read(./.env)`, and `Read(./.env.*)`.
- Install with `npx github:tchlyah/dev-loop#<tag> init`. Repair or update with `npx github:tchlyah/dev-loop#<tag> upgrade`. Validate with `python3 .agents/skills/dev-loop/scripts/check_setup.py --harness claude --repo .`.

## Skill and agent names

- Domain work: `mattpocock-skills:domain-modeling`, `mattpocock-skills:codebase-design`, `mattpocock-skills:code-review`.
- Core loop: Stage 1 `mattpocock-skills:grilling`, `mattpocock-skills:prototype`. Stage 4 `superpowers:writing-plans`. Stage 5 `superpowers:executing-plans` (Track M), `superpowers:subagent-driven-development` (Track L), `mattpocock-skills:tdd`, `mattpocock-skills:diagnosing-bugs`. Stage 7 `mattpocock-skills:pr`.
- Denied in the project, never invoke: `Skill(superpowers:brainstorming)`, `Skill(superpowers:test-driven-development)`, `Skill(superpowers:systematic-debugging)`, `Skill(superpowers:finishing-a-development-branch)`.
- Acceptance author: `spec-tester`.
- SDD implementers: `subagent_type: implementer`.
- Task reviewers and re-reviewers: `general-purpose`. Never use `caveman:cavecrew-*`.
- Subagent models: name them by alias only, `haiku`, `sonnet`, `opus` or `fable`, in plans, Agent calls and agent frontmatter. An alias resolves to the newest model the running Claude Code knows. A full ID such as `claude-sonnet-5` pins an old model.

## Feature gate

Four phases, in order. First rebase onto `origin/main`, fresh stack, `GATE_SHA` = HEAD (SKILL.md Stage 6).

1. **Review wave, once, in parallel.** In one message, four Agent calls, all `general-purpose` with `model: opus`. Three reviewers: native `code-review`, Pocock (Pocock = two desks: spec, standards), `security-review`. Each call works in the feature worktree on `main...GATE_SHA`: whole branch, report only, no fixes. Returns a table: file, line, finding, verdict or severity, reviewer. Never `caveman:cavecrew-*`.
   - Invoke skill `code-review`, args `high <feature branch>`: effort `high`, the feature branch as target (no target reviews only the working diff). No `--fix`.
   - Invoke skill `security-review`. Review `git diff main...GATE_SHA` from the feature worktree, not the working tree.
   - Pocock spec desk: read `mattpocock-skills:code-review` SKILL.md, do its spec desk only, fixed point `main`.
   - Pocock standards desk: read `mattpocock-skills:code-review` SKILL.md, do its standards desk only, fixed point `main`.
   - Both desks: direct Agent calls, no nested agents. A desk started inside a subagent reports after its parent hands back.
2. **Triage, controller.** SKILL.md Stage 6.
3. **Fix wave.** One `implementer` per file cluster, one at a time, for the Act On list: every defect, plus optional items the lead picks. Each passes `scripts/devloop.py gate --summary`.
4. **Acceptance loop.** SKILL.md Stage 6. Spec `acceptance: none`: skipped. Runner lines when `commands.acceptance` is absent, from `stacks:`. Python: `pytest tests/acceptance -q --tb=no`. Go: `go test ./tests/acceptance/...`. UI: `npx playwright test tests/acceptance`. Failures only when `commands.acceptance_failed` is absent: `pytest tests/acceptance --lf -q --tb=no`, `npx playwright test tests/acceptance --last-failed`. Every run after a fix repeats the stack reset first. Stale image run does not count.

No `/verify` step. `/verify` is a real bundled skill but user-invoked only; the model cannot call it. Playwright E2E is the model's live check. The user may type `/verify` after the gate.

Controller never edits `tests/acceptance/` in this stage. Read `seal` and `weakened` from the gate JSON in triage. An unplanned deployment or infra gap discovered by the gate is a new slice, not a fix round. Deployment or infra work explicitly named in the approved spec or plan stays in the current slice and is implemented normally.

Never invoke `superpowers:verification-before-completion`. The shell gate owns verification.
