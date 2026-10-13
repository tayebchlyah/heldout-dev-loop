#!/bin/sh
# Stop and SubagentStop gate. Run a dirty repository's commands.gate from
# dev-loop.json (sh -c) when mapped, else its executable scripts/gate.sh.
# The hook asks for the selective gate (GATE_SCOPE=changed). A full gate is a direct call.
input=$(cat)
[ "$(printf '%s' "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)" = true ] && exit 0
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] || exit 0
cd "$cwd" 2>/dev/null || exit 0
gate=$(python3 -c 'import json; c = json.load(open("dev-loop.json")).get("commands", {}).get("gate"); print(c if isinstance(c, str) else "")' 2>/dev/null)
[ -n "$gate" ] || [ -x scripts/gate.sh ] || exit 0
[ -n "$(git status --porcelain 2>/dev/null)" ] || exit 0

if [ -n "$gate" ]; then set -- sh -c "$gate"; else set -- scripts/gate.sh; fi
output=$(mktemp)
if ! GATE_SCOPE=changed "$@" >"$output" 2>&1; then
  reason=$(tail -c 3000 "$output")
  jq -n --arg reason "$reason" \
    '{decision:"block",reason:("Gate failed. Fix it. Do not weaken or skip tests.\n\n" + $reason)}'
fi
rm -f "$output"
exit 0
