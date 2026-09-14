#!/bin/sh
# Tell Codex when the active repository requires the one-stage dev loop.
input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] || exit 0
agents="$cwd/AGENTS.md"
[ -f "$agents" ] || exit 0
if grep -q '^vault_notes:' "$agents"; then
  printf '%s\n' 'This repo runs the dev-loop skill. Invoke it before any other action. Run one stage, then stop.'
fi
exit 0
