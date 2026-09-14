#!/bin/bash
# Block any tool call whose input names one of the given path fragments.
# Usage in a subagent frontmatter hook: deny-path.sh <fragment> [<fragment> ...]
IN=$(cat)
INPUT=$(echo "$IN" | jq -r '.tool_input | tostring')
for FRAG in "$@"; do
  if echo "$INPUT" | grep -q -- "$FRAG"; then
    echo "Blocked: $FRAG is outside this agent's territory." >&2
    exit 2
  fi
done
exit 0
