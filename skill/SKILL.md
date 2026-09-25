---
name: dev-loop
description: Use at the start of any coding session, and after finishing any task, in a repo whose AGENTS.md declares a vault_notes path. Detects the current stage from repo and vault state, runs that one stage, stops.
---

# Dev loop

Run ONE stage per invocation. Detect, run, stop. Never chain stages in a turn.

Select one harness route before detecting the stage:

- Claude Code: read [references/claude.md](references/claude.md).
- Codex: read [references/codex.md](references/codex.md).

Run `scripts/check_setup.py --harness claude|codex --repo .`. Stage 0 owns any failure. Do not infer Codex readiness from Claude files, or Claude readiness from Codex files.

Read `vault_notes:` from repo AGENTS.md. Specs and plans live there, never in repo.
Read the vault root from `vault` in `~/.agents/dev-loop.config.json`. `$VAULT` below means that value.

Full design, gates and failure modes: vault note `Software/Agent Instructions/Agent Development Workflow.md`.

## Detect the stage

Run in order. First failing check is the stage.

| Check | Stage |
|---|---|
| `test -f CONTEXT.md` fails, or `git rev-parse HEAD` fails | 0 Bootstrap |
| No spec in `$VAULT/<vault_notes>/specs/` for requested work | 1 Frame |
| Spec exists, frontmatter `review_status` not `approved` | 2 Spec |
| Spec approved, `tests/acceptance/` has no test for it | 3 Acceptance |
| Acceptance exists, no plan in `$VAULT/<vault_notes>/plans/` | 4 Plan |
| Plan exists, ledger `.superpowers/sdd/<plan>/progress.md` incomplete | 5 Build |
| Ledger complete, acceptance not yet green on this branch | 6 Feature gate |
| Feature gate passed, branch not integrated | 7 Ship |

State stage and the check that chose it, one line. Then run it.

## 0 Bootstrap
Invoke `new-repo-setup`. Apply the selected harness setup contract. Claude Code writes its `.claude/settings.json`. Codex writes its `.codex/config.toml` only when a project override is needed. Security review runs once per feature in Stage 6, not per Stop and commit.
`git init`, one commit. Invoke the selected route's domain-modeling skill for first terms of CONTEXT.md.
Detect stacks: `pyproject.toml` = python, `go.mod` = go, `package.json` with UI framework or `frontend/` = ui. Confirm with the user. Write `stacks: <list>` in repo AGENTS.md. Every later stage reads that line.
Layout: PyPA src layout, `uv init --package`. One stack: `src/<pkg>/` + `tests/unit/`. Two stacks: uv workspace root, `backend/src/<pkg>/` + `backend/tests/unit/`, `frontend/src/` + `frontend/tests/unit/`. FastAPI-template `backend/app/` also acceptable; the spec-tester wall covers `src/ app/ backend/ frontend/`. Acceptance tests once, at root `tests/acceptance/` (`api/`, and `e2e/` when ui). Root only: spec-tester writes it and cannot enter a stack dir.
Write `scripts/gate.sh`: runs only the stack `git diff --name-only HEAD` touched. python: ruff, pyright, `pytest tests/unit`. go: `go vet`, `go test ./...`. ui: biome, `tsc --noEmit`, `vitest run`. Never E2E.
`ui` in stacks: `docker-compose` test profile with seeded fixtures, for Stage 6 E2E. Else skip.
Mutation setup per stack, as [references/mutation.md](references/mutation.md) section Setup states. `check_setup.py` verifies it.
Done when `scripts/gate.sh` blocks a deliberately broken unit test.

## 1 Frame
Invoke the selected route's grilling skill. Then `superpowers:brainstorming`.
Done when brainstorming has named the path out loud.

## 2 Spec
Continue `superpowers:brainstorming`. Spec to `$VAULT/<vault_notes>/specs/`.
Spec must declare public interfaces: names, parameter types, return types, seams under test. `ui` in stacks: also routes and accessible names.
Two or more stacks: task 1 of the plan is the contract (OpenAPI or shared types), later tasks are vertical slices. One stack: no contract task.
Stop when the user approves. Do NOT let brainstorming invoke writing-plans.
Set `review_status: approved` in spec frontmatter.

## 3 Acceptance
Dispatch the selected route's `spec-tester` or `spec_tester` subagent with spec path and CONTEXT.md path.
It writes `tests/acceptance/`: API tier always. `ui` in stacks: plus 1 to 3 Playwright E2E per feature. Tell the user to review. Commit.
Suite frozen after this commit. Only `spec-tester` edits `tests/acceptance/`, only after spec oracle changes. Controller never writes a test there.
Stop. Tell the user to run `/clear` before Stage 4.

## 4 Plan
Invoke `superpowers:writing-plans`. Plan to `$VAULT/<vault_notes>/plans/`.
Plan never references `tests/acceptance/`. Choose subagent-driven at handoff.

## 5 Build
Invoke `superpowers:subagent-driven-development`.
Use the selected route's implementer and reviewer roles. Claude Code implementers use `subagent_type: implementer`; Codex implementers use custom agent `implementer`. Task numbers 1, 2, 3, no suffixes.
Ledger gets `## Discoveries` section, one line per surprise.
Deferred findings go to vault project note `## Deferred`, not to ledger.

## 6 Feature gate
Run the selected route's feature gate. Order: review wave, triage, fix wave, acceptance loop. Each step is one real skill, agent, tool, or command invocation. Report six lines at the end, one per gate step (acceptance, simplify, code review, spec review, mutation, security): pass or fail. Complete only when all six pass.
Review wave runs once per feature, before any acceptance run, all reviewers pinned to one commit SHA. Never rerun it. A fix never reruns a reviewer.
Defect = one of: (a) finding that quotes a spec `## Acceptance oracle` line or a `CONTEXT.md` invariant; (b) correctness finding the reviewer marks CONFIRMED; (c) security finding marked confirmed or high. Quote the line or the verdict in the fix commit. (b) and (c) get a unit test, never an acceptance test. Simplify diffs are behaviour-preserving and apply as is. Mutation survivors get unit tests. Anything else: `## Deferred`, not fixed, no test.
Acceptance loop: full run once, then fix rounds on failures only (`pytest --lf`, `npx playwright test --last-failed`), then one final full run. Fix until green against the frozen suite. Progress guard: every round must reduce the failing count. Two rounds without a reduction, or one test red after three rounds: stop, report, ask the user. Never ship red, never defer a red acceptance test.
Flaky = red, then green on rerun with no code change. Record it in `## Deferred` as flaky, no code change for it.
Stale image rule. Compose services are `build:` images, source copied in, no bind mount. Host fix changes nothing in running container. Before every acceptance run after any change under `backend/` or `frontend/`: `docker compose --profile test up -d --build --wait`. Then confirm: `docker compose ps --format '{{.Service}} {{.CreatedAt}}'`, created time newer than the fix commit. Run on stale image = not a run, does not count.
Escaped bug: the user adds the oracle line to spec first, `spec-tester` adds the test, then the fix. Controller never edits `tests/acceptance/`.
Unplanned environment, deployment or infra gap discovered by the gate: new slice with its own spec. Never a fix round.
Deployment or infra work explicitly named in the approved spec or plan stays in the current slice. Implement and verify it normally; do not reclassify planned work as a gap.
Never invoke `superpowers:verification-before-completion`. The shell gate owns routine verification.

## 7 Ship
Invoke `superpowers:finishing-a-development-branch`. Option 1 is rebase, never merge.
