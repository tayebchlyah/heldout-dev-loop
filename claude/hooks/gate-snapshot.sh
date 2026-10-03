#!/bin/bash
# SubagentStart: record the tree hash of the subagent's start, by agent id.
# gate.sh reads it on SubagentStop and runs no gate when nothing changed.
IN=$(cat)
ID=$(echo "$IN" | jq -r '.agent_id // empty')
[ -n "$ID" ] || exit 0
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
DIR="$(git rev-parse --git-dir 2>/dev/null)/dev-loop-subagents" || exit 0
mkdir -p "$DIR" && "$(dirname "$0")/tree-hash.sh" >"$DIR/$ID"
exit 0
