#!/bin/bash
# Stop and SubagentStop gate. Runs the repo's own scripts/gate.sh if one exists.
IN=$(cat)
[ "$(echo "$IN" | jq -r '.stop_hook_active // false')" = "true" ] && exit 0
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
[ -x scripts/gate.sh ] || exit 0
[ -z "$(git status --porcelain 2>/dev/null)" ] && exit 0
OUT=$(mktemp)
if ! scripts/gate.sh >"$OUT" 2>&1; then
  jq -n --arg r "$(tail -c 3000 "$OUT")" \
    '{decision:"block", reason:("Gate failed. Fix it. Do not weaken or skip tests.\n\n" + $r)}'
fi
exit 0
