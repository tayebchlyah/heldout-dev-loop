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
- Validate with `python3 ~/.agents/skills/dev-loop/scripts/check_setup.py --harness codex --repo .`.

## Skill and agent names

- Domain work: `domain-modeling`, `grilling`, `codebase-design`, `code-review`, `research`, `handoff` from the local Matt Pocock installation.
- Core loop: `superpowers:brainstorming`, `superpowers:writing-plans`, `superpowers:subagent-driven-development`, `superpowers:test-driven-development`, `superpowers:systematic-debugging`, `superpowers:finishing-a-development-branch`.
- Acceptance author: custom agent `spec_tester`.
- SDD implementers: custom agent `implementer`.
- Task reviewers and re-reviewers: custom read-only agent `task_reviewer`.
- Explicitly request each custom role. A generic worker does not provide the held-out-test wall.

## Feature gate

Run in order. Stop on first failure.

1. Acceptance runners from `stacks:`. Python: `pytest tests/acceptance -q --tb=no`. Go: `go test ./tests/acceptance/...`. UI: `npx playwright test tests/acceptance`, then drive one real journey with browser or computer-use tools.
2. Dispatch custom agent `simplifier`. It inspects reuse, simplification, efficiency, and abstraction level, then applies only in-scope fixes. Rerun acceptance.
3. Dispatch custom read-only agent `final_reviewer`. In the CLI, Codex `/review` is the interactive equivalent. Fix confirmed correctness findings, then re-review.
4. Invoke skill `code-review` with fixed point `main`.
5. Mutation on the diff: `mutmut run` or `npx stryker run --since main --incremental`.
6. Dispatch custom read-only agent `security_reviewer`. Fix confirmed security findings, then re-review.

Do not substitute Claude-only review, security, or verification slash commands in the Codex branch. Do not invoke `superpowers:verification-before-completion`. The Stop gate owns routine verification.
