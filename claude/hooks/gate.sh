#!/bin/bash
# Stop and SubagentStop gate. Runs commands.gate from dev-loop.json (sh -c) when
# mapped, else the repo's own scripts/gate.sh if one exists.
# The hook asks for the selective gate (GATE_SCOPE=changed). A repo gate that
# knows the variable runs only what the diff reaches and writes no green
# record; one that does not runs in full. A full gate is a direct call.
# SubagentStop runs no gate for a read-only subagent (Explore, Plan) or for a
# subagent whose tree is unchanged since its SubagentStart snapshot.
# It tests the worktree that holds the hook cwd (an EnterWorktree session, #347),
# CLAUDE_PROJECT_DIR when the input has none. The cwd repo counts only when it shares the
# git common dir of CLAUDE_PROJECT_DIR (a linked worktree of the project); a nested
# separate repo (submodule, vendored clone) gates the project. gate-snapshot.sh binds alike.
. "${0%/*}/git-pin.sh"
IN=$(cat)
# Refresh the progress board in the background. Called on every path that does not block.
board() {
  local ev=stop
  [ "$(echo "$IN" | jq -r '.hook_event_name // empty')" = "SubagentStop" ] && ev=subagent-stop
  printf '%s' "$IN" | bash "$(dirname "$0")/board.sh" "$ev"
}
[ "$(echo "$IN" | jq -r '.stop_hook_active // false')" = "true" ] && { board; exit 0; }
CWD=$(echo "$IN" | jq -r '.cwd // empty')
cd "${CWD:-${CLAUDE_PROJECT_DIR:-.}}" || { board; exit 0; }
TOP=$(git rev-parse --show-toplevel 2>/dev/null) && cd "$TOP"
if [ -n "$CLAUDE_PROJECT_DIR" ] && [ "$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" != "$(git -C "$CLAUDE_PROJECT_DIR" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" ]; then
  cd "$CLAUDE_PROJECT_DIR" || { board; exit 0; }
  TOP=$(git rev-parse --show-toplevel 2>/dev/null) && cd "$TOP"
fi
GATE=$(python3 -I -c 'import json; c = json.load(open("dev-loop.json")).get("commands", {}).get("gate"); print(c if isinstance(c, str) else "")' 2>/dev/null)
[ -n "$GATE" ] || [ -x scripts/gate.sh ] || { board; exit 0; }
git_pin_repo .
DIRTY=$(git status --porcelain --ignore-submodules=dirty 2>/dev/null) && [ -z "$DIRTY" ] && { board; exit 0; }
if [ "$(echo "$IN" | jq -r '.hook_event_name // empty')" = "SubagentStop" ]; then
  case "$(echo "$IN" | jq -r '.agent_type // empty')" in Explore|Plan) { board; exit 0; } ;; esac
  ID=$(echo "$IN" | jq -r '.agent_id // empty')
  SNAP="$(git rev-parse --git-dir)/dev-loop-subagents/$ID"
  if [ -n "$ID" ] && [ -f "$SNAP" ]; then
    BEFORE=$(cat "$SNAP")
    rm -f "$SNAP"
    [ "$BEFORE" = "$("$(dirname "$0")/tree-hash.sh")" ] && { board; exit 0; }
  fi
fi
if [ -n "$GATE" ]; then set -- sh -c "$GATE"; else set -- scripts/gate.sh; fi
OUT=$(mktemp)
if GATE_SCOPE=changed "$@" >"$OUT" 2>&1; then
  board
else
  jq -n --arg r "$(tail -c 3000 "$OUT")" \
    '{decision:"block", reason:("Gate failed. Fix it. Do not weaken or skip tests.\n\n" + $r)}'
fi
rm -f "$OUT"
exit 0
