import test from 'node:test'
import assert from 'node:assert/strict'
import { mergeHooks, mergeDeny, mergeSettings } from '../lib/merge-json.mjs'

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
