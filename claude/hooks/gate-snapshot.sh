#!/bin/bash
# SubagentStart: record the tree hash of the subagent's start, by agent id.
# gate.sh reads it on SubagentStop and runs no gate when nothing changed.
# Also writes <agent_id>.head, the HEAD `devloop.py audit` starts from, before the board call: a board failure
# cannot skip it. Then tells the board the subagent runs: a Stop while it runs waits for nobody.
GIT_CONFIG_N=${GIT_CONFIG_COUNT:-0}
export "GIT_CONFIG_KEY_$GIT_CONFIG_N=core.fsmonitor" "GIT_CONFIG_VALUE_$GIT_CONFIG_N=false" GIT_CONFIG_COUNT=$((GIT_CONFIG_N + 1))
IN=$(cat)
ID=$(echo "$IN" | jq -r '.agent_id // empty')
[ -n "$ID" ] || exit 0
HOOKS=$(cd "$(dirname "$0")" && pwd)
# The .head file goes to the git-dir of the hook cwd, the repo `devloop.py audit` reads (CLAUDE_PROJECT_DIR: fallback).
CWD=$(echo "$IN" | jq -r '.cwd // empty')
REPO=${CWD:-${CLAUDE_PROJECT_DIR:-.}}
HEADS="$(git -C "$REPO" rev-parse --absolute-git-dir 2>/dev/null)/dev-loop-subagents" \
  && HEAD=$(git -C "$REPO" rev-parse HEAD 2>/dev/null) && mkdir -p "$HEADS" && printf '%s\n' "$HEAD" >"$HEADS/$ID.head"
DIR=
if cd "${CLAUDE_PROJECT_DIR:-.}" && GIT_DIR_=$(git rev-parse --git-dir 2>/dev/null); then
  DIR="$GIT_DIR_/dev-loop-subagents"
fi
printf '%s' "$IN" | bash "$HOOKS/board.sh" subagent-start
[ -n "$DIR" ] || exit 0
mkdir -p "$DIR" && "$HOOKS/tree-hash.sh" >"$DIR/$ID"
exit 0
