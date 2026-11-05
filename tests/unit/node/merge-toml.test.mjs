import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { KEY_MERGED_TABLES, mergeCodexConfig, splitToml } from '../../../lib/merge-toml.mjs'
import { assertValidToml, packageRoot } from './helpers.mjs'

const realFragment = readFileSync(`${packageRoot}/codex/config.permissions.toml`, 'utf8')

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

test('a merge removes the legacy root sandbox_mode while preserving other user settings', () => {
  const current = 'model = "gpt-5"\nsandbox_mode = "workspace-write"\n\n[plugins.mine]\nenabled = true\n'
  const { text, changed } = mergeCodexConfig(current, realFragment)

  assert.equal(changed, true)
  assert.ok(!text.includes('sandbox_mode'))
  assert.ok(text.includes('model = "gpt-5"'))
  assert.ok(text.includes('[plugins.mine]\nenabled = true'))
  assert.ok(text.includes('default_permissions = "dev-workspace"'))
  assertValidToml(text)
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

test('mergeCodexConfig reports the items it skipped because the target already declares them', () => {
  const current = 'model = "gpt"\n\n[features]\nhooks = false\n'
  const { skipped } = mergeCodexConfig(current, realFragment)
  assert.deepEqual(skipped, ['[features] hooks'])
})

test('a clean merge with no overlapping tables reports no skipped headers', () => {
  const { skipped } = mergeCodexConfig('model = "gpt"\n', fragment)
  assert.deepEqual(skipped, [])
})

test('a no-op merge adds nothing and names no table equal to its own', () => {
  const { text } = mergeCodexConfig('model = "gpt"\n', fragment)
  const again = mergeCodexConfig(text, fragment)
  assert.equal(again.changed, false)
  assert.equal(again.text, text)
  assert.deepEqual(again.skipped, [])
})

test('a target whose own root key shares a name with the fragment keeps its value once, and the merge parses as TOML', () => {
  const current = 'model = "gpt-5"\ndefault_permissions = "my-own-profile"\n\n[features]\nhooks = false\n'
  const { text, skipped } = mergeCodexConfig(current, realFragment)

  const keyCount = text.split('\n').filter((line) => line.trim().startsWith('default_permissions')).length
  assert.equal(keyCount, 1, 'the fragment must not add a second default_permissions')
  assert.ok(text.includes('default_permissions = "my-own-profile"'))
  assert.ok(skipped.includes('default_permissions'))
  assert.ok(skipped.includes('[features] hooks'))

  assertValidToml(text)
})

test('a quoted root key is the bare key: the user value stays once, and the merge parses as TOML', () => {
  for (const key of ['"model_reasoning_effort"', "'model_reasoning_effort'", '"sandbox_mode"']) {
    const current = `${key} = "low"\n\n[features]\nhooks = true\n`
    const { text } = mergeCodexConfig(current, realFragment)
    assertValidToml(text)
    const bare = key.slice(1, -1)
    if (bare === 'sandbox_mode') assert.ok(!text.includes(key), 'the legacy sandbox_mode goes in any quote style')
    else assert.ok(text.includes(`${key} = "low"`) && !text.includes(`${bare} = "high"`), text)
  }
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
  assert.ok(text.includes('[features]'))
  assert.ok(text.includes('description = "mine"'))
  assertValidToml(text)
})

test('a settled target reports the items the user changed on every run, and changes nothing', () => {
  const { text } = mergeCodexConfig('model = "gpt-5"\n', realFragment)
  const edited = text.replace('hooks = true', 'hooks = false')
  assert.notEqual(edited, text)
  const again = mergeCodexConfig(edited, realFragment)
  assert.equal(again.changed, false)
  assert.equal(again.text, edited)
  assert.deepEqual(again.skipped, ['[features] hooks'])
})

const D2_FRAGMENT = [
  'default_permissions = "dev-workspace"', '',
  '[features]', 'hooks = true', 'network_proxy = true', '',
  '[permissions.dev-workspace]', 'extends = ":workspace"', '',
  '[permissions.dev-workspace.network]', 'enabled = true', 'mode = "full"', 'allow_local_binding = true', '',
  '[permissions.dev-workspace.network.domains]', '"github.com" = "allow"', '"*.github.com" = "allow"', '',
  '[agents]', 'max_threads = 4', '',
].join('\n')
const parse = (text) => JSON.parse(execFileSync('python3', ['-c', 'import json, sys, tomllib; print(json.dumps(tomllib.loads(sys.stdin.read())))'], { input: text, encoding: 'utf8' }))
const d2Merge = (current) => {
  const result = mergeCodexConfig(current, D2_FRAGMENT)
  assertValidToml(result.text)
  return result
}

test('KEY_MERGED_TABLES names the three D2 headers', () => {
  assert.deepEqual([...KEY_MERGED_TABLES].sort(), ['[features]', '[permissions.dev-workspace.network.domains]', '[permissions.dev-workspace.network]'].sort())
})

test('a missing kit key is added to the user table after its last definition line', () => {
  const { text, skipped } = d2Merge('[features]\nhooks = true\n\n[permissions.dev-workspace.network]\nenabled = true\n')
  const parsed = parse(text)
  assert.deepEqual(parsed.features, { hooks: true, network_proxy: true })
  assert.deepEqual(parsed.permissions['dev-workspace'].network, { enabled: true, mode: 'full', allow_local_binding: true, domains: { 'github.com': 'allow', '*.github.com': 'allow' } })
  assert.deepEqual(skipped, [])
  const lines = text.split('\n')
  assert.equal(lines[lines.indexOf('hooks = true') + 1], 'network_proxy = true')
})

test('a user value of a kit key stays and is reported as header and key', () => {
  const { text, skipped } = d2Merge('[features]\nhooks = true\nnetwork_proxy = false\n\n[permissions.dev-workspace.network]\nenabled = true\nmode = "limited"\n\n[permissions.dev-workspace.network.domains]\n"example.com" = "allow"\n')
  const parsed = parse(text)
  assert.equal(parsed.features.network_proxy, false)
  assert.equal(parsed.permissions['dev-workspace'].network.mode, 'limited')
  assert.deepEqual(Object.keys(parsed.permissions['dev-workspace'].network.domains).sort(), ['*.github.com', 'example.com', 'github.com'])
  assert.ok(skipped.includes('[features] network_proxy'))
  assert.ok(skipped.includes('[permissions.dev-workspace.network] mode'))
  assert.ok(!skipped.includes('[features]'))
})

test('a second merge after a key merge is byte-identical and reports nothing', () => {
  const { text } = d2Merge('[features]\nhooks = true\n\n[permissions.dev-workspace.network]\nenabled = true\n')
  const again = d2Merge(text)
  assert.equal(again.changed, false)
  assert.equal(again.text, text)
  assert.deepEqual(again.skipped, [])
})

test('an inline domains key keeps the domains table out and names it', () => {
  const { text, skipped } = d2Merge('[permissions.dev-workspace.network]\nenabled = true\ndomains = { "example.com" = "allow" }\n')
  assert.deepEqual(parse(text).permissions['dev-workspace'].network.domains, { 'example.com': 'allow' })
  assert.ok(skipped.includes('[permissions.dev-workspace.network.domains]'))
  assert.ok(!text.split('\n').some((line) => line.trim() === '[permissions.dev-workspace.network.domains]'))
})

test('a network table set inside the profile table is not given a second definition', () => {
  for (const line of ['network = { enabled = false }', 'network.enabled = false']) {
    const { text, skipped } = d2Merge(`[permissions.dev-workspace]\nextends = ":workspace"\n${line}\n`)
    assert.deepEqual(parse(text).permissions['dev-workspace'].network, { enabled: false }, line)
    assert.ok(skipped.includes('[permissions.dev-workspace.network]'), line)
    assert.ok(skipped.includes('[permissions.dev-workspace.network.domains]'), line)
  }
})

test('a kit host in other quotes is not added twice', () => {
  const { text, skipped } = d2Merge('[permissions.dev-workspace.network.domains]\n\'github.com\' = "allow"\n')
  const domains = parse(text).permissions['dev-workspace'].network.domains
  assert.equal(Object.keys(domains).filter((host) => host === 'github.com').length, 1)
  assert.ok(!text.includes('"github.com" = "allow"'))
  assert.ok(!skipped.some((item) => item.includes('github.com')))
})

test('a same value with other spacing or a comment is not reported', () => {
  const { text, skipped } = d2Merge('[permissions.dev-workspace.network]\nenabled=true  # mine\n')
  assert.ok(!skipped.some((item) => item.includes('enabled')))
  assert.equal(d2Merge(text).changed, false)
})

test('the added key lands in its own table before comments and the next table', () => {
  const { text } = d2Merge('[features]\nhooks = true\n# about plugins\n\n[plugins.x]\nenabled = true')
  const parsed = parse(text)
  assert.equal(parsed.features.network_proxy, true)
  assert.deepEqual(parsed.plugins.x, { enabled: true })
  assert.ok(text.indexOf('network_proxy = true') < text.indexOf('# about plugins'))
})

test('a table outside the D2 set keeps the table-level merge', () => {
  const { text, skipped } = d2Merge('[agents]\nmax_threads = 8\n')
  assert.deepEqual(parse(text).agents, { max_threads: 8 })
  assert.ok(skipped.includes('[agents]'))
})

test('a same string in another quote style is the same value', () => {
  const { text, skipped } = d2Merge("[permissions.dev-workspace.network]\nmode = 'full'\n")
  assert.deepEqual(skipped, [])
  assert.equal(parse(text).permissions['dev-workspace'].network.mode, 'full')
})

test('a header with a quoted segment is the kit table and gets the key merge', () => {
  const { text, skipped } = d2Merge('[features]\nhooks = true\n[permissions."dev-workspace".network]\nenabled = true\n')
  assert.equal(parse(text).permissions['dev-workspace'].network.mode, 'full')
  assert.deepEqual(skipped, [])
})

test('a root inline table that names a kit table declares it', () => {
  const { text, skipped } = d2Merge('features = { hooks = true }\n')
  assert.deepEqual(parse(text).features, { hooks: true })
  assert.ok(skipped.includes('[features]'))
  assert.ok(!text.split('\n').some((line) => line.trim() === '[features]'))
})

test('text inside a multi-line string or array is not a key', () => {
  for (const value of ['note = """\nnetwork_proxy = false\n"""', "note = '''\nnetwork_proxy = false\n'''", 'note = [\n  "a",\n  "network_proxy = false",\n]']) {
    const { text } = d2Merge(`[features]\nhooks = true\n${value}\n`)
    assert.equal(parse(text).features.network_proxy, true, value)
  }
})

test('a bare dotted key is not the kit host, which is still added', () => {
  const { text } = d2Merge('[permissions.dev-workspace.network.domains]\ngithub.com = "allow"\n')
  const domains = parse(text).permissions['dev-workspace'].network.domains
  assert.equal(domains['github.com'], 'allow')
  assert.deepEqual(domains.github, { com: 'allow' })
})

const LEGACY_IMPLEMENTER = [
  '[permissions.dev-implementer]',
  'description = "Implementation access without held-out acceptance tests."',
  'extends = "dev-workspace"',
  '',
  '[permissions.dev-implementer.filesystem.":workspace_roots"]',
  '"tests/acceptance" = "deny"',
  '"tests/acceptance/**" = "deny"',
  '',
].join('\n')

test('a table whose body equals the 2.1.1 profile is removed and reported, with its filesystem table', () => {
  const current = `model = "gpt"\n\n[features]\nhooks = true\n\n${LEGACY_IMPLEMENTER}`
  const { text, removed, changed } = mergeCodexConfig(current, realFragment)
  assert.equal(changed, true)
  assert.deepEqual(removed, [
    '[permissions.dev-implementer]',
    '[permissions.dev-implementer.filesystem.":workspace_roots"]',
  ])
  assert.ok(!text.includes('dev-implementer'))
  assert.ok(text.includes('[permissions.dev-workspace]'))
  assertValidToml(text)
})

test('a legacy table with comments and blank lines still counts as equal', () => {
  const current = LEGACY_IMPLEMENTER.replace('extends', '# mine\n\nextends')
  const { removed } = mergeCodexConfig(current, realFragment)
  assert.ok(removed.includes('[permissions.dev-implementer]'))
})

test('a legacy table whose body the user changed stays', () => {
  const current = LEGACY_IMPLEMENTER.replace('"tests/acceptance/**" = "deny"', '"tests/acceptance/**" = "allow"')
  const { text, removed } = mergeCodexConfig(current, realFragment)
  assert.deepEqual(removed, ['[permissions.dev-implementer]'])
  assert.ok(text.includes('[permissions.dev-implementer.filesystem.":workspace_roots"]'))
  assert.ok(text.includes('"tests/acceptance/**" = "allow"'))
  assertValidToml(text)
})

test('a merge into no config, or into one with no legacy table, removes nothing', () => {
  assert.deepEqual(mergeCodexConfig(null, realFragment).removed, [])
  assert.deepEqual(mergeCodexConfig('model = "gpt"\n', realFragment).removed, [])
})
