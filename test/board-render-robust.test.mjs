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
