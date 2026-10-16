// renderBoard on malformed notes: a note from the synced vault can hold any JSON value in any key.
// Loaded like test/board-render.test.mjs: render.js is a plain script, evaluated with node:vm.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'

const RENDER = join(dirname(fileURLToPath(import.meta.url)), '..', 'skill', 'scripts', 'board', 'render.js')

test('renderBoard returns HTML for notes with missing or wrongly typed values', () => {
  const ctx = {}
  vm.createContext(ctx)
  vm.runInContext(readFileSync(RENDER, 'utf8'), ctx)
  const html = ctx.renderBoard(
    [{}, { stage: 'x', tasks: {}, stage_times: null }, { type: 'dev-loop-status', schema: 1, decisions: 'not a list', title: 42 }],
    Date.parse('2026-10-15T12:00:00Z'),
    {},
  )
  assert.equal(typeof html, 'string')
})

function loadRender() {
  const ctx = {}
  vm.createContext(ctx)
  vm.runInContext(readFileSync(RENDER, 'utf8'), ctx)
  return ctx.renderBoard
}

const T0 = Date.parse('2026-10-15T12:00:00Z')
const stamp = (min) => new Date(T0 - min * 60000).toISOString().replace(/\.\d{3}Z$/, 'Z')
const baseNote = (over) => ({
  type: 'dev-loop-status', schema: 1, project: 'acme-widgets', feature: 'gadget', title: 'Gadget Export',
  description: '', machine_id: '0123456789abcdef', owner: 'Robin', machine: 'lab-mac', track: 'M', stage: 5,
  stage_times: { 1: stamp(300), 5: stamp(60) }, tasks: [], waiting: '', decisions: [], assumptions_baseline: [],
  shipped_at: '', updated: stamp(5), ...over,
})

test('D4: a Needs you entry shows no waiting duration', () => {
  const html = loadRender()([baseNote({ stage: 2, waiting: 'approve the spec', updated: stamp(90) })], T0, {})
  assert.ok(html.includes('approve the spec'))
  assert.ok(!html.includes('Waiting '))
})

test('D9: done tasks of zero duration still give a Time left at Stage 5', () => {
  const tasks = [
    { state: 'done', started: stamp(10), done: stamp(10) },
    { state: 'todo' },
  ]
  const html = loadRender()([baseNote({ tasks })], T0, {})
  assert.ok(html.includes('data-time-left'))
})

test('O4: render.js holds no triple backtick and no closing script tag', () => {
  const src = readFileSync(RENDER, 'utf8')
  assert.ok(!src.includes('```'))
  assert.ok(!src.includes('</script'))
})

test('D1: notes with one machine_id but different owners give two groups', () => {
  const html = loadRender()([
    baseNote({ feature: 'a', owner: 'Robin' }), baseNote({ feature: 'b', owner: 'Sam' }),
  ], T0, {})
  assert.equal(html.split('class="grp"').length - 1, 2)
})

test('D2: task segments show only while Stage 5 runs', () => {
  const tasks = [{ title: 'one', state: 'done', started: stamp(30), done: stamp(20) }, { title: 'two', state: 'todo' }]
  const seg = (note) => loadRender()([note], T0, {}).includes('data-task-segment')
  assert.ok(seg(baseNote({ tasks })))
  assert.ok(!seg(baseNote({ tasks, stage: 6, stage_times: { 1: stamp(300), 5: stamp(60), 6: stamp(10) } })))
  const shipped = baseNote({ tasks, stage: 7, shipped_at: stamp(2), stage_times: { 1: stamp(300), 5: stamp(60), 6: stamp(10), 7: stamp(3) } })
  assert.ok(!seg(shipped))
})
