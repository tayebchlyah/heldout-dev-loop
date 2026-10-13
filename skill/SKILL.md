---
name: dev-loop
description: Use at the start of any coding session, and after finishing any task, in a repo with dev-loop.json at its root (or, until 1.6, an AGENTS.md that declares vault_notes). Detects the current stage from repo and notes state, runs that one stage, stops.
---

# Dev loop

Run one stage per invocation. Detect, run, stop. Never chain stages in a turn.
End every stage that changed the repo with a direct `scripts/gate.sh`. The Stop hook runs a selective gate (`GATE_SCOPE=changed`); only a direct, full gate writes a green record.

Select one harness route before detecting the stage:

- Claude Code: read [references/claude.md](references/claude.md).
- Codex: read [references/codex.md](references/codex.md).

Run `scripts/check_setup.py --harness claude|codex --repo .`. Stage 0 owns any failure. Do not infer Codex readiness from Claude files, or Claude readiness from Codex files.

Run `scripts/notes_path.py`. `$NOTES` below means its output. Specs and plans live in the notes folder. It fails: Stage 0 owns it.

Glossary = `GLOSSARY.md` at the repo root, else `CONTEXT.md` (the name before Pocock skills 1.3). Both exist: `GLOSSARY.md` wins. New repos write `GLOSSARY.md`.

## Detect the stage

Run in order. First failing check is the stage.

| Check | Stage |
|---|---|
| No glossary (`test -f GLOSSARY.md \|\| test -f CONTEXT.md` fails), or `git rev-parse HEAD` fails | 0 Bootstrap |
| No spec in `$NOTES/specs/` for requested work | 1 Frame |
| Spec exists, frontmatter `review_status` not `approved` | 2 Spec |
| Spec approved, `tests/acceptance/` has no test for it | 3 Acceptance |
| Acceptance exists, no plan in `$NOTES/plans/` | 4 Plan |
| Plan exists, ledger `.superpowers/sdd/<plan>/progress.md` incomplete | 5 Build |
| Ledger complete, acceptance not yet green on this branch | 6 Feature gate |
| Feature gate passed, branch not integrated | 7 Ship |

State stage and the check that chose it, one line. Then run it.

## Where each stage runs

Stages 0 to 2 run in the main checkout on `main`. Stages 3 to 7 run in the feature's own worktree, on the feature's own branch, one worktree for the feature's whole life.
At the start of Stages 3 to 7, work in that worktree yourself: find it with `git worktree list`, or create it with the harness's own worktree mechanism. The branch exists: attach the worktree to it. Else: branch from `origin/main` after `git fetch origin`. Never ask the user to change directory.

Rebase onto `origin/main` at four points only, each right after `git fetch origin`: Stage 2 start (on `main`), Stage 3 start, Stage 6 start, Stage 7 (through `finishing-a-development-branch`). Never during Stages 4 and 5: the SDD ledger and the review packages name commits by hash.
Rebase conflict in `tests/acceptance/`: another feature's file keeps the upstream side (during a rebase that side is `--ours`: `git checkout --ours <file>`). This feature's own file: stop, `spec-tester` resolves it.

## 0 Bootstrap
Invoke `new-repo-setup`. Apply the selected harness setup contract. Claude Code writes its `.claude/settings.json`. Codex writes its `.codex/config.toml` only when a project override is needed. Security review runs once per feature in Stage 6, not per Stop and commit.
`git init`, one commit. Invoke the selected route's domain-modeling skill for first terms of `GLOSSARY.md`.
Detect stacks: `pyproject.toml` = python, `go.mod` = go, `package.json` with UI framework or `frontend/` = ui. Confirm with the user. Write `stacks: <list>` in repo AGENTS.md. Every later stage reads that line.
Layout: PyPA src layout, `uv init --package`. One stack: `src/<pkg>/` + `tests/unit/`. Two stacks: uv workspace root, `backend/src/<pkg>/` + `backend/tests/unit/`, `frontend/src/` + `frontend/tests/unit/`. FastAPI-template `backend/app/` also acceptable; the spec-tester wall covers `src/ app/ backend/ frontend/`. Acceptance tests once, at root `tests/acceptance/` (`api/`, and `e2e/` when ui). Root only: spec-tester writes it and cannot enter a stack dir.
Write `scripts/gate.sh`: runs only the stack `git diff --name-only HEAD` touched. python: ruff, pyright, `pytest tests/unit`. go: `go vet`, `go test ./...`. ui: biome, `tsc --noEmit`, `vitest run`. Never E2E.
`ui` in stacks: `docker-compose` test profile with seeded fixtures, for Stage 6 E2E. Else skip.
Mutation setup per stack, as [references/mutation.md](references/mutation.md) section Setup states. `check_setup.py` verifies it.
Notes path: ask for it, the folder must exist. Write `{"notes": "<path>"}` to `dev-loop.local.json` in the main worktree. Add it to `.gitignore` when `git check-ignore` says it is not ignored.
Done when `scripts/gate.sh` blocks a deliberately broken unit test.

## 1 Frame
Invoke the selected route's grilling skill. Then `superpowers:brainstorming`.
Done when brainstorming has named the path out loud. Superpowers 7 also names a size (quick task, small change, project): every size continues to Stage 2 Spec. No track rule per size.

## 2 Spec
Start fresh: `git fetch origin`, `git switch main`, `git rebase origin/main`. Local commits on `main` or uncommitted changes: stop, ask the user. The analysis below reads current specs and current code.
Continue `superpowers:brainstorming`. Spec to `$NOTES/specs/`.
Spec must declare public interfaces: names, parameter types, return types, seams under test. `ui` in stacks: also routes and accessible names.
Two or more stacks: task 1 of the plan is the contract (OpenAPI or shared types), later tasks are vertical slices. One stack: no contract task.
Supersedes: read the earlier specs in `$NOTES/specs/`. List every earlier `## Acceptance oracle` line the new behaviour contradicts, quoted, each with its replacement. None: write "Supersedes: none". The user approves this list with the spec.
Stop when the user approves. Do not let brainstorming invoke writing-plans: acceptance tests (Stage 3) come before the plan.
Set `review_status: approved` in spec frontmatter. Under each superseded line in its old spec add: `Amended by <feature> on <date>: <replacement>`.

## 3 Acceptance
Start in the feature worktree (see Where each stage runs), then `git fetch origin`, `git rebase origin/main`.
Dispatch the selected route's `spec-tester` or `spec_tester` subagent with spec path and glossary path.
It writes `tests/acceptance/`: API tier always. `ui` in stacks: plus 1 to 3 Playwright E2E per feature. It also updates the earlier tests that assert a superseded line, to the replacement, and no other earlier test. Tell the user to review. One commit for both.
Suite frozen after this commit. Only `spec-tester` edits `tests/acceptance/`, only after a spec oracle change: a Supersedes line or an escaped bug. Controller never writes a test there.
Stop. Tell the user to run `/clear` before Stage 4.

## 4 Plan
Invoke `superpowers:writing-plans`. Plan to `$NOTES/plans/`.
Plan never references `tests/acceptance/`. Choose subagent-driven at handoff.

## 5 Build
Invoke `superpowers:subagent-driven-development`. Work in the feature worktree: SDD's worktree step reuses it and creates none.
Use the selected route's implementer and reviewer roles. Claude Code implementers use `subagent_type: implementer`; Codex implementers use custom agent `implementer`. Task numbers 1, 2, 3, no suffixes.
Ledger gets `## Discoveries` section, one line per surprise.
Deferred findings go to the project note `## Deferred` in the notes folder, not to ledger.

## 6 Feature gate
Run the selected route's feature gate. Order: review wave, triage, fix wave, acceptance loop. Each step is one real skill, agent, tool, or command invocation. Report six lines at the end, one per gate step (acceptance, simplify, code review, spec review, mutation, security): pass or fail. Complete only when all six pass.
Review wave runs once per feature, before any acceptance run, all reviewers pinned to one commit SHA. Never rerun it. A fix never reruns a reviewer.
Defect = one of: (a) finding that quotes a spec `## Acceptance oracle` line or a glossary invariant; (b) correctness finding the reviewer marks CONFIRMED; (c) security finding marked confirmed or high. Quote the line or the verdict in the fix commit. (b) and (c) get a unit test, never an acceptance test. Simplify diffs are behaviour-preserving and apply as is. Mutation: one unit test per function the mutation report lists, as [references/mutation.md](references/mutation.md) states; message-only survivors go to `## Deferred`. Anything else: `## Deferred`, not fixed, no test.
Acceptance loop: full run once, then fix rounds on failures only (`pytest --lf`, `npx playwright test --last-failed`), then one final full run. Fix until green against the frozen suite. Progress guard: every round must reduce the failing count. Two rounds without a reduction, or one test red after three rounds: stop, report, ask the user. Never ship red, never defer a red acceptance test.
Flaky = red, then green on rerun with no code change. Flaky is a race: a read before the write it asserts, a resource shared with a parallel test, or a tool version CI and local disagree on. Name the race with evidence, fix it in the fix loop like any red test, and confirm the fix under CI load. Repo flaky-test rules, when `AGENTS.md` points at them, say how. A rerun to green, a retry or a raised global timeout is not a fix.
Stale image rule. Compose services are `build:` images, source copied in, no bind mount. Host fix changes nothing in running container. Before every acceptance run after any change under `backend/` or `frontend/`: `docker compose --profile test up -d --build --wait`. Then confirm: `docker compose ps --format '{{.Service}} {{.CreatedAt}}'`, created time newer than the fix commit. Run on stale image = not a run, does not count.
Escaped bug: the user adds the oracle line to spec first, `spec-tester` adds the test, then the fix. Controller never edits `tests/acceptance/`.
Red acceptance test of an earlier feature whose line this spec does not supersede: a regression. It goes through the fix loop like any red test. If the user rules it a conflict nobody listed, it takes the escaped bug path.
Unplanned environment, deployment or infra gap discovered by the gate: new slice with its own spec. Never a fix round.
Deployment or infra work explicitly named in the approved spec or plan stays in the current slice. Implement and verify it normally; do not reclassify planned work as a gap.
Never invoke `superpowers:verification-before-completion`. The shell gate owns routine verification.

## 7 Ship
Invoke `superpowers:finishing-a-development-branch`. Option 1 is rebase, never merge.
