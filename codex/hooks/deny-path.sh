#!/bin/sh
# PreToolUse wall. Deny a tool call whose PATH FIELDS name one of the supplied paths.
# Path fields only, never the whole tool input: a write whose body mentions a
# path is not access to it, and blocking that teaches agents to work around
# the wall instead of reporting it.
input=$(cat)
payload=$(printf '%s' "$input" | jq -r '
  [.tool_input.file_path?, .tool_input.path?, .tool_input.notebook_path?,
   .tool_input.pattern?, .tool_input.command?]
  | map(select(type == "string")) | join("\n")' 2>/dev/null) || exit 2

for denied in "$@"; do
  if printf '%s' "$payload" | grep -Fq -- "$denied"; then
    jq -n --arg reason "Blocked: this agent cannot access $denied" \
      '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$reason}}'
    exit 0
  fi
done
exit 0
