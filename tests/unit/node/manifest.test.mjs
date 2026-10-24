import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtempSync, mkdirSync, rmSync, symlinkSync, unlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { sha256, buildManifest, readManifest, compareManifest } from '../../../lib/manifest.mjs'

const kit = {
  files: [
    { path: '.dev-loop/hooks/claude/gate.sh', content: 'gate\n', executable: true },
    { path: '.agents/skills/dev-loop/SKILL.md', content: 'skill\n', executable: false },
  ],
  links: [
    { path: '.claude/skills/dev-loop', target: '../../.agents/skills/dev-loop' },
    { path: '.claude/skills/pause', target: '../../.agents/skills/pause' },
  ],
}

function writeKit(repo) {
  for (const file of kit.files) {
    mkdirSync(dirname(join(repo, file.path)), { recursive: true })
    writeFileSync(join(repo, file.path), file.content)
  }
  mkdirSync(join(repo, '.claude', 'skills'), { recursive: true })
  for (const link of kit.links) symlinkSync(link.target, join(repo, link.path))
}

test('sha256 names the algorithm and hashes the utf8 content', () => {
  assert.equal(sha256('é'), `sha256:${createHash('sha256').update(Buffer.from('é', 'utf8')).digest('hex')}`)
})

test('the manifest lists the version and each kit path, sorted, with the link', () => {
  const manifest = buildManifest('1.5.0', kit)
  assert.equal(manifest.version, '1.5.0')
  assert.deepEqual(Object.keys(manifest.files), [
    '.agents/skills/dev-loop/SKILL.md',
    '.claude/skills/dev-loop',
    '.claude/skills/pause',
    '.dev-loop/hooks/claude/gate.sh',
  ])
  assert.equal(manifest.files['.claude/skills/dev-loop'], 'link:../../.agents/skills/dev-loop')
  assert.equal(manifest.files['.dev-loop/hooks/claude/gate.sh'], sha256('gate\n'))
  assert.equal(manifest.files['.claude/skills/pause'], 'link:../../.agents/skills/pause')
  assert.equal(buildManifest('1.5.0', { ...kit, links: [] }).files['.claude/skills/dev-loop'], undefined)
})

test('readManifest returns the parsed file, or null when there is none', () => {
  const repo = mkdtempSync(join(tmpdir(), 'manifest-'))
  assert.equal(readManifest(repo), null)
  mkdirSync(join(repo, '.dev-loop'))
  writeFileSync(join(repo, '.dev-loop', 'MANIFEST.json'), JSON.stringify({ version: '1.5.0', files: {} }))
  assert.deepEqual(readManifest(repo), { version: '1.5.0', files: {} })
})

test('compareManifest finds nothing on an untouched kit', () => {
  const repo = mkdtempSync(join(tmpdir(), 'manifest-'))
  writeKit(repo)
  assert.deepEqual(compareManifest(repo, buildManifest('1.5.0', kit)), { changed: [], missing: [] })
})

test('compareManifest reports a changed byte, a deleted file and a changed link target', () => {
  const repo = mkdtempSync(join(tmpdir(), 'manifest-'))
  writeKit(repo)
  writeFileSync(join(repo, '.dev-loop/hooks/claude/gate.sh'), 'gatE\n')
  rmSync(join(repo, '.agents/skills/dev-loop/SKILL.md'))
  unlinkSync(join(repo, '.claude/skills/dev-loop'))
  symlinkSync('../../elsewhere', join(repo, '.claude/skills/dev-loop'))
  assert.deepEqual(compareManifest(repo, buildManifest('1.5.0', kit)), {
    changed: ['.claude/skills/dev-loop', '.dev-loop/hooks/claude/gate.sh'],
    missing: ['.agents/skills/dev-loop/SKILL.md'],
  })
})

test('compareManifest calls a missing link missing, and a real folder in its place changed', () => {
  const repo = mkdtempSync(join(tmpdir(), 'manifest-'))
  writeKit(repo)
  unlinkSync(join(repo, '.claude/skills/dev-loop'))
  const manifest = buildManifest('1.5.0', kit)
  assert.deepEqual(compareManifest(repo, manifest), { changed: [], missing: ['.claude/skills/dev-loop'] })
  mkdirSync(join(repo, '.claude/skills/dev-loop'))
  assert.deepEqual(compareManifest(repo, manifest), { changed: ['.claude/skills/dev-loop'], missing: [] })
})

test('compareManifest calls a folder in place of a kit file changed', () => {
  const repo = mkdtempSync(join(tmpdir(), 'manifest-'))
  writeKit(repo)
  rmSync(join(repo, '.dev-loop/hooks/claude/gate.sh'))
  mkdirSync(join(repo, '.dev-loop/hooks/claude/gate.sh'))
  assert.deepEqual(compareManifest(repo, buildManifest('1.5.0', kit)).changed, ['.dev-loop/hooks/claude/gate.sh'])
})
