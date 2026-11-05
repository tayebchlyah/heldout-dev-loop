---
name: continue
description: Continue stopped dev-loop work after a usage limit, a pause, or a switch of account or harness. Wakes stopped subagents in the same session, or takes over from a pause note in a new one. Use on /continue, a plain "continue" after a stop, or a session that names a paused feature.
---

# Continue

`CLI` = `python3 .agents/skills/dev-loop/scripts/devloop.py`.

## Find the feature

Run `CLI status` (`--feature <slug>` when the user named one).

- On `main`, no `--feature`: exactly one `features` item with `paused: true`: take it, rerun with `--feature <slug>`. More than one: ask which. The only question allowed here. None, but `paused` in the output: a Shaping pause of the main checkout, `feature` of the note is the slug.
- Work in that feature's worktree (Claude Code: `EnterWorktree` with its `path`). No worktree (a Shaping pause): stay in the main checkout.
- `<checkout>` below: that worktree, else the main checkout. `<slug>`: `feature` of the note.

Then one of three cases.

## 1. `paused` in the output: handoff

The note comes from another session (another account, another harness, a new chat), or from `/pause`.

1. Read the note: `intent`, `verified`, `open`, `next`, `agents`, `runs`, `dirty`, `ledger_last`, `last_decision`.
2. Read the last 20 assistant messages of `transcript` (when not null), the spec `## Decision log`, the ledger, `git status`.
3. Log it: `CLI decide --kind discovery --feature <slug> --repo <checkout> --what "resumed from a pause note (<reason>)" --why "<one line>" --evidence "<transcript path, or the note path>"`. No log yet (`feature` null, or Stage 1 with no spec: `decide` exits 1): skip this step.
4. `CLI pause --clear --feature <slug> --repo <checkout>` (`feature` null: no `--feature`).
5. Each agent in `agents`: dispatch a fresh one, same role, its full brief, plus this line: "The uncommitted work in the worktree is yours, continue it." Never a second agent on the same task.
6. Each log in `runs`: its process alive (the log still grows): wait on it, never start it again. Dead: read its tail, run it again.
7. Continue the stage: `CLI status`, then its `next`.

## 2. No `paused`, same session: wake

The stop hit this session (a usage limit, an API error). Nothing is lost: each agent keeps its history.

1. Each subagent that stopped during its work (an error result, or no final report): send it a message to continue. Claude Code: `SendMessage` to its id. Codex: `send_input` (v1) or `followup_task` (v2); a closed agent: `resume_agent` first.
2. Never dispatch a new agent on unfinished work. A wake that fails: a fresh agent with the full brief plus the "uncommitted work is yours" line.
3. Each background run still alive: re-attach (its log, a monitor on it). Never start a run again while it runs.
4. Continue the stage from `status`.

## 3. Nothing stopped

Continue the stage: `CLI status`, then its `next`.

## Always

- A finished agent never gets new work (a fix round, a new task): new work goes to a fresh agent with the full brief.
- Delivery runs under the autonomy grant: decide, log with `CLI decide`, go on.
