// test/fragments.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, resolve } from 'node:path'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const readJson = (relative) => JSON.parse(readFileSync(join(repoRoot, relative), 'utf8'))

test('the Claude fragment covers the four hook events', () => {
  const fragment = readJson('claude/settings.fragment.json')
  for (const event of ['SessionStart', 'PreToolUse', 'Stop', 'SubagentStop']) {
    assert.ok(Array.isArray(fragment.hooks[event]), `missing event ${event}`)
    assert.ok(fragment.hooks[event].length > 0, `empty event ${event}`)
  }
})

test('the Claude fragment carries the three deny rules', () => {
  const fragment = readJson('claude/settings.fragment.json')
  assert.deepEqual(fragment.permissions.deny, [
    'Skill(superpowers:verification-before-completion)',
    'Read(./.env)',
    'Read(./.env.*)',
  ])
})

test('the Claude fragment references no hook that dev-loop does not own', () => {
  const fragment = readJson('claude/settings.fragment.json')
  const commands = Object.values(fragment.hooks)
    .flat()
    .flatMap((group) => group.hooks.map((hook) => hook.command))
  for (const command of commands) {
    assert.ok(!command.includes('style-routing.sh'), 'style-routing.sh is not a dev-loop hook')
    assert.ok(!command.includes('format.sh'), 'format.sh is not a dev-loop hook')
  }
})

test('the Codex fragment covers the four hook events and omits style.sh', () => {
  const fragment = readJson('codex/hooks.fragment.json')
  for (const event of ['SessionStart', 'PreToolUse', 'Stop', 'SubagentStop']) {
    assert.ok(Array.isArray(fragment.hooks[event]), `missing event ${event}`)
  }
  assert.ok(!JSON.stringify(fragment).includes('style.sh'))
})

test('the Codex TOML fragment puts its root keys before the first table', () => {
  const text = readFileSync(join(repoRoot, 'codex/config.permissions.toml'), 'utf8')
  const lines = text.split('\n')
  const firstTable = lines.findIndex((line) => line.trimStart().startsWith('['))
  assert.ok(firstTable > 0, 'the fragment needs at least one root key and one table')
  const root = lines.slice(0, firstTable).join('\n')
  assert.ok(root.includes('default_permissions = "dev-workspace"'))
  assert.ok(text.includes('[permissions.dev-workspace]'))
  assert.ok(text.includes('[permissions.dev-implementer]'))
  assert.ok(text.includes('[permissions.dev-spec-tester]'))
  assert.ok(text.includes('[features]'))
})
