# Sourced by every kit hook before its first git or CLI call (#155). Pins git config a repo an agent can write must not
# set for a hook: core.fsmonitor and log.showSignature off, and every filter driver's clean, process and required unset,
# because each of them starts a program. Appends to any GIT_CONFIG_COUNT pin already in the environment.
# git_pin_repo <dir> adds the filter drivers of that repo; sourcing it pins the repo of the current directory.
__gp_add() {
  export "GIT_CONFIG_KEY_$__gp_n=$1" "GIT_CONFIG_VALUE_$__gp_n=$2" "GIT_CONFIG_COUNT=$((__gp_n + 1))"
  __gp_n=$((__gp_n + 1))
}
git_pin_repo() {
  __gp_n=${GIT_CONFIG_COUNT:-0}
  __gp_add core.fsmonitor false
  __gp_add log.showSignature false
  __gp_names=$({ git -C "${1:-.}" config --name-only --get-regexp '^filter\.' | sed -n 's/^filter\.\(.*\)\.[^.]*$/\1/p' | sort -u; } 2>/dev/null)
  while IFS= read -r __gp_name; do
    [ -n "$__gp_name" ] || continue
    __gp_add "filter.$__gp_name.clean" ""
    __gp_add "filter.$__gp_name.process" ""
    __gp_add "filter.$__gp_name.required" false
  done <<__GP_EOF__
$__gp_names
__GP_EOF__
  unset __gp_n __gp_names __gp_name
}
git_pin_repo .
