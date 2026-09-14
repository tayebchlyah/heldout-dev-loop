#!/bin/sh
# PreToolUse wall. Deny a tool call whose PATH FIELDS name one of the supplied paths.
# Path fields only, never the whole tool input: a write whose body mentions a
# path is not access to it, and blocking that teaches agents to work around
# the wall instead of reporting it.
# Every field a tool can reach a path through must be listed here, glob
# included, and path+glob is also joined: a grep splits a location across the two.
input=$(cat)
payload=$(printf '%s' "$input" | jq -r '
  ([.tool_input.file_path?, .tool_input.path?, .tool_input.notebook_path?,
    .tool_input.pattern?, .tool_input.command?, .tool_input.glob?,
    .tool_input.paths?, .tool_input.files?]
   | flatten | map(select(type == "string")))
  + (if (.tool_input.path? | type) == "string" and (.tool_input.glob? | type) == "string"
     then [.tool_input.path + "/" + .tool_input.glob] else [] end)
  | join("\n")' 2>/dev/null) || exit 2

for denied in "$@"; do
  if printf '%s' "$payload" | grep -Fq -- "$denied"; then
    jq -n --arg reason "Blocked: this agent cannot access $denied" \
      '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$reason}}'
    exit 0
  fi
done
exit 0
