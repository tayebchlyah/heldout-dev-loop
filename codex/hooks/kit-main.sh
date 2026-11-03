# Sourced by the Codex hooks that run kit checker code. kit_main <dir>: the main checkout whose kit code a hook runs,
# by the rule of the Codex hook commands in lib/kit.mjs (#212, #222, #224): the innermost repo, unless an outer folder
# is the first worktree of its own repo, that worktree has .dev-loop, and a hook trust entry of the Codex config names
# a path that resolves to it.
kit_main() {
  __km_d=$(cd "${1:-.}" 2>/dev/null && pwd -P) || return 0
  __km_m= __km_i=
  while :; do
    if [ -e "$__km_d/.git" ]; then
      __km_w=$(git -C "$__km_d" worktree list --porcelain 2>/dev/null | awk 'NR==1{w=substr($0,10)} /^bare$/{w=""} /^$/{exit} END{print w}')
      if [ -z "$__km_i" ]; then __km_m=$__km_w
      elif [ -d "$__km_w/.dev-loop" ] && [ "$(cd "$__km_w" 2>/dev/null && pwd -P)" = "$__km_d" ] &&
        sed -n -E 's|^[[]hooks[.]state[.]"(/.*)/[.]codex/hooks[.]json:[^"]*"[]][[:space:]]*$|\1|p' "${CODEX_HOME:-$HOME/.codex}/config.toml" 2>/dev/null |
          { while IFS= read -r __km_p; do [ "$(cd "$__km_p" 2>/dev/null && pwd -P)" = "$__km_w" ] && exit 0; done; exit 1; }
      then __km_m=$__km_w
      fi
      __km_i=1
    fi
    [ "$__km_d" = / ] && break
    __km_d=${__km_d%/*} __km_d=${__km_d:-/}
  done
  printf '%s\n' "$__km_m"
  unset __km_d __km_m __km_i __km_w __km_p
}
