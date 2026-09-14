#!/bin/sh
# PreToolUse wall. Deny any tool input that names one of the supplied paths.
input=$(cat)
payload=$(printf '%s' "$input" | jq -c '.tool_input // {}' 2>/dev/null) || exit 2

for denied in "$@"; do
  if printf '%s' "$payload" | grep -Fq -- "$denied"; then
    jq -n --arg reason "Blocked: this agent cannot access $denied" \
      '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$reason}}'
    exit 0
  fi
done
exit 0
