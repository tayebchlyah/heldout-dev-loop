# Temp leak rule of scripts/gate.sh and scripts/acceptance.sh, sourced. temp_leak <dir>: each entry of <dir>
# the tests left, one per line, nothing when none. Allowed: node-compile-cache and a Python tempfile probe
# (8 characters [a-z0-9_], a regular file, empty or "blat").
# allow-comment: dependency, Python's first gettempdir() writes "blat" to an 8-character file and unlinks it; a killed child leaves it (#106).
is_probe() {
  [[ $2 =~ ^[a-z0-9_]{8}$ ]] || return 1
  [ -f "$1/$2" ] && [ ! -L "$1/$2" ] || return 1
  [ ! -s "$1/$2" ] || cmp -s "$1/$2" <(printf blat)
}
temp_leak() {
  ls -A "$1" | grep -vx node-compile-cache | while IFS= read -r name; do
    is_probe "$1" "$name" || printf '%s\n' "$name"
  done
}
