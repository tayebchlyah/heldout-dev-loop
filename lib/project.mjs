import { execFileSync } from 'node:child_process'
import { chmodSync, existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, readlinkSync, realpathSync, rmdirSync, rmSync, statSync, symlinkSync, unlinkSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { HARNESSES } from './args.mjs'
import { buildKit, projectFragments } from './kit.mjs'
import { freeBackupPath } from './fsops.mjs'
import { buildManifest, compareManifest, readManifest } from './manifest.mjs'
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

// Remove the folders a deletion left empty, up to the repository root.
function pruneEmptyParents(repo, target) {
  for (let dir = dirname(target); dir !== repo && readdirSync(dir).length === 0; dir = dirname(dir)) rmdirSync(dir)
}

function lstatOrNull(path) {
  try {
    return lstatSync(path)
  } catch {
    return null
  }
}

// D4: a kit file gets no backup, because git holds the old copy.
function writeKitFile(repo, file, dryRun) {
  const target = join(repo, file.path)
  const stat = lstatOrNull(target)
  let action = 'create'
  if (stat !== null) {
    const same = readFileSync(target, 'utf8') === file.content && (!file.executable || (stat.mode & 0o111) !== 0)
    action = same ? 'skip' : 'replace'
  }
  if (action !== 'skip' && !dryRun) {
    mkdirSync(dirname(target), { recursive: true })
    writeFileSync(target, file.content)
    if (file.executable) chmodSync(target, 0o755)
  }
  return entry(target, action)
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

export function writeKit({ repo, kit, oldManifest, dryRun }) {
  const reports = kit.files.map((file) => writeKitFile(repo, file, dryRun))
  if (kit.link) reports.push(writeKitLink(repo, kit.link, dryRun))

  const kept = new Set([...kit.files.map((file) => file.path), ...(kit.link ? [kit.link.path] : [])])
  for (const path of Object.keys(oldManifest?.files ?? {})) {
    const target = join(repo, path)
    const stat = lstatOrNull(target)
    if (kept.has(path) || stat === null) continue
    if (!dryRun) {
      remove(target, stat)
      pruneEmptyParents(repo, target)
    }
    reports.push(entry(target, 'delete'))
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
// the way to a written path, the path itself included. The only symlink
// allowed is the kit link, and its path must hold nothing else, so that a
// user folder there is never deleted.
export function assertSafeTargets(repo, paths, link) {
  for (const path of paths) {
    const parts = path.split('/')
    for (let index = 1; index <= parts.length; index++) {
      const prefix = parts.slice(0, index).join('/')
      const stat = lstatOrNull(join(repo, prefix))
      if (stat === null) break
      if (prefix === link?.path) {
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

// D15: init runs only at a git top level.
function assertTopLevel(cwd) {
  let top
  try {
    top = execFileSync('git', ['rev-parse', '--show-toplevel'], { cwd, encoding: 'utf8', stdio: 'pipe' }).trim()
  } catch {
    throw new Error('init must run in a git repository')
  }
  if (realpathSync(top) !== realpathSync(cwd)) throw new Error(`init must run at the repository top level: ${top}`)
}

// D7: a `~` path expands from HOME, a relative path is relative to the
// repository. The value is stored as given.
function assertNotesFolder(notes, { repo, home }) {
  const path = notes === '~' || notes.startsWith('~/') ? join(home, notes.slice(1)) : resolve(repo, notes)
  if (!existsSync(path) || !statSync(path).isDirectory()) throw new Error(`notes folder not found: ${notes}`)
}

const kitPaths = (kit) => [...kit.files.map((file) => file.path), ...(kit.link ? [kit.link.path] : [])]

// Builds the kit and the project merges. A merged file that cannot be
// parsed, or a path that leads out of the repository, fails here, before
// the first write. `written` lists the other paths the command writes. The
// paths of `oldManifest` that the new kit drops are deleted, so they are
// checked too, the old link included.
function prepareKit({ repo, home, packageRoot, harnesses, written, oldManifest = null }) {
  const kit = buildKit({ packageRoot, harnesses })
  const fragments = projectFragments({ packageRoot, harnesses })
  const merges = fragments.map((action) => ({ ...action, target: join(repo, action.target) }))
  const kept = new Set(kitPaths(kit))
  const oldEntries = Object.entries(oldManifest?.files ?? {})
  const dropped = oldEntries.map(([path]) => path).filter((path) => !kept.has(path))
  const oldLink = oldEntries.find(([, value]) => value.startsWith('link:'))
  const link = kit.link ?? (oldLink ? { path: oldLink[0] } : null)
  const paths = [...kept, ...fragments.map((action) => action.target), 'dev-loop.json', '.dev-loop/MANIFEST.json', ...written, ...dropped]
  assertSafeTargets(repo, paths, link)
  runPlan(merges, { dryRun: true, home })
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
  const written = ['.gitignore', ...(notes === null ? [] : ['dev-loop.local.json'])]
  const { kit, merges } = prepareKit({ repo, home, packageRoot, harnesses, written })

  const reports = [
    ...writeKit({ repo, kit, oldManifest: null, dryRun: flags.dryRun }),
    ...runPlan(merges, { dryRun: flags.dryRun, home }),
    writeText(join(repo, 'dev-loop.json'), json({ version, harnesses }), flags.dryRun),
    writeText(join(repo, '.dev-loop', 'MANIFEST.json'), json(buildManifest(version, kit)), flags.dryRun),
  ]
  if (notes !== null) reports.push(writeText(join(repo, 'dev-loop.local.json'), json({ notes }), flags.dryRun, { backup: true }))
  reports.push(addGitignoreLine(repo, flags.dryRun))
  return reports
}

const VERSION = /^(\d+)\.(\d+)\.(\d+)$/

function compareVersions(a, b) {
  const [x, y] = [a, b].map((value) => {
    const match = VERSION.exec(value)
    if (!match) throw new Error(`not a x.y.z version: ${value}`)
    return match.slice(1).map(Number)
  })
  for (let index = 0; index < 3; index++) {
    if (x[index] !== y[index]) return x[index] - y[index]
  }
  return 0
}

// D16: a `## [<version>]` or `## [v<version>]` heading starts a section, and
// any other `## ` heading ends it. Keep the sections after `fromVersion`, up
// to and including `toVersion`.
export function releaseNotes(changelogText, fromVersion, toVersion) {
  const kept = []
  let keep = false
  for (const line of changelogText.split(/\r?\n/)) {
    if (line.startsWith('## ')) {
      const version = /^## \[v?(\d+\.\d+\.\d+)\]/.exec(line)?.[1]
      keep = version !== undefined && compareVersions(version, fromVersion) > 0 && compareVersions(version, toVersion) <= 0
    }
    if (keep) kept.push(line)
  }
  return kept.join('\n').trim()
}

// D4, D15, D16. Order: read dev-loop.json, compare the versions, compare the
// kit with the old manifest, then write. --force skips only the local-edit
// refusal.
export async function runUpgrade({ flags, home, cwd, packageRoot }) {
  const repo = cwd
  const devLoopPath = join(repo, 'dev-loop.json')
  if (!existsSync(devLoopPath)) throw new Error('dev-loop.json not found: run init first')
  const devLoop = JSON.parse(readFileSync(devLoopPath, 'utf8'))

  const version = readVersion(packageRoot)
  if (compareVersions(version, devLoop.version) < 0) {
    throw new Error(`this package is ${version}, older than the installed ${devLoop.version}: to roll back, git revert the upgrade commit`)
  }

  const oldManifest = readManifest(repo)
  if (!flags.force) {
    const edited = oldManifest === null ? ['.dev-loop/MANIFEST.json'] : Object.values(compareManifest(repo, oldManifest)).flat().sort()
    if (edited.length > 0) {
      throw new Error(`local changes to dev-loop files:\n${edited.map((path) => `  ${path}`).join('\n')}\nmove the change to dev-loop first, or rerun with --force`)
    }
  }

  const { kit, merges } = prepareKit({ repo, home, packageRoot, harnesses: devLoop.harnesses, written: [], oldManifest })

  const reports = [
    ...writeKit({ repo, kit, oldManifest, dryRun: flags.dryRun }),
    ...runPlan(merges, { dryRun: flags.dryRun, home }),
    writeText(devLoopPath, json({ ...devLoop, version }), flags.dryRun),
    writeText(join(repo, '.dev-loop', 'MANIFEST.json'), json(buildManifest(version, kit)), flags.dryRun),
  ]

  const changelog = join(packageRoot, 'CHANGELOG.md')
  const notes = existsSync(changelog) ? releaseNotes(readFileSync(changelog, 'utf8'), devLoop.version, version) : ''
  if (notes !== '') console.log(notes)
  return reports
}
