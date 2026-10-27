import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { runPlan } from '../../../lib/plan.mjs'
import { tempDir } from './helpers.mjs'

test('a merge applies its rewrite to the fragment', () => {
  const dir = tempDir('dev-loop-rewrite-')
  const source = join(dir, 'fragment.json')
  writeFileSync(source, JSON.stringify({ hooks: { Stop: [{ hooks: [{ type: 'command', command: '{{HOME}}/.codex/hooks/gate.sh' }] }] } }))
  const rewrite = (text) => text.replace('{{HOME}}/.codex/hooks/gate.sh', 'project-gate.sh')
  const target = join(dir, 'hooks.json')
  runPlan([{ kind: 'merge-json', merge: 'hooks', source, target, rewrite }], { dryRun: false })
  const written = readFileSync(target, 'utf8')
  assert.ok(written.includes('project-gate.sh'))
  assert.ok(!written.includes('{{HOME}}'))
})
