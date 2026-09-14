import test from 'node:test'
import assert from 'node:assert/strict'
import { mergeCodexConfig, splitToml } from '../lib/merge-toml.mjs'

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
