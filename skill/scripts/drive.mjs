#!/usr/bin/env node
// dev-loop drive runner: drives the routes of one feature map entry in Chromium.
// stdin: {"base_url", "storage_state" (absolute path or null), "routes", "evidence" (folder), "entry", "video" (folder),
// "playwright" (absolute path of the Playwright package folder)}.
// Records no video: every route result has `video` null.
// stdout: {"routes": [<route result>]}, exit 0. No Playwright at the input path: {"error"}, exit 1.
// Playwright comes from the input path only: no lookup, no Node parent walk, no NODE_PATH, no global install.
import { existsSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join } from 'node:path'

const VIEWPORT = { width: 1280, height: 800 }
const NAME_TIMEOUT_MS = 10_000

async function driveRoute(context, input, route, n) {
  const started = Date.now()
  const page = await context.newPage()
  const consoleErrors = []
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()) })
  page.on('pageerror', (error) => consoleErrors.push(String(error)))
  let status = null
  let error = null
  const missing = []
  try {
    const response = await page.goto(`${input.base_url.replace(/\/$/, '')}${route.path}`, { waitUntil: 'load' })
    status = response ? response.status() : null
    for (const name of route.names ?? []) {
      try {
        await page.getByRole(name.role, { name: name.name, exact: true }).first().waitFor({ state: 'visible', timeout: NAME_TIMEOUT_MS })
      } catch {
        missing.push({ role: name.role, name: name.name })
      }
    }
  } catch (exc) {
    error = String(exc.message ?? exc)
  }
  let screenshot = join(input.evidence, `${input.entry}-${n}.jpg`)
  try {
    await page.screenshot({ path: screenshot, fullPage: true, type: 'jpeg', quality: 70 })
  } catch {
    screenshot = null
  }
  await page.close()
  const ok = status !== null && status < 400 && missing.length === 0 && consoleErrors.length === 0 && error === null
  return {
    entry: input.entry, path: route.path, ok, status, missing, console_errors: consoleErrors, error, screenshot, video: null,
    seconds: Math.round((Date.now() - started) / 100) / 10,
  }
}

async function main() {
  const input = JSON.parse(readFileSync(0, 'utf8'))
  const dir = input.playwright
  if (typeof dir !== 'string' || !existsSync(dir)) {
    console.log(JSON.stringify({ error: `playwright not found: ${dir}` }))
    return 1
  }
  const { chromium } = createRequire(import.meta.url)(dir)
  const browser = await chromium.launch()
  try {
    const context = await browser.newContext({ viewport: VIEWPORT, storageState: input.storage_state ?? undefined })
    const routes = []
    for (const [i, route] of input.routes.entries()) routes.push(await driveRoute(context, input, route, i + 1))
    console.log(JSON.stringify({ routes }))
    return 0
  } finally {
    await browser.close()
  }
}

process.exitCode = await main()
