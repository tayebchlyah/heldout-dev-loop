import { test, before, after } from 'node:test'
import assert from 'node:assert/strict'
import { spawn, execFileSync } from 'node:child_process'
import { createServer } from 'node:http'
import { existsSync, mkdirSync, readFileSync, symlinkSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { tempDir } from './helpers.mjs'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..')
const DRIVE = join(ROOT, 'skill', 'scripts', 'drive.mjs')

const PAGES = {
  '/ok': '<title>ok</title><h1>Clusters</h1>',
  '/app/prefixed': '<title>app ok</title><h1>Clusters</h1>',
  '/missing': '<title>missing</title><h1>Buckets</h1>',
  '/console': '<title>console</title><h1>Clusters</h1><script>console.error("boom")</script>',
  '/throw': '<title>throw</title><h1>Clusters</h1><script>throw new Error("uncaught boom")</script>',
}

let server
let baseUrl

before(async () => {
  server = createServer((req, res) => {
    if (req.url === '/cookie') {
      const in_ = /(^|;\s*)session=1/.test(req.headers.cookie ?? '')
      res.writeHead(200, { 'content-type': 'text/html' })
      return res.end(in_ ? '<h1>Clusters</h1>' : '<h1>Login</h1>')
    }
    const page = PAGES[req.url]
    res.writeHead(page ? 200 : 404, { 'content-type': 'text/html' })
    res.end(page ?? '<h1>Not found</h1>')
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  baseUrl = `http://127.0.0.1:${server.address().port}`
})

after(() => server.close())

function repo({ link = true } = {}) {
  const dir = tempDir('drive-')
  execFileSync('git', ['init', '-q'], { cwd: dir })
  if (link) symlinkSync(join(ROOT, 'node_modules'), join(dir, 'node_modules'))
  mkdirSync(join(dir, 'evidence'))
  return dir
}

// Async: the fixture server runs in this process, a sync spawn would block it.
function drive(cwd, input) {
  const env = { ...process.env }
  delete env.NODE_PATH
  const child = spawn('node', [DRIVE], { cwd, env })
  let stdout = ''
  let stderr = ''
  child.stdout.on('data', (chunk) => { stdout += chunk })
  child.stderr.on('data', (chunk) => { stderr += chunk })
  child.stdin.end(JSON.stringify(input))
  return new Promise((resolve) => child.on('close', (code) => resolve({ code, out: JSON.parse(stdout || '{}'), stderr })))
}

const CLUSTERS = [{ role: 'heading', name: 'Clusters' }]

function input(dir, routes, extra = {}) {
  return { base_url: baseUrl, storage_state: null, routes, evidence: join(dir, 'evidence'), entry: 'clusters', ...extra }
}

test('this repo has Playwright installed: run npm ci && npx playwright install chromium', () => {
  assert.ok(existsSync(join(ROOT, 'node_modules', 'playwright')), 'run npm ci && npx playwright install chromium')
})

test('drive.mjs checks status, names and console errors, one JPEG per route', async () => {
  const dir = repo()
  const { code, out, stderr } = await drive(dir, input(dir, [
    { path: '/ok', names: CLUSTERS },
    { path: '/missing', names: CLUSTERS },
    { path: '/console', names: CLUSTERS },
    { path: '/throw', names: CLUSTERS },
    { path: '/404', names: [] },
  ]))
  assert.equal(code, 0, stderr)
  const [ok, missing, console_, thrown, notFound] = out.routes
  assert.equal(ok.ok, true)
  assert.equal(ok.status, 200)
  assert.equal(ok.entry, 'clusters')
  assert.equal(ok.path, '/ok')
  assert.deepEqual(ok.missing, [])
  assert.deepEqual(ok.console_errors, [])
  assert.equal(ok.error, null)
  assert.equal(typeof ok.seconds, 'number')
  assert.equal(ok.screenshot, join(dir, 'evidence', 'clusters-1.jpg'))
  assert.deepEqual([...readFileSync(ok.screenshot).subarray(0, 3)], [0xff, 0xd8, 0xff])
  assert.equal(missing.ok, false)
  assert.deepEqual(missing.missing, CLUSTERS)
  assert.equal(missing.screenshot, join(dir, 'evidence', 'clusters-2.jpg'))
  assert.equal(console_.ok, false)
  assert.ok(console_.console_errors.some((e) => e.includes('boom')))
  assert.equal(thrown.ok, false)
  assert.ok(thrown.console_errors.some((e) => e.includes('uncaught boom')))
  assert.equal(notFound.status, 404)
  assert.equal(notFound.ok, false)
})

test('drive.mjs loads the storage state into the context', async () => {
  const dir = repo()
  const state = join(dir, 'state.json')
  writeFileSync(state, JSON.stringify({
    cookies: [{ name: 'session', value: '1', domain: '127.0.0.1', path: '/', expires: -1, httpOnly: false, secure: false, sameSite: 'Lax' }],
    origins: [],
  }))
  const { code, out } = await drive(dir, input(dir, [{ path: '/cookie', names: CLUSTERS }], { storage_state: state }))
  assert.equal(code, 0)
  assert.equal(out.routes[0].ok, true)
  assert.equal(out.routes[0].video, null)
})

test('drive.mjs finds no Playwright outside the four repo folders', async () => {
  const dir = repo({ link: false })
  const { code, out } = await drive(dir, input(dir, [{ path: '/ok', names: [] }]))
  assert.equal(code, 1)
  assert.match(out.error, /playwright/)
})

test('drive.mjs joins a route path to a base_url that has a path prefix', async () => {
  for (const base of [`${baseUrl}/app`, `${baseUrl}/app/`]) {
    const dir = repo()
    const { code, out, stderr } = await drive(dir, input(dir, [{ path: '/prefixed', names: CLUSTERS }], { base_url: base }))
    assert.equal(code, 0, stderr)
    assert.equal(out.routes[0].status, 200)
    assert.equal(out.routes[0].ok, true)
  }
})
