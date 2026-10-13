import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { backupPath, freeBackupPath } from '../lib/fsops.mjs'

test('backupPath appends a timestamp suffix', () => {
  assert.equal(backupPath('/x/y.json', new Date('2026-09-14T10:20:30Z')), '/x/y.json.bak-20260914T102030Z')
})

test('backupPath moves a skill backup out of the skill discovery folder', () => {
  const now = new Date('2026-09-14T10:20:30Z')
  assert.equal(backupPath('/h/.agents/skills/dev-loop', now), '/h/.agents/backups/skills/dev-loop.bak-20260914T102030Z')
  assert.equal(backupPath('/h/.claude/skills/dev-loop', now), '/h/.claude/backups/skills/dev-loop.bak-20260914T102030Z')
  assert.equal(backupPath('/h/.claude/hooks/gate.sh', now), '/h/.claude/hooks/gate.sh.bak-20260914T102030Z')
})

test('backupPath is pure: same arguments always return the same string', () => {
  const now = new Date('2026-09-14T10:20:30.000Z')
  const first = backupPath('/x/y.json', now)
  const second = backupPath('/x/y.json', now)
  assert.equal(first, second)
})

test('freeBackupPath steps past a name already taken on disk', () => {
  const root = mkdtempSync(join(tmpdir(), 'dev-loop-fs-'))
  const target = join(root, 'out', 'taken.json')
  mkdirSync(join(root, 'out'), { recursive: true })
  writeFileSync(target, 'live\n')
  const first = freeBackupPath(target)
  writeFileSync(first, 'older\n')
  assert.equal(freeBackupPath(target), `${first}-2`)
})
