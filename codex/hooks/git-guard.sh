#!/bin/sh
# PreToolUse(Bash) safety wall for destructive commands and vault git writes.
input=$(cat)
command=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$command" ] && exit 0

vault=$(python3 -c 'import json,os,pathlib
p = pathlib.Path(os.path.expanduser("~/.agents/dev-loop.config.json"))
print(json.loads(p.read_text()).get("vault", "") if p.is_file() else "")' 2>/dev/null)
git_pattern='git( +-C +[^ ]+)? +'
git_write="${git_pattern}(commit|push|add|checkout|switch|reset|rebase|merge|stash|tag|cherry-pick|am|apply)( |$)|${git_pattern}branch +-[dD]( |$)"

deny() {
  jq -n --arg reason "$1" \
    '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$reason}}'
  exit 0
}
matches() { printf '%s' "$command" | grep -Eq "$1"; }

matches 'rm +-[rf]+ +/( |$)|rm +-[rf]+ +/\*' && deny 'Blocked: rm -rf / is never allowed.'
matches "${git_pattern}reset +--hard" && deny 'Blocked: git reset --hard discards work.'

if [ -n "$vault" ]; then
  case "$cwd" in
    "$vault"|"$vault"/*)
      matches "$git_write" && deny 'Blocked: never run git writes inside the Obsidian vault.'
      ;;
  esac
fi
exit 0
