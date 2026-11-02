#!/bin/sh
# Stop and SubagentStop gate. Run a dirty repository's commands.gate from
# dev-loop.json (sh -c) when mapped, else its executable scripts/gate.sh.
# The hook asks for the selective gate (GATE_SCOPE=changed). A full gate is a direct call.
. "${0%/*}/git-pin.sh"
input=$(cat)
# Refresh the progress board in the background. Called on every path that does not block.
board() {
  ev=stop
  [ "$(printf '%s' "$input" | jq -r '.hook_event_name // empty' 2>/dev/null)" = SubagentStop ] && ev=subagent-stop
  printf '%s' "$input" | sh "$(dirname "$0")/board.sh" "$ev"
}
[ "$(printf '%s' "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)" = true ] && { board; exit 0; }
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] || { board; exit 0; }
cd "$cwd" 2>/dev/null || { board; exit 0; }
gate=$(python3 -I -c 'import json; c = json.load(open("dev-loop.json")).get("commands", {}).get("gate"); print(c if isinstance(c, str) else "")' 2>/dev/null)
[ -n "$gate" ] || [ -x scripts/gate.sh ] || { board; exit 0; }
git_pin_repo .
dirty=$(git status --porcelain 2>/dev/null) && [ -z "$dirty" ] && { board; exit 0; }

if [ -n "$gate" ]; then set -- sh -c "$gate"; else set -- scripts/gate.sh; fi
output=$(mktemp)
if GATE_SCOPE=changed "$@" >"$output" 2>&1; then
  board
else
  reason=$(tail -c 3000 "$output")
  jq -n --arg reason "$reason" \
    '{decision:"block",reason:("Gate failed. Fix it. Do not weaken or skip tests.\n\n" + $reason)}'
fi
rm -f "$output"
exit 0
