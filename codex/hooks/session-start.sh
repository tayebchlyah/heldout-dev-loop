#!/bin/sh
# Tell Codex when the active repository requires the one-stage dev loop.
input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] || exit 0
if [ -f "$cwd/dev-loop.json" ] || grep -qs '^vault_notes:' "$cwd/AGENTS.md"; then
  printf '%s\n' 'This repo runs the dev-loop skill. Invoke it before any other action. Run one stage, then stop.'
fi
exit 0
