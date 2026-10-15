#!/bin/sh
# SessionStart and UserPromptSubmit: refresh the progress board in the background.
# Usage: board.sh session-start|prompt. Hook input JSON on stdin. Always silent, exit 0.
input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] || exit 0
top=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || exit 0
cli="$top/.agents/skills/dev-loop/scripts/devloop.py"
[ -f "$cli" ] && (cd "$cwd" && printf '%s' "$input" | python3 "$cli" board --event "$1" >/dev/null 2>&1 &)
exit 0
