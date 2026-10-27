// Held-out flow test for the board ledger layout (spec: Board Ledger), flow 3. Flows 1, 2 and 4 are in
// test_board_ledger.py.
//
// Loaded like board-render.test.mjs: render.js is one plain script, evaluated with node:vm in a fresh context,
// and renderBoard is a global of that context. Every name in here is invented.
//
// Markers this file relies on (the spec's words): each row is a <details> with data-key="<project>/<feature>",
// and its <summary> is the row summary.

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
  return { renderBoard: ctx.renderBoard, open: (keys) => new (vm.runInContext('Set', ctx))(keys) }
}

const NOW = Date.parse('2026-10-27T12:00:00Z')
const iso = (minutesBeforeNow) => new Date(NOW - minutesBeforeNow * 60000).toISOString().replace(/\.\d{3}Z$/, 'Z')
const text = (html) => html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ')

function rowSummary(html, key) {
  const tag = new RegExp(`<details\\b[^>]*\\bdata-key="${key}"[^>]*>`).exec(html)
  assert.ok(tag, `no <details data-key="${key}">`)
  return html.slice(tag.index, html.indexOf('</summary>', tag.index))
}

test('F3, D6, D9: a feature idle for 182 h 35 min shows "7 d 14 h" as its total and in "Idle, 7 d 14 h ago"', () => {
  const { renderBoard, open } = load()
  const idle = 182 * 60 + 35
  const html = renderBoard(
    [
      {
        type: 'dev-loop-status',
        schema: 1,
        project: 'acme-widgets',
        feature: 'dusty',
        title: 'Dusty Feature',
        description: '',
        machine_id: '0123456789abcdef',
        owner: 'Robin',
        machine: 'lab-mac',
        track: 'M',
        stage: 2,
        stage_times: { 1: iso(idle), 2: iso(idle) },
        tasks: [],
        waiting: '',
        decisions: [],
        assumptions_baseline: [],
        shipped_at: '',
        updated: iso(idle),
      },
    ],
    NOW,
    { open: open(['project:acme-widgets']), here: '0123456789abcdef', me: 'Robin' },
  )
  const summary = text(rowSummary(html, 'acme-widgets/dusty'))
  assert.ok(summary.includes('Idle, 7 d 14 h ago'), summary)
  assert.ok(summary.split('7 d 14 h').length - 1 >= 2, `no total "7 d 14 h" besides the idle time: ${summary}`)
})
