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

- Run instead of ask. Before a question or a stop, find out whether a run answers it: a command, a test, a prototype, a read. Yes: do the run, decide from its result. Claude: the ask wall denies `AskUserQuestion` in a feature worktree.
- Make each open decision yourself: pick the option you would recommend, then log it: `scripts/devloop.py decide --kind decision --what <pick> --why <one line> --evidence <run, file:line, or link> [--result <text>]`. Add `--risk` when a wrong pick changes what users see, changes or loses data, touches security, or costs more than one task to undo. Each surprise: `--kind discovery`. Only the controller runs `decide`: the log is in the spec (`## Decision log`, Track S: `<git-dir>/dev-loop/decisions.md`). Old specs keep their Assumptions section, read only.
- Stop and report only for: a gate still red after three fix rounds, a one-way door the approved spec does not cover (a stop item, never a `decide` row), an action that deletes data or reaches production, a tag push, a release PR merge, a usage limit, a conflict with an earlier spec that its Supersedes list does not name. Stop item: stop, report it as the final message, no question; the user answers in the next turn.
- Push the feature branch, open the PR. Merge only under Stage 7 merge rule. Tag and release PR: Stage 7 release rule.
- Report once, at the end, the full report. First sentence: what waits on the user. Then each waiting item, riskiest first: each `risk` row, the stop item when the session stopped, each Attention item (Stage 7 log audit), each new `## Deferred` item. Per item: link, what it changes for users, evidence, risk, pick. Never cut or shrink an item for length. Then the PR link and the gate lines. The PR body carries the same list in its `Decisions` section.


Monitor rule: a command longer than two minutes runs in the background as `scripts/devloop.py logged -- <cmd>`, full log in a file, with a Monitor on it. Never end a turn while a run has nobody watching.

Verification budget. The kit enforces no time cap: a hook the harness kills is a non-blocking error, so a hard cap could let a red gate pass. Each project caps T0 in its own `gate.sh`.

| Tier | When | Budget | Content |
|---|---|---|---|
| T0 | Stop hook, each turn | 60 s or less, never falls back to the full gate | lint, types, impacted tests without `proof` or `slow` markers |
| T1 | End of a stage, direct `gate.sh` | 4 min or less | full fast tier, writes the green record |
| T2 | CI on each PR | 8 min or less, jobs split | fast tier, proof tier, UI |
| T3 | CI, feature gate | API 11 min, Playwright 9 min per shard | held-out acceptance, flows only |
| T4 | Nightly | about 60 min | mutation on critical modules, test-value audit, flake statistics |

## Pause and continue

A usage limit, a switch of account or of harness stops the work. Two kit skills, both harnesses (Codex: `$pause`, `$continue`):

- `/pause`: the user stops the work now, any stage. Stop each running subagent, leave background runs alive, write the note with `scripts/devloop.py pause`. User-invoked only.
- `/continue`: the user calls it, or you run it after a plain "continue". It runs the procedure below.
- Claude: a `StopFailure` `rate_limit` in a feature worktree writes the note itself (`pause.sh`). Codex: only `/pause` writes one.

`status` reads the note (`<git-dir>/dev-loop/pause.json`). `paused` in the output, `next` `resume from the pause note`: another session left it; hand off: read the note, the last 20 assistant messages of its `transcript`, the decision log, the ledger, `git status`; log `decide --kind discovery --what "resumed from a pause note (<reason>)"` with the transcript as evidence; `scripts/devloop.py pause --clear`; give each note agent to a fresh agent with its full brief plus "the uncommitted work in the worktree is yours, continue it"; continue the stage. A session start, a new session that names the feature (`status --feature <slug>`), and a bare "continue" on `main` (one `paused: true` feature: take it; more: ask which) all land here.
No `paused`, same session: wake. Each subagent that stopped during its work gets a message to continue (Claude `SendMessage`; Codex `send_input` or `followup_task`). Never dispatch a new agent on unfinished work. Re-attach to each background run still alive; never start it again. A finished agent never gets new work: new work goes to a fresh agent.

## Detect the stage

Run `scripts/devloop.py status` (`--feature <slug>` from `main` names one open feature). State `stage` and `check`, one line. Run it. `next` names the next action. `shipped`: nothing left.

Checklist. Claude: one `TaskCreate` item per stage; end of Stage 4: one item per plan task, subject `Task N: <plan heading>`; `in_progress` at start, `completed` after its ledger line; close a stage item before the next. Codex: no plan tool, the ledger is the record. Both: `task-start` for each task, inline too.

Board: hooks write it. Never edit a status note. Never send `devloop.py` JSON to `/dev/null`. `status` has `board`: tell the user the board did not update, with the reasons.

## Where each stage runs

Stages 0 to 2 run in the main checkout on `main`. Stages 3 to 7 run in the feature's own worktree, on the feature's own branch, one worktree for the feature's whole life.
At the start of Stages 3 to 7, work in that worktree yourself: find it with `git worktree list`, or create it with the harness's own worktree mechanism. The branch exists: attach the worktree to it. Else: branch from `origin/main` after `git fetch origin`. Never ask the user to change directory. Claude: enter it with `EnterWorktree` (`path` for an existing worktree): the session cwd, and so the hook `cwd` of the ask wall and of `pause.sh`, is then the worktree. A `cd` in Bash does not move it.

Rebase onto `origin/main` at four points only, each right after `git fetch origin`: Stage 2 start (on `main`), Stage 3 start, Stage 6 start, Stage 7. Never during Stages 4 and 5: the SDD ledger and the review packages name commits by hash.
Every conflict under `tests/acceptance/` goes to `spec-tester`: it resolves the file and runs `scripts/devloop.py seal --rebase-continue`, which writes the new seal value into the commit message of the rebase and continues it. A conflict happens only in a file the seal commit touched.

## 0 Bootstrap
Invoke `new-repo-setup`. Apply the selected harness setup contract. Claude Code writes its `.claude/settings.json`. Codex writes its `.codex/config.toml` only when a project override is needed. Security review runs once per feature in Stage 6, not per Stop and commit.
`git init`, one commit. Invoke the selected route's domain-modeling skill for first terms of `GLOSSARY.md`.
Detect stacks: `pyproject.toml` = python, `go.mod` = go, `package.json` with UI framework or `frontend/` = ui. Confirm with the user. Write `stacks: <list>` in repo AGENTS.md. Every later stage reads that line.
Layout: PyPA src layout, `uv init --package`. One stack: `src/<pkg>/` + `tests/unit/`. Two stacks: uv workspace root, `backend/src/<pkg>/` + `backend/tests/unit/`, `frontend/src/` + `frontend/tests/unit/`. FastAPI-template `backend/app/` also acceptable; the spec-tester wall covers `src/ app/ backend/ frontend/`. Acceptance tests once, at root `tests/acceptance/` (`api/`, and `e2e/` when ui). Root only: spec-tester writes it and cannot enter a stack dir.
Write `scripts/gate.sh`: runs only the stack `git diff --name-only HEAD` touched. python: ruff, pyright, `pytest tests/unit`. go: `go vet`, `go test ./...`. ui: biome, `tsc --noEmit`, `vitest run`. Never E2E. It exports `core.fsmonitor=false` through `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_<n>`, `GIT_CONFIG_VALUE_<n>`, appended after the caller's entries, before its first git call (`check_setup.py` checks it, and skips it when `commands.gate` runs another command).
`ui` in stacks: `docker-compose` test profile with seeded fixtures, for Stage 6 E2E. Else skip.
Verify skill, `ui` in stacks: `scripts/devloop.py verify-init --base-url <url>`, then follow [references/verify.md](references/verify.md): fill the skill, seed 3 to 5 map entries, prove it once. Other stacks: none, or opt in the same way. `"verify": false` in `dev-loop.json` opts out. `check_setup.py` fails on a `ui` stack with no `verify` key.
Mutation setup per stack, as [references/mutation.md](references/mutation.md) section Setup states. `check_setup.py` verifies it. New repo with CI only: the nightly mutation workflow, as `mutation.md` section Nightly CI states.
Write the app CLI skeleton (stack up, stack reset, acceptance run), mapped in `commands` of `dev-loop.json`.
New repo only: empty `CODING_STANDARDS.md` at the root. Only the standards reviewer reads it.
Notes path: ask for it, the folder must exist. Write `{"notes": "<path>"}` to `dev-loop.local.json` in the main worktree. Add it to `.gitignore` when `git check-ignore` says it is not ignored.
Done when `scripts/gate.sh` blocks a deliberately broken unit test.

## 1 Frame
Second reviewer ask, Session A only (`status` has no `feature`), before framing: `check_setup.py` printed `Choose the <reviewer> second reviewer` (the project's `second_review` is true at the main ref). Ask the user: enable this reviewer? The enable question has no default answer: no answer leaves `dev-loop.local.json` as it is (an "unanswered takes the recommended answer" rule does not apply to it). Yes: ask which model, default answer the CLI's default model. Write the answer into `second_review` of `dev-loop.local.json` in the main worktree, other keys kept: `{"<reviewer>": {"model": "<model>"}}` (`{}` for the CLI default), or `{"<reviewer>": false}`. Session B never asks: the run gives `skipped: not-chosen`, the report says so.
Then the cap check: when `## Deferred` holds 20 open items (the cap), triage it, then frame new work.
Invoke the selected route's `grilling` skill. No other framing skill. UI work: the route's `prototype` skill first, a throwaway prototype.
Work larger than one session: tell the user to run `/wayfinder`. User-invoked only, the agent cannot call it.
Propose the track, S, M or L, with a one-line reason. Track S: no spec, no plan. Branch `<prefix>/<slug>` from `origin/main` in its own worktree, then Stage 5. Tracks M and L: Stage 2 Spec. Track S, `acceptance: regression` and `acceptance: none` never change `tests/acceptance/`. A change that breaks a held-out test changes a declared interface: it needs a Track M or L spec with a `Supersedes` line. Done when the user accepts the track.

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
It writes `tests/acceptance/`: API tier always. `ui` in stacks: plus 1 to 3 Playwright E2E per feature. It also updates the earlier tests that assert a superseded line, to the replacement, and no other earlier test. No human review. `spec-tester` commits with `scripts/devloop.py seal --feature <slug> --message <subject>`: one commit for both, with the `Acceptance: <slug>` line and the `Dev-Loop-Seal` trailer. A plain `git commit` is blocked for it. Nobody else runs `seal` or types the trailer. A rebase conflict under `tests/acceptance/` goes to it: `scripts/devloop.py seal --rebase-continue`.
Suite frozen after this commit. Only `spec-tester` edits `tests/acceptance/`, only after a spec oracle change: a Supersedes line or an escaped bug. Controller never writes a test there: a hook denies `Edit`, `Write` and `NotebookEdit` there to the main session, and `seal-check` finds a write through Bash. The seal check runs at SubagentStop and Stop, and in `devloop.py gate`.

## 4 Plan
Invoke `superpowers:writing-plans`. Plan to `$NOTES/plans/`. Plan frontmatter `spec: <spec file name>`.
Plan never references `tests/acceptance/`. At handoff: Track M inline execution, Track L subagent-driven.
Smart zone rule. Each task fits one smart zone: a fresh agent finishes it from its brief alone. One module or one seam, the brief names each file it changes, the task needs no ruling from another task. A task that fails: split it before Stage 5.

## 5 Build
Track S: no plan, no executor. Build in the feature worktree yourself, test-first, then the Stage 6 review wave, triage and fix wave, then Stage 7.
Track S bug fix: reproduce with a failing test first, paste its fail output, fix, paste the pass output. Both outputs go in the PR body. The failing test is not committed alone: `main` gets no red commit.
Tracks M and L: executor by spec `track:`. Track M: `superpowers:executing-plans`. Track L: `superpowers:subagent-driven-development`. Work in the feature worktree: the executor's worktree step reuses it and creates none.
Skip the executor's final whole-branch review: the Stage 6 review wave replaces it. Keep its workspace: the ledger stays.
Whoever writes code reads [references/building.md](references/building.md) and [references/tests.md](references/tests.md) first. A test that needs a running stack: `commands.stack_up`, else `docker compose --profile test up -d --wait`. Test-first: the route's `tdd` skill. A bug or a failing test with no clear cause: the route's `diagnosing-bugs` skill.
Track L: use the selected route's implementer and reviewer roles. Claude Code implementers use `subagent_type: implementer`; Codex implementers use custom agent `implementer`. Task numbers 1, 2, 3, no suffixes.
Taint: an implementer or spec-tester read what its wall hides (transcript audit at SubagentStop). `gate --summary` is red with `taint`, the Stop hooks block until it closes. Implementer taint: revert the listed commits with `git revert`, give the task to a fresh implementer. Spec-tester taint: a fresh spec-tester runs `git revert --no-commit <sha>`, then `scripts/devloop.py seal --feature <slug> --reverts <sha> --message <subject>` with only the revert staged, then writes the tests again and runs `scripts/devloop.py seal --feature <slug> --message <subject>`. A tainted subagent with no commit closes its record, a warning only. An implementer that meets a guard block or a taint report stops and reports; it never rephrases the call.
Each implementer final report ends with a `Discoveries` list. The controller logs each line: `scripts/devloop.py decide --kind discovery`. Own surprises too.
Deferred findings go to the project note `## Deferred` in the notes folder, not to ledger.

## 6 Feature gate
Start: `git fetch origin`, `git rebase origin/main`. Fresh stack: `commands.stack_reset`, else `docker compose --profile test up -d --build --wait` when the repo has a compose test profile. `GATE_SHA` = HEAD. `commands.<key>` = key of `commands` in `dev-loop.json`.
Then run the selected route's feature gate. Order: review wave, triage, fix wave, drive, acceptance loop. Each step is one real skill, agent, tool, or command invocation.
Track S: review wave, triage, fix wave and drive only. No acceptance loop, no ledger line. After the drive: `mkdir -p "$(git rev-parse --git-dir)/dev-loop"`, then `git rev-parse HEAD > "$(git rev-parse --git-dir)/dev-loop/review-done"`. Then Stage 7.
Review wave: the route's three reviewers, whole branch against `main`. Review wave runs once per feature, before any acceptance run, all reviewers pinned to `GATE_SHA`. Never rerun it. A fix never reruns a reviewer.
Second review, first in the wave, before the other reviewers: in the background, `scripts/devloop.py logged --name second-review -- python3 <kit>/scripts/devloop.py second-review --harness <harness>` (`<kit>`: this skill's folder; the route gives the line), with a Monitor. It reviews `GATE_SHA`: the worktree does not move during the wave. The reviewer is the other family's CLI. Triage waits for it and reads its JSON from the log: `done`: merge its `output` table, reviewer `second-review:<reviewer>`; its security rows go to the security verifier like any other. `skipped`, `failed`, `tainted`: no table. Report line `second review: pass`, `fail` (a defect not fixed), `skipped: <reason>`, `failed: <reason>` or `tainted`. Only `fail` fails the gate.
Triage, controller. Read `seal` and `weakened` from `scripts/devloop.py gate --summary`. A `seal` violation or an open `taint` is red: fix it before triage (Stage 5 remedy). Each `weakened` item (a test file with an added skip marker or fewer assertion lines than at the merge-base) gets a fix or a quoted reason why it is correct. Merge the tables, dedupe by file and line. Filters first: test each finding against nitpick, hypothetical against actual, premature abstraction, taste, missing context. A non-defect a filter matches is dropped: a `dropped:<filter>` row in the yield log, nothing in `## Gardener`. A correctness finding with no concrete failing input or state is hypothetical: not CONFIRMED, not a defect. Defect = CONFIRMED finding and one of: (a) finding that quotes a spec `## Acceptance oracle` line or a glossary invariant; (b) correctness finding; (c) security finding CONFIRMED by the verifier. Every defect is fixed, no cap. More than 5 defects: fix all, say so in the final report, sign the slice was too large. Act On list: every defect, plus at most 5 optional non-defect items, lead's judgment picks. Quote the line or the verdict in the fix commit. (b) and (c) get a unit test, never an acceptance test. Same standards finding in two or more places: `## Gardener`, marked lint candidate. Every other kept non-defect finding: `## Gardener`, not fixed, no test.
Security verifier: each finding any reviewer marks as security goes to one fresh agent (the route names it) with the candidate-verifier prompt of the installed `security-audit` skill (`VALIDATION-AND-REPORTING.md`, Phase 3) and the finding. Labels: CONFIRMED: defect, rule (c). NEEDS VALIDATION: `## Deferred` as `- <date> (security, needs-validation, <slug>): <text>` (`<slug>` the feature), with the one unresolved fact and a safe check, no severity. REJECTED: a `rejected` row with a one-line reason. A hardening gap: `## Gardener`.
Yield log: `$NOTES/Review Yield.md`, one per project. End of triage: append `## <slug> (<date>)` (Track S: branch slug), one row per deduped finding. Columns: `#`, `file:line`, `reviewers`, `class`, `triage verdict`, `fix sha`, `user verdict`. `class`: `defect-a`, `defect-b`, `defect-c`, `optional`, `gardener`, `dropped:<filter>`, `rejected`, `deferred`. Below the table, one cost line per reviewer: tokens and wall time when the harness reports them, else `n/a`. Second review: reviewer `second-review:<reviewer>`, its cost line from `tokens` and `seconds` of its JSON. After the fix wave: fill `fix sha`. The user fills `user verdict` when they want: never waited on.
`## Gardener` in the project note: standards findings and non-defect items, triaged weekly. `## Deferred` holds at most 20 open items. At the cap, Stage 1 triages before it frames new work. Open security item in `## Deferred` blocks a release.
Fix wave: one `implementer` per file cluster, one at a time, for the Act On list. Every fix round, here and in the acceptance loop, starts a new `implementer`. Never resume a finished agent. Each passes `scripts/devloop.py gate --summary` (`exit` 0). Red: read `failures` and `log` from its JSON.
Drive, after the fix wave, before the acceptance loop: the final full acceptance run then covers every drive fix. [references/verify.md](references/verify.md) section Stage 6: map pass (commit the entry of each route the spec declares; Track S: each route the diff changes), the verify skill's Launch and Doctor, `scripts/devloop.py drive`, the layout check by one fresh subagent on the screenshots of routes with `ok` true, Cleanup after the last drive. Each failure is product (fix round through `implementer`), map (fix the entry) or environment (fix it once at its root). Rerun: `scripts/devloop.py drive --since <sha of the fix round start>`. The progress guard and the stale image rule apply to each rerun. `verify` absent or `false`: skipped.
Acceptance loop. Spec `acceptance: none`: no loop, the acceptance report line reads `skipped: <the oracle line reason>`. `regression`: the loop runs the existing suite, as below.
Run: `commands.acceptance`, else the route's runner lines. Full run once, then fix rounds on failures only (`commands.acceptance_failed`, else `pytest --lf`, `npx playwright test --last-failed`), then one final full run. Fix until green against the frozen suite.
Before each rerun, classify every failure as product, test or environment, with evidence. Product: fix round through `implementer`. Test: the held-out test contradicts the spec: `spec-tester` fixes it, quoting the spec line. Environment: stack, data, tool version or race (a read before the write it asserts, a resource shared with a parallel test, a tool version CI and local disagree on). Fix it once at its root, confirm under CI load. Repo flaky-test rules, when `AGENTS.md` points at them, say how. Never rerun to green: a rerun, a retry or a raised global timeout is not a fix.
Progress guard: every round must reduce the failing count. Two rounds without a reduction, or one test red after three rounds: a stop item. Never ship red, never defer a red acceptance test. Two failed fixes that share one premise, here or in the fix wave: write the premise down, test the premise before a third fix (`references/building.md`, Attack the premise).
Stale image rule. Compose services are `build:` images, source copied in, no bind mount. Host fix changes nothing in running container. Before every acceptance run after any change under `backend/` or `frontend/`: `commands.stack_reset`, else `docker compose --profile test up -d --build --wait`. Then confirm: `docker compose ps --format '{{.Service}} {{.CreatedAt}}'`, created time newer than the fix commit. Run on stale image = not a run, does not count.
Escaped bug: the user adds the oracle line to spec first, `spec-tester` adds the test, then the fix. Controller never edits `tests/acceptance/`.
Red acceptance test of an earlier feature whose line this spec does not supersede: a regression. It goes through the fix loop like any red test. If the user rules it a conflict nobody listed, it takes the escaped bug path.
Unplanned environment, deployment or infra gap discovered by the gate: new slice with its own spec. Never a fix round.
Deployment or infra work explicitly named in the approved spec or plan stays in the current slice. Implement and verify it normally; do not reclassify planned work as a gap.
Closing drive, after the final full acceptance run: a commit after the `head` of the last drive changes a mapped path: Launch and Doctor, `scripts/devloop.py drive --since <head of the last drive>`, the layout check, Cleanup. A failure: a fix round, the final full acceptance run again, then this step again. No such commit, or `verify` absent or `false`: no closing drive. Track S has no acceptance loop: its drive is already the last.
End: append `Feature gate: green at <sha>` to the ledger `.superpowers/sdd/<plan basename>/progress.md`. `<sha>` = HEAD after the final full run (`GATE_SHA` when no fix landed). Report six lines: acceptance, then each of the three reviewers (passes when its defects are fixed), pass or fail, then the `second review:` line, then `drive: pass`, `drive: fail` or `drive: skipped: <reason>`. Complete only when none fails.
Never invoke `superpowers:verification-before-completion`. The shell gate owns routine verification.

## 7 Ship
dev-loop's own steps, no finishing skill.
`git fetch origin`, `git rebase origin/main`, `scripts/devloop.py gate --summary` (`exit` 0, else fix first), `git push`.
Log audit, before the PR body: one fresh subagent, same harness, gets the decision log, the main transcript of this session, and each transcript a handoff row names in its `Evidence`. It reads only assistant text and tool calls, part by part when a file is large. It lists decisions in the transcripts with no row, and rows whose evidence does not exist. A transcript it cannot find: an item "not audited: <path>". Its list is the `Attention` section of the final report and of the PR body.
PR body through the route's `pr` skill. It names the door (one-way or two-way) and the blast radius. Sections `Decisions` (the final report list: each `risk` row, the stop item when the session stopped, each Attention item, each new `## Deferred` item) and `Attention`. Track S: the rows of `<git-dir>/dev-loop/decisions.md` too.
Captures, right after the PR opens, [references/verify.md](references/verify.md) section Stage 7: the before run in a temporary detached worktree at the base (base Launch, this worktree's `scripts/devloop.py drive --routes-from <after.json>`, base Cleanup), then `scripts/devloop.py capture --pr <n> --after <after.json> [--before <before drive.json>]` from the feature worktree, where `<after.json>` comes from `scripts/devloop.py capture --write-after <after.json>` run first in the feature worktree, then `git worktree remove --force` of the temporary worktree. Report line `captures: <n> routes` or `captures: skipped: <reason>`. A failure never blocks the merge. `verify` absent or `false`, or no route with `ok` true: no before run, no capture.
`scripts/devloop.py ci wait`. `state` `fail`: read `failed_job` and `first_error`, fix, push, wait again. Three red rounds: stop, report.
Merge rule. Take `<sha>` = `git rev-parse HEAD` in the feature worktree, then run `scripts/devloop.py merge-check`: it checks that HEAD and reads `merge` and `merge_human_paths` from `dev-loop.json` on the main ref, never the branch. Merge without the user only when it returns `merge` `auto` and, after green CI and the Stage 6 review wave, the PR is Track S or M, a two-way door with a small blast radius. Else wait for the user and name each of its `reasons`. A one-way door or Track L waits for the user. `merge: auto` is the default, `merge: human`: every PR waits for the user.
Merge danger: a one-way door, or a `human-path` reason. Before the PR waits for the user, find the one fact the change is safe because of. Prove it as far down this ladder as is cheap: 1 You said so (worthless alone). 2 You pointed at the line (`file:line`). 3 You showed the bad case cannot happen. 4 You ran it: a script or test that calls the real code and fails loud if the fact is false. 5 You reproduced it in the running app. Once the danger is known (a one-way door at PR time, `human-path` after `merge-check`), add the fact, the step reached, the proof, or `unproven` to the PR body with `gh pr edit`.
Release rule. A release is the user's. Merge the release PR first, then on `main`, at the commit to tag, run `/release-audit` and push the `v*` tag only when `release-check` is ok. A confirmed finding gets a fix slice, then a new audit of the diff. Session B never pushes a tag and never merges a release PR (stop items). `release-check` reads the open rule: a `confirmed` security item in `## Deferred` is open until its line is removed, a NEEDS VALIDATION one (`needs-validation`) until a ruling under it says `accept` or `close`.
Merge by `gh pr merge --rebase --match-head-commit <sha>` (`<sha>` the HEAD merge-check checked: a remote head one commit ahead refuses), never a merge commit. Right after the merge: write `shipped: <merge date>` (`YYYY-MM-DD`) and `pr: <PR url>` into the spec frontmatter in the notes folder. Track S: no spec, skip the write. Every track: `git fetch origin` right after the merge. Claude Code, every track: then `scripts/devloop.py metrics --write` in the feature worktree, report line `metrics: <file>`, or `metrics: failed: <error>`. A failure blocks nothing. Report once (autonomy grant).
