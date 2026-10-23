#!/bin/bash
# SubagentStart: record the tree hash of the subagent's start, by agent id.
# gate.sh reads it on SubagentStop and runs no gate when nothing changed.
# Also writes <agent_id>.head, the HEAD `devloop.py audit` starts from, before the board call: a board failure
# cannot skip it. Then tells the board the subagent runs: a Stop while it runs waits for nobody.
IN=$(cat)
ID=$(echo "$IN" | jq -r '.agent_id // empty')
[ -n "$ID" ] || exit 0
HOOKS=$(cd "$(dirname "$0")" && pwd)
if cd "${CLAUDE_PROJECT_DIR:-.}" && GIT_DIR_=$(git rev-parse --git-dir 2>/dev/null); then
  DIR="$GIT_DIR_/dev-loop-subagents"
  HEAD=$(git rev-parse HEAD 2>/dev/null) && mkdir -p "$DIR" && printf '%s\n' "$HEAD" >"$DIR/$ID.head"
fi
printf '%s' "$IN" | bash "$HOOKS/board.sh" subagent-start
[ -n "$DIR" ] || exit 0
mkdir -p "$DIR" && "$HOOKS/tree-hash.sh" >"$DIR/$ID"
exit 0
