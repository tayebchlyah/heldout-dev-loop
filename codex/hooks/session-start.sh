#!/bin/sh
# Tell Codex when the active repository runs the dev loop.
input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] || exit 0
if [ -f "$cwd/dev-loop.json" ]; then
  printf '%s\n' 'This repo runs the dev-loop skill. Invoke it before any other action.'
fi
exit 0
