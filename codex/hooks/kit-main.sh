# Sourced by the Codex hooks that run kit checker code. kit_main <dir>: the main checkout whose kit code a hook runs,
# by the rule of the Codex hook commands in lib/kit.mjs (#212).
kit_main() {
  __km_d=$(cd "${1:-.}" 2>/dev/null && pwd -P) || return 0
  __km_m= __km_i=
  while :; do
    if [ -e "$__km_d/.git" ]; then
      __km_w=$(git -C "$__km_d" worktree list --porcelain 2>/dev/null | awk 'NR==1{w=substr($0,10)} /^bare$/{w=""} /^$/{exit} END{print w}')
      { [ -z "$__km_i" ] || [ -d "$__km_w/.dev-loop" ]; } && __km_m=$__km_w
      __km_i=1
    fi
    [ "$__km_d" = / ] && break
    __km_d=${__km_d%/*} __km_d=${__km_d:-/}
  done
  printf '%s\n' "$__km_m"
  unset __km_d __km_m __km_i __km_w
}
