# Codex route

Read this file only in Codex. Use Codex names and walls, not Claude paths or slash commands.

## Setup contract

- Project agents: `.codex/agents/implementer.toml`, `.codex/agents/spec-tester.toml`, `.codex/agents/task-reviewer.toml`.
- Review agents: `.codex/agents/final-reviewer.toml`, `.codex/agents/security-reviewer.toml`.
- Project hooks: `.codex/hooks.json`, plus scripts in `.dev-loop/hooks/codex/`: `gate.sh`, `deny-path.sh`, `git-guard.sh`, `session-start.sh`, and `board.sh`.
- `features.hooks = true`. Trust exact hook definitions through `/hooks` before relying on them.
- The active `dev-workspace` permission profile extends `:workspace` and denies environment files. `dev-read-only` preserves the same denies for reviewers. No legacy `sandbox_mode` key can remain in a loaded config layer.
- `dev-implementer` denies `tests/acceptance` through filesystem policy. `dev-spec-tester` denies `src`, `app`, `backend`, and `frontend` through filesystem policy. These walls do not depend on command spelling or hook trust.
- Command rules prompt for direct pushes and every `git -C` form. They forbid direct hard resets and `rm -rf /`. The trusted Git hook provides defense in depth for wrapped commands.
- Install with `npx github:tchlyah/dev-loop#<tag> init`. Repair or update with `npx github:tchlyah/dev-loop#<tag> upgrade`. Validate with `python3 .agents/skills/dev-loop/scripts/check_setup.py --harness codex --repo .`.

## Skill and agent names

- Matt Pocock skills from the local installation: `grilling`, `domain-modeling`, `codebase-design`, `code-review`, `tdd`, `diagnosing-bugs`, `pr`, `prototype`.
- Core loop: Stage 1 `grilling`, `prototype`. Stage 4 `superpowers:writing-plans`. Stage 5 `superpowers:executing-plans` (Track M), `superpowers:subagent-driven-development` (Track L), `tdd`, `diagnosing-bugs`. Stage 7 `pr`.
- Never invoke these Superpowers skills (Codex has no skill deny list): `superpowers:brainstorming`, `superpowers:test-driven-development`, `superpowers:systematic-debugging`, `superpowers:finishing-a-development-branch`.
- Acceptance author: custom agent `spec_tester`.
- SDD implementers: custom agent `implementer`.
- Task reviewers and re-reviewers: custom read-only agent `task_reviewer`.
- Explicitly request each custom role. A generic worker does not provide the held-out-test wall.

## Feature gate

Four phases, in order. First rebase onto `origin/main`, fresh stack, `GATE_SHA` = HEAD (SKILL.md Stage 6).

1. **Review wave, once.** Three reviewers, in parallel where the session allows it, otherwise in sequence. Each works in the feature worktree on `main...GATE_SHA`: whole branch, report only, and returns a table: file, line, finding, verdict or severity, reviewer.
   - Custom read-only agent `final_reviewer`. In the CLI, Codex `/review` is the interactive equivalent.
   - Invoke skill `code-review` with fixed point `main`.
   - Custom read-only agent `security_reviewer`.
2. **Triage, controller.** SKILL.md Stage 6.
3. **Fix wave.** Custom agent `implementer`, one file cluster at a time, for the Act On list: every defect, plus optional items the lead picks. Each passes `scripts/devloop.py gate --summary`.
4. **Acceptance loop.** SKILL.md Stage 6. Spec `acceptance: none`: skipped. Runner lines when `commands.acceptance` is absent, from `stacks:`. Python: `pytest tests/acceptance -q --tb=no`. Go: `go test ./tests/acceptance/...`. UI: `npx playwright test tests/acceptance`, then drive one real journey with browser or computer-use tools. Failures only when `commands.acceptance_failed` is absent: `--lf` or `--last-failed`.

Do not substitute Claude-only review, security, or verification slash commands in the Codex branch. Do not invoke `superpowers:verification-before-completion`. The Stop gate owns routine verification.
