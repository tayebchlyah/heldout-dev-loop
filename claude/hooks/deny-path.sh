#!/bin/bash
# Block any tool call whose PATH FIELDS name one of the given fragments.
# Usage in a subagent frontmatter hook: deny-path.sh <fragment> [<fragment> ...]
# Path fields only, never the whole tool input: a write whose body mentions a
# path is not access to it, and blocking that teaches agents to work around
# the wall instead of reporting it.
IN=$(cat)
INPUT=$(echo "$IN" | jq -r '
  [.tool_input.file_path?, .tool_input.path?, .tool_input.notebook_path?,
   .tool_input.pattern?, .tool_input.command?]
  | map(select(type == "string")) | join("\n")')
for FRAG in "$@"; do
  if echo "$INPUT" | grep -q -- "$FRAG"; then
    echo "Blocked: $FRAG is outside this agent's territory." >&2
    exit 2
  fi
done
exit 0
