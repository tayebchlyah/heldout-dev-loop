#!/bin/bash
# PreToolUse(Bash) wall for the global git rules.
IN=$(cat)
CMD=$(printf '%s' "$IN" | jq -r '.tool_input.command // empty')
CWD=$(printf '%s' "$IN" | jq -r '.cwd // empty')
[ -z "$CMD" ] && exit 0

VAULT=$(python3 -c 'import json,os,pathlib
p = pathlib.Path(os.path.expanduser("~/.agents/dev-loop.config.json"))
print(json.loads(p.read_text()).get("vault", "") if p.is_file() else "")' 2>/dev/null)
# -C takes a path that may be quoted and may hold spaces.
G='git( +-C +("[^"]+"|'"'"'[^'"'"']+'"'"'|[^ ]+))? +'
WRITE="${G}(commit|push|add|checkout|switch|reset|rebase|merge|stash|tag|cherry-pick|am|apply)( |$)|${G}branch +-[dD]( |$)"

deny() { echo "$1" >&2; exit 2; }
has()  { printf '%s' "$CMD" | grep -Eq "$1"; }

# 1. rm -rf on root only.
has 'rm +-[rf]+ +/( |$)|rm +-[rf]+ +/\*' && deny "Blocked: rm -rf / is never allowed."

# 2. No git write of any kind inside the Obsidian vault.
# The repo a command acts on is not always the session cwd: a leading "cd <path> &&"
# or a "git -C <path>" retargets it. Resolve those, so work in another repo is not
# blocked just because the session started in the vault. Anything unresolved falls
# back to the cwd, so the vault stays protected by default.
# An unquoted path may hold spaces (the vault path does), so cut the argument at
# the next shell operator rather than at the next space, then strip quotes.
arg() { printf '%s' "$1" | sed -E 's/[[:space:]]*(&&|\|\||;|\|).*$//; s/[[:space:]]+$//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/'; }
TARGET="$CWD"
lead_cd=$(printf '%s' "$CMD" | sed -nE 's/^[[:space:]]*cd[[:space:]]+(.*)$/\1/p')
[ -n "$lead_cd" ] && TARGET=$(arg "$lead_cd")
# "git -C <path>" is followed by the subcommand, so this argument ends at a space
# unless it is quoted.
git_c=$(printf '%s' "$CMD" | sed -nE 's/.*git[[:space:]]+-C[[:space:]]+("[^"]+"|'"'"'[^'"'"']+'"'"'|[^[:space:]]+).*/\1/p')
[ -n "$git_c" ] && TARGET=$(arg "$git_c")
case "$TARGET" in /*) ;; *) TARGET="$CWD/$TARGET" ;; esac

if [ -n "$VAULT" ]; then
  case "$TARGET" in
    "$VAULT"|"$VAULT"/*)
      has "$WRITE" && deny "Never run git inside the vault. The Obsidian Git plugin owns commits."
      ;;
  esac
fi

# 3. Hard reset destroys work.
has "${G}reset +--hard" && deny "Blocked: git reset --hard discards work. Use git stash or --soft."

# 4. Push always needs a fresh explicit yes.
if has "${G}push( |$)"; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"Global rule: git push needs explicit permission each time."}}'
fi
exit 0
