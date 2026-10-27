import test from 'node:test'
import assert from 'node:assert/strict'
import { mergeHooks, mergeDeny, mergeSettings, mergeHooksFile, removeHookCommands } from '../../../lib/merge-json.mjs'

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

test('a known old dev-loop SessionStart entry is replaced, any other dev-loop.json command is kept', () => {
  const old = 'd="${CLAUDE_PROJECT_DIR:-.}"; { [ -f "$d/dev-loop.json" ] || grep -qs \'^vault_notes:\' "$d/AGENTS.md"; } && echo \'This repo runs the dev-loop skill. Invoke it before any other action.\'; exit 0'
  const mine = 'test -f dev-loop.json && echo mine'
  const current = { SessionStart: [{ matcher: 'm', hooks: [{ type: 'command', command: old }, { type: 'command', command: mine }] }] }
  const fragment = { SessionStart: [{ matcher: 'm', hooks: [{ type: 'command', command: 'new dev-loop.json' }] }] }
  const { value, removed } = mergeHooks(current, fragment)
  assert.deepEqual(value.SessionStart[0].hooks.map((hook) => hook.command), ['new dev-loop.json', mine])
  assert.deepEqual(removed, [`SessionStart: ${old}`])
})

const ownCommand = '"${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}/.dev-loop/hooks/claude/deny.sh"'
const ownFragment = (matcher, extra = {}) => ({
  PreToolUse: [{ matcher, hooks: [{ type: 'command', command: ownCommand, timeout: 5, ...extra }] }],
})

test('a dev-loop hook entry that differs from the fragment is replaced in place, a user hook is kept', () => {
  const mine = { type: 'command', command: 'mine.sh' }
  const current = {
    PreToolUse: [
      { matcher: 'Read|Bash', hooks: [{ type: 'command', command: ownCommand, timeout: 5 }, mine] },
      { matcher: 'Other', hooks: [mine] },
    ],
  }
  const { value, added, changed } = mergeHooks(current, ownFragment('Read|NotebookEdit|Bash', { onFailure: 'block' }))
  assert.deepEqual(value.PreToolUse, [
    { matcher: 'Read|Bash', hooks: [mine] },
    { matcher: 'Read|NotebookEdit|Bash', hooks: [{ type: 'command', command: ownCommand, timeout: 5, onFailure: 'block' }] },
    { matcher: 'Other', hooks: [mine] },
  ])
  assert.deepEqual(added, [])
  assert.deepEqual(changed, [`PreToolUse: ${ownCommand}`])
  assert.deepEqual(mergeHooks(value, ownFragment('Read|NotebookEdit|Bash', { onFailure: 'block' })).changed, [])
})

test('two dev-loop commands that differ only by arguments are two hooks', () => {
  const stop = ownCommand + ' stop'
  const turn = ownCommand + ' turn'
  const fragment = { Stop: [{ hooks: [{ type: 'command', command: stop }, { type: 'command', command: turn, timeout: 9 }] }] }
  const current = { Stop: [{ hooks: [{ type: 'command', command: stop }, { type: 'command', command: turn }] }] }
  const { value, changed } = mergeHooks(current, fragment)
  assert.deepEqual(changed, [`Stop: ${turn}`])
  assert.equal(value.Stop.length, 1)
  assert.deepEqual(value.Stop[0].hooks, [{ type: 'command', command: stop }, { type: 'command', command: turn, timeout: 9 }])
})

test('a Codex hooks file gets the same in-place replacement', () => {
  const command = '"$(git rev-parse --show-toplevel)/.dev-loop/hooks/codex/gate.sh"'
  const old = JSON.stringify({ hooks: { Stop: [{ hooks: [{ type: 'command', command, timeout: 1 }] }] } })
  const next = JSON.stringify({ hooks: { Stop: [{ hooks: [{ type: 'command', command, timeout: 600 }] }] } })
  const result = mergeHooksFile(old, next)
  assert.deepEqual(JSON.parse(result.text).hooks.Stop, [{ hooks: [{ type: 'command', command, timeout: 600 }] }])
  assert.deepEqual(result.updated, [`Stop: ${command}`])
})

test('a user group that was already empty survives a merge', () => {
  const empty = { matcher: 'X', hooks: [] }
  const current = { PreToolUse: [empty, { matcher: 'Bash', hooks: [{ type: 'command', command: 'mine.sh' }] }] }
  const { value } = mergeHooks(current, ownFragment('Bash', {}))
  assert.deepEqual(value.PreToolUse[0], empty)
})

test('an emptied dev-loop group is replaced at its own index between user groups', () => {
  const one = { matcher: 'One', hooks: [{ type: 'command', command: 'one.sh' }] }
  const two = { matcher: 'Two', hooks: [{ type: 'command', command: 'two.sh' }] }
  const current = { PreToolUse: [one, { matcher: 'Read|Bash', hooks: [{ type: 'command', command: ownCommand }] }, two] }
  const { value, changed } = mergeHooks(current, ownFragment('Read|NotebookEdit|Bash', { onFailure: 'block' }))
  assert.deepEqual(value.PreToolUse.map((group) => group.matcher), ['One', 'Read|NotebookEdit|Bash', 'Two'])
  assert.deepEqual(changed, [`PreToolUse: ${ownCommand}`])
  const again = mergeHooks(value, ownFragment('Read|NotebookEdit|Bash', { onFailure: 'block' }))
  assert.deepEqual([again.added, again.changed, again.removed], [[], [], []])
  assert.deepEqual(again.value, value)
})

test('a replaced hook with an unchanged matcher keeps its index in the group', () => {
  const mine = { type: 'command', command: 'mine.sh' }
  const current = { PreToolUse: [{ matcher: 'Bash', hooks: [mine, { type: 'command', command: ownCommand, timeout: 1 }, { type: 'command', command: 'later.sh' }] }] }
  const { value } = mergeHooks(current, ownFragment('Bash', { timeout: 9 }))
  assert.deepEqual(value.PreToolUse[0].hooks.map((hook) => hook.command), ['mine.sh', ownCommand, 'later.sh'])
  assert.equal(value.PreToolUse[0].hooks[1].timeout, 9)
})

test('one dev-loop command under two matchers stays stable', () => {
  const fragment = { PostToolUse: [
    { matcher: 'Bash', hooks: [{ type: 'command', command: ownCommand }] },
    { matcher: 'TaskCreate|TaskUpdate', hooks: [{ type: 'command', command: ownCommand }] },
  ] }
  const first = mergeHooks({}, fragment)
  const second = mergeHooks(first.value, fragment)
  assert.deepEqual([second.added, second.changed, second.removed], [[], [], []])
  assert.deepEqual(second.value, first.value)
})

test('mergeSettings adds a missing top-level key and keeps the value the user set', () => {
  const frag = { effortLevel: 'high', autoCompactWindow: 400000 }
  const fresh = mergeSettings(null, frag)
  const parsed = JSON.parse(fresh.text)
  assert.equal(parsed.effortLevel, 'high')
  assert.equal(parsed.autoCompactWindow, 400000)
  assert.ok(fresh.added.includes('effortLevel=high'))
  assert.ok(fresh.added.includes('autoCompactWindow=400000'))
  assert.deepEqual(mergeSettings(fresh.text, frag).added, [])
  const set = mergeSettings('{"effortLevel": "medium", "autoCompactWindow": 300000}', frag)
  assert.equal(JSON.parse(set.text).effortLevel, 'medium')
  assert.equal(JSON.parse(set.text).autoCompactWindow, 300000)
  assert.deepEqual(set.added, [])
})

test('mergeSettings keeps a present key of any value', () => {
  const frag = { effortLevel: 'high', autoCompactWindow: 400000 }
  const out = mergeSettings('{"effortLevel": null, "autoCompactWindow": "300000"}', frag)
  const parsed = JSON.parse(out.text)
  assert.equal(parsed.effortLevel, null)
  assert.equal(parsed.autoCompactWindow, '300000')
  assert.deepEqual(out.added, [])
})
