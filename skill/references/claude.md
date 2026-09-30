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
- Subagent models: name them by alias only, `haiku`, `sonnet`, `opus` or `fable`, in plans, Agent calls and agent frontmatter. An alias resolves to the newest model the running Claude Code knows. A full ID such as `claude-sonnet-5` pins an old model.

## Feature gate

Four phases, in order. First rebase onto `origin/main` (SKILL.md, Where each stage runs), then record the branch HEAD as `GATE_SHA`.

1. **Review wave, once, in parallel.** One message, five Agent calls, all `general-purpose`. Each invokes one skill or command against `GATE_SHA` and returns a table: file, line, finding, verdict or severity, step. Never `caveman:cavecrew-*`.
   - Skill `simplify`. `isolation: worktree`. The worktree starts from the default branch, not HEAD: first command `git checkout --detach GATE_SHA`. Let it apply fixes there, commit, return the commit SHA and the finding table.
   - Skill `code-review`, args `medium`. Report only, no `--fix`.
   - Skill `mattpocock-skills:code-review`, fixed point `main`. Report only.
   - Skill `security-review`. Report only.
   - Mutation on the diff. `isolation: worktree`, same `git checkout --detach GATE_SHA` first. Then follow [mutation.md](mutation.md) and return its report.
2. **Triage, controller.** Merge the five tables. Dedupe by file and line. Apply the defect rule in SKILL.md Stage 6. Everything else to `## Deferred`.
3. **Fix wave.** `git cherry-pick` the simplify commit first. Then one `implementer` per file cluster, one at a time, for defects (b) and (c). Each passes `gate.sh`. Then the mutation tests, all in one message, in parallel: one `implementer` per group of listed functions, as [mutation.md](mutation.md) section Fix states.
4. **Acceptance loop.** Stack first, always fresh: `docker compose --profile test up -d --build --wait`. Confirm `docker compose ps --format '{{.Service}} {{.CreatedAt}}'` shows created time newer than HEAD. Then acceptance runners from `stacks:`. Python: `pytest tests/acceptance -q --tb=no`. Go: `go test ./tests/acceptance/...`. UI: `npx playwright test tests/acceptance`. On failures: fix round through `implementer`, rebuild, rerun failures only with `pytest tests/acceptance --lf -q --tb=no` and `npx playwright test tests/acceptance --last-failed`. Repeat under the progress guard. Then one final full run. Every run repeats the `--build` line first. Stale image run does not count.

No `/verify` step. `/verify` is a real bundled skill but user-invoked only; the model cannot call it. Playwright E2E is the model's live check. The user may type `/verify` after the gate.

Controller never edits `tests/acceptance/` in this stage. An unplanned deployment or infra gap discovered by the gate is a new slice, not a fix round. Deployment or infra work explicitly named in the approved spec or plan stays in the current slice and is implemented normally.

Never invoke `superpowers:verification-before-completion`. The shell gate owns verification.
