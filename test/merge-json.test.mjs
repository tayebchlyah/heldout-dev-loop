import test from 'node:test'
import assert from 'node:assert/strict'
import { mergeHooks, mergeDeny, mergeSettings, mergeHooksFile, removeHookCommands } from '../lib/merge-json.mjs'

const fragment = {
  hooks: {
    Stop: [{ hooks: [{ type: 'command', command: '~/.claude/hooks/gate.sh', timeout: 600 }] }],
    PreToolUse: [{ matcher: 'Bash', hooks: [{ type: 'command', command: '~/.claude/hooks/git-guard.sh' }] }],
  },
  permissions: { deny: ['Read(./.env)'] },
}

test('a merge into an empty object adds every entry', () => {
  const { value, added } = mergeHooks({}, fragment.hooks)
  assert.equal(value.Stop.length, 1)
  assert.equal(value.PreToolUse.length, 1)
  assert.equal(added.length, 2)
})

test('a second merge adds nothing', () => {
  const first = mergeHooks({}, fragment.hooks)
  const second = mergeHooks(first.value, fragment.hooks)
  assert.deepEqual(second.added, [])
  assert.deepEqual(second.value, first.value)
})

test('a foreign hook in the same event survives', () => {
  const current = {
    Stop: [{ hooks: [{ type: 'command', command: '~/.claude/hooks/format.sh' }] }],
  }
  const { value } = mergeHooks(current, fragment.hooks)
  const commands = value.Stop.flatMap((group) => group.hooks.map((hook) => hook.command))
  assert.deepEqual(commands, ['~/.claude/hooks/format.sh', '~/.claude/hooks/gate.sh'])
})

test('a matching matcher gains the hook instead of a second group', () => {
  const current = {
    PreToolUse: [{ matcher: 'Bash', hooks: [{ type: 'command', command: 'other.sh' }] }],
  }
  const { value } = mergeHooks(current, fragment.hooks)
  assert.equal(value.PreToolUse.length, 1)
  assert.equal(value.PreToolUse[0].hooks.length, 2)
})

test('mergeDeny appends only the missing rules', () => {
  const { value, added } = mergeDeny({ deny: ['Read(./.env)', 'Bash(rm)'] }, ['Read(./.env)', 'Read(./.env.*)'])
  assert.deepEqual(value.deny, ['Read(./.env)', 'Bash(rm)', 'Read(./.env.*)'])
  assert.deepEqual(added, ['Read(./.env.*)'])
})

test('mergeSettings handles a missing file and keeps other top-level keys', () => {
  const first = mergeSettings(null, fragment)
  const parsed = JSON.parse(first.text)
  assert.equal(parsed.hooks.Stop.length, 1)
  assert.deepEqual(parsed.permissions.deny, ['Read(./.env)'])

  const withModel = JSON.stringify({ model: 'opus', hooks: {} })
  const second = mergeSettings(withModel, fragment)
  assert.equal(JSON.parse(second.text).model, 'opus')
})

test('mergeSettings adds a missing env variable and keeps the value the user set', () => {
  const current = JSON.stringify({ env: { A: 'mine', OTHER: 'x' } })
  const result = mergeSettings(current, { ...fragment, env: { A: '0', B: '0' } })
  assert.deepEqual(JSON.parse(result.text).env, { A: 'mine', OTHER: 'x', B: '0' })
  assert.ok(result.added.includes('env B=0'))
  assert.ok(!result.added.some((entry) => entry.startsWith('env A')))
  assert.equal(JSON.parse(mergeSettings(null, fragment).text).env, undefined)
})

test('mergeSettings on an already merged file reports no addition', () => {
  const first = mergeSettings(null, fragment)
  const second = mergeSettings(first.text, fragment)
  assert.deepEqual(second.added, [])
})

test('mergeSettings preserves unrelated top-level keys and existing hook event order', () => {
  const withExtras = JSON.stringify({
    model: 'opus',
    env: { FOO: 'bar' },
    hooks: { Notification: [{ hooks: [{ type: 'command', command: 'notify.sh' }] }] },
  })
  const { text } = mergeSettings(withExtras, fragment)
  const parsed = JSON.parse(text)
  assert.equal(parsed.model, 'opus')
  assert.deepEqual(parsed.env, { FOO: 'bar' })
  assert.deepEqual(Object.keys(parsed.hooks), ['Notification', 'Stop', 'PreToolUse'])
})

test('a hook group whose hooks are not an array is skipped and named', () => {
  const current = { Stop: [{ matcher: 'x' }] }
  const { value, notes } = mergeHooks(current, { Stop: [{ hooks: [{ type: 'command', command: 'gate.sh' }] }] })
  assert.deepEqual(value.Stop[0], { matcher: 'x' }, 'the malformed group survives untouched')
  assert.ok(value.Stop.some((group) => Array.isArray(group.hooks)), "dev-loop's own group is still added")
  assert.equal(notes.length, 1)
  assert.match(notes[0], /Stop/)
})

test('a fragment group with no hooks array is skipped and named', () => {
  const { value, added, notes } = mergeHooks({}, { Stop: [{ matcher: 'x' }] })
  assert.deepEqual(value.Stop, [])
  assert.deepEqual(added, [])
  assert.equal(notes.length, 1)
})

test('an event whose value is not an array is left alone as foreign', () => {
  const current = { Stop: 'nonsense' }
  const { value, added, notes } = mergeHooks(current, { Stop: [{ hooks: [{ type: 'command', command: 'gate.sh' }] }] })
  assert.equal(value.Stop, 'nonsense')
  assert.deepEqual(added, [])
  assert.equal(notes.length, 1)
  assert.match(notes[0], /Stop/)
})

test('mergeSettings carries the notes of a malformed hook group', () => {
  const current = JSON.stringify({ hooks: { Stop: 'nonsense' } })
  const { notes } = mergeSettings(current, fragment)
  assert.equal(notes.length, 1)
})

test('mergeHooksFile merges into the hooks key and keeps the rest of the file', () => {
  const current = JSON.stringify({ description: 'mine', hooks: {} })
  const { text, added } = mergeHooksFile(current, JSON.stringify(fragment))
  const value = JSON.parse(text)
  assert.equal(value.description, 'mine')
  assert.equal(value.hooks.Stop.length, 1)
  assert.equal(added.length, 2)
})

const installed = JSON.stringify({
  model: 'opus',
  hooks: {
    PreToolUse: [
      { matcher: 'Bash', hooks: [{ type: 'command', command: '~/.claude/hooks/git-guard.sh' }, { type: 'command', command: 'mine.sh' }] },
      { matcher: 'Edit', hooks: [{ type: 'command', command: '~/.claude/hooks/deny-path.sh' }] },
    ],
    Stop: [{ hooks: [{ type: 'command', command: '~/.claude/hooks/gate.sh' }] }],
    Notification: [{ hooks: [{ type: 'command', command: 'notify.sh' }] }],
  },
  permissions: { deny: ['Read(./.env)'] },
})

test('removeHookCommands removes only the listed commands and names them', () => {
  const commands = ['~/.claude/hooks/git-guard.sh', '~/.claude/hooks/deny-path.sh', '~/.claude/hooks/gate.sh']
  const { text, removed } = removeHookCommands(installed, commands)
  const value = JSON.parse(text)
  assert.deepEqual(removed, [
    'PreToolUse: ~/.claude/hooks/git-guard.sh',
    'PreToolUse: ~/.claude/hooks/deny-path.sh',
    'Stop: ~/.claude/hooks/gate.sh',
  ])
  assert.deepEqual(value.hooks.PreToolUse, [{ matcher: 'Bash', hooks: [{ type: 'command', command: 'mine.sh' }] }])
  assert.equal('Stop' in value.hooks, false, 'an event with no group left is dropped')
  assert.deepEqual(value.hooks.Notification, [{ hooks: [{ type: 'command', command: 'notify.sh' }] }])
  assert.equal(value.model, 'opus')
  assert.deepEqual(value.permissions, { deny: ['Read(./.env)'] })
})

test('removeHookCommands with no listed command present removes nothing', () => {
  const { removed } = removeHookCommands(installed, ['other.sh'])
  assert.deepEqual(removed, [])
})

test('removeHookCommands leaves a malformed event or group alone', () => {
  const current = JSON.stringify({ hooks: { Stop: 'nonsense', PreToolUse: [{ matcher: 'x' }, { hooks: [{ command: 'gate.sh' }] }] } })
  const { text, removed } = removeHookCommands(current, ['gate.sh'])
  assert.deepEqual(JSON.parse(text).hooks, { Stop: 'nonsense', PreToolUse: [{ matcher: 'x' }] })
  assert.deepEqual(removed, ['PreToolUse: gate.sh'])
})
