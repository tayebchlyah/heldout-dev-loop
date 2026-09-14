import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { mergeCodexConfig, splitToml } from '../lib/merge-toml.mjs'
import { assertValidToml } from './helpers.mjs'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const realFragment = readFileSync(`${repoRoot}/codex/config.permissions.toml`, 'utf8')

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

test('a second merge of the same fragment adds nothing', () => {
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

test('a header with a trailing comment still matches the fragment header, and the merge parses as TOML', () => {
  const current = 'model = "gpt"\n\n[features]  # user comment\nhooks = false\n'
  const { text } = mergeCodexConfig(current, realFragment)

  const headerCount = text.split('\n').filter((line) => line.includes('[features]')).length
  assert.equal(headerCount, 1, 'the trailing comment must not hide the duplicate table')

  assertValidToml(text)
})

test('a header with internal whitespace still matches the fragment header, and the merge parses as TOML', () => {
  const current = 'model = "gpt"\n\n[ features ]\nhooks = false\n'
  const { text } = mergeCodexConfig(current, realFragment)

  const headerCount = text.split('\n').filter((line) => line.includes('features')).length
  assert.equal(headerCount, 1, 'the spaced header must not hide the duplicate table')

  assertValidToml(text)
})

test('mergeCodexConfig reports the table headers it skipped because the target already declares them', () => {
  const current = 'model = "gpt"\n\n[features]\nhooks = false\n'
  const { skipped } = mergeCodexConfig(current, realFragment)
  assert.deepEqual(skipped, ['[features]'])
})

test('a clean merge with no overlapping tables reports no skipped headers', () => {
  const { skipped } = mergeCodexConfig('model = "gpt"\n', fragment)
  assert.deepEqual(skipped, [])
})

test('a no-op merge adds nothing and names every table it kept', () => {
  const { text } = mergeCodexConfig('model = "gpt"\n', fragment)
  const again = mergeCodexConfig(text, fragment)
  assert.equal(again.changed, false)
  assert.equal(again.text, text)
  assert.deepEqual(again.skipped, ['default_permissions', '[features]', '[permissions.dev-workspace]'])
})

test('a target whose own root key shares a name with the fragment keeps its value once, and the merge parses as TOML', () => {
  const current = 'model = "gpt-5"\ndefault_permissions = "my-own-profile"\n\n[features]\nhooks = false\n'
  const { text, skipped } = mergeCodexConfig(current, realFragment)

  const keyCount = text.split('\n').filter((line) => line.trim().startsWith('default_permissions')).length
  assert.equal(keyCount, 1, 'the fragment must not add a second default_permissions')
  assert.ok(text.includes('default_permissions = "my-own-profile"'))
  assert.ok(skipped.includes('default_permissions'))
  assert.ok(skipped.includes('[features]'))

  assertValidToml(text)
})

test('a target that defines a table by a root dotted key does not also receive that table', () => {
  const current = 'model = "gpt-5"\nfeatures.hooks = false\n'
  const { text } = mergeCodexConfig(current, realFragment)
  assert.ok(!text.split('\n').some((line) => line.trim() === '[features]'), 'the dotted key already declares [features]')
  assertValidToml(text)
})

test('a fragment root dotted key is dropped when the target declares that table', () => {
  const dotted = 'features.hooks = true\n\n[permissions.dev-workspace]\nextends = ":workspace"\n'
  const current = 'model = "gpt-5"\n\n[features]\nhooks = false\n'
  const { text, skipped } = mergeCodexConfig(current, dotted)
  assert.ok(!text.includes('features.hooks = true'))
  assert.ok(skipped.includes('features.hooks'))
  assertValidToml(text)
})

test('a later run still adds what a partial target is missing', () => {
  const current = '[permissions.dev-workspace]\ndescription = "mine"\nextends = ":workspace"\n'
  const { text, changed, skipped } = mergeCodexConfig(current, realFragment)
  assert.equal(changed, true)
  assert.ok(skipped.includes('[permissions.dev-workspace]'))
  assert.ok(text.includes('[permissions.dev-spec-tester]'))
  assert.ok(text.includes('description = "mine"'))
  assertValidToml(text)
})

test('a settled target reports the tables it keeps on every run, and changes nothing', () => {
  const { text } = mergeCodexConfig('model = "gpt-5"\n', realFragment)
  const again = mergeCodexConfig(text, realFragment)
  assert.equal(again.changed, false)
  assert.equal(again.text, text)
  assert.ok(again.skipped.includes('[features]'))
})
