// renderBoard ledger layout (Board Ledger spec): project folds, ledger rows, stage chips, task bars, durations.
// Loaded like the board render oracle test: render.js is a plain script, evaluated with node:vm.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import vm from 'node:vm'
import { packageRoot } from './helpers.mjs'

const RENDER = join(packageRoot, 'skill', 'scripts', 'board', 'render.js')
const ctx = {}
vm.createContext(ctx)
vm.runInContext(readFileSync(RENDER, 'utf8'), ctx)
const renderBoard = ctx.renderBoard

const T0 = Date.parse('2026-10-27T12:00:00Z')
const MIN = 60000
const iso = (t) => new Date(t).toISOString().replace(/\.\d{3}Z$/, 'Z')
const ago = (min, from = T0) => iso(from - min * MIN)
const note = (over) => ({
  type: 'dev-loop-status', schema: 1, project: 'p', feature: 'f', title: 'Feature F', machine_id: 'A', owner: 'Ana',
  machine: 'mac-a', track: 'M', stage: 5, stage_times: { 1: ago(145) }, tasks: [], waiting: '', decisions: [],
  shipped_at: '', updated: ago(1), ...over,
})
const SHIP = '2026-10-26T19:41:49Z'
const shippedNote = (over) => note({ stage: 7, shipped_at: SHIP, stage_times: { 1: ago(145, Date.parse(SHIP)) }, ...over })
const html = (notes, opts = {}, now = T0) => renderBoard(notes, now, opts)
const text = (h) => h.replace(/<style>[\s\S]*?<\/style>/, '').replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ')
const openTag = (h, key) => h.match(new RegExp(`<details[^>]*data-key="${key}"[^>]*>`))?.[0]
const row = (h, key = 'p/f') => {
  const i = h.indexOf(`data-key="${key}"`)
  assert.ok(i >= 0, `no row ${key}`)
  return h.slice(i, h.indexOf('</details>', i))
}
const summary = (r) => r.slice(r.indexOf('<summary>'), r.indexOf('</summary>'))
const projectSummary = (h, p) => {
  const i = h.indexOf(`data-key="project:${p}"`)
  return text(h.slice(i, h.indexOf('</summary>', i)))
}
const keys = (h, re) => [...h.matchAll(/data-key="([^"]+)"/g)].map((m) => m[1]).filter((k) => re.test(k))
const tasks = (startMin, ...specs) => specs.map(([state, minutes], i) => ({
  n: i + 1, title: `Task ${i + 1}`, state,
  ...(state === 'todo' ? {} : { started: ago(startMin - i * 20) }),
  ...(state === 'done' ? { done: ago(startMin - i * 20 - minutes) } : {}),
}))


test('O17: every duration format, through the total time of a row summary', () => {
  for (const [minutes, want] of [[0.5, 'under a minute'], [43, '43 min'], [145, '2 h 25 min'], [180, '3 h'],
    [182 * 60 + 35, '7 d 14 h'], [72 * 60, '3 d']]) {
    const s = text(summary(row(html([note({ stage: 1, stage_times: { 1: ago(minutes) } })]))))
    assert.ok(s.includes(want), `${minutes} min: ${s}`)
  }
})

test('O18: a feature idle for 182 h 35 min shows "Idle, 7 d 14 h ago"', () => {
  const s = text(summary(row(html([note({ updated: ago(182 * 60 + 35) })]))))
  assert.ok(s.includes('Idle, 7 d 14 h ago'), s)
})


test('O8: an in-progress Track M row shows "Track M" and its total time', () => {
  const s = text(summary(row(html([note()]))))
  assert.ok(s.includes('Track M') && s.includes('2 h 25 min'), s)
})

test('O9: a shipped row shows the track, the total to shipped_at, "Shipped" and the UTC ship date', () => {
  const s = text(summary(row(html([shippedNote()]))))
  for (const want of ['Track M', '2 h 25 min', 'Shipped', '2026-10-26']) assert.ok(s.includes(want), `${want}: ${s}`)
})

test('O10: Stage 5 with 2 of 4 tasks done shows the stage, the task count and the time left', () => {
  const sm = summary(row(html([note({ tasks: tasks(100, ['done', 10], ['done', 10], ['doing'], ['todo']) })])))
  assert.ok(text(sm).includes('5. Build') && text(sm).includes('2 of 4 tasks'), text(sm))
  assert.match(sm, /<[^>]*data-time-left[^>]*>About [^<]+ left</)
})

test('O7: only rows of the here machine show "this machine"', () => {
  const h = html([note(), note({ feature: 'g', machine_id: 'B', machine: 'mac-b' })], { here: 'A' })
  assert.ok(summary(row(h)).includes('this machine'))
  assert.ok(!row(h, 'p/g').includes('this machine'))
  assert.ok(text(summary(row(h, 'p/g'))).includes('Ana, mac-b'))
})

test('O20a: a shipped note that keeps a waiting shows no "Needs you" and is not counted', () => {
  const h = html([shippedNote({ waiting: 'review the PR' })])
  assert.ok(!row(h).includes('Needs you'))
  assert.ok(!/needs? you/.test(projectSummary(h, 'p')), projectSummary(h, 'p'))
})


test('O13: a shipped row lists each done task with its duration', () => {
  const r = text(row(html([shippedNote({ tasks: tasks(100, ['done', 12], ['done', 7], ['done', 43]).map((t) => ({ ...t,
    started: ago(100, Date.parse(SHIP)), done: ago(100 - { 1: 12, 2: 7, 3: 43 }[t.n], Date.parse(SHIP)) })) })])))
  for (const want of ['Task 1', '12 min', 'Task 2', '7 min', 'Task 3', '43 min']) assert.ok(r.includes(want), `${want}: ${r}`)
})

test('O16: task bars scale to the longest task; doing says running, to do says to do', () => {
  const r = row(html([note({ tasks: tasks(100, ['done', 10], ['done', 5], ['doing'], ['todo']) })]))
  const list = r.slice(r.indexOf('<ol class="lt">'))
  const items = list.split('<li').slice(1)
  const width = (li) => Number(li.match(/width:([\d.]+)%/)[1])
  assert.equal(width(items[2]), 100)
  assert.ok(Math.abs(width(items[0]) / width(items[1]) - 2) < 0.05)
  assert.match(text(items[2]), /running 1 h/)
  assert.match(text(items[3]), /to do/)
})

test('O16: the 10 min done task is 100% and the 5 min one 50% when no task runs longer', () => {
  const r = row(html([note({ tasks: tasks(100, ['done', 10], ['done', 5], ['doing'], ['todo']) })], {}, Date.parse(ago(78))))
  const items = r.slice(r.indexOf('<ol class="lt">')).split('<li').slice(1)
  assert.deepEqual(items.slice(0, 2).map((li) => Number(li.match(/width:([\d.]+)%/)[1])), [100, 50])
})

test('O20b: a shipped doing task ends at shipped_at: 30 min and no running, at any now', () => {
  const t = [{ n: 1, title: 'Last one', state: 'doing', started: ago(30, Date.parse(SHIP)) }]
  for (const now of [T0, T0 + 600 * MIN]) {
    const r = text(row(html([shippedNote({ tasks: t })], {}, now)))
    assert.ok(r.includes('30 min') && !r.includes('running'), r)
  }
})

test('O14: chips only for stages with a recorded time', () => {
  const r = row(html([note({ stage_times: { 2: ago(300), 3: ago(200), 5: ago(60) } })]))
  const chips = text(r.slice(r.indexOf('class="chips"'), r.indexOf('</div>', r.indexOf('class="chips"'))))
  for (const want of ['2. Spec', '3. Acceptance', '5. Build']) assert.ok(chips.includes(want), chips)
  for (const no of ['1. Frame', '4. Plan']) assert.ok(!chips.includes(no), chips)
})

test('O15: the current stage chip says so far, under the Time per stage heading', () => {
  const r = row(html([note({ stage: 6, stage_times: { 1: ago(300), 6: ago(20) } })]))
  assert.ok(text(r).includes('Time per stage'))
  assert.match(text(r.slice(r.indexOf('class="chips"'))), /6\. Gate 20 min so far/)
})

test('O19: Times are estimated shows on a backfilled row, in progress and shipped', () => {
  assert.ok(row(html([note({ backfilled: true })])).includes('Times are estimated'))
  assert.ok(row(html([shippedNote({ backfilled: true })])).includes('Times are estimated'))
})

test('decisions show under Decided without you, with no fold', () => {
  const r = row(html([note({ decisions: ['picked the default'] })]))
  assert.ok(text(r).includes('Decided without you') && text(r).includes('picked the default'))
})

test('Review Focus 1: a note with no stage times leaves its times out', () => {
  for (const n of [note({ stage_times: {} }), shippedNote({ stage_times: {} })]) {
    const r = text(row(html([n])))
    assert.ok(!/unknown|NaN|Infinity/.test(r), r)
  }
})

test('Review Focus 2: an unreadable shipped_at renders with no NaN', () => {
  const r = text(row(html([shippedNote({ shipped_at: 'soon' })])))
  assert.ok(r.includes('Shipped') && !/NaN|Invalid/.test(r), r)
})

test('Review Focus 5: a Track S row with no tasks says no plan, no tasks', () => {
  assert.ok(text(row(html([note({ track: 'S' })]))).includes('Track S: no plan, no tasks.'))
  assert.ok(text(row(html([note()]))).includes('No plan yet. Tasks appear at Stage 4.'))
})


test('O1: one fold per project, open only when opts.open has its key', () => {
  assert.ok(!/ open/.test(openTag(html([note()]), 'project:p')))
  assert.match(openTag(html([note()], { open: new Set(['project:p']) }), 'project:p'), / open/)
})

test('O2: the project summary counts in progress per stage, shipped and needs you', () => {
  const h = html([note({ feature: 'a' }), note({ feature: 'b', waiting: 'approve the spec' }), note({ feature: 'c', stage: 6 }),
    shippedNote({ feature: 'd' }), shippedNote({ feature: 'e' }), shippedNote({ project: 'q' })])
  const s = projectSummary(h, 'p')
  for (const want of ['3 in progress (2 in 5. Build, 1 in 6. Gate)', '2 recently shipped', '1 needs you']) assert.ok(s.includes(want), s)
  assert.ok(projectSummary(h, 'q').includes('nothing in progress'))
})

test('O3: no owner and machine group heading', () => {
  const h = html([note(), note({ feature: 'g', machine_id: 'B', owner: 'Bob', machine: 'mac-b' })], { here: 'A' })
  assert.ok(!text(h).includes('Ana, on mac-a') && !text(h).includes('Bob, on mac-b'))
})

test('O4: a project with a feature of the here machine comes first', () => {
  const h = html([note({ project: 'alpha', machine_id: 'B' }), note({ project: 'zeta' })], { here: 'A' })
  assert.deepEqual(keys(h, /^project:/), ['project:zeta', 'project:alpha'])
})

test('O5: in a project, here machine, then its owner, then others; waiting, then newest', () => {
  const h = html([
    note({ feature: 'other', machine_id: 'C', owner: 'Bob', updated: ago(1) }),
    note({ feature: 'mine-old', updated: ago(30) }),
    note({ feature: 'mine-new', updated: ago(2) }),
    note({ feature: 'mine-wait', updated: ago(60), waiting: 'reply' }),
    note({ feature: 'ana-b', machine_id: 'B', updated: ago(1) }),
  ], { here: 'A' })
  assert.deepEqual(keys(h, /^p\//), ['p/mine-wait', 'p/mine-new', 'p/mine-old', 'p/ana-b', 'p/other'])
})

test('O2 rank: opts.me alone ranks that owner first', () => {
  const h = html([note({ project: 'alpha', owner: 'Bob' }), note({ project: 'zeta', owner: 'Ana' })], { me: 'Ana' })
  assert.deepEqual(keys(h, /^project:/), ['project:zeta', 'project:alpha'])
})

test('O6: no viewer: projects by name, waiting first, then newest', () => {
  const h = html([note({ project: 'zeta' }), note({ project: 'alpha', feature: 'x', updated: ago(5) }),
    note({ project: 'alpha', feature: 'y', updated: ago(1) }), note({ project: 'alpha', feature: 'w', updated: ago(9), waiting: 'go' })])
  assert.deepEqual(keys(h, /^project:/), ['project:alpha', 'project:zeta'])
  assert.deepEqual(keys(h, /^alpha\//), ['alpha/w', 'alpha/y', 'alpha/x'])
})

test('O11: the shipped fold of a project, closed unless opts.open has its key', () => {
  const h = html([shippedNote()])
  assert.ok(!/ open/.test(openTag(h, 'shipped:p')))
  assert.match(text(h), /Recently shipped 1/)
  assert.match(openTag(html([shippedNote()], { open: new Set(['shipped:p']) }), 'shipped:p'), / open/)
})

test('O12: 6 shipped features show the 5 latest, viewer first then newest', () => {
  const notes = [1, 2, 3, 4, 5, 6].map((d) => shippedNote({ feature: `s${d}`, machine_id: 'B', shipped_at: `2026-10-0${d}T12:00:00Z` }))
  notes[1].machine_id = 'A'
  assert.deepEqual(keys(html(notes, { here: 'A' }), /^p\//), ['p/s2', 'p/s6', 'p/s5', 'p/s4', 'p/s3'])
})

test('O20: shipped features stay out of the header, the Needs you panel and the stage counts', () => {
  const h = html([note({ waiting: 'reply' }), shippedNote({ feature: 's', waiting: 'old', decisions: ['x'] })])
  const head = text(h.slice(0, h.indexOf('data-key="project:')))
  assert.ok(head.includes('1 feature in progress. 1 needs you.'), head)
  assert.ok(!head.includes('old') && !head.includes('Shipped'), head)
  assert.ok(/1 in 5\. Build/.test(head) && !/in 7\. Ship/.test(head), head)
})

test('O21: only shipped features: the empty text, the hint, then the project folds', () => {
  const h = html([shippedNote()])
  assert.ok(text(h).includes('No feature in progress.') && text(h).includes('Start one with the dev-loop skill'))
  assert.ok(openTag(h, 'project:p') && openTag(h, 'p/f'))
})

test('Review Focus 4: a project name with markup is escaped in the fold key and summary', () => {
  const h = html([note({ project: 'a<b"&' })])
  assert.ok(h.includes('data-key="project:a&lt;b&quot;&amp;"'))
  assert.ok(!h.includes('a<b"&'))
})

test('D9: a feature updated 20 s ago reads "under a minute ago"', () => {
  const s = text(summary(row(html([note({ updated: iso(T0 - 20000) })]))))
  assert.ok(s.includes('under a minute ago'), s)
  assert.ok(!s.includes('just now'), s)
})

test('D7: a negative stage duration from clock skew shows no chip and no total', () => {
  const r = row(html([note({ stage: 6, stage_times: { 5: ago(10), 6: ago(40) } })]))
  assert.ok(!text(r).includes('unknown'), text(r))
  const s = row(html([shippedNote({ stage_times: { 1: iso(Date.parse(SHIP) + 30 * MIN) } })]))
  assert.ok(!text(s).includes('unknown'), text(s))
})

test('#230: a title shows in Title Case; a slug title shows its words, a spec title keeps its hyphens and capitals', () => {
  for (const [title, want] of [['codex-gate-managed-deny', 'Codex-gate-managed-deny'], ['', 'F'],
    ['held-out temp leak in CI', 'Held-out Temp Leak In CI'],
    ['(wip) board ledger', '(Wip) Board Ledger'], ['fix `code` now', 'Fix `Code` Now'], ['élan 9lives', 'Élan 9Lives']]) {
    const s = text(summary(row(html([note({ title })]))))
    assert.ok(s.includes(want), `${title}: ${s}`)
  }
  const s = text(summary(row(html([note({ feature: 'codex-gate-managed-deny', title: 'codex-gate-managed-deny' })]),
    'p/codex-gate-managed-deny')))
  assert.ok(s.includes('Codex Gate Managed Deny'), s)
})

test('#230: a project shows as many shipped rows as the highest shipped_kept of its notes, else 5', () => {
  const ships = (n, over = {}) => Array.from({ length: n }, (_, i) => shippedNote({ feature: `s${i}`, ...over }))
  const shown = (notes) => keys(html(notes), /^p\/s/).length
  assert.equal(shown(ships(8)), 5)
  assert.equal(shown([...ships(8), note({ shipped_kept: 7 })]), 7)
  assert.equal(shown(ships(8, { shipped_kept: 2 })), 2)
  assert.equal(shown([...ships(8, { shipped_kept: 2 }), note({ feature: 'x', shipped_kept: 3 })]), 3)
  assert.equal(shown(ships(8, { shipped_kept: 0 })), 0)
  assert.equal(shown(ships(8, { shipped_kept: '9' })), 5)
  assert.equal(shown([...ships(8), note({ project: 'q', shipped_kept: 1 })]), 5)
})
