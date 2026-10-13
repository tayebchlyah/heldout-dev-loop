import { createHash } from 'node:crypto'
import { existsSync, lstatSync, readFileSync, readlinkSync } from 'node:fs'
import { join } from 'node:path'

const MANIFEST_PATH = join('.dev-loop', 'MANIFEST.json')
const LINK_PREFIX = 'link:'

export function sha256(content) {
  return `sha256:${createHash('sha256').update(content, 'utf8').digest('hex')}`
}

// D3: {"version", "files": {"<repo-relative path>": "sha256:<hex>" | "link:<target>"}},
// paths sorted, merged files never listed.
export function buildManifest(version, kit) {
  const entries = kit.files.map((file) => [file.path, sha256(file.content)])
  if (kit.link) entries.push([kit.link.path, `${LINK_PREFIX}${kit.link.target}`])
  entries.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
  return { version, files: Object.fromEntries(entries) }
}

export function readManifest(repo) {
  const path = join(repo, MANIFEST_PATH)
  return existsSync(path) ? JSON.parse(readFileSync(path, 'utf8')) : null
}

export function entryState(path, expected) {
  let stat
  try {
    stat = lstatSync(path)
  } catch {
    return 'missing'
  }
  if (expected.startsWith(LINK_PREFIX)) {
    const ok = stat.isSymbolicLink() && readlinkSync(path) === expected.slice(LINK_PREFIX.length)
    return ok ? 'same' : 'changed'
  }
  if (!stat.isFile()) return 'changed'
  return sha256(readFileSync(path)) === expected ? 'same' : 'changed'
}

export function compareManifest(repo, manifest) {
  const changed = []
  const missing = []
  for (const [path, expected] of Object.entries(manifest.files)) {
    const state = entryState(join(repo, path), expected)
    if (state === 'changed') changed.push(path)
    if (state === 'missing') missing.push(path)
  }
  return { changed, missing }
}
