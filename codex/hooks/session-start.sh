#!/bin/sh
# Tell Codex when the active repository runs the dev loop.
# Codex skips an untrusted project hook without a message: name the untrusted events. This line shows
# only when this hook itself is trusted; check_setup.py stays the full check.
GIT_CONFIG_N=${GIT_CONFIG_COUNT:-0}
export "GIT_CONFIG_KEY_$GIT_CONFIG_N=core.fsmonitor" "GIT_CONFIG_VALUE_$GIT_CONFIG_N=false" GIT_CONFIG_COUNT=$((GIT_CONFIG_N + 1))
input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] || exit 0
if [ -f "$cwd/dev-loop.json" ]; then
  printf '%s\n' 'This repo runs the dev-loop skill. Invoke it before any other action.'
  checker="$cwd/.agents/skills/dev-loop/scripts/check_setup.py"
  [ -f "$checker" ] || exit 0
  untrusted=$(python3 "$checker" --harness codex --repo "$cwd" --hooks-trust 2>/dev/null)
  [ -z "$untrusted" ] || printf 'Warning: Codex skips these untrusted dev-loop hooks: %s. Tell the user to run /hooks in Codex and trust them.\n' "$untrusted"
fi
exit 0
