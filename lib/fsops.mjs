import { lstatSync } from 'node:fs'
import { basename, dirname, join } from 'node:path'

// A backup beside a skill would sit inside a skill discovery folder, where a
// harness loads it as a second skill of the same name. Those backups go to
// <root>/backups/skills/ instead, for example ~/.agents/backups/skills/.
// With `sibling`, the backup always sits beside its target.
export function backupPath(target, now = new Date(), { sibling = false } = {}) {
  const stamp = now.toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z')
  const parent = dirname(target)
  if (!sibling && basename(parent) === 'skills') {
    return join(dirname(parent), 'backups', 'skills', `${basename(target)}.bak-${stamp}`)
  }
  return `${target}.bak-${stamp}`
}

// True when something already occupies `path`, including a symbolic link
// that points at nothing (existsSync follows links and reports false for
// those).
function occupies(path) {
  try {
    lstatSync(path)
    return true
  } catch {
    return false
  }
}

// Picks the backup name for `target`: the plain timestamped name from
// backupPath(), or, if that name is already taken on disk (two placements
// landed in the same millisecond), the first -2, -3, ... suffix that is
// free. Disk state is the source of truth, not any in-memory counter.
export function freeBackupPath(target, { sibling = false } = {}) {
  const base = backupPath(target, new Date(), { sibling })
  if (!occupies(base)) return base
  let n = 2
  while (occupies(`${base}-${n}`)) n += 1
  return `${base}-${n}`
}
