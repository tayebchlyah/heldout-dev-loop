# Codex route

Read this file only in Codex. Use Codex names and walls, not Claude paths or slash commands.

## Setup contract

- User agents: `~/.codex/agents/implementer.toml`, `~/.codex/agents/spec-tester.toml`, `~/.codex/agents/task-reviewer.toml`.
- Review agents: `~/.codex/agents/simplifier.toml`, `~/.codex/agents/final-reviewer.toml`, `~/.codex/agents/security-reviewer.toml`.
- User hooks: `~/.codex/hooks.json`, plus scripts `gate.sh`, `deny-path.sh`, `git-guard.sh`, and `session-start.sh`.
- `features.hooks = true`. Trust exact hook definitions through `/hooks` before relying on them.
- The active `dev-workspace` permission profile extends `:workspace` and denies environment files. `dev-read-only` preserves the same denies for reviewers. No legacy `sandbox_mode` key can remain in a loaded config layer.
- `dev-implementer` denies `tests/acceptance` through filesystem policy. `dev-spec-tester` denies `src`, `app`, `backend`, and `frontend` through filesystem policy. These walls do not depend on command spelling or hook trust.
- Command rules prompt for direct pushes and every `git -C` form. They forbid direct hard resets and `rm -rf /`. The trusted Git hook provides defense in depth for wrapped commands.
- Install or repair with `npx github:tchlyah/dev-loop`. Validate with `python3 ~/.agents/skills/dev-loop/scripts/check_setup.py --harness codex --repo .`.

## Skill and agent names

- Domain work: `domain-modeling`, `grilling`, `codebase-design`, `code-review`, `research`, `handoff` from the local Matt Pocock installation.
- Core loop: `superpowers:brainstorming`, `superpowers:writing-plans`, `superpowers:subagent-driven-development`, `superpowers:test-driven-development`, `superpowers:systematic-debugging`, `superpowers:finishing-a-development-branch`.
- Acceptance author: custom agent `spec_tester`.
- SDD implementers: custom agent `implementer`.
- Task reviewers and re-reviewers: custom read-only agent `task_reviewer`.
- Explicitly request each custom role. A generic worker does not provide the held-out-test wall.

## Feature gate

Four phases, in order. Record the branch HEAD as `GATE_SHA` first.

1. **Review wave, once.** Dispatch in parallel where the session allows it, otherwise in sequence. All review `GATE_SHA` and return a table: file, line, finding, verdict or severity, step.
   - Custom read-only agent `final_reviewer`. In the CLI, Codex `/review` is the interactive equivalent.
   - Invoke skill `code-review` with fixed point `main`.
   - Custom read-only agent `security_reviewer`.
   - Mutation on the diff. Follow [mutation.md](mutation.md) and return its report.
   - Custom agent `simplifier` last, after the read-only reviewers have returned, because it writes. It applies only in-scope, behaviour-preserving fixes and returns its finding table.
2. **Triage, controller.** Merge the tables. Dedupe by file and line. Apply the defect rule in SKILL.md Stage 6. Everything else to `## Deferred`.
3. **Fix wave.** Custom agent `implementer`, one file cluster at a time: defects (b) and (c), mutation survivors. Each passes the Stop gate.
4. **Acceptance loop.** Stack fresh with `docker compose --profile test up -d --build --wait` when `ui` is in stacks. Acceptance runners from `stacks:`. Python: `pytest tests/acceptance -q --tb=no`. Go: `go test ./tests/acceptance/...`. UI: `npx playwright test tests/acceptance`, then drive one real journey with browser or computer-use tools. On failures: fix round through `implementer`, rebuild, rerun failures only with `--lf` or `--last-failed`. Repeat under the progress guard. Then one final full run.

Do not substitute Claude-only review, security, or verification slash commands in the Codex branch. Do not invoke `superpowers:verification-before-completion`. The Stop gate owns routine verification.
