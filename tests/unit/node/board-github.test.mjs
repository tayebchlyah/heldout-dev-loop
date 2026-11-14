// renderBoard GitHub links (#319): the issue and the PR of each row, under its title.
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
const html = (notes) => renderBoard(notes, T0, {})
const attr = (tag, name) => tag.match(new RegExp(` ${name}="([^"]*)"`))?.[1]
const links = (h) => [...h.matchAll(/(<a class="gh"[^>]*>)([\s\S]*?)<\/a>/g)].map(([, open, inner]) => ({
  href: attr(open, 'href'), label: attr(open, 'aria-label'), target: attr(open, 'target'), rel: attr(open, 'rel'),
  text: inner.replace(/<svg[\s\S]*?<\/svg>/, ''), svg: inner.match(/<svg[\s\S]*?<\/svg>/)?.[0] ?? '',
}))
const plain = (l) => l.map(({ href, label, text, target, rel }) => ({ href, label, text, target, rel }))
const summary = (h) => h.match(/<summary><span class="t">[\s\S]*?<\/summary>/)?.[0] ?? ''

const live = note({ repo: 'https://github.com/o/r', issue: 316, pr: 318 })
const done = { ...live, stage: 7, shipped_at: ago(30) }

test('oracle 14: in progress: issue then PR, names, hrefs, text, target and rel', () => {
  assert.deepEqual(plain(links(html([live]))), [
    { href: 'https://github.com/o/r/issues/316', label: 'Issue #316, open', text: '#316', target: '_blank', rel: 'noopener' },
    { href: 'https://github.com/o/r/pull/318', label: 'Pull request #318, open', text: '#318', target: '_blank', rel: 'noopener' },
  ])
})

test('oracle 15: shipped: closed and merged', () => {
  assert.deepEqual(links(html([done])).map((l) => l.label), ['Issue #316, closed', 'Pull request #318, merged'])
})

test('oracle 16: the icon carries var(--gh-open) or var(--gh-done) in its own markup', () => {
  for (const [n, on, off] of [[live, 'var(--gh-open)', 'var(--gh-done)'], [done, 'var(--gh-done)', 'var(--gh-open)']]) {
    const l = links(html([n]))
    assert.equal(l.length, 2)
    for (const { svg } of l) {
      assert.ok(svg.includes(on) && !svg.includes(off), svg)
      assert.match(svg, /aria-hidden="true"/)
    }
  }
})

test('oracle 16: theme tokens light and dark', () => {
  const h = html([live])
  const rule = (sel) => h.match(new RegExp(`${sel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\{([^}]*)\\}`))?.[1] ?? ''
  assert.match(rule('.dlb[data-theme=light]'), /--gh-open:#1a7f37/)
  assert.match(rule('.dlb[data-theme=light]'), /--gh-done:#8250df/)
  assert.match(rule('.dlb[data-theme=dark]'), /--gh-open:#3fb950/)
  assert.match(rule('.dlb[data-theme=dark]'), /--gh-done:#a371f7/)
})

test('oracle 17: one link, or none and no ghl element', () => {
  assert.deepEqual(links(html([{ ...live, pr: null }])).map((l) => l.label), ['Issue #316, open'])
  assert.deepEqual(links(html([{ ...live, issue: null }])).map((l) => l.label), ['Pull request #318, open'])
  const none = html([{ ...live, issue: null, pr: null }])
  assert.equal(links(none).length, 0)
  assert.ok(!none.includes('class="ghl"'))
  const norepo = html([{ ...live, repo: '' }])
  assert.doesNotMatch(summary(norepo), /github\.com/)
  assert.ok(!norepo.includes('class="ghl"'))
})

test('oracle 18: guard', () => {
  for (const repo of ['javascript:alert(1)', 'https://evil.example/o/r', 'https://github.com/o/r/x']) {
    const h = html([{ ...live, repo }])
    assert.equal(links(h).length, 0, repo)
    assert.ok(!h.includes('class="ghl"'), repo)
  }
  for (const issue of ['316', 3.5]) {
    assert.deepEqual(links(html([{ ...live, issue }])).map((l) => l.label), ['Pull request #318, open'], String(issue))
  }
})

test('Review Focus 3: a non-string repo or a boolean pr gives no link', () => {
  assert.equal(links(html([{ ...live, repo: ['https://github.com/o/r'] }])).length, 0)
  assert.deepEqual(links(html([{ ...live, pr: true }])).map((l) => l.label), ['Issue #316, open'])
})

test('oracle 19: the link line comes after Needs you', () => {
  const s = summary(html([{ ...live, waiting: 'approve the spec' }]))
  const you = s.indexOf('Needs you: approve the spec')
  assert.ok(you >= 0 && you < s.indexOf('class="ghl"'), s)
})

test('oracle 20: a slug title drops the issue number', () => {
  const row = (issue) => summary(html([note({ feature: '316-board-header', title: '316-board-header', issue })]))
  assert.match(row(316), /<span class="t">Board Header</)
  assert.match(row(null), /<span class="t">316 Board Header</)
})

test('links stay out of the open panel', () => {
  const h = html([live])
  assert.ok(!h.slice(h.indexOf('<div class="lg-body">')).includes('class="gh"'))
})
