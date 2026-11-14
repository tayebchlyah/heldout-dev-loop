// Held-out tests for the board GitHub links (spec: 20261114 Board GitHub Links): the renderer, seam 2.
// One test per `## Acceptance oracle` line 14 to 20 (O<n>) and flow 3 (F3). Oracles 1 to 10, 21 and flows 1
// and 2 are in test_board_github_links.py, 11 to 13 in test_board_github_links_ci.py, 22 and 23 in
// test_board_github_links_kit_text.py.
//
// Loaded like board-render.test.mjs: render.js is one plain script, evaluated with node:vm in a fresh context,
// and renderBoard is a global of that context. Every fold is open. Every name in here is invented.
//
// Fixture choices the spec leaves open:
// - a row is the <details data-key="<project>/<feature>"> of the Board Ledger spec; its summary runs to the
//   next </summary>, and the links of a row are the <a> elements in that summary;
// - a link's visible text is the text of its content with the tags removed (the SVG icon has no text);
// - the icon colour of O16 is read in the markup of the link element, from <a to </a>.

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
  const AllOpen = vm.runInContext('(class extends Set { has() { return true } })', ctx)
  return { renderBoard: ctx.renderBoard, allOpen: () => new AllOpen() }
}

const NOW = Date.parse('2026-11-14T12:00:00Z')
const iso = (minutesBeforeNow) => new Date(NOW - minutesBeforeNow * 60000).toISOString().replace(/\.\d{3}Z$/, 'Z')
const HERE = { machine_id: '0123456789abcdef', owner: 'Robin', machine: 'lab-mac' }
const REPO = 'https://github.com/o/r'

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
    stage: 5,
    stage_times: { 1: iso(300), 2: iso(240), 3: iso(180), 4: iso(120), 5: iso(60) },
    tasks: [],
    waiting: '',
    decisions: [],
    assumptions_baseline: [],
    shipped_at: '',
    updated: iso(5),
    issue: 316,
    pr: 318,
    repo: REPO,
    ...over,
  }
}

function shipped(over = {}) {
  return note({ stage: 7, stage_times: { 1: iso(600), 5: iso(300), 7: iso(120) }, shipped_at: iso(60), updated: iso(60), ...over })
}

function board(notes) {
  const { renderBoard, allOpen } = load()
  return renderBoard(notes, NOW, { open: allOpen(), here: HERE.machine_id, me: HERE.owner })
}

const decode = (s) =>
  s
    .replace(/&quot;/g, '"')
    .replace(/&#0*39;|&#x0*27;|&apos;/gi, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&#0*35;|&#x0*23;/gi, '#')
    .replace(/&amp;/g, '&')
const text = (html) => decode(html.replace(/<[^>]*>/g, ' ')).replace(/\s+/g, ' ').trim()

function rowSummary(html, feature, project = 'acme-widgets') {
  const tag = new RegExp(`<details\\b[^>]*\\bdata-key="${project}/${feature}"[^>]*>`).exec(html)
  assert.ok(tag, `no <details data-key="${project}/${feature}">`)
  const end = html.indexOf('</summary>', tag.index)
  return html.slice(tag.index, end < 0 ? undefined : end)
}

function links(fragment) {
  const found = []
  for (const m of fragment.matchAll(/<a\b([^>]*)>([\s\S]*?)<\/a\s*>/gi)) {
    const attrs = {}
    for (const a of m[1].matchAll(/([A-Za-z_:][-\w:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/g)) {
      attrs[a[1].toLowerCase()] = decode(a[2] ?? a[3])
    }
    found.push({ attrs, text: text(m[2]), markup: m[0], at: m.index })
  }
  return found
}

const githubLinks = (fragment) => links(fragment).filter((l) => (l.attrs.href || '').startsWith('https://github.com'))
const names = (fragment) => githubLinks(fragment).map((l) => l.attrs['aria-label'])
const hasGhl = (fragment) => /\bclass\s*=\s*["'][^"']*\bghl\b/.test(fragment)

test('O14, D5, D6: an in-progress row shows the issue link, then the PR link, named open, in a new tab', () => {
  const row = rowSummary(board([note()]), 'gadget')
  const found = githubLinks(row)
  assert.deepEqual(
    found.map((l) => [l.attrs.href, l.attrs['aria-label'], l.text]),
    [
      [`${REPO}/issues/316`, 'Issue #316, open', '#316'],
      [`${REPO}/pull/318`, 'Pull request #318, open', '#318'],
    ],
  )
  for (const link of found) {
    assert.equal(link.attrs.target, '_blank', link.markup)
    assert.equal(link.attrs.rel, 'noopener', link.markup)
  }
})

test('O15: the same note shipped names the issue closed and the PR merged', () => {
  const row = rowSummary(board([shipped()]), 'gadget')
  assert.deepEqual(names(row), ['Issue #316, closed', 'Pull request #318, merged'])
})

test('O16, D5: the icon of an in-progress link uses var(--gh-open), of a shipped link var(--gh-done)', () => {
  const open = githubLinks(rowSummary(board([note()]), 'gadget'))
  const done = githubLinks(rowSummary(board([shipped()]), 'gadget'))
  assert.equal(open.length, 2)
  assert.equal(done.length, 2)
  for (const link of open) {
    assert.ok(link.markup.includes('var(--gh-open)'), link.markup)
    assert.ok(!link.markup.includes('var(--gh-done)'), link.markup)
  }
  for (const link of done) {
    assert.ok(link.markup.includes('var(--gh-done)'), link.markup)
    assert.ok(!link.markup.includes('var(--gh-open)'), link.markup)
  }
})

test('O17: only an issue or only a PR gives one link; neither, or repo "", gives no link and no ghl element', () => {
  const html = board([
    note({ feature: 'only-issue', title: 'Only Issue', pr: null }),
    note({ feature: 'only-pr', title: 'Only Pr', issue: null }),
    note({ feature: 'neither', title: 'Neither', issue: null, pr: null }),
    note({ feature: 'no-repo', title: 'No Repo', repo: '' }),
  ])
  assert.deepEqual(names(rowSummary(html, 'only-issue')), ['Issue #316, open'])
  assert.deepEqual(names(rowSummary(html, 'only-pr')), ['Pull request #318, open'])
  for (const feature of ['neither', 'no-repo']) {
    const row = rowSummary(html, feature)
    assert.deepEqual(githubLinks(row), [], feature)
    assert.ok(!row.includes('github.com'), feature)
    assert.ok(!hasGhl(row), `${feature} has a ghl element`)
  }
})

test('O18, D6: a repo or number outside the guard gives no link for that value', () => {
  const badRepos = ['javascript:alert(1)', 'https://evil.example/o/r', 'https://github.com/o/r/x']
  const html = board([
    ...badRepos.map((repo, n) => note({ feature: `repo-${n}`, title: `Repo ${n}`, repo })),
    note({ feature: 'text-issue', title: 'Text Issue', issue: '316' }),
    note({ feature: 'fraction-issue', title: 'Fraction Issue', issue: 3.5 }),
  ])
  badRepos.forEach((repo, n) => {
    const row = rowSummary(html, `repo-${n}`)
    assert.deepEqual(links(row).filter((l) => /\/(issues|pull)\//.test(l.attrs.href || '') || (l.attrs.href || '').startsWith('javascript:')), [], repo)
  })
  assert.ok(!/href\s*=\s*["']\s*javascript:/i.test(html), 'a javascript: href')
  for (const feature of ['text-issue', 'fraction-issue']) {
    const row = rowSummary(html, feature)
    assert.deepEqual(names(row), ['Pull request #318, open'], feature)
    assert.ok(!githubLinks(row).some((l) => l.attrs.href.includes('/issues/')), feature)
  }
})

test('O19: a row that needs the user shows the link line after "Needs you: ..."', () => {
  const row = rowSummary(board([note({ waiting: 'approve the spec' })]), 'gadget')
  const needs = row.indexOf('Needs you')
  assert.ok(needs >= 0, `no "Needs you" in the row: ${text(row)}`)
  const found = githubLinks(row)
  assert.equal(found.length, 2)
  for (const link of found) assert.ok(link.at > needs, 'a link before "Needs you"')
})

test('O20, D7: a slug title starting with the issue number drops it; with issue null it keeps it', () => {
  const html = board([note({ feature: '316-board-header', title: '316-board-header', issue: 316, pr: null })])
  const dropped = text(rowSummary(html, '316-board-header'))
  assert.ok(dropped.includes('Board Header'), dropped)
  assert.ok(!dropped.includes('316 Board Header'), dropped)
  const kept = text(rowSummary(board([note({ feature: '316-board-header', title: '316-board-header', issue: null, pr: null })]), '316-board-header'))
  assert.ok(kept.includes('316 Board Header'), kept)
})

test('F3: a shipped note shows under "Recently shipped" with the issue closed and the PR merged', () => {
  const html = board([shipped({ feature: 'parcel', title: 'Parcel Labels', issue: 298, pr: 303 })])
  const fold = /<details\b[^>]*\bdata-key="shipped:acme-widgets"[^>]*>/.exec(html)
  assert.ok(fold, 'no shipped fold')
  assert.ok(text(html.slice(fold.index, html.indexOf('</summary>', fold.index))).includes('Recently shipped'))
  const rowAt = html.search(/<details\b[^>]*\bdata-key="acme-widgets\/parcel"/)
  assert.ok(rowAt > fold.index, 'the row is not in the shipped fold')
  const row = rowSummary(html, 'parcel')
  assert.deepEqual(names(row), ['Issue #298, closed', 'Pull request #303, merged'])
  assert.deepEqual(githubLinks(row).map((l) => l.attrs.href), [`${REPO}/issues/298`, `${REPO}/pull/303`])
})
