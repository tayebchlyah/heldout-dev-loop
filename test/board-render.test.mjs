// Held-out oracle tests for renderBoard (spec: Progress Board, seam 3), oracles 48 to 60 and 63.
//
// Load contract: skill/scripts/board/render.js is one plain script. The server page and the Obsidian
// dataviewjs block inline the same text, and package.json is "type": "module", so the file cannot use
// import, export or require. These tests read its text and evaluate it with node:vm in a fresh context.
// `renderBoard` must then be a global of that context (a top-level function declaration, or a property
// set on globalThis). Every name in here is invented (D33).
//
// HTML markers this file relies on (the spec does not name them):
// - each stage segment of a stage bar carries data-stage-segment="<stage number>" and an inline
//   `width:<number>%` in the style of the same element, a percent of the bar;
// - each Stage 5 task sub-segment carries data-task-segment="<task number>";
// - the element that shows a time left estimate carries a data-time-left attribute, absent when there
//   is no estimate.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'

const RENDER = join(dirname(fileURLToPath(import.meta.url)), '..', 'skill', 'scripts', 'board', 'render.js')

function load() {
  const ctx = { console }
  vm.createContext(ctx)
  vm.runInContext(readFileSync(RENDER, 'utf8'), ctx, { filename: RENDER })
  assert.equal(typeof ctx.renderBoard, 'function', 'render.js defines no global renderBoard')
  // A Set made in the context realm whose has() is always true: every row is open, whatever the key.
  const AllOpen = vm.runInContext('(class extends Set { has() { return true } })', ctx)
  return { renderBoard: ctx.renderBoard, allOpen: () => new AllOpen() }
}

const NOW = Date.parse('2026-10-15T12:00:00Z')
const iso = (minutesBeforeNow) => new Date(NOW - minutesBeforeNow * 60000).toISOString().replace(/\.\d{3}Z$/, 'Z')

const HERE = { machine_id: '0123456789abcdef', owner: 'Robin', machine: 'lab-mac' }

function note(over = {}) {
  return {
    type: 'dev-loop-status',
    schema: 1,
    project: 'acme-widgets',
    feature: 'gadget',
    title: 'Gadget Export',
    description: '',
    ...HERE,
    track: 'M',
    stage: 3,
    stage_times: { 1: iso(300), 2: iso(240), 3: iso(60) },
    tasks: [],
    waiting: '',
    decisions: [],
    assumptions_baseline: [],
    shipped_at: '',
    updated: iso(5),
    ...over,
  }
}

function segments(html) {
  const found = []
  for (const match of html.matchAll(/<[^>]*\bdata-stage-segment="(\d+)"[^>]*>/g)) {
    const width = /width:\s*([\d.]+)%/.exec(match[0])
    found.push({ stage: Number(match[1]), width: width ? Number(width[1]) : null })
  }
  return found
}

const taskSegments = (html) => [...html.matchAll(/data-task-segment="(\d+)"/g)].length

test('O48: every string from a note is escaped; style values come from numbers only', () => {
  const { renderBoard, allOpen } = load()
  const evil = '<img src=x onerror=alert(1)>'
  const quoted = 'Tom & "Jerry" <b>'
  const html = renderBoard(
    [
      note({ title: evil, description: quoted, project: `${evil}p`, owner: `${evil}o`, machine: `${evil}m`, feature: `${evil}f` }),
      note({
        feature: 'second',
        title: quoted,
        stage: 5,
        waiting: evil,
        decisions: [evil, quoted],
        tasks: [{ n: 1, title: evil, state: 'doing', started: iso(30) }],
        stage_times: { 1: 'x;background:url(javascript:alert(1))', 5: iso(30) },
      }),
    ],
    NOW,
    { open: allOpen(), here: HERE.machine_id, me: 'Robin' },
  )
  assert.ok(!html.includes('<img'), 'raw <img in the HTML')
  assert.ok(!html.includes('<b>'), 'raw <b> in the HTML')
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'), 'the title is not shown as text')
  assert.ok(!html.includes('"Jerry"'), 'raw double quotes from a note')
  assert.ok(html.includes('&amp;'), 'no escaped ampersand')
  assert.ok(!html.includes('javascript:'), 'a string from a note reached a style value')
})

test('O49: no features shows "No feature in progress."', () => {
  const { renderBoard } = load()
  assert.ok(renderBoard([], NOW, {}).includes('No feature in progress.'))
})

const GROUP_NOTES = [
  note({ feature: 'f-lab', title: 'Lab Feature', ...HERE }),
  note({ feature: 'f-zeta', title: 'Zeta Feature', machine_id: 'aaaaaaaaaaaaaaa1', owner: 'Robin', machine: 'zeta-mac' }),
  note({ feature: 'f-alpha', title: 'Alpha Feature', machine_id: 'aaaaaaaaaaaaaaa2', owner: 'Robin', machine: 'alpha-mac' }),
  note({ feature: 'f-desk', title: 'Desk Feature', machine_id: 'aaaaaaaaaaaaaaa3', owner: 'Casey', machine: 'desk-pc' }),
  note({ feature: 'f-tower', title: 'Tower Feature', machine_id: 'aaaaaaaaaaaaaaa4', owner: 'Blair', machine: 'tower-pc' }),
]

test('O50: groups by owner and machine: here first and marked, then its owner, then other owners by name', () => {
  const { renderBoard } = load()
  const html = renderBoard(GROUP_NOTES, NOW, { here: HERE.machine_id, me: 'Robin' })
  const at = (s) => {
    const i = html.indexOf(s)
    assert.ok(i >= 0, `${s} missing`)
    return i
  }
  assert.ok(html.includes('this machine'))
  assert.ok(at('lab-mac') < at('alpha-mac') && at('lab-mac') < at('zeta-mac'), 'this machine is not first')
  assert.ok(at('this machine') < at('alpha-mac'))
  assert.ok(Math.max(at('alpha-mac'), at('zeta-mac')) < at('tower-pc'), 'other machines of the owner come next')
  assert.ok(at('tower-pc') < at('desk-pc'), 'other owners by name')
})

test('O50, D26: with no here (Obsidian note), owners by name, then machines by name, no "this machine"', () => {
  const { renderBoard } = load()
  const html = renderBoard(GROUP_NOTES, NOW, {})
  const order = ['tower-pc', 'desk-pc', 'alpha-mac', 'lab-mac', 'zeta-mac'].map((s) => html.indexOf(s))
  assert.ok(order.every((i) => i >= 0), 'a machine is missing')
  assert.deepEqual([...order].sort((a, b) => a - b), order)
  assert.ok(!html.includes('this machine'))
})

test('O51: one subheading per project in a group, projects by name', () => {
  const { renderBoard } = load()
  const html = renderBoard(
    [
      note({ project: 'zephyr-app', feature: 'zed', title: 'Zed Feature' }),
      note({ project: 'acme-widgets', feature: 'alpha', title: 'Alpha Feature' }),
      note({ project: 'acme-widgets', feature: 'beta', title: 'Beta Feature' }),
    ],
    NOW,
    { here: HERE.machine_id, me: 'Robin' },
  )
  const acme = html.indexOf('acme-widgets')
  const zephyr = html.indexOf('zephyr-app')
  assert.ok(acme >= 0 && zephyr > acme)
  for (const title of ['Alpha Feature', 'Beta Feature']) {
    const i = html.indexOf(title)
    assert.ok(i > acme && i < zephyr, `${title} not under acme-widgets`)
  }
  assert.ok(html.indexOf('Zed Feature') > zephyr)
})

test('O52: two notes of one feature on one machine: only the newer shows', () => {
  const { renderBoard } = load()
  const older = note({ title: 'Old Title', updated: iso(60) })
  const newer = note({ title: 'New Title', updated: iso(2) })
  for (const features of [[older, newer], [newer, older]]) {
    const html = renderBoard(features, NOW, {})
    assert.ok(html.includes('New Title'))
    assert.ok(!html.includes('Old Title'))
  }
})

test('O53, F3: one feature on two machines: the higher stage, with the earliest time of each stage', () => {
  const { renderBoard, allOpen } = load()
  const a = note({
    title: 'Title From A',
    stage: 3,
    stage_times: { 1: iso(140), 2: iso(80), 3: iso(20) },
    updated: iso(1),
  })
  const b = note({
    title: 'Title From B',
    machine_id: 'bbbbbbbbbbbbbbbb',
    owner: 'Casey',
    machine: 'bench-pc',
    stage: 4,
    stage_times: { 2: iso(30), 3: iso(20), 4: iso(10) },
    updated: iso(3),
  })
  const opts = () => ({ open: allOpen(), here: HERE.machine_id, me: 'Robin' })
  const merged = renderBoard([a, b], NOW, opts())
  const alone = renderBoard([b], NOW, opts())
  assert.ok(merged.includes('Title From B'))
  assert.ok(!merged.includes('Title From A'))
  assert.notEqual(merged, alone)
  const spec = (html) => segments(html).find((s) => s.stage === 2)
  assert.ok(spec(merged) && spec(alone), 'no Stage 2 segment')
  assert.ok(spec(merged).width > spec(alone).width, 'the Stage 2 time of machine A is not used')
})

test('O54: the Tasks column shows "N of M" and no progress bar', () => {
  const { renderBoard } = load()
  const html = renderBoard(
    [
      note({
        stage: 5,
        stage_times: { 1: iso(300), 2: iso(240), 3: iso(200), 4: iso(150), 5: iso(100) },
        tasks: [
          { n: 1, title: 'One', state: 'done', started: iso(100), done: iso(60) },
          { n: 2, title: 'Two', state: 'doing', started: iso(60) },
          { n: 3, title: 'Three', state: 'todo' },
        ],
      }),
    ],
    NOW,
    {},
  )
  assert.ok(html.includes('1 of 3'))
  assert.ok(!/<progress\b/i.test(html))
  assert.ok(!/role="progressbar"/i.test(html))
})

test('O55: one stage segment per stage of the track: 7, or 4 for Track S', () => {
  const { renderBoard, allOpen } = load()
  const m = segments(renderBoard([note()], NOW, { open: allOpen() }))
  assert.deepEqual(m.map((s) => s.stage), [1, 2, 3, 4, 5, 6, 7])
  const s = segments(
    renderBoard([note({ track: 'S', stage: 6, stage_times: { 1: iso(120), 5: iso(90), 6: iso(10) } })], NOW, { open: allOpen() }),
  )
  assert.deepEqual(s.map((x) => x.stage), [1, 5, 6, 7])
})

test('O56: next stage segments have equal width, 1/N of the bar', () => {
  const { renderBoard, allOpen } = load()
  const m = segments(renderBoard([note({ stage: 2, stage_times: { 1: iso(100), 2: iso(50) } })], NOW, { open: allOpen() }))
  for (const seg of m.filter((x) => x.stage > 2)) assert.ok(Math.abs(seg.width - 100 / 7) < 0.1, `stage ${seg.stage}: ${seg.width}`)
  const s = segments(renderBoard([note({ track: 'S', stage: 1, stage_times: { 1: iso(20) } })], NOW, { open: allOpen() }))
  assert.deepEqual(s.map((x) => x.stage), [1, 5, 6, 7])
  for (const seg of s.filter((x) => x.stage > 1)) assert.ok(Math.abs(seg.width - 25) < 0.1, `stage ${seg.stage}: ${seg.width}`)
})

const BUILD = note({
  stage: 5,
  stage_times: { 1: iso(300), 2: iso(240), 3: iso(200), 4: iso(150), 5: iso(100) },
  tasks: [
    { n: 1, title: 'One', state: 'done', started: iso(100), done: iso(70) },
    { n: 2, title: 'Two', state: 'done', started: iso(70), done: iso(40) },
    { n: 3, title: 'Three', state: 'doing', started: iso(40) },
    { n: 4, title: 'Four', state: 'todo' },
  ],
})

test('O57: at Stage 5, the bar has one sub-segment per task', () => {
  const { renderBoard, allOpen } = load()
  assert.equal(taskSegments(renderBoard([BUILD], NOW, { open: allOpen() })), 4)
})

test('O58: stage labels read "<number>. <name>"', () => {
  const { renderBoard, allOpen } = load()
  const html = renderBoard([note({ stage_times: { 1: iso(370), 2: iso(365), 3: iso(5) } })], NOW, { open: allOpen() })
  assert.ok(html.includes('2. Spec'))
})

test('O59: "Time left" shows only at Stage 5 when at least one task is done', () => {
  const { renderBoard, allOpen } = load()
  const has = (n) => /data-time-left/.test(renderBoard([n], NOW, { open: allOpen() }))
  assert.ok(has(BUILD), 'no estimate at Stage 5 with done tasks')
  const noneDone = note({
    stage: 5,
    stage_times: BUILD.stage_times,
    tasks: [
      { n: 1, title: 'One', state: 'doing', started: iso(100) },
      { n: 2, title: 'Two', state: 'todo' },
    ],
  })
  assert.ok(!has(noneDone), 'an estimate with no done task')
  const gate = note({
    stage: 6,
    stage_times: { ...BUILD.stage_times, 6: iso(10) },
    tasks: BUILD.tasks.map((t) => ({ ...t, state: 'done', started: t.started ?? iso(30), done: t.done ?? iso(20) })),
  })
  assert.ok(!has(gate), 'an estimate at Stage 6')
  assert.ok(!has(note()), 'an estimate at Stage 3')
})

test('O60, F1: "Needs you" lists each feature with a non-empty waiting, and each decision', () => {
  const { renderBoard } = load()
  const html = renderBoard(
    [
      note({ feature: 'one', title: 'Spec Wait', stage: 2, stage_times: { 1: iso(50), 2: iso(40) }, waiting: 'approve the spec' }),
      note({ feature: 'two', title: 'Build Wait', stage: 5, stage_times: { 5: iso(30) }, waiting: 'reply in the session' }),
      note({ feature: 'three', title: 'Has Decision', decisions: ['Retries use a queue.'] }),
      note({ feature: 'four', title: 'Quiet One' }),
    ],
    NOW,
    { here: HERE.machine_id, me: 'Robin' },
  )
  for (const text of ['Needs you', 'approve the spec', 'reply in the session', 'Retries use a queue.']) {
    assert.ok(html.includes(text), `${text} missing`)
  }
})

test('D25: a line counts notes that need a newer dev-loop', () => {
  const { renderBoard } = load()
  assert.ok(renderBoard([note()], NOW, { newerSchema: 2 }).includes('2 notes need a newer dev-loop'))
})

const shipped = (n, day, over = {}) =>
  note({
    feature: `ship-${n}`,
    title: `Ship ${n}`,
    machine_id: 'cccccccccccccccc',
    owner: 'Blair',
    machine: 'tower-pc',
    stage: 7,
    stage_times: { 1: `2026-10-0${day}T01:00:00Z`, 7: `2026-10-0${day}T05:00:00Z` },
    shipped_at: `2026-10-0${day}T06:00:00Z`,
    updated: `2026-10-0${day}T06:00:00Z`,
    ...over,
  })

test('O63, F4: "Recently shipped" lists the 3 latest per project, newest first, out of the groups', () => {
  const { renderBoard } = load()
  const html = renderBoard(
    [shipped('One', 1), shipped('Three', 3), shipped('Four', 4), shipped('Two', 2), note({ title: 'Active One' })],
    NOW,
    { here: HERE.machine_id, me: 'Robin' },
  )
  const section = html.indexOf('Recently shipped')
  assert.ok(section >= 0)
  assert.ok(!html.includes('Ship One'), 'more than 3 shipped features in a project')
  const at = ['Ship Four', 'Ship Three', 'Ship Two'].map((t) => html.indexOf(t))
  assert.ok(at.every((i) => i > section), 'a shipped feature outside "Recently shipped"')
  assert.ok(at[0] < at[1] && at[1] < at[2], 'not newest first')
  for (const t of ['Ship Four', 'Ship Three', 'Ship Two']) assert.equal(html.split(t).length - 1, 1, `${t} shown twice`)
})

test('O63: shipped notes stay out of the header count, "Needs you" and the count per stage', () => {
  const { renderBoard } = load()
  const active = note({ title: 'Active One' })
  const done = shipped('Done', 4, { waiting: 'review the PR', decisions: ['Shipped decision text.'] })
  const opts = { here: HERE.machine_id, me: 'Robin' }
  const without = renderBoard([active], NOW, opts)
  const withShipped = renderBoard([active, done], NOW, opts)
  assert.ok(!withShipped.includes('review the PR'))
  assert.ok(!withShipped.includes('Shipped decision text.'))
  const firstGroup = without.indexOf('lab-mac')
  assert.ok(firstGroup > 0)
  assert.equal(withShipped.slice(0, firstGroup), without.slice(0, firstGroup), 'the header, panel or counts changed')
})

test('O63: the shipped row shows the total time from the first stage time to shipped_at', () => {
  const { renderBoard } = load()
  const short = renderBoard([shipped('Done', 4)], NOW, {})
  const long = renderBoard(
    [shipped('Done', 4, { stage_times: { 1: '2026-10-01T01:00:00Z', 7: '2026-10-04T05:00:00Z' } })],
    NOW,
    {},
  )
  assert.notEqual(short, long)
})

test('O63, D22: a feature shipped on one machine and open on another shows only in "Recently shipped"', () => {
  const { renderBoard } = load()
  const html = renderBoard(
    [
      note({ feature: 'merge', title: 'Merge Feature', stage: 6, waiting: 'reply in the session', updated: iso(1) }),
      shipped('Merge', 4, { feature: 'merge', title: 'Merge Feature' }),
    ],
    NOW,
    { here: HERE.machine_id, me: 'Robin' },
  )
  assert.equal(html.split('Merge Feature').length - 1, 1)
  assert.ok(html.indexOf('Merge Feature') > html.indexOf('Recently shipped'))
  assert.ok(!html.includes('reply in the session'))
})
