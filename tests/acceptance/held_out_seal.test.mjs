// Held-out oracle tests for the held-out seal (spec: Held-Out Seal), installer flows.
// Flow 11: upgrade removes the dead Codex profiles and updates the kit hook entries (oracle 24, 25, 29).
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

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

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const DENY_ACCEPTANCE = 'deny-acceptance-to-subagents.sh'

// The 2.1.1 PreToolUse entry for the acceptance wall: matcher without NotebookEdit, no onFailure.
const OLD_DENY_GROUP = {
  matcher: 'Read|Edit|Write|Grep|Glob|Bash',
  hooks: [{
    type: 'command',
    command: '"${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}/.dev-loop/hooks/claude/deny-acceptance-to-subagents.sh"',
    timeout: 5,
  }],
}
const USER_GROUP = { matcher: 'Bash', hooks: [{ type: 'command', command: '"$HOME/bin/audit-my-shell.sh"', timeout: 7 }] }

// The exact dev-implementer tables an earlier dev-loop (2.1.1) wrote into .codex/config.toml.
const OLD_IMPLEMENTER_TABLE = `[permissions.dev-implementer]
description = "Implementation access without held-out acceptance tests."
extends = "dev-workspace"

[permissions.dev-implementer.filesystem.":workspace_roots"]
"tests/acceptance" = "deny"
"tests/acceptance/**" = "deny"
`
// A dev-spec-tester table the user changed: it must stay.
const USER_SPEC_TESTER_TABLE = `[permissions.dev-spec-tester]
description = "My own spec-tester profile."
extends = "dev-workspace"

[permissions.dev-spec-tester.filesystem.":workspace_roots"]
"src" = "deny"
"src/**" = "deny"
"docs/private" = "deny"
`
const DEAD_PROFILE = /^\[permissions\."?(dev-implementer|dev-spec-tester|dev-read-only)"?[.\]]/

function run(command, repo, home, args) {
  return execFileSync('node', [join(packageRoot, 'bin', 'install.mjs'), command, ...args], {
    cwd: repo,
    encoding: 'utf8',
    stdio: 'pipe',
    env: { ...process.env, HOME: home },
  })
}

function assertValidToml(text) {
  execFileSync('python3', ['-c', 'import tomllib, sys; tomllib.loads(sys.stdin.read())'], { input: text })
}

// The TOML text without the tables whose header matches `header`.
function dropTables(text, header) {
  const kept = []
  let dropping = false
  for (const line of text.split('\n')) {
    if (line.startsWith('[')) dropping = header.test(line)
    if (!dropping) kept.push(line)
  }
  return kept.join('\n')
}

const readJson = (path) => JSON.parse(readFileSync(path, 'utf8'))

function entriesFor(settings, script) {
  const found = []
  for (const group of settings.hooks.PreToolUse) {
    for (const hook of group.hooks) if (hook.command.includes(script)) found.push({ group, hook })
  }
  return found
}

test('F11: upgrade removes the dead Codex profiles and updates the kit hook entries (O24, O25, O29)', () => {
  const home = mkdtempSync(join(tmpdir(), 'held-out-seal-home-'))
  const repo = realpathSync(mkdtempSync(join(tmpdir(), 'held-out-seal-repo-')))
  execFileSync('git', ['init', '-q', repo])
  run('init', repo, home, ['--yes', '--harness=both'])

  // A project as 2.1.1 left it: the old acceptance wall entry, one user entry, the old profiles.
  const settingsPath = join(repo, '.claude', 'settings.json')
  const settings = readJson(settingsPath)
  settings.hooks.PreToolUse = settings.hooks.PreToolUse.filter(
    (group) => !group.hooks.some((hook) => hook.command.includes(DENY_ACCEPTANCE)),
  )
  settings.hooks.PreToolUse.push(structuredClone(OLD_DENY_GROUP), structuredClone(USER_GROUP))
  writeFileSync(settingsPath, `${JSON.stringify(settings, null, 2)}\n`)
  const configPath = join(repo, '.codex', 'config.toml')
  const config = dropTables(readFileSync(configPath, 'utf8'), DEAD_PROFILE).trimEnd()
  writeFileSync(configPath, `${config}\n\n${OLD_IMPLEMENTER_TABLE}\n${USER_SPEC_TESTER_TABLE}`)

  const output = run('upgrade', repo, home, [])

  // O24: the exact earlier table is gone and reported as removed; the changed table stays.
  const after = readFileSync(configPath, 'utf8')
  assertValidToml(after)
  assert.doesNotMatch(after, /^\[permissions\."?dev-implementer"?[.\]]/m)
  assert.ok(after.includes(USER_SPEC_TESTER_TABLE.trimEnd()), after)
  assert.match(output, /^.*(removed.*dev-implementer|dev-implementer.*removed).*$/im)

  // O29: one entry for the kit command, new matcher and onFailure; the user entry is unchanged.
  const updated = readJson(settingsPath)
  const entries = entriesFor(updated, DENY_ACCEPTANCE)
  assert.equal(entries.length, 1, JSON.stringify(entries))
  assert.deepEqual(new Set(entries[0].group.matcher.split('|')),
    new Set(['Read', 'Edit', 'Write', 'NotebookEdit', 'Grep', 'Glob', 'Bash']))
  assert.equal(entries[0].hook.onFailure, 'block')
  const user = entriesFor(updated, 'audit-my-shell.sh')
  assert.equal(user.length, 1)
  assert.deepEqual(user[0].group, USER_GROUP)

  // O25: the kit source has no dead profile left.
  for (const name of readdirSync(join(packageRoot, 'codex', 'agents')).filter((n) => n.endsWith('.toml'))) {
    assert.doesNotMatch(readFileSync(join(packageRoot, 'codex', 'agents', name), 'utf8'), /^\s*default_permissions\s*=/m, name)
  }
  const permissions = readFileSync(join(packageRoot, 'codex', 'config.permissions.toml'), 'utf8')
  for (const line of permissions.split('\n')) assert.doesNotMatch(line, DEAD_PROFILE)
})
