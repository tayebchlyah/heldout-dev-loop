#!/bin/bash
# Held-out seal. Usage: seal.sh stop|turn, hook JSON on stdin. gate-snapshot.sh writes the SubagentStart .head file.
# stop (SubagentStop): run `devloop.py audit`, then `seal-check`; block on a seal violation only.
# turn (Stop): run `seal-check`; block on a seal violation or an open taint record.
# stop_hook_active true: no block. Input that is not JSON, no dev-loop.json, or no CLI: exit 0, silent.
# The CLI is the one of the worktree that holds the hook cwd (CLAUDE_PROJECT_DIR when the input has none).
# Never starts the board.
. "${0%/*}/git-pin.sh"
mode=$1
input=$(cat)
printf '%s' "$input" | jq -e 'type == "object"' >/dev/null 2>&1 || exit 0
dir=$(printf '%s' "$input" | jq -r '.cwd // empty')
[ -n "$dir" ] || dir=$CLAUDE_PROJECT_DIR
[ -n "$dir" ] || exit 0
top=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null) || exit 0
cli="$top/.agents/skills/dev-loop/scripts/devloop.py"
[ -f "$top/dev-loop.json" ] && [ -f "$cli" ] || exit 0
cd "$top" || exit 0
git_pin_repo .
case "$mode" in
  stop) printf '%s' "$input" | python3 "$cli" audit >/dev/null 2>&1 ;;
  turn) ;;
  *) exit 0 ;;
esac
[ "$(printf '%s' "$input" | jq -r '.stop_hook_active // false')" = true ] && exit 0
check=$(python3 "$cli" seal-check 2>/dev/null)
printf '%s' "$check" | jq -e 'type == "object"' >/dev/null 2>&1 || exit 0
# A path violation names the commits seal-check lists for it (valid seal commits left out, D6).
found=$(printf '%s' "$check" | jq -j '.violations[]? | "\n- \(.reason) \((.commits // [.commit // empty])
  | join(" ") | if . == "" then "-" else . end) \(.path // "-")"')
if [ "$mode" = turn ]; then
  found="$found$(printf '%s' "$check" | jq -r '.taint[]? | "\n- open taint: \(.agent_type) \([.commits[]?.sha] | join(" "))"')"
fi
[ -n "$found" ] || exit 0
remedy="Remedy: only spec-tester commits under tests/acceptance/, with devloop.py seal; git revert an unsealed or changed commit. Tainted implementer commits: git revert them and give the task to a fresh implementer. Tainted spec-tester commits: a fresh spec-tester runs git revert --no-commit <sha> and devloop.py seal --reverts <sha>, then writes the tests again and runs devloop.py seal."
if printf '%s' "$check" | jq -e 'any(.violations[]?; .reason == "kit-changed")' >/dev/null 2>&1; then
  remedy="$remedy Kit files (kit-changed) change only by upgrade: restore them with git checkout <base> -- <path> (or git revert the commit), then change dev-loop and run upgrade."
fi
if printf '%s' "$check" | jq -e 'any(.violations[]?; .reason == "changed-before-seal")' >/dev/null 2>&1; then
  remedy="$remedy A non-seal commit before a seal commit changed a sealed file (changed-before-seal): drop that commit with git rebase --onto <sha from commits>^ <sha from commits>, or remove only its change to the path; a conflict under tests/acceptance/ goes to spec-tester (devloop.py seal --rebase-continue). A git revert does not clear changed-before-seal: the seal commit's parent still holds the change."
fi
if printf '%s' "$check" | jq -e 'any(.violations[]?; .reason == "seal-dropped")' >/dev/null 2>&1; then
  remedy="$remedy A recorded seal commit is gone from the branch (seal-dropped): put it back with git cherry-pick <commit> (the recorded sha); on a conflict, git cherry-pick --abort, then stop and report to the user. Only the user edits seals.json."
fi
if printf '%s' "$check" | jq -e 'any(.violations[]?; .reason == "seal-record-unreadable")' >/dev/null 2>&1; then
  remedy="$remedy The seal record cannot be read (seal-record-unreadable): stop and report to the user; the user repairs or removes the file."
fi
jq -n --arg reason "Held-out seal check failed.$found
$remedy" '{decision: "block", reason: $reason}'
exit 0
