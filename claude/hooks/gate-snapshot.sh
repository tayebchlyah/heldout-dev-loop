#!/bin/bash
# SubagentStart: record the tree hash of the subagent's start, by agent id.
# gate.sh reads it on SubagentStop and runs no gate when nothing changed.
# Also writes <agent_id>.head, the HEAD `devloop.py audit` starts from, before the board call: a board failure
# cannot skip it. Only once: a wake fires SubagentStart again with the same agent id (#281).
# The .head file goes to the git-dir of the hook cwd, the repo `devloop.py audit` reads (CLAUDE_PROJECT_DIR: fallback).
# The tree snapshot is of the tree gate.sh tests: the project when the cwd repo is not a worktree of it.
# Then tells the board the subagent runs: a Stop while it runs waits for nobody.
. "${0%/*}/git-pin.sh"
IN=$(cat)
ID=$(echo "$IN" | jq -r '.agent_id // empty')
[ -n "$ID" ] || exit 0
HOOKS=$(cd "$(dirname "$0")" && pwd)
CWD=$(echo "$IN" | jq -r '.cwd // empty')
REPO=${CWD:-${CLAUDE_PROJECT_DIR:-.}}
HEADS="$(git -C "$REPO" rev-parse --absolute-git-dir 2>/dev/null)/dev-loop-subagents" \
  && [ ! -L "$HEADS" ] && [ ! -L "$HEADS/$ID.head" ] && [ ! -e "$HEADS/$ID.head" ] && HEAD=$(git -C "$REPO" rev-parse HEAD 2>/dev/null) && mkdir -p "$HEADS" \
  && printf '%s\n' "$HEAD" >"$HEADS/$ID.head"
[ -z "$CLAUDE_PROJECT_DIR" ] || [ "$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" = "$(git -C "$CLAUDE_PROJECT_DIR" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" ] || TREE=$CLAUDE_PROJECT_DIR
DIR=
if cd "${TREE:-$REPO}" && GIT_DIR_=$(git rev-parse --absolute-git-dir 2>/dev/null) && cd "$(git rev-parse --show-toplevel)"; then
  DIR="$GIT_DIR_/dev-loop-subagents"
fi
printf '%s' "$IN" | bash "$HOOKS/board.sh" subagent-start
[ -n "$DIR" ] && [ ! -L "$DIR" ] && [ ! -L "$DIR/$ID" ] || exit 0
mkdir -p "$DIR" && "$HOOKS/tree-hash.sh" >"$DIR/$ID"
exit 0
