import { execFileSync } from 'node:child_process'
import { chmodSync, existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, readlinkSync, realpathSync, renameSync, rmdirSync, rmSync, statSync, symlinkSync, unlinkSync, writeFileSync } from 'node:fs'
import { dirname, isAbsolute, join, resolve, sep } from 'node:path'
import { HARNESSES } from './args.mjs'
import { buildKit, droppedHookCommands, KIT_SKILLS, projectFragments } from './kit.mjs'
import { freeBackupPath } from './fsops.mjs'
import { buildManifest, compareManifest, entryState, readManifest } from './manifest.mjs'
import { runPlan } from './plan.mjs'

const GITIGNORE_LINE = 'dev-loop.local.json'

export function readVersion(packageRoot) {
  return JSON.parse(readFileSync(join(packageRoot, 'package.json'), 'utf8')).version
}

const json = (value) => `${JSON.stringify(value, null, 2)}\n`

const entry = (target, action) => ({ target, action, backup: null, added: [], skipped: [], notes: [] })

function remove(path, stat) {
  if (stat.isDirectory()) rmSync(path, { recursive: true })
  else unlinkSync(path)
}

// Remove the folders a deletion left empty, up to the repository root,
// never leaving it.
function pruneEmptyParents(repo, target) {
  for (let dir = dirname(target); dir.startsWith(repo + sep) && readdirSync(dir).length === 0; dir = dirname(dir)) rmdirSync(dir)
}

function lstatOrNull(path) {
  try {
    return lstatSync(path)
  } catch {
    return null
  }
}

// True when `target` holds something other than `content`.
function holdsOther(target, content) {
  const stat = lstatOrNull(target)
  return stat !== null && (!stat.isFile() || readFileSync(target, 'utf8') !== content)
}

// The backup sits beside its target, in a folder assertSafeTargets checked,
// never in a backups/skills/ folder that a committed link could lead out of
// the repository.
function moveAside(target, dryRun) {
  const backup = freeBackupPath(target, { sibling: true })
  if (!dryRun) renameSync(target, backup)
  return backup
}

// A kit file gets no backup, because git holds the old copy, except a path
// in `backupOther`: a file of the user, moved aside first.
function writeKitFile(repo, file, dryRun, backupOther) {
  const target = join(repo, file.path)
  const stat = lstatOrNull(target)
  let action = 'create'
  if (stat !== null) {
    const same = readFileSync(target, 'utf8') === file.content && (!file.executable || (stat.mode & 0o111) !== 0)
    action = same ? 'skip' : 'replace'
  }
  const report = entry(target, action)
  if (action !== 'skip' && backupOther.includes(file.path)) report.backup = moveAside(target, dryRun)
  if (action !== 'skip' && !dryRun) {
    mkdirSync(dirname(target), { recursive: true })
    writeFileSync(target, file.content)
    if (file.executable) chmodSync(target, 0o755)
  }
  return report
}

function writeKitLink(repo, link, dryRun) {
  const target = join(repo, link.path)
  const stat = lstatOrNull(target)
  let action = 'create'
  if (stat !== null) action = stat.isSymbolicLink() && readlinkSync(target) === link.target ? 'skip' : 'replace'
  if (action !== 'skip' && !dryRun) {
    mkdirSync(dirname(target), { recursive: true })
    if (stat !== null) remove(target, stat)
    symlinkSync(link.target, target)
  }
  return entry(target, action)
}

export function writeKit({ repo, kit, oldManifest, dryRun, backupOther = [] }) {
  const reports = kit.files.map((file) => writeKitFile(repo, file, dryRun, backupOther))
  for (const link of kit.links) reports.push(writeKitLink(repo, link, dryRun))

  const kept = new Set(kitPaths(kit))
  for (const path of Object.keys(oldManifest?.files ?? {})) {
    const target = join(repo, path)
    const stat = lstatOrNull(target)
    if (kept.has(path) || stat === null) continue
    // A deletion never recurses and never follows a link.
    if (stat.isDirectory()) throw new Error(`${path} is a folder: dev-loop deletes only files`)
    // A path that no longer matches its old manifest entry is moved aside,
    // never deleted.
    const report = entry(target, 'delete')
    if (entryState(target, oldManifest.files[path]) !== 'same') report.backup = moveAside(target, dryRun)
    else if (!dryRun) unlinkSync(target)
    if (!dryRun) pruneEmptyParents(repo, target)
    reports.push(report)
  }
  return reports
}

function writeText(target, text, dryRun, { backup = false } = {}) {
  const current = existsSync(target) ? readFileSync(target, 'utf8') : null
  const action = current === null ? 'create' : current === text ? 'skip' : 'replace'
  const report = entry(target, action)
  if (action !== 'skip' && !dryRun) {
    mkdirSync(dirname(target), { recursive: true })
    if (backup && current !== null) {
      report.backup = freeBackupPath(target)
      writeFileSync(report.backup, current)
    }
    writeFileSync(target, text)
  }
  return report
}

// A write must stay inside the repository: refuse any existing symlink on
// the way to a written path, the path itself included. The only symlinks
// allowed are the kit links, and their paths must hold nothing else, so that a
// user folder there is never deleted.
export function assertSafeTargets(repo, paths, links = []) {
  const linkPaths = new Set(links.map((link) => link.path))
  for (const path of paths) {
    const parts = path.split('/')
    for (let index = 1; index <= parts.length; index++) {
      const prefix = parts.slice(0, index).join('/')
      const stat = lstatOrNull(join(repo, prefix))
      if (stat === null) break
      if (linkPaths.has(prefix)) {
        if (!stat.isSymbolicLink()) throw new Error(`${prefix} exists and is not a symlink: move it away first`)
      } else if (stat.isSymbolicLink()) {
        throw new Error(`${prefix} is a symlink: dev-loop does not write through it`)
      }
    }
  }
}

function addGitignoreLine(repo, dryRun) {
  const target = join(repo, '.gitignore')
  const current = existsSync(target) ? readFileSync(target, 'utf8') : ''
  if (current.split(/\r?\n/).some((line) => line.trim() === GITIGNORE_LINE)) return entry(target, 'skip')
  const separator = current === '' || current.endsWith('\n') ? '' : '\n'
  return writeText(target, `${current}${separator}${GITIGNORE_LINE}\n`, dryRun)
}

// init runs only at a git top level.
function assertTopLevel(cwd) {
  let top
  try {
    top = execFileSync('git', ['rev-parse', '--show-toplevel'], { cwd, encoding: 'utf8', stdio: 'pipe' }).trim()
  } catch {
    throw new Error('init must run in a git repository')
  }
  if (realpathSync(top) !== realpathSync(cwd)) throw new Error(`init must run at the repository top level: ${top}`)
}

// A `~` path expands from HOME, a relative path is relative to the
// repository. The value is stored as given.
function assertNotesFolder(notes, { repo, home }) {
  const path = notes === '~' || notes.startsWith('~/') ? join(home, notes.slice(1)) : resolve(repo, notes)
  if (!existsSync(path) || !statSync(path).isDirectory()) throw new Error(`notes folder not found: ${notes}`)
}

// The only paths an old MANIFEST.json may name: it is read from the
// repository, so it is untrusted, and its dropped paths are deleted.
const OWNED_SKILLS = ['dev-loop', ...KIT_SKILLS]
const OWNED_DIRS = [...OWNED_SKILLS.map((name) => `.agents/skills/${name}/`), '.claude/agents/', '.codex/agents/', '.dev-loop/hooks/']
const OWNED_FILES = ['.codex/rules/dev-loop.rules']
const OWNED_LINKS = OWNED_SKILLS.map((name) => `.claude/skills/${name}`)

function isOwnedPath(repo, path) {
  if (path === '' || isAbsolute(path) || path.includes('\\')) return false
  if (path.split('/').some((part) => part === '' || part === '.' || part === '..')) return false
  if (!resolve(repo, path).startsWith(repo + sep)) return false
  return OWNED_FILES.includes(path) || OWNED_LINKS.includes(path) || OWNED_DIRS.some((dir) => path.startsWith(dir))
}

function assertOwnedPaths(repo, manifest) {
  const foreign = Object.keys(manifest.files ?? {}).filter((path) => !isOwnedPath(repo, path))
  if (foreign.length > 0) {
    throw new Error(foreign.map((path) => `.dev-loop/MANIFEST.json names a path dev-loop does not own: ${path}`).join('\n'))
  }
}

// The kit file paths `oldManifest` does not list that hold a file of the
// user. Refused unless `force`: then they are backed up.
function userFilesAtKitPaths(repo, kit, oldManifest, force) {
  const owned = oldManifest?.files ?? {}
  const occupied = kit.files.filter((file) => !Object.hasOwn(owned, file.path) && holdsOther(join(repo, file.path), file.content)).map((file) => file.path)
  if (occupied.length > 0 && !force) {
    throw new Error(`files of yours at dev-loop paths:\n${occupied.map((path) => `  ${path}`).join('\n')}\nmove them away first, or rerun with --force to back them up`)
  }
  return occupied
}

const kitPaths = (kit) => [...kit.files.map((file) => file.path), ...kit.links.map((link) => link.path)]

// Builds the kit and the project merges. A merged file that cannot be
// parsed, or a path that leads out of the repository, fails here, before
// the first write. `written` lists the other paths the command writes. The
// paths of `oldManifest` that the new kit drops are deleted, so they are
// checked too, the old link included. With `dropHooks`, a merged hook file
// also loses the entries that run a kit hook script the new kit dropped.
function prepareKit({ repo, packageRoot, harnesses, written, oldManifest = null, dropHooks = false }) {
  const kit = buildKit({ packageRoot, harnesses })
  const fragments = projectFragments({ packageRoot, harnesses })
  const kept = new Set(kitPaths(kit))
  const merges = fragments.map((action) => ({
    ...action,
    target: join(repo, action.target),
    ...(dropHooks && action.kind === 'merge-json' ? { dropCommands: (text) => droppedHookCommands(text, kept) } : {}),
  }))
  const dropped = Object.keys(oldManifest?.files ?? {}).filter((path) => !kept.has(path))
  const paths = [...kept, ...fragments.map((action) => action.target), 'dev-loop.json', '.dev-loop/MANIFEST.json', ...written]
  // A dropped path is unlinked, never followed: only the folders on the way
  // to it must not be links, and it must not be a folder.
  assertSafeTargets(repo, [...paths, ...dropped.map((path) => dirname(path))], kit.links)
  for (const path of dropped) {
    if (lstatOrNull(join(repo, path))?.isDirectory()) throw new Error(`${path} is a folder: dev-loop deletes only files`)
  }
  runPlan(merges, { dryRun: true })
  return { kit, merges }
}

export async function runInit({ flags, home, cwd, packageRoot, ask }) {
  const repo = cwd
  assertTopLevel(repo)
  if (existsSync(join(repo, 'dev-loop.json'))) throw new Error('dev-loop.json already exists: run upgrade instead')

  const harness = flags.harness ?? (flags.yes ? 'both' : await ask('Harnesses (claude, codex, both)', 'both'))
  if (!HARNESSES.includes(harness)) throw new Error('harness must be claude, codex or both')
  const notes = (flags.notes ?? (flags.yes ? '' : await ask('Notes folder (empty to skip)', ''))) || null
  if (notes !== null) assertNotesFolder(notes, { repo, home })

  const harnesses = harness === 'both' ? ['claude', 'codex'] : [harness]
  const version = readVersion(packageRoot)
  parseVersion(version)
  const written = ['.gitignore', ...(notes === null ? [] : ['dev-loop.local.json'])]
  const { kit, merges } = prepareKit({ repo, packageRoot, harnesses, written })
  const occupied = userFilesAtKitPaths(repo, kit, null, flags.force)

  const reports = [
    ...writeKit({ repo, kit, oldManifest: null, dryRun: flags.dryRun, backupOther: occupied }),
    ...runPlan(merges, { dryRun: flags.dryRun }),
    writeText(join(repo, 'dev-loop.json'), json({ version, harnesses }), flags.dryRun),
    writeText(join(repo, '.dev-loop', 'MANIFEST.json'), json(buildManifest(version, kit)), flags.dryRun),
  ]
  if (notes !== null) reports.push(writeText(join(repo, 'dev-loop.local.json'), json({ notes }), flags.dryRun, { backup: true }))
  reports.push(addGitignoreLine(repo, flags.dryRun))
  return reports
}

function assertHarnesses(harnesses) {
  const ok = Array.isArray(harnesses) && harnesses.length > 0 && harnesses.every((name) => name === 'claude' || name === 'codex')
  if (!ok) throw new Error('dev-loop.json harnesses must be a non-empty list of claude and codex')
}

const VERSION = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-rc\.([1-9]\d*))?$/

function parseVersion(value) {
  const match = VERSION.exec(value)
  if (!match) throw new Error(`not a x.y.z or x.y.z-rc.n version: ${value}`)
  return match.slice(1, 4).map(Number).concat(match[4] === undefined ? Infinity : Number(match[4]))
}

function compareVersions(a, b) {
  const [x, y] = [a, b].map(parseVersion)
  for (let index = 0; index < 4; index++) {
    if (x[index] !== y[index]) return x[index] < y[index] ? -1 : 1
  }
  return 0
}

/**
 * A `## [<version>]` or `## [v<version>]` heading, the version x.y.z or
 * x.y.z-rc.n, starts a section, and any other `## ` heading ends it. Keep
 * the sections after `fromVersion`, up to and including `toVersion`.
 */
export function releaseNotes(changelogText, fromVersion, toVersion) {
  const kept = []
  let keep = false
  for (const line of changelogText.split(/\r?\n/)) {
    if (line.startsWith('## ')) {
      const version = /^## \[v?(\d+\.\d+\.\d+(?:-rc\.\d+)?)\]/.exec(line)?.[1]
      keep = version !== undefined && VERSION.test(version) && compareVersions(version, fromVersion) > 0 && compareVersions(version, toVersion) <= 0
    }
    if (keep) kept.push(line)
  }
  return kept.join('\n').trim()
}

// Order: read dev-loop.json, compare the versions, compare the kit with
// the old manifest, check the new kit paths, then write. --force skips the
// local-edit refusal and backs up a file of the user at a new kit path.
export async function runUpgrade({ flags, cwd, packageRoot }) {
  const repo = cwd
  const devLoopPath = join(repo, 'dev-loop.json')
  if (!existsSync(devLoopPath)) throw new Error('dev-loop.json not found: run init first')
  const devLoop = JSON.parse(readFileSync(devLoopPath, 'utf8'))
  assertHarnesses(devLoop.harnesses)

  const version = readVersion(packageRoot)
  if (compareVersions(version, devLoop.version) < 0) {
    throw new Error(`this package is ${version}, older than the installed ${devLoop.version}: to roll back, git revert the upgrade commit`)
  }

  const oldManifest = readManifest(repo)
  if (oldManifest !== null) assertOwnedPaths(repo, oldManifest)
  if (!flags.force) {
    const edited = oldManifest === null ? ['.dev-loop/MANIFEST.json'] : Object.values(compareManifest(repo, oldManifest)).flat().sort()
    if (edited.length > 0) {
      throw new Error(`local changes to dev-loop files:\n${edited.map((path) => `  ${path}`).join('\n')}\nmove the change to dev-loop first, or rerun with --force`)
    }
  }

  const { kit, merges } = prepareKit({ repo, packageRoot, harnesses: devLoop.harnesses, written: [], oldManifest, dropHooks: true })
  const occupied = userFilesAtKitPaths(repo, kit, oldManifest, flags.force)

  const reports = [
    ...writeKit({ repo, kit, oldManifest, dryRun: flags.dryRun, backupOther: occupied }),
    ...runPlan(merges, { dryRun: flags.dryRun }),
    writeText(devLoopPath, json({ ...devLoop, version }), flags.dryRun),
    writeText(join(repo, '.dev-loop', 'MANIFEST.json'), json(buildManifest(version, kit)), flags.dryRun),
  ]

  const changelog = join(packageRoot, 'CHANGELOG.md')
  const notes = existsSync(changelog) ? releaseNotes(readFileSync(changelog, 'utf8'), devLoop.version, version) : ''
  if (notes !== '') console.log(notes)
  return reports
}
