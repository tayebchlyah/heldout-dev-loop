import {
  chmodSync, cpSync, existsSync, lstatSync, mkdirSync, readFileSync,
  readdirSync, readlinkSync, renameSync, symlinkSync, writeFileSync,
} from 'node:fs'
import { basename, dirname, join } from 'node:path'

export function renderTemplate(text, { home }) {
  return text.split('{{HOME}}').join(home)
}

// A backup beside a skill would sit inside a skill discovery folder, where a
// harness loads it as a second skill of the same name. Those backups go to
// <root>/backups/skills/ instead, for example ~/.agents/backups/skills/.
export function backupPath(target, now = new Date()) {
  const stamp = now.toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z')
  const parent = dirname(target)
  if (basename(parent) === 'skills') {
    return join(dirname(parent), 'backups', 'skills', `${basename(target)}.bak-${stamp}`)
  }
  return `${target}.bak-${stamp}`
}

// True when something already occupies `path`, including a symbolic link
// that points at nothing (existsSync follows links and reports false for
// those, but the link itself still needs to be backed up and replaced).
function occupies(path) {
  try {
    lstatSync(path)
    return true
  } catch {
    return false
  }
}

function isLinkTo(path, source) {
  try {
    return lstatSync(path).isSymbolicLink() && readlinkSync(path) === source
  } catch {
    return false
  }
}

// Picks the backup name for `target`: the plain timestamped name from
// backupPath(), or, if that name is already taken on disk (two placements
// landed in the same millisecond), the first -2, -3, ... suffix that is
// free. Disk state is the source of truth, not any in-memory counter.
export function freeBackupPath(target) {
  const base = backupPath(target)
  if (!occupies(base)) return base
  let n = 2
  while (occupies(`${base}-${n}`)) n += 1
  return `${base}-${n}`
}

// existsSync(target) is false for a symlink whose target is missing, so
// occupies() (lstat-based) is the honest "is anything there" check; reading
// through it for a content comparison would throw, so that comparison is
// guarded separately below.
function moveAside(target, dryRun) {
  const backup = freeBackupPath(target)
  if (!dryRun) {
    mkdirSync(dirname(backup), { recursive: true })
    renameSync(target, backup)
  }
  return backup
}

function placeLink({ source, target, dryRun }) {
  const exists = occupies(target)
  if (exists && isLinkTo(target, source)) return { target, action: 'skip', backup: null }
  if (dryRun) return { target, action: exists ? 'replace' : 'create', backup: null }
  const backup = exists ? moveAside(target, false) : null
  mkdirSync(dirname(target), { recursive: true })
  symlinkSync(source, target)
  return { target, action: backup ? 'replace' : 'create', backup }
}

// Placement reads the source as UTF-8 text and writes text back out, so this
// module can only place text files, not binary ones. That is fine for this
// project's payload (shell scripts, markdown, JSON), but it would corrupt a
// binary source.
export function placeFile({ source, target, mode = 'copy', dryRun = false, executable = false, home = null }) {
  if (mode === 'link') return placeLink({ source, target, dryRun })

  const exists = occupies(target)
  const raw = readFileSync(source, 'utf8')
  const next = home === null ? raw : renderTemplate(raw, { home })

  // A symbolic link left by an earlier --link install reads back as the
  // clone's own content, so a content comparison would call it equal and
  // keep the link; a copy install must end with a real file.
  const targetIsUsableFile = exists && existsSync(target) && !lstatSync(target).isSymbolicLink()
  if (targetIsUsableFile && readFileSync(target, 'utf8') === next) {
    return { target, action: 'skip', backup: null }
  }

  if (dryRun) return { target, action: exists ? 'replace' : 'create', backup: null }

  const backup = exists ? moveAside(target, false) : null
  mkdirSync(dirname(target), { recursive: true })
  writeFileSync(target, next)
  if (executable) chmodSync(target, 0o755)
  return { target, action: backup ? 'replace' : 'create', backup }
}

// Recursively compares two directory trees by structure and file bytes, so
// a repeat install of an unchanged directory can be reported as a skip
// instead of backing up and re-copying on every run.
function treesEqual(a, b) {
  try {
    const aEntries = readdirSync(a, { withFileTypes: true }).sort((x, y) => x.name.localeCompare(y.name))
    const bEntries = readdirSync(b, { withFileTypes: true }).sort((x, y) => x.name.localeCompare(y.name))
    if (aEntries.length !== bEntries.length) return false
    return aEntries.every((entryA, index) => {
      const entryB = bEntries[index]
      if (entryA.name !== entryB.name || entryA.isDirectory() !== entryB.isDirectory()) return false
      const pathA = join(a, entryA.name)
      const pathB = join(b, entryB.name)
      if (entryA.isDirectory()) return treesEqual(pathA, pathB)
      return readFileSync(pathA).equals(readFileSync(pathB))
    })
  } catch {
    // Anything unreadable (a dangling symlink, a permission error, a race)
    // means "cannot prove equal": the safe fallback is 'not equal', which
    // moves the tree aside and re-copies it, the same self-healing behavior
    // placeDir had before this comparison existed.
    return false
  }
}

export function placeDir({ source, target, mode = 'copy', dryRun = false }) {
  if (mode === 'link') return placeLink({ source, target, dryRun })

  // A symbolic link left by an earlier --link install reads through to the
  // clone, so its tree compares equal. Treat it as not equal, or a copy
  // install would keep depending on a clone the user may later move away.
  const exists = occupies(target)
  const usable = exists && existsSync(target) && !lstatSync(target).isSymbolicLink()
  if (usable && treesEqual(source, target)) {
    return { target, action: 'skip', backup: null }
  }
  if (dryRun) return { target, action: exists ? 'replace' : 'create', backup: null }

  const backup = exists ? moveAside(target, false) : null
  mkdirSync(dirname(target), { recursive: true })
  cpSync(source, target, { recursive: true })
  return { target, action: backup ? 'replace' : 'create', backup }
}
