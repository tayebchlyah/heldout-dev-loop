#!/bin/sh
# Tell Codex when the active repository runs the dev loop.
# Codex skips an untrusted project hook without a message: name the untrusted events. This line shows
# only when this hook itself is trusted; check_setup.py stays the full check.
# The checker comes from KIT_ROOT (the trusted kit copy), else from KIT_MAIN, else from the first worktree of
# the hook cwd (a direct run). With KIT_ROOT and KIT_MAIN set, the D6 files of KIT_MAIN that differ from the copy
# are named, at most 5: then every line goes out as one hookSpecificOutput.additionalContext JSON object.
. "${0%/*}/git-pin.sh"
input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] || exit 0
[ -f "$cwd/dev-loop.json" ] || exit 0
out='This repo runs the dev-loop skill. Invoke it before any other action.'
main=$KIT_MAIN
[ -n "$main" ] || main=$(git -C "$cwd" worktree list --porcelain 2>/dev/null | awk 'NR==1{w=substr($0,10)} /^bare$/{w=""} /^$/{exit} END{print w}')
checker="${KIT_ROOT:-$main}/.agents/skills/dev-loop/scripts/check_setup.py"
if [ -f "$checker" ]; then
  untrusted=$(python3 -I "$checker" --harness codex --repo "$cwd" --hooks-trust 2>/dev/null)
  [ -z "$untrusted" ] || out="$out
Warning: Codex skips these untrusted dev-loop hooks: $untrusted. Tell the user to run /hooks in Codex and trust them."
fi
drift=
if [ -n "$KIT_ROOT" ] && [ -n "$KIT_MAIN" ]; then
  drift=$(python3 -I -c 'import json, os, sys
copy, main = sys.argv[1], sys.argv[2]
manifest = ".dev-loop/MANIFEST.json"
def read(base, rel):
    try:
        with open(os.path.join(base, rel), "rb") as f:
            return f.read()
    except OSError:
        return None
paths = {manifest}
try:
    paths |= {p for p, v in json.loads(read(copy, manifest)).get("files").items() if isinstance(v, str)
              and v.startswith("sha256:") and p.startswith((".dev-loop/hooks/codex/", ".agents/skills/dev-loop/scripts/"))}
except (TypeError, ValueError, AttributeError):
    pass
differ = sorted(p for p in paths if read(copy, p) is None or read(copy, p) != read(main, p))
print(", ".join(differ[:5]))' "$KIT_ROOT" "$KIT_MAIN" 2>/dev/null)
fi
if [ -n "$drift" ]; then
  jq -cn --arg c "$out
dev-loop: the hooks run the trusted kit copy; these files differ from it: $drift" \
    '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $c}}'
else
  printf '%s\n' "$out"
fi
exit 0
