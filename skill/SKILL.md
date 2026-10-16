---
name: dev-loop
description: Use at the start of any coding session, and after finishing any task, in a repo with dev-loop.json at its root. Detects the current stage with scripts/devloop.py status, then runs it and the stages its session chains.
---

# Dev loop

End every stage that changed the repo with a direct gate: `commands.gate` from `dev-loop.json` when mapped, else `scripts/gate.sh`. The Stop hook runs a selective gate (`GATE_SCOPE=changed`); only a direct, full gate writes a green record.

Select one harness route before detecting the stage:

- Claude Code: read [references/claude.md](references/claude.md).
- Codex: read [references/codex.md](references/codex.md).

Run `scripts/check_setup.py --harness claude|codex --repo .`. Stage 0 owns any failure. Do not infer Codex readiness from Claude files, or Claude readiness from Codex files.

Run `scripts/notes_path.py`. `$NOTES` below means its output. Specs and plans live in the notes folder. It fails: Stage 0 owns it.

Glossary = `GLOSSARY.md` at the repo root, else `CONTEXT.md` (the name before Pocock skills 1.3). Both exist: `GLOSSARY.md` wins. New repos write `GLOSSARY.md`.

## Tracks and sessions

Stage 1 sizes the work:

- Track S: one session, no spec, no plan, branch `<prefix>/<slug>`. Stage 1, Stage 5, Stage 6 review wave, triage and fix wave only, Stage 7. `devloop status` reports Track S Stage 6 until the `review-done` marker exists, written after the fix wave.
- Track M: Session A, then Session B. Build `superpowers:executing-plans`.
- Track L: Session A, then Session B. Build `superpowers:subagent-driven-development`. Spec adds threat model. Never auto-merged.

Session A: Stages 1 and 2, on `main`. Stops at the spec approval. Track S: Stage 1, then build in the same session under the grant.
Session B: Stages 3 to 7 chained, under the grant. Each stage ends, run `scripts/devloop.py status` again, run the next stage. Session B start, `status` has `kit_drift`: stop; tell the user to merge the kit upgrade to `main` and start a new session there. Kit upgrade: own PR on `main` before Session B.

Autonomy grant (Session B, Track S after Stage 1):

- Make each open decision yourself: pick the option you would recommend, record it in the spec under `## Assumptions`, one line of reason. Track S: in the PR body.
- Stop and report only for: a gate still red after three fix rounds, a one-way door the approved spec does not cover, an action that deletes data or reaches production, a usage limit, a conflict with an earlier spec that its Supersedes list does not name.
- Push the feature branch, open the PR. Merge only under Stage 7 merge rule. Ask before a tag push.
- Report once, at the end: PR link, gate results, the `## Assumptions` list, deferred items.

Monitor rule: a command longer than two minutes runs in the background as `scripts/devloop.py logged -- <cmd>`, full log in a file, with a Monitor on it. Never end a turn while a run has nobody watching.

Verification budget. The kit enforces no time cap: a hook the harness kills is a non-blocking error, so a hard cap could let a red gate pass. Each project caps T0 in its own `gate.sh`.

| Tier | When | Budget | Content |
|---|---|---|---|
| T0 | Stop hook, each turn | 60 s or less, never falls back to the full gate | lint, types, impacted tests without `proof` or `slow` markers |
| T1 | End of a stage, direct `gate.sh` | 4 min or less | full fast tier, writes the green record |
| T2 | CI on each PR | 8 min or less, jobs split | fast tier, proof tier, UI |
| T3 | CI, feature gate | API 11 min, Playwright 9 min per shard | held-out acceptance, flows only |
| T4 | Nightly | about 60 min | mutation on critical modules, test-value audit, flake statistics |

## Detect the stage

Run `scripts/devloop.py status` (`--feature <slug>` from `main` names one open feature). State `stage` and `check`, one line. Run it. `next` names the next action. `shipped`: nothing left.

Checklist. Claude: one `TaskCreate` item per stage; end of Stage 4: one item per plan task, subject `Task N: <plan heading>`; `in_progress` at start, `completed` after its ledger line; close a stage item before the next. Codex: no plan tool, the ledger is the record. Both: `task-start` for each task, inline too.

Board: hooks write it. Never edit a status note. Never send `devloop.py` JSON to `/dev/null`. `status` has `board`: tell the user the board did not update, with the reasons.

## Where each stage runs

Stages 0 to 2 run in the main checkout on `main`. Stages 3 to 7 run in the feature's own worktree, on the feature's own branch, one worktree for the feature's whole life.
At the start of Stages 3 to 7, work in that worktree yourself: find it with `git worktree list`, or create it with the harness's own worktree mechanism. The branch exists: attach the worktree to it. Else: branch from `origin/main` after `git fetch origin`. Never ask the user to change directory.

Rebase onto `origin/main` at four points only, each right after `git fetch origin`: Stage 2 start (on `main`), Stage 3 start, Stage 6 start, Stage 7. Never during Stages 4 and 5: the SDD ledger and the review packages name commits by hash.
Rebase conflict in `tests/acceptance/`: another feature's file keeps the upstream side (during a rebase that side is `--ours`: `git checkout --ours <file>`). This feature's own file: stop, `spec-tester` resolves it.

## 0 Bootstrap
Invoke `new-repo-setup`. Apply the selected harness setup contract. Claude Code writes its `.claude/settings.json`. Codex writes its `.codex/config.toml` only when a project override is needed. Security review runs once per feature in Stage 6, not per Stop and commit.
`git init`, one commit. Invoke the selected route's domain-modeling skill for first terms of `GLOSSARY.md`.
Detect stacks: `pyproject.toml` = python, `go.mod` = go, `package.json` with UI framework or `frontend/` = ui. Confirm with the user. Write `stacks: <list>` in repo AGENTS.md. Every later stage reads that line.
Layout: PyPA src layout, `uv init --package`. One stack: `src/<pkg>/` + `tests/unit/`. Two stacks: uv workspace root, `backend/src/<pkg>/` + `backend/tests/unit/`, `frontend/src/` + `frontend/tests/unit/`. FastAPI-template `backend/app/` also acceptable; the spec-tester wall covers `src/ app/ backend/ frontend/`. Acceptance tests once, at root `tests/acceptance/` (`api/`, and `e2e/` when ui). Root only: spec-tester writes it and cannot enter a stack dir.
Write `scripts/gate.sh`: runs only the stack `git diff --name-only HEAD` touched. python: ruff, pyright, `pytest tests/unit`. go: `go vet`, `go test ./...`. ui: biome, `tsc --noEmit`, `vitest run`. Never E2E.
`ui` in stacks: `docker-compose` test profile with seeded fixtures, for Stage 6 E2E. Else skip.
Mutation setup per stack, as [references/mutation.md](references/mutation.md) section Setup states. `check_setup.py` verifies it. New repo with CI only: the nightly mutation workflow, as `mutation.md` section Nightly CI states.
Write the app CLI skeleton (stack up, stack reset, acceptance run), mapped in `commands` of `dev-loop.json`.
New repo only: empty `CODING_STANDARDS.md` at the root. Only the standards reviewer reads it.
Notes path: ask for it, the folder must exist. Write `{"notes": "<path>"}` to `dev-loop.local.json` in the main worktree. Add it to `.gitignore` when `git check-ignore` says it is not ignored.
Done when `scripts/gate.sh` blocks a deliberately broken unit test.

## 1 Frame
Cap check first: when `## Deferred` holds 20 open items (the cap), triage it, then frame new work.
Invoke the selected route's `grilling` skill. No other framing skill. UI work: the route's `prototype` skill first, a throwaway prototype.
Work larger than one session: tell the user to run `/wayfinder`. User-invoked only, the agent cannot call it.
Propose the track, S, M or L, with a one-line reason. Track S: no spec, no plan. Branch `<prefix>/<slug>` from `origin/main` in its own worktree, then Stage 5. Tracks M and L: Stage 2 Spec. Done when the user accepts the track.

## 2 Spec
Start fresh: `git fetch origin`, `git switch main`, `git rebase origin/main`. Local commits on `main` or uncommitted changes: stop, ask the user. The analysis below reads current specs and current code.
Write the spec from [references/spec.md](references/spec.md) to `$NOTES/specs/`. Run its builder check. Then once, the only board command: `scripts/devloop.py board --claim <slug>`. Track L: its threat model. One-way door: its doubt review.
Spec must declare public interfaces: names, parameter types, return types, seams under test. `ui` in stacks: also routes and accessible names.
Two or more stacks: task 1 of the plan is the contract (OpenAPI or shared types), later tasks are vertical slices. One stack: no contract task.
Supersedes: read the earlier specs in `$NOTES/specs/`. List every earlier `## Acceptance oracle` line the new behaviour contradicts, quoted, each with its replacement. None: write "Supersedes: none". The user approves this list with the spec.
Acceptance: decide from Stages 1 and 2, write it in spec frontmatter `acceptance:`. A new or changed user or system flow: empty. No flow change, product code changes (refactor, performance): `regression`. No product code change (tests, CI, tooling, docs only): `none`. `regression` or `none`: one `## Acceptance oracle` line says why no flow changes, `## Acceptance flows` says none. The user approves it with the spec.
Stop when the user approves: end of Session A. Acceptance tests (Stage 3) come before the plan.
Set `review_status: approved` in spec frontmatter. Under each superseded line in its old spec add: `Amended by <feature> on <date>: <replacement>`.

## 3 Acceptance
Start in the feature worktree (see Where each stage runs), then `git fetch origin`, `git rebase origin/main`.
Spec `acceptance: regression` or `none`: stop here, no `spec-tester`, no commit. `devloop status` reports Stage 4.
Dispatch the selected route's `spec-tester` or `spec_tester` subagent with spec path and glossary path, plus the feature slug (`feature` from `devloop status`).
It writes `tests/acceptance/`: API tier always. `ui` in stacks: plus 1 to 3 Playwright E2E per feature. It also updates the earlier tests that assert a superseded line, to the replacement, and no other earlier test. No human review. One commit for both.
Suite frozen after this commit. Only `spec-tester` edits `tests/acceptance/`, only after a spec oracle change: a Supersedes line or an escaped bug. Controller never writes a test there.

## 4 Plan
Invoke `superpowers:writing-plans`. Plan to `$NOTES/plans/`. Plan frontmatter `spec: <spec file name>`.
Plan never references `tests/acceptance/`. At handoff: Track M inline execution, Track L subagent-driven.
Smart zone rule. Each task fits one smart zone: a fresh agent finishes it from its brief alone. One module or one seam, the brief names each file it changes, the task needs no ruling from another task. A task that fails: split it before Stage 5.

## 5 Build
Track S: no plan, no executor. Build in the feature worktree yourself, test-first, then the Stage 6 review wave, triage and fix wave, then Stage 7.
Tracks M and L: executor by spec `track:`. Track M: `superpowers:executing-plans`. Track L: `superpowers:subagent-driven-development`. Work in the feature worktree: the executor's worktree step reuses it and creates none.
Skip the executor's final whole-branch review: the Stage 6 review wave replaces it. Keep its workspace: the ledger stays.
Whoever writes code reads [references/building.md](references/building.md) and [references/tests.md](references/tests.md) first. A test that needs a running stack: `commands.stack_up`, else `docker compose --profile test up -d --wait`. Test-first: the route's `tdd` skill. A bug or a failing test with no clear cause: the route's `diagnosing-bugs` skill.
Track L: use the selected route's implementer and reviewer roles. Claude Code implementers use `subagent_type: implementer`; Codex implementers use custom agent `implementer`. Task numbers 1, 2, 3, no suffixes.
Ledger gets `## Discoveries` section, one line per surprise.
Deferred findings go to the project note `## Deferred` in the notes folder, not to ledger.

## 6 Feature gate
Start: `git fetch origin`, `git rebase origin/main`. Fresh stack: `commands.stack_reset`, else `docker compose --profile test up -d --build --wait` when the repo has a compose test profile. `GATE_SHA` = HEAD. `commands.<key>` = key of `commands` in `dev-loop.json`.
Then run the selected route's feature gate. Order: review wave, triage, fix wave, acceptance loop. Each step is one real skill, agent, tool, or command invocation.
Track S: review wave, triage and fix wave only. No acceptance loop, no ledger line. After the fix wave: `mkdir -p "$(git rev-parse --git-dir)/dev-loop"`, then `git rev-parse HEAD > "$(git rev-parse --git-dir)/dev-loop/review-done"`. Then Stage 7.
Review wave: the route's three reviewers, whole branch against `main`. Review wave runs once per feature, before any acceptance run, all reviewers pinned to `GATE_SHA`. Never rerun it. A fix never reruns a reviewer.
Triage, controller. Merge the tables, dedupe by file and line. Defect = CONFIRMED finding and one of: (a) finding that quotes a spec `## Acceptance oracle` line or a glossary invariant; (b) correctness finding; (c) security finding. Every defect is fixed, no cap. More than 5 defects: fix all, say so in the final report, sign the slice was too large. Act On list: every defect, plus at most 5 optional non-defect items, lead's judgment picks. Quote the line or the verdict in the fix commit. (b) and (c) get a unit test, never an acceptance test. Same standards finding in two or more places: `## Gardener`, marked lint candidate. Every other non-defect finding: `## Gardener`, not fixed, no test.
`## Gardener` in the project note: standards findings and non-defect items, triaged weekly. `## Deferred` holds at most 20 open items. At the cap, Stage 1 triages before it frames new work. Open security item in `## Deferred` blocks a release.
Fix wave: one `implementer` per file cluster, one at a time, for the Act On list. Each passes `scripts/devloop.py gate --summary` (`exit` 0). Red: read `failures` and `log` from its JSON.
Acceptance loop. Spec `acceptance: none`: no loop, the acceptance report line reads `skipped: <the oracle line reason>`. `regression`: the loop runs the existing suite, as below.
Run: `commands.acceptance`, else the route's runner lines. Full run once, then fix rounds on failures only (`commands.acceptance_failed`, else `pytest --lf`, `npx playwright test --last-failed`), then one final full run. Fix until green against the frozen suite.
Before each rerun, classify every failure as product, test or environment, with evidence. Product: fix round through `implementer`. Test: the held-out test contradicts the spec: `spec-tester` fixes it, quoting the spec line. Environment: stack, data, tool version or race (a read before the write it asserts, a resource shared with a parallel test, a tool version CI and local disagree on). Fix it once at its root, confirm under CI load. Repo flaky-test rules, when `AGENTS.md` points at them, say how. Never rerun to green: a rerun, a retry or a raised global timeout is not a fix.
Progress guard: every round must reduce the failing count. Two rounds without a reduction, or one test red after three rounds: stop, report, ask the user. Never ship red, never defer a red acceptance test.
Stale image rule. Compose services are `build:` images, source copied in, no bind mount. Host fix changes nothing in running container. Before every acceptance run after any change under `backend/` or `frontend/`: `commands.stack_reset`, else `docker compose --profile test up -d --build --wait`. Then confirm: `docker compose ps --format '{{.Service}} {{.CreatedAt}}'`, created time newer than the fix commit. Run on stale image = not a run, does not count.
Escaped bug: the user adds the oracle line to spec first, `spec-tester` adds the test, then the fix. Controller never edits `tests/acceptance/`.
Red acceptance test of an earlier feature whose line this spec does not supersede: a regression. It goes through the fix loop like any red test. If the user rules it a conflict nobody listed, it takes the escaped bug path.
Unplanned environment, deployment or infra gap discovered by the gate: new slice with its own spec. Never a fix round.
Deployment or infra work explicitly named in the approved spec or plan stays in the current slice. Implement and verify it normally; do not reclassify planned work as a gap.
End: append `Feature gate: green at <sha>` to the ledger `.superpowers/sdd/<plan basename>/progress.md`. `<sha>` = HEAD after the final full run (`GATE_SHA` when no fix landed). Report four lines: acceptance, then each of the three reviewers (passes when its defects are fixed), pass or fail. Complete only when all four pass.
Never invoke `superpowers:verification-before-completion`. The shell gate owns routine verification.

## 7 Ship
dev-loop's own steps, no finishing skill.
`git fetch origin`, `git rebase origin/main`, `scripts/devloop.py gate --summary` (`exit` 0, else fix first), `git push`.
PR body through the route's `pr` skill. It names the door (one-way or two-way) and the blast radius.
`scripts/devloop.py ci wait`. `state` `fail`: read `failed_job` and `first_error`, fix, push, wait again. Three red rounds: stop, report.
Merge rule, `merge` in `dev-loop.json`. `merge: auto` (default): after green CI and the Stage 6 review wave, merge a Track S or M PR that is a two-way door with a small blast radius. A one-way door or Track L waits for the user. `merge: human`: every PR waits for the user.
Open security item in `## Deferred` blocks a release. Never merge a release PR or push a tag while one is open.
Merge by `gh pr merge --rebase`, never a merge commit. Right after the merge: write `shipped: <merge date>` (`YYYY-MM-DD`) and `pr: <PR url>` into the spec frontmatter in the notes folder. Track S: no spec, skip the write. Every track: `git fetch origin` right after the merge. Report once (autonomy grant).
