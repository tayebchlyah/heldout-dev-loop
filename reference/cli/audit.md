# audit

- `audit`: SubagentStop hook JSON on stdin (`agent_id`, `agent_type`, `agent_transcript_path`, `cwd`). Compares the subagent's tool output with the files it must not read (`spec-tester`: `src/`, `app/`, `backend/`, `frontend/`; any other: `tests/acceptance/`). 2 or more distinct distinctive lines (30 characters or more): a taint record in `<git-dir>/dev-loop/taint.json`; no `<agent_id>.head`: the range starts at `<base>`, else the root. A re-stop replaces the agent's record or skip. JSON `taint` (record or null), `skipped`. Exit 0, also on a skip.
