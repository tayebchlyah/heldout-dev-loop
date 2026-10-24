---
name: pause
description: Stop the dev-loop work now and write a pause note, so this or another session can continue it. User-invoked only.
disable-model-invocation: true
---

# Pause

User asked to stop now. Any stage, any reason. Do this, in order, nothing else:

1. No new dispatch, no new command that changes the repo.
2. Stop each running subagent. Claude Code: `TaskStop` on each. Codex: `interrupt_agent` (v2), else `close_agent` (v1). Note each agent id and its task (`Task N: <heading>`, or one line).
3. Background runs (gate, acceptance, `logged` runs): leave them alive. Note each log path.
4. Write the note, from the repo or worktree you work in:

   ```bash
   python3 .agents/skills/dev-loop/scripts/devloop.py pause \
     --intent "<what you were doing>" --verified "<what is proven, with the run>" \
     --open "<what is open or unproven>" --next "<the next action>" \
     --agent "<id>=<task>" ... --run "<log path>" ...
   ```

   One `--agent` per stopped agent, one `--run` per live run. The note holds the rest itself: stage, ledger line, uncommitted paths, last decision log row, session and transcript.
5. End the turn. Report in 3 lines: what stopped, the note path, how to resume (`/continue` here or in a new session, Codex `$continue`).

No commit: the worktree keeps the uncommitted work.
