#!/bin/sh
# Stop and SubagentStop gate. Run a dirty repository's executable scripts/gate.sh.
input=$(cat)
[ "$(printf '%s' "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)" = true ] && exit 0
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] || exit 0
cd "$cwd" 2>/dev/null || exit 0
[ -x scripts/gate.sh ] || exit 0
[ -n "$(git status --porcelain 2>/dev/null)" ] || exit 0

output=$(mktemp)
if ! scripts/gate.sh >"$output" 2>&1; then
  reason=$(tail -c 3000 "$output")
  jq -n --arg reason "$reason" \
    '{decision:"block",reason:("Gate failed. Fix it. Do not weaken or skip tests.\n\n" + $reason)}'
fi
rm -f "$output"
exit 0
