import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, lstatSync, symlinkSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { placeFile, placeDir, renderTemplate, backupPath } from '../lib/fsops.mjs'

function sandbox() {
  const root = mkdtempSync(join(tmpdir(), 'dev-loop-fs-'))
  mkdirSync(join(root, 'src'), { recursive: true })
  mkdirSync(join(root, 'dst'), { recursive: true })
  writeFileSync(join(root, 'src', 'a.sh'), '#!/bin/sh\necho {{HOME}}\n')
  return root
}

test('renderTemplate replaces every home token', () => {
  assert.equal(renderTemplate('a {{HOME}} b {{HOME}}', { home: '/h' }), 'a /h b /h')
})

test('backupPath appends a timestamp suffix', () => {
  assert.equal(backupPath('/x/y.json', new Date('2026-09-14T10:20:30Z')), '/x/y.json.bak-20260914T102030Z')
})

test('copy mode creates the file and sets the executable bit', () => {
  const root = sandbox()
  const target = join(root, 'dst', 'a.sh')
  const result = placeFile({ source: join(root, 'src', 'a.sh'), target, mode: 'copy', dryRun: false, executable: true })
  assert.equal(result.action, 'create')
  assert.equal(result.backup, null)
  assert.ok(lstatSync(target).mode & 0o111)
})

test('copy mode backs up an existing different file', () => {
  const root = sandbox()
  const target = join(root, 'dst', 'a.sh')
  writeFileSync(target, 'old\n')
  const result = placeFile({ source: join(root, 'src', 'a.sh'), target, mode: 'copy', dryRun: false })
  assert.equal(result.action, 'replace')
  assert.ok(result.backup)
  assert.equal(readFileSync(result.backup, 'utf8'), 'old\n')
})

test('copy mode skips an identical file', () => {
  const root = sandbox()
  const target = join(root, 'dst', 'a.sh')
  placeFile({ source: join(root, 'src', 'a.sh'), target, mode: 'copy', dryRun: false })
  const again = placeFile({ source: join(root, 'src', 'a.sh'), target, mode: 'copy', dryRun: false })
  assert.equal(again.action, 'skip')
})

test('link mode creates a symbolic link', () => {
  const root = sandbox()
  const target = join(root, 'dst', 'a.sh')
  const result = placeFile({ source: join(root, 'src', 'a.sh'), target, mode: 'link', dryRun: false })
  assert.equal(result.action, 'create')
  assert.ok(lstatSync(target).isSymbolicLink())
})

test('a dry run writes nothing and reports create, not replace', () => {
  const root = sandbox()
  const target = join(root, 'dst', 'a.sh')
  const result = placeFile({ source: join(root, 'src', 'a.sh'), target, mode: 'copy', dryRun: true })
  assert.equal(result.action, 'create')
  assert.equal(result.backup, null)
  assert.equal(existsSync(target), false)
})

test('a dry run against an existing file reports replace with no backup path, and writes nothing', () => {
  const root = sandbox()
  const target = join(root, 'dst', 'a.sh')
  writeFileSync(target, 'old\n')
  const result = placeFile({ source: join(root, 'src', 'a.sh'), target, mode: 'copy', dryRun: true })
  assert.equal(result.action, 'replace')
  assert.equal(result.backup, null)
  assert.equal(readFileSync(target, 'utf8'), 'old\n')
})

test('placeDir copies a whole tree and backs up the old one', () => {
  const root = sandbox()
  mkdirSync(join(root, 'src', 'tree', 'deep'), { recursive: true })
  writeFileSync(join(root, 'src', 'tree', 'deep', 'f.txt'), 'hello\n')
  const target = join(root, 'dst', 'tree')
  mkdirSync(target, { recursive: true })
  writeFileSync(join(target, 'stale.txt'), 'stale\n')

  const result = placeDir({ source: join(root, 'src', 'tree'), target, mode: 'copy', dryRun: false })
  assert.equal(result.action, 'replace')
  assert.ok(existsSync(join(result.backup, 'stale.txt')))
  assert.equal(readFileSync(join(target, 'deep', 'f.txt'), 'utf8'), 'hello\n')
  assert.equal(existsSync(join(target, 'stale.txt')), false)
})

test('placeFile with home set renders {{HOME}}', () => {
  const root = sandbox()
  const target = join(root, 'dst', 'a.sh')
  const result = placeFile({ source: join(root, 'src', 'a.sh'), target, mode: 'copy', dryRun: false, home: '/Users/me' })
  assert.equal(result.action, 'create')
  assert.equal(readFileSync(target, 'utf8'), '#!/bin/sh\necho /Users/me\n')
})

test('placeFile with home null writes the bytes unchanged, token included', () => {
  const root = sandbox()
  const target = join(root, 'dst', 'a.sh')
  const result = placeFile({ source: join(root, 'src', 'a.sh'), target, mode: 'copy', dryRun: false, home: null })
  assert.equal(result.action, 'create')
  assert.equal(readFileSync(target, 'utf8'), '#!/bin/sh\necho {{HOME}}\n')
})

test('a target that is a symbolic link to a missing file is replaced without throwing', () => {
  const root = sandbox()
  const target = join(root, 'dst', 'a.sh')
  symlinkSync(join(root, 'src', 'does-not-exist.sh'), target)
  const result = placeFile({ source: join(root, 'src', 'a.sh'), target, mode: 'copy', dryRun: false })
  assert.equal(result.action, 'replace')
  assert.equal(readFileSync(target, 'utf8'), '#!/bin/sh\necho {{HOME}}\n')
})

test('placeDir in link mode is a no-op skip when the target already links to the source', () => {
  const root = sandbox()
  mkdirSync(join(root, 'src', 'tree'), { recursive: true })
  const target = join(root, 'dst', 'tree')
  symlinkSync(join(root, 'src', 'tree'), target)
  const result = placeDir({ source: join(root, 'src', 'tree'), target, mode: 'link', dryRun: false })
  assert.equal(result.action, 'skip')
  assert.equal(result.backup, null)
})

test('two placements in the same millisecond produce distinct backup paths', () => {
  const root = sandbox()
  const target = join(root, 'dst', 'a.sh')
  const now = new Date('2026-09-14T10:20:30.000Z')
  const first = backupPath(target, now)
  const second = backupPath(target, now)
  assert.notEqual(first, second)
})
