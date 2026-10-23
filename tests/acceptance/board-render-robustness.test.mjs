// Held-out oracle tests for renderBoard (spec: Board Robustness, seam 4), oracles 29 to 31.
//
// Loaded like test/board-render.test.mjs: render.js is one plain script, evaluated with node:vm in a fresh
// context, and renderBoard is a global of that context. Every name in here is invented.
//
// Markers this file relies on (from the Progress Board tests, or the spec's words):
// - each Stage 5 bar segment carries data-task-segment, one per task in task order, and its hover text is
//   the title attribute of that element;
// - the task list is in the feature details, shown when opts.open has the row.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'

const RENDER = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'skill', 'scripts', 'board', 'render.js')

function load() {
  const ctx = { console }
  vm.createContext(ctx)
  vm.runInContext(readFileSync(RENDER, 'utf8'), ctx, { filename: RENDER })
  assert.equal(typeof ctx.renderBoard, 'function', 'render.js defines no global renderBoard')
  // A Set made in the context realm whose has() is always true: every row is open, whatever the key.
  const AllOpen = vm.runInContext('(class extends Set { has() { return true } })', ctx)
  return { renderBoard: ctx.renderBoard, allOpen: () => new AllOpen() }
}

const NOW = Date.parse('2026-10-16T12:00:00Z')
const iso = (minutesBeforeNow) => new Date(NOW - minutesBeforeNow * 60000).toISOString().replace(/\.\d{3}Z$/, 'Z')

function note(over = {}) {
  return {
    type: 'dev-loop-status',
    schema: 1,
    project: 'acme-widgets',
    feature: 'gadget',
    title: 'Gadget Export',
    description: '',
    machine_id: '0123456789abcdef',
    owner: 'Robin',
    machine: 'lab-mac',
    track: 'M',
    stage: 5,
    stage_times: { 1: iso(400), 2: iso(350), 3: iso(300), 4: iso(250), 5: iso(200) },
    tasks: [],
    waiting: '',
    decisions: [],
    assumptions_baseline: [],
    shipped_at: '',
    updated: iso(5),
    ...over,
  }
}

// Task 6 is missing from the plan: the seventh task in the list is task 7, not task 6.
const TASKS = [
  { n: 1, title: 'Read Crates', state: 'done', started: iso(200), done: iso(180) },
  { n: 2, title: 'Count Bolts', state: 'done', started: iso(180), done: iso(160) },
  { n: 3, title: 'Sort Gears', state: 'done', started: iso(160), done: iso(140) },
  { n: 4, title: 'Weigh Boxes', state: 'done', started: iso(140), done: iso(120) },
  { n: 5, title: 'Label Bins', state: 'doing', started: iso(120) },
  { n: 7, title: 'Polish Widgets', state: 'doing', started: iso(60) },
  { n: 8, title: 'Ship Pallets', state: 'todo' },
]

// The visible text of the HTML: tags (and so attribute values) out, white space folded.
const text = (html) => html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ')

const unescape = (s) => s.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')

function segmentTitles(html) {
  return [...html.matchAll(/<[^>]*\bdata-task-segment="[^"]*"[^>]*>/g)].map((m) => {
    const title = /\btitle="([^"]*)"/.exec(m[0])
    return title ? unescape(title[1]) : null
  })
}

test('O29: the task list shows the plan number after the icon, also with a gap in the plan', () => {
  const { renderBoard, allOpen } = load()
  const shown = text(renderBoard([note({ tasks: TASKS })], NOW, { open: allOpen() }))
  for (const t of TASKS) {
    assert.ok(new RegExp(`(^|\\s)${t.n}\\. ${t.title}(\\s|$)`).test(shown), `no "${t.n}. ${t.title}" in the task list`)
  }
  assert.ok(!shown.includes('6. Polish Widgets'), 'task 7 numbered by its list position')
})

test('O30: the hover text of each Stage 5 bar segment starts with its task number', () => {
  const { renderBoard, allOpen } = load()
  const titles = segmentTitles(renderBoard([note({ tasks: TASKS })], NOW, { open: allOpen() }))
  assert.equal(titles.length, TASKS.length, 'one segment per task')
  TASKS.forEach((t, i) => {
    assert.ok(titles[i] && titles[i].startsWith(`${t.n}. ${t.title}`), `segment ${i + 1}: ${titles[i]}`)
  })
})

test('O31: a note with backfilled: true shows "Times are estimated" in its details', () => {
  const { renderBoard, allOpen } = load()
  const tasks = TASKS.slice(0, 2)
  assert.ok(renderBoard([note({ tasks, backfilled: true })], NOW, { open: allOpen() }).includes('Times are estimated'))
  assert.ok(!renderBoard([note({ tasks, backfilled: false })], NOW, { open: allOpen() }).includes('Times are estimated'))
  assert.ok(!renderBoard([note({ tasks })], NOW, { open: allOpen() }).includes('Times are estimated'))
})
