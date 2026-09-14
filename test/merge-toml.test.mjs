import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { mergeCodexConfig, splitToml } from '../lib/merge-toml.mjs'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const realFragment = readFileSync(`${repoRoot}/codex/config.permissions.toml`, 'utf8')

function assertValidToml(text) {
  execFileSync('python3', ['-c', 'import tomllib, sys; tomllib.loads(sys.stdin.read())'], { input: text })
}

const fragment = [
  'default_permissions = "dev-workspace"',
  '',
  '[features]',
  'hooks = true',
  '',
  '[permissions.dev-workspace]',
  'extends = ":workspace"',
  '',
].join('\n')

test('splitToml cuts at the first table header', () => {
  const { root, tables } = splitToml('a = 1\n\n[t]\nb = 2\n')
  assert.deepEqual(root, ['a = 1', ''])
  assert.deepEqual(tables, ['[t]', 'b = 2', ''])
})

test('a root key lands before the first table of the target', () => {
  const current = 'model = "gpt"\n\n[plugins.x]\nenabled = true\n'
  const { text, changed } = mergeCodexConfig(current, fragment)
  assert.equal(changed, true)
  const lines = text.split('\n')
  const rootIndex = lines.indexOf('default_permissions = "dev-workspace"')
  const tableIndex = lines.indexOf('[plugins.x]')
  assert.ok(rootIndex > -1 && rootIndex < tableIndex, 'the root key must precede the first table')
  assert.ok(text.includes('[permissions.dev-workspace]'))
  assert.ok(text.includes('[features]'))
})

test('an existing profile blocks a second merge', () => {
  const { text } = mergeCodexConfig('model = "gpt"\n', fragment)
  const again = mergeCodexConfig(text, fragment)
  assert.equal(again.changed, false)
  assert.equal(again.text, text)
})

test('a missing file yields the fragment alone', () => {
  const { text, changed } = mergeCodexConfig(null, fragment)
  assert.equal(changed, true)
  assert.equal(text.trimEnd(), fragment.trimEnd())
})

test('a target with no table keeps its own root keys', () => {
  const { text } = mergeCodexConfig('model = "gpt"\n', fragment)
  assert.ok(text.includes('model = "gpt"'))
  assert.ok(text.indexOf('model = "gpt"') < text.indexOf('[features]'))
})

test('the marker inside a comment does not block a real merge', () => {
  const current = '# see [permissions.dev-workspace] docs\nmodel = "gpt"\n'
  const { text, changed } = mergeCodexConfig(current, fragment)
  assert.equal(changed, true)
  assert.ok(text.includes('[permissions.dev-workspace]\nextends'))
})

test('a target file without a trailing newline still merges to valid TOML', () => {
  const current = 'model = "gpt"'
  const { text } = mergeCodexConfig(current, fragment)
  assert.ok(text.includes('model = "gpt"'))
  assert.ok(text.includes('[permissions.dev-workspace]'))
  // no line glues two statements together (e.g. `model = "gpt"[features]`)
  assert.ok(!text.includes('"gpt"['))
})

test('an empty target file merges to just the fragment content', () => {
  const { text, changed } = mergeCodexConfig('', fragment)
  assert.equal(changed, true)
  assert.ok(text.includes('[permissions.dev-workspace]'))
  assert.ok(text.includes('[features]'))
})

test('a target that already declares a fragment table is not given a duplicate header, and the result parses as TOML', () => {
  const current = 'model = "gpt"\n\n[features]\nhooks = false\n'
  const { text, changed } = mergeCodexConfig(current, realFragment)
  assert.equal(changed, true)

  const headerCount = text.split('\n').filter((line) => line.trim() === '[features]').length
  assert.equal(headerCount, 1, 'the fragment must not add a second [features] header')

  assertValidToml(text)
})

test('the target keeps its own value for a table the fragment also declares', () => {
  const current = 'model = "gpt"\n\n[features]\nhooks = false\n'
  const { text } = mergeCodexConfig(current, realFragment)
  assert.ok(text.includes('hooks = false'), "the target's own value must survive untouched")
  assert.ok(!text.includes('hooks = true'), "the fragment's conflicting [features] table must not be appended")
})
