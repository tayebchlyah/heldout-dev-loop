import { execFileSync } from 'node:child_process'
import { chmodSync, existsSync, lstatSync, mkdirSync, readFileSync, readlinkSync, realpathSync, rmSync, statSync, symlinkSync, unlinkSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { HARNESSES } from './args.mjs'
import { buildKit, projectFragments } from './kit.mjs'
import { buildManifest } from './manifest.mjs'
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
    if (!dryRun) remove(target, stat)
    reports.push(entry(target, 'delete'))
  }
  return reports
}

function writeText(target, text, dryRun) {
  const current = existsSync(target) ? readFileSync(target, 'utf8') : null
  const action = current === null ? 'create' : current === text ? 'skip' : 'replace'
  if (action !== 'skip' && !dryRun) {
    mkdirSync(dirname(target), { recursive: true })
    writeFileSync(target, text)
  }
  return entry(target, action)
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
  const path = notes.startsWith('~') ? join(home, notes.slice(1)) : resolve(repo, notes)
  if (!existsSync(path) || !statSync(path).isDirectory()) throw new Error(`notes folder not found: ${notes}`)
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
  const kit = buildKit({ packageRoot, harnesses })
  const merges = projectFragments({ packageRoot, harnesses }).map((action) => ({ ...action, target: join(repo, action.target) }))

  // A merged file that cannot be parsed must fail before the first write.
  runPlan(merges, { dryRun: true, home })

  const reports = [
    ...writeKit({ repo, kit, oldManifest: null, dryRun: flags.dryRun }),
    ...runPlan(merges, { dryRun: flags.dryRun, home }),
    writeText(join(repo, 'dev-loop.json'), json({ version, harnesses }), flags.dryRun),
    writeText(join(repo, '.dev-loop', 'MANIFEST.json'), json(buildManifest(version, kit)), flags.dryRun),
  ]
  if (notes !== null) reports.push(writeText(join(repo, 'dev-loop.local.json'), json({ notes }), flags.dryRun))
  reports.push(addGitignoreLine(repo, flags.dryRun))
  return reports
}
