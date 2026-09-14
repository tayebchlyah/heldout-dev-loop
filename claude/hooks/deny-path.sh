#!/bin/bash
# Block any tool call whose PATH FIELDS name one of the given fragments.
# Usage in a subagent frontmatter hook: deny-path.sh <fragment> [<fragment> ...]
# Path fields only, never the whole tool input: a write whose body mentions a
# path is not access to it, and blocking that teaches agents to work around
# the wall instead of reporting it.
# Every field a tool can reach a path through must be listed here, glob
# included, and path+glob is also joined: Grep splits a location across the two.
IN=$(cat)
INPUT=$(echo "$IN" | jq -r '
  [.tool_input.file_path?, .tool_input.path?, .tool_input.notebook_path?,
   .tool_input.pattern?, .tool_input.command?, .tool_input.glob?,
   .tool_input.paths?, .tool_input.files?]
  | flatten | map(select(type == "string")) | join("\n")')
JOINED=$(echo "$IN" | jq -r '
  if (.tool_input.path? | type) == "string" and (.tool_input.glob? | type) == "string"
  then .tool_input.path + "/" + .tool_input.glob else empty end')
CANDIDATES=$(printf '%s\n%s' "$INPUT" "$JOINED")
for FRAG in "$@"; do
  if echo "$CANDIDATES" | grep -q -- "$FRAG"; then
    echo "Blocked: $FRAG is outside this agent's territory." >&2
    exit 2
  fi
done
exit 0
