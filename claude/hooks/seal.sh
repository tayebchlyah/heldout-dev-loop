#!/bin/bash
# Held-out seal. Usage: seal.sh stop|turn, hook JSON on stdin. gate-snapshot.sh writes the SubagentStart .head file.
# stop (SubagentStop): run `devloop.py audit`, then `seal-check`; block on a seal violation only.
# turn (Stop): run `seal-check`; block on a seal violation or an open taint record.
# stop_hook_active true: no block. Input that is not JSON, no dev-loop.json, or no CLI: exit 0, silent.
# The CLI is the one of the worktree that holds CLAUDE_PROJECT_DIR (the hook cwd when unset). Never starts the board.
mode=$1
input=$(cat)
printf '%s' "$input" | jq -e 'type == "object"' >/dev/null 2>&1 || exit 0
dir=${CLAUDE_PROJECT_DIR:-$(printf '%s' "$input" | jq -r '.cwd // empty')}
[ -n "$dir" ] || exit 0
top=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null) || exit 0
cli="$top/.agents/skills/dev-loop/scripts/devloop.py"
[ -f "$top/dev-loop.json" ] && [ -f "$cli" ] || exit 0
cd "$top" || exit 0
case "$mode" in
  stop) printf '%s' "$input" | python3 "$cli" audit >/dev/null 2>&1 ;;
  turn) ;;
  *) exit 0 ;;
esac
[ "$(printf '%s' "$input" | jq -r '.stop_hook_active // false')" = true ] && exit 0
check=$(python3 "$cli" seal-check 2>/dev/null)
printf '%s' "$check" | jq -e 'type == "object"' >/dev/null 2>&1 || exit 0
# seal-check names no commit for a path: name each branch commit that touched it.
base=$(git merge-base HEAD origin/main 2>/dev/null || git merge-base HEAD main 2>/dev/null)
sep=$(printf '\037')
found=
while IFS=$sep read -r why commit path; do
  [ -n "$why" ] || continue
  [ -n "$commit" ] || [ -z "$base" ] || [ -z "$path" ] \
    || commit=$(git log --format=%h "$base..HEAD" -- "$path" 2>/dev/null | tr '\n' ' ' | sed 's/ $//')
  found="$found
- $why ${commit:--} ${path:--}"
done <<EOF
$(printf '%s' "$check" | jq -r '.violations[]? | [.reason, ((.commit // "")[0:7]), (.path // "")] | join("\u001f")')
EOF
if [ "$mode" = turn ]; then
  found="$found$(printf '%s' "$check" | jq -r '.taint[]? | "\n- open taint: \(.agent_type) \([.commits[]?.sha[0:7]] | join(" "))"')"
fi
[ -n "$found" ] || exit 0
remedy="Remedy: only spec-tester commits under tests/acceptance/, with devloop.py seal; git revert an unsealed or changed commit. Tainted implementer commits: git revert them and give the task to a fresh implementer. Tainted spec-tester commits: a fresh spec-tester runs git revert --no-commit <sha> and devloop.py seal --reverts <sha>, then writes the tests again and runs devloop.py seal."
jq -n --arg reason "Held-out seal check failed.$found
$remedy" '{decision: "block", reason: $reason}'
exit 0
