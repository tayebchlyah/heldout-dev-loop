import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { cpSync, lstatSync, mkdtempSync, readdirSync, readFileSync, readlinkSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { after } from 'node:test'
import { fileURLToPath } from 'node:url'

import { buildKit } from '../../../lib/kit.mjs'

// core.fsmonitor in a user or repo config starts a daemon in every throwaway repo these tests make.
// The override wins over every config file, appends to the caller's GIT_CONFIG_* entries, skips if already set.
const gitConfigCount = Number(process.env.GIT_CONFIG_COUNT || 0)
const fsmonitorOff = Array.from({ length: gitConfigCount }, (_, i) => i).some(
  (i) => process.env[`GIT_CONFIG_KEY_${i}`] === 'core.fsmonitor' && process.env[`GIT_CONFIG_VALUE_${i}`] === 'false',
)
if (!fsmonitorOff) {
  process.env[`GIT_CONFIG_KEY_${gitConfigCount}`] = 'core.fsmonitor'
  process.env[`GIT_CONFIG_VALUE_${gitConfigCount}`] = 'false'
  process.env.GIT_CONFIG_COUNT = String(gitConfigCount + 1)
}
// A Codex hook command exports KIT_ROOT and KIT_MAIN, and its Stop gate runs this suite (#227): no test hook sees them.
delete process.env.KIT_ROOT
delete process.env.KIT_MAIN

export const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..')
export const version = JSON.parse(readFileSync(join(packageRoot, 'package.json'), 'utf8')).version
const wallMatcher = (fragment, script) => JSON.parse(readFileSync(join(packageRoot, fragment), 'utf8')).hooks.PreToolUse
  .find((entry) => entry.hooks.some((hook) => hook.command.includes(script))).matcher
export const CLAUDE_WALL_MATCHER = wallMatcher('claude/settings.fragment.json', 'deny-acceptance-to-subagents.sh')
export const CODEX_WALL_MATCHER = wallMatcher('codex/hooks.fragment.json', 'acceptance-wall.sh')

const temps = []
after(() => temps.forEach((dir) => rmSync(dir, { recursive: true, force: true })))

export function tempDir(prefix) {
  temps.push(mkdtempSync(join(tmpdir(), prefix)))
  return temps.at(-1)
}

export function setup() {
  const home = tempDir('dev-loop-home-')
  const repo = realpathSync(tempDir('dev-loop-repo-'))
  execFileSync('git', ['init', '-q', repo])
  return { home, repo }
}

export function init(repo, home, args, { cwd = repo, input } = {}) {
  return execFileSync('node', [join(packageRoot, 'bin', 'install.mjs'), 'init', ...args], {
    cwd,
    input,
    encoding: 'utf8',
    stdio: 'pipe',
    env: { ...process.env, HOME: home },
  })
}

export function initFails(repo, home, args, pattern, options) {
  assert.throws(() => init(repo, home, args, options), (error) => {
    assert.equal(error.status, 1)
    assert.match(error.stdout + error.stderr, pattern)
    return true
  })
}

// Every entry of the repository except .git, so a test can prove a failed
// or dry run left the repository as it was.
export function entries(repo) {
  return readdirSync(repo).filter((name) => name !== '.git').sort()
}

export const readJson = (path) => JSON.parse(readFileSync(path, 'utf8'))

export const kitContent = (path) => buildKit({ packageRoot, harnesses: ['claude'] }).files.find((file) => file.path === path).content

// A copy of the package with another version, so that upgrade has something
// to move to. `edit` changes the copy before it is used.
export function fakePackage(nextVersion, edit = () => {}) {
  const root = tempDir('dev-loop-pkg-')
  for (const name of ['bin', 'lib', 'skill', 'skills', 'claude', 'codex', 'package.json']) {
    cpSync(join(packageRoot, name), join(root, name), { recursive: true })
  }
  const pkg = readJson(join(root, 'package.json'))
  writeFileSync(join(root, 'package.json'), `${JSON.stringify({ ...pkg, version: nextVersion }, null, 2)}\n`)
  edit(root)
  return root
}

export function run(pkg, command, repo, home, args) {
  return execFileSync('node', [join(pkg, 'bin', 'install.mjs'), command, ...args], {
    cwd: repo,
    encoding: 'utf8',
    stdio: 'pipe',
    env: { ...process.env, HOME: home },
  })
}

export const upgrade = (repo, home, args = [], pkg = packageRoot) => run(pkg, 'upgrade', repo, home, args)

// Every path under the repository (except .git) with its content or link
// target, so a test can prove a run changed nothing.
export function snapshot(repo, relative = '') {
  const result = {}
  for (const name of readdirSync(join(repo, relative)).sort()) {
    if (relative === '' && name === '.git') continue
    const path = relative ? `${relative}/${name}` : name
    const stat = lstatSync(join(repo, path))
    if (stat.isSymbolicLink()) result[path] = `link:${readlinkSync(join(repo, path))}`
    else if (stat.isDirectory()) Object.assign(result, { [path]: 'dir' }, snapshot(repo, path))
    else result[path] = `${stat.mode}:${readFileSync(join(repo, path), 'utf8')}`
  }
  return result
}

export function hookCommands(path) {
  return Object.values(readJson(path).hooks).flat().flatMap((group) => group.hooks.map((hook) => hook.command))
}

export const git = (repo, ...args) => execFileSync('git', ['-c', 'user.name=t', '-c', 'user.email=t@example.com', ...args], { cwd: repo, encoding: 'utf8' })

// Parses `text` with Python's tomllib and throws if it is not valid TOML.
// The installer's own merge cannot prove a file still parses; only a real
// TOML parser can, and every machine that runs this suite already has
// python3 (the skill's own test suite needs it).
export function assertValidToml(text) {
  execFileSync('python3', ['-c', 'import tomllib, sys; tomllib.loads(sys.stdin.read())'], { input: text })
}
