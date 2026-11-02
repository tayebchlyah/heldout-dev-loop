// The dataviewjs block of Dev Loop Board.md (Board Ledger D10, D11), run under Node with stub dv, require and
// localStorage, built from board-note.md the way writer.py builds the note.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import vm from 'node:vm'
import { packageRoot } from './helpers.mjs'

const BOARD = join(packageRoot, 'skill', 'scripts', 'board')
const TEMPLATE = readFileSync(join(BOARD, 'board-note.md'), 'utf8')
const NOTE = TEMPLATE.replace('/*VERSION*/', '9.9.9').replace('/*RENDER_JS*/', readFileSync(join(BOARD, 'render.js'), 'utf8'))
const BLOCK = NOTE.match(/```dataviewjs\n([\s\S]*?)\n```/)[1]

const status = (over) => ({
  type: 'dev-loop-status', schema: 1, project: 'p', feature: 'f', title: 'F', machine_id: 'B', owner: 'Bob',
  machine: 'mac-b', track: 'M', stage: 5, stage_times: { 1: new Date(Date.now() - 3600000).toISOString() },
  tasks: [], waiting: '', decisions: [], shipped_at: '', updated: new Date().toISOString(), ...over,
})

function run({ notes, machine = '{"id": "A", "owner": "Ana"}', stored = null, requireThrows = false }) {
  const el = { innerHTML: '', listeners: [], addEventListener(type, fn, capture) { this.listeners.push({ type, fn, capture }) } }
  const store = new Map(stored === null ? [] : [['dlb-open', stored]])
  const ctx = {
    dv: {
      pages: () => ({ where: (fn) => notes.map((n) => ({ file: { frontmatter: n } })).filter((p) => fn(p)) }),
      el: () => el,
    },
    require: (name) => {
      if (requireThrows) throw new Error('no require on mobile')
      if (name === 'os') return { homedir: () => '/h' }
      if (name === 'fs') return { readFileSync: (path) => { if (path === '/h/.config/dev-loop/machine.json') return machine; throw new Error('ENOENT') } }
      throw new Error(`module ${name}`)
    },
    localStorage: { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)) },
  }
  vm.createContext(ctx)
  vm.runInContext(BLOCK, ctx)
  const toggle = (key, open) => el.listeners.filter((l) => l.type === 'toggle').forEach((l) => l.fn({ target: { dataset: { key }, open } }))
  return { el, store, toggle }
}
const projects = (h) => [...h.matchAll(/data-key="project:([^"]+)"/g)].map((m) => m[1])

test('O23: with machine.json, the row of machine A shows this machine and its project comes first', () => {
  const { el } = run({ notes: [status({ project: 'alpha' }), status({ project: 'zeta', machine_id: 'A', owner: 'Ana' })] })
  assert.deepEqual(projects(el.innerHTML), ['zeta', 'alpha'])
  assert.equal(el.innerHTML.split('this machine').length - 1, 1)
})

test('O24: a require that throws still renders, no this machine, projects by name', () => {
  const { el } = run({ notes: [status({ project: 'zeta', machine_id: 'A' }), status({ project: 'alpha' })], requireThrows: true })
  assert.deepEqual(projects(el.innerHTML), ['alpha', 'zeta'])
  assert.ok(!el.innerHTML.includes('this machine'))
})

test('O24: no machine.json or bad JSON: name order', () => {
  for (const machine of ['{not json', null]) {
    const { el } = run({ notes: [status({ project: 'zeta', machine_id: 'A' }), status({ project: 'alpha' })], machine })
    assert.deepEqual(projects(el.innerHTML), ['alpha', 'zeta'])
  }
})

test('O25: dlb-open opens its folds; toggle events add and remove keys', () => {
  const { el, store, toggle } = run({ notes: [status(), status({ project: 'q' })], stored: '["project:p"]' })
  assert.match(el.innerHTML, /<details[^>]*data-key="project:p" open>/)
  assert.doesNotMatch(el.innerHTML, /<details[^>]*data-key="project:q" open>/)
  assert.ok(el.listeners.some((l) => l.type === 'toggle' && l.capture === true))
  toggle('project:q', true)
  assert.deepEqual(JSON.parse(store.get('dlb-open')).sort(), ['project:p', 'project:q'])
  toggle('project:p', false)
  assert.deepEqual(JSON.parse(store.get('dlb-open')), ['project:q'])
})

test('O25: a toggle with no data-key changes nothing', () => {
  const { store, toggle } = run({ notes: [status()], stored: '["project:p"]' })
  toggle(undefined, true)
  assert.equal(store.get('dlb-open'), '["project:p"]')
})

test('Review Focus 3: bad JSON or a non-array in dlb-open renders with no open fold', () => {
  for (const stored of ['{bad', '{"project:p": true}', '"project:p"']) {
    const { el } = run({ notes: [status()], stored })
    assert.doesNotMatch(el.innerHTML, /data-key="project:p" open/)
  }
})

test('O26: the note template holds no status data', () => {
  assert.ok(!/dev-loop-status"?\s*\n|stage_times:|machine_id:/.test(TEMPLATE.replace(/```dataviewjs[\s\S]*```/, '')))
})
