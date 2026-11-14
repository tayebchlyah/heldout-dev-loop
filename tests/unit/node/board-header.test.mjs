// renderBoard header (#316): logo, need-you total, one cell per stage with its count and need-you badge.
// Loaded like the board render oracle test: render.js is a plain script, evaluated with node:vm.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import vm from 'node:vm'
import { packageRoot } from './helpers.mjs'

const BOARD = join(packageRoot, 'skill', 'scripts', 'board')
const ctx = {}
vm.createContext(ctx)
vm.runInContext(readFileSync(join(BOARD, 'render.js'), 'utf8'), ctx)
const renderBoard = ctx.renderBoard

const T0 = Date.parse('2026-11-14T12:00:00Z')
const ago = (min) => new Date(T0 - min * 60000).toISOString().replace(/\.\d{3}Z$/, 'Z')
const note = (over) => ({
  type: 'dev-loop-status', schema: 1, project: 'p', feature: 'f', title: 'Feature F', machine_id: 'A', owner: 'Ana',
  machine: 'mac-a', track: 'M', stage: 5, stage_times: { 1: ago(145) }, tasks: [], waiting: '', decisions: [],
  shipped_at: '', updated: ago(1), ...over,
})
const html = (notes, opts = {}) => renderBoard(notes, T0, opts)
const text = (h) => h.replace(/<style>[\s\S]*?<\/style>/, '').replace(/<svg[\s\S]*?<\/svg>/, '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ')
const head = (h) => h.slice(0, h.indexOf('data-key="project:'))
const cells = (h) => [...h.matchAll(/<div class="([^"]*)" data-stage-count="(\d)">([\s\S]*?)<\/div>/g)]
  .map(([, cls, n, inner]) => ({ n: Number(n), cls, text: text(inner).trim() }))
const svg = (h) => h.match(/<svg[\s\S]*?<\/svg>/)?.[0] ?? ''

const busy = [
  note({ feature: 'a', stage: 2, waiting: 'approve the spec' }), note({ feature: 'b', stage: 2, waiting: 'approve the spec' }),
  note({ feature: 'c', stage: 5 }), note({ feature: 'd', stage: 5 }), note({ feature: 'e', stage: 6, waiting: 'a stop item' }),
  note({ feature: 's', stage: 7, shipped_at: ago(30), waiting: 'old' }),
]

test('#316: the header shows the Heldout Dev Loop logo as one inline SVG', () => {
  const s = svg(head(html(busy)))
  assert.match(s, /aria-label="Heldout Dev Loop"/)
  assert.ok(!s.includes('<title'), 'no title element: its id would leak into the page')
  assert.equal((head(html(busy)).match(/<svg/g) || []).length, 1)
})

test('#316: every id of the logo starts with dlb-, and every reference names one', () => {
  const s = svg(html(busy))
  const ids = [...s.matchAll(/ id="([^"]+)"/g)].map((m) => m[1])
  assert.ok(ids.length > 10 && ids.every((i) => i.startsWith('dlb-')), ids.join(' '))
  const refs = [...s.matchAll(/(?:url\(#|href="#)([^)"]+)/g)].map((m) => m[1])
  assert.ok(refs.length > 10 && refs.every((r) => ids.includes(r)), refs.join(' '))
})

test('#316: the logo colours that differ in dark come from theme tokens, light and dark', () => {
  const h = html(busy)
  for (const tok of ['lg-code-a', 'lg-code-b', 'lg-ink-a', 'lg-ink-b', 'lg-violet-a', 'lg-violet-b', 'lg-rim']) assert.ok(svg(h).includes(`var(--${tok})`), tok)
  const rule = (sel) => h.match(new RegExp(`${sel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\{([^}]*)\\}`))?.[1] ?? ''
  assert.match(rule('.dlb[data-theme=light]'), /--lg-ink-a:#080b2b/)
  assert.match(rule('.dlb[data-theme=dark]'), /--lg-ink-a:#f5f7ff/)
  assert.match(rule('body.theme-dark .dlb:not([data-theme])'), /--lg-rim:\.25/)
})

test('#316: one cell per stage in order, with its count; an empty stage is marked zero', () => {
  const c = cells(head(html(busy)))
  assert.deepEqual(c.map((x) => x.n), [1, 2, 3, 4, 5, 6, 7])
  assert.deepEqual(c.map((x) => /\bzero\b/.test(x.cls)), [true, false, true, true, false, false, true])
  assert.match(c[4].text, /^2 5\. Build/)
  assert.match(c[0].text, /^0 1\. Frame/)
})

test('#316: a cell with waiting features shows their number and need you; shipped features never count', () => {
  const c = cells(head(html(busy)))
  assert.match(c[1].text, /^2 need you 2 2\. Spec/)
  assert.match(c[5].text, /^1 needs you 1 6\. Gate/)
  assert.ok(!/need/.test(c[4].text) && !/need/.test(c[6].text))
})

test('#316: the header gives the total that need you, active features only', () => {
  assert.match(text(head(html(busy))), /3 need you/)
  assert.match(text(head(html([note({ waiting: 'reply' })]))), /1 needs you/)
  assert.match(text(head(html([note()]))), /Nothing needs you/)
})

test('#316: no Needs you panel and no counts line in the header; decisions stay in their row', () => {
  const h = html([note({ waiting: 'reply', decisions: ['kept the default adapter'] })])
  assert.ok(!h.includes('you-panel') && !h.includes('class="counts"'))
  assert.ok(!text(head(h)).includes('went on with a default') && !text(head(h)).includes('kept the default adapter'))
  assert.ok(text(h).includes('Decided without you') && text(h).includes('kept the default adapter'))
})

test('#316: no feature in progress: the logo and the empty text, no stage cells and no total', () => {
  const h = html([note({ stage: 7, shipped_at: ago(30) })])
  assert.ok(svg(h) && text(h).includes('No feature in progress.'))
  assert.equal(cells(h).length, 0)
  assert.ok(!/need/.test(text(head(h))))
})

test('#316: a count and its words are separated by a space in the plain text, with no tag spacing', () => {
  const h = html(busy)
  const plain = (s) => s.replace(/<[^>]+>/g, '')
  assert.equal(plain(h.match(/<span class="tot[^>]*>[\s\S]*?<\/span>/)[0]), '3 need you')
  assert.match(plain(h.match(/data-stage-count="2">[\s\S]*?<\/span>/)[0]), /2 need you$/)
})

test('#316: the board page title is Heldout Dev Loop', () => {
  assert.match(readFileSync(join(BOARD, 'page.html'), 'utf8'), /<title>Heldout Dev Loop<\/title>/)
})
