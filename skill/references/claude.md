# Claude Code route

Read this file only in Claude Code. Preserve these names and walls.

## Setup contract

- User agents: `~/.claude/agents/implementer.md`, `~/.claude/agents/spec-tester.md`.
- User hooks: `~/.claude/hooks/gate.sh`, `deny-path.sh`, `deny-acceptance-to-subagents.sh`, `git-guard.sh`.
- Repo settings: `.claude/settings.json` denies `Agent(caveman:cavecrew-reviewer)`, `Agent(caveman:cavecrew-builder)`, and `Agent(caveman:cavecrew-investigator)`. It sets `ENABLE_STOP_REVIEW=0`, `ENABLE_COMMIT_REVIEW=0`, and `SG_PUSH_SWEEP=0`.
- User deny list keeps `Skill(superpowers:verification-before-completion)`, `Read(./.env)`, and `Read(./.env.*)`.
- Install or repair with `npx github:tchlyah/dev-loop`. Validate with `python3 ~/.agents/skills/dev-loop/scripts/check_setup.py --harness claude --repo .`.

## Skill and agent names

- Domain work: `mattpocock-skills:domain-modeling`, `mattpocock-skills:grilling`, `mattpocock-skills:codebase-design`, `mattpocock-skills:code-review`, `mattpocock-skills:research`, `mattpocock-skills:handoff`.
- Core loop: `superpowers:brainstorming`, `superpowers:writing-plans`, `superpowers:subagent-driven-development`, `superpowers:test-driven-development`, `superpowers:systematic-debugging`, `superpowers:finishing-a-development-branch`.
- Acceptance author: `spec-tester`.
- SDD implementers: `subagent_type: implementer`.
- Task reviewers and re-reviewers: `general-purpose`. Never use `caveman:cavecrew-*`.

## Feature gate

Run in order. Stop on first failure. Steps 2 to 6 run once per feature. A fix reruns step 1 only.

1. Stack first, always fresh: `docker compose --profile test up -d --build --wait`. Confirm `docker compose ps --format '{{.Service}} {{.CreatedAt}}'` shows created time newer than HEAD. Then acceptance runners from `stacks:`. Python: `pytest tests/acceptance -q --tb=no`. Go: `go test ./tests/acceptance/...`. UI: `npx playwright test tests/acceptance`, then `/verify`. Fail: one reopen, back to Stage 5. Second fail: rest to `## Deferred`, ship. Any rerun after a fix repeats the `--build` line first. Stale image rerun does not count.
2. Skill tool, skill `simplify`. Its fixes are the controller-write exception. Rerun step 1.
3. Skill tool, skill `code-review`, args `medium`. Fix only findings that quote a spec oracle line or `CONTEXT.md` invariant. Rest to `## Deferred`.
4. Skill tool, skill `mattpocock-skills:code-review`, fixed point `main`. Same filter as step 3.
5. Mutation on the diff: `mutmut run` or `npx stryker run --since main --incremental`. Survivors get unit tests, never acceptance tests.
6. Skill tool, skill `security-review`.

Controller never edits `tests/acceptance/` in this stage. An unplanned deployment or infra gap discovered by the gate is a new slice, not a reopen. Deployment or infra work explicitly named in the approved spec or plan stays in the current slice and is implemented normally.

Never invoke `superpowers:verification-before-completion`. The shell gate owns verification.
