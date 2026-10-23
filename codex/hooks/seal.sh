#!/bin/sh
# Held-out seal. Usage: seal.sh start|stop|turn, hook JSON on stdin.
# start (SubagentStart): write <git-dir>/dev-loop-subagents/<agent_id>.head, the HEAD the audit starts from.
# stop (SubagentStop): run `devloop.py audit`, then `seal-check`; block on a seal violation only.
# turn (Stop): run `seal-check`; block on a seal violation or an open taint record.
# stop_hook_active true: no block. Input that is not JSON, no dev-loop.json, or no CLI: exit 0, silent.
# The CLI is the one of the worktree that holds the hook cwd. Never starts the board.
mode=$1
input=$(cat)
printf '%s' "$input" | jq -e 'type == "object"' >/dev/null 2>&1 || exit 0
dir=$(printf '%s' "$input" | jq -r '.cwd // empty')
[ -n "$dir" ] || exit 0
top=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null) || exit 0
cli="$top/.agents/skills/dev-loop/scripts/devloop.py"
[ -f "$top/dev-loop.json" ] && [ -f "$cli" ] || exit 0
cd "$top" || exit 0
case "$mode" in
  start)
    id=$(printf '%s' "$input" | jq -r '.agent_id // empty')
    [ -n "$id" ] || exit 0
    head=$(git rev-parse HEAD 2>/dev/null) || exit 0
    subagents="$(git rev-parse --absolute-git-dir)/dev-loop-subagents"
    mkdir -p "$subagents" && printf '%s\n' "$head" >"$subagents/$id.head"
    exit 0 ;;
  stop) printf '%s' "$input" | python3 "$cli" audit >/dev/null 2>&1 ;;
  turn) ;;
  *) exit 0 ;;
esac
[ "$(printf '%s' "$input" | jq -r '.stop_hook_active // false')" = true ] && exit 0
check=$(python3 "$cli" seal-check 2>/dev/null)
printf '%s' "$check" | jq -e 'type == "object"' >/dev/null 2>&1 || exit 0
# seal-check names no commit for a path: name each branch commit that touched it, seal commits left out
# (a bad seal has its own line).
base=$(git merge-base HEAD origin/main 2>/dev/null || git merge-base HEAD main 2>/dev/null)
sep=$(printf '\037')
found=
while IFS=$sep read -r why commit path; do
  [ -n "$why" ] || continue
  [ -n "$commit" ] || [ -z "$base" ] || [ -z "$path" ] \
    || commit=$(git log --format='%H %(trailers:key=Dev-Loop-Seal,valueonly,separator=%x2C)' "$base..HEAD" -- "$path" \
      2>/dev/null | awk 'NF == 1 { printf "%s%s", sep, $1; sep = " " }')
  found="$found
- $why ${commit:--} ${path:--}"
done <<EOF
$(printf '%s' "$check" | jq -r '.violations[]? | [.reason, (.commit // ""), (.path // "")] | join("\u001f")')
EOF
if [ "$mode" = turn ]; then
  found="$found$(printf '%s' "$check" | jq -r '.taint[]? | "\n- open taint: \(.agent_type) \([.commits[]?.sha] | join(" "))"')"
fi
[ -n "$found" ] || exit 0
remedy="Remedy: only spec-tester commits under tests/acceptance/, with devloop.py seal; git revert an unsealed or changed commit. Tainted implementer commits: git revert them and give the task to a fresh implementer. Tainted spec-tester commits: a fresh spec-tester runs git revert --no-commit <sha> and devloop.py seal --reverts <sha>, then writes the tests again and runs devloop.py seal."
jq -n --arg reason "Held-out seal check failed.$found
$remedy" '{decision: "block", reason: $reason}'
exit 0
