# Claude Code route

Read this file only in Claude Code. Preserve these names and walls.

## Setup contract

- Project agents: `.claude/agents/implementer.md`, `.claude/agents/spec-tester.md`.
- Project hooks: `.dev-loop/hooks/claude/gate.sh`, `gate-snapshot.sh`, `tree-hash.sh`, `deny-path.sh`, `deny-acceptance-to-subagents.sh`, `git-guard.sh`.
- Repo settings: `.claude/settings.json` denies `Agent(caveman:cavecrew-reviewer)`, `Agent(caveman:cavecrew-builder)`, and `Agent(caveman:cavecrew-investigator)`. It sets `ENABLE_STOP_REVIEW=0`, `ENABLE_COMMIT_REVIEW=0`, and `SG_PUSH_SWEEP=0`.
- User deny list keeps `Skill(superpowers:verification-before-completion)`, `Read(./.env)`, and `Read(./.env.*)`.
- Install with `npx github:tchlyah/dev-loop#<tag> init`. Repair or update with `npx github:tchlyah/dev-loop#<tag> upgrade`. Validate with `python3 .agents/skills/dev-loop/scripts/check_setup.py --harness claude --repo .`.

## Skill and agent names

- Domain work: `mattpocock-skills:domain-modeling`, `mattpocock-skills:codebase-design`, `mattpocock-skills:code-review`.
- Core loop: Stage 1 `mattpocock-skills:grilling`, `mattpocock-skills:prototype`. Stage 4 `superpowers:writing-plans`. Stage 5 `superpowers:executing-plans` (Tracks S, M), `superpowers:subagent-driven-development` (Track L), `mattpocock-skills:tdd`, `mattpocock-skills:diagnosing-bugs`. Stage 7 `mattpocock-skills:pr`.
- Denied in the project, never invoke: `Skill(superpowers:brainstorming)`, `Skill(superpowers:test-driven-development)`, `Skill(superpowers:systematic-debugging)`, `Skill(superpowers:finishing-a-development-branch)`.
- Acceptance author: `spec-tester`.
- SDD implementers: `subagent_type: implementer`.
- Task reviewers and re-reviewers: `general-purpose`. Never use `caveman:cavecrew-*`.
- Subagent models: name them by alias only, `haiku`, `sonnet`, `opus` or `fable`, in plans, Agent calls and agent frontmatter. An alias resolves to the newest model the running Claude Code knows. A full ID such as `claude-sonnet-5` pins an old model.

## Feature gate

Four phases, in order. First rebase onto `origin/main`, fresh stack, `GATE_SHA` = HEAD (SKILL.md Stage 6).

1. **Review wave, once, in parallel.** In one message, three Agent calls, all `general-purpose` with `model: opus`. Each reviews the whole branch `main...GATE_SHA`, report only, no fixes, and returns a table: file, line, finding, verdict or severity, reviewer. Never `caveman:cavecrew-*`.
   - Invoke skill `code-review`, args `high`. No `--fix`.
   - Invoke skill `mattpocock-skills:code-review`, fixed point `main`.
   - Invoke skill `security-review`.
2. **Triage, controller.** SKILL.md Stage 6.
3. **Fix wave.** One `implementer` per file cluster, one at a time, for the Act On defects. Each passes `scripts/devloop.py gate --summary`.
4. **Acceptance loop.** SKILL.md Stage 6. Runner lines when `commands.acceptance` is absent, from `stacks:`. Python: `pytest tests/acceptance -q --tb=no`. Go: `go test ./tests/acceptance/...`. UI: `npx playwright test tests/acceptance`. Failures only when `commands.acceptance_failed` is absent: `pytest tests/acceptance --lf -q --tb=no`, `npx playwright test tests/acceptance --last-failed`. Every run after a fix repeats the stack reset first. Stale image run does not count.

No `/verify` step. `/verify` is a real bundled skill but user-invoked only; the model cannot call it. Playwright E2E is the model's live check. The user may type `/verify` after the gate.

Controller never edits `tests/acceptance/` in this stage. An unplanned deployment or infra gap discovered by the gate is a new slice, not a fix round. Deployment or infra work explicitly named in the approved spec or plan stays in the current slice and is implemented normally.

Never invoke `superpowers:verification-before-completion`. The shell gate owns verification.
