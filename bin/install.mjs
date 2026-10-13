#!/usr/bin/env node
import { createInterface } from 'node:readline/promises'
import { stdin, stdout, argv, env, exit } from 'node:process'
import { fileURLToPath } from 'node:url'
import { dirname, relative, resolve } from 'node:path'
import { homedir } from 'node:os'
import { parseArgs } from '../lib/args.mjs'
import { buildConfig, writeConfig } from '../lib/config.mjs'
import { buildPlan, runPlan, assertLinkSource } from '../lib/plan.mjs'
import { runInit } from '../lib/project.mjs'

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')

const USAGE = `usage: dev-loop <command> [flags]
  init              install the dev-loop kit into this repository
  upgrade           update the kit in this repository
  uninstall-global  remove the 1.4 global install
  --global          run the 1.4 global install (1.5.x only)`

const notImplemented = (name) => () => {
  throw new Error(`${name} is not implemented`)
}
const runUpgrade = notImplemented('upgrade')
const runUninstallGlobal = notImplemented('uninstall-global')

async function main() {
  const flags = parseArgs(argv.slice(2))
  const home = env.HOME ?? homedir()

  if (flags.command === null && !flags.global) {
    console.error(USAGE)
    exit(1)
  }
  if (flags.command !== null) {
    const rl = flags.yes ? null : createInterface({ input: stdin, output: stdout })
    const ctx = { flags, home, cwd: process.cwd(), packageRoot, ask: asker(rl) }
    const runners = { init: runInit, upgrade: runUpgrade, 'uninstall-global': runUninstallGlobal }
    let reports
    try {
      reports = await runners[flags.command](ctx)
    } finally {
      rl?.close()
    }
    console.log(flags.dryRun ? 'dry run, nothing written' : 'installed')
    printReports(reports, flags, (path) => relative(ctx.cwd, path))
    return
  }

  const repoRoot = flags.link ? process.cwd() : packageRoot
  if (flags.link) assertLinkSource(repoRoot)

  const rl = flags.yes ? null : createInterface({ input: stdin, output: stdout })
  const config = await buildConfig({ flags, home, env, ask: asker(rl) })
  rl?.close()

  const configResult = writeConfig(home, config, { dryRun: flags.dryRun })
  const actions = buildPlan({ repoRoot, home, harnesses: config.harnesses, mode: flags.link ? 'link' : 'copy' })
  const reports = runPlan(actions, { dryRun: flags.dryRun, home })

  report(
    [{ target: configResult.path, action: configResult.changed ? 'write' : 'skip', backup: configResult.backup, added: [], skipped: [], notes: [] }, ...reports],
    flags,
    home,
    config,
  )
}

// The line iterator buffers input, so piped answers that arrive before their
// question are not lost (rl.question drops them). At the end of the input,
// every question takes its fallback.
function asker(rl) {
  if (rl === null) return null
  const lines = rl[Symbol.asyncIterator]()
  return async (question, fallback) => {
    stdout.write(`${question} [${fallback}]: `)
    const { value, done } = await lines.next()
    const answer = done ? '' : value.trim()
    return answer === '' ? fallback : answer
  }
}

function report(reports, flags, home, config) {
  console.log(flags.dryRun ? 'dry run, nothing written' : 'installed')
  console.log(`mode: ${flags.link ? 'link' : 'copy'}`)

  // Ruling R1: config.vault can be null. The installer still succeeds, but
  // says how to set it, since a silent null is easy to miss.
  if (config.vault === null) {
    console.log('vault not set; pass --vault=<path> or set DEV_LOOP_VAULT to set it')
  }

  printReports(reports, flags, (path) => path.replace(home, '~'))
}

function printReports(reports, flags, short) {
  const changed = reports.filter((entry) => entry.action !== 'skip')
  for (const entry of changed) {
    console.log(`  ${entry.action.padEnd(7)} ${short(entry.target)}`)
    if (entry.backup) console.log(`          backup ${short(entry.backup)}`)
    for (const added of entry.added) console.log(`          + ${added}`)
  }

  // A shape this installer cannot merge (a hand-edited hook group with no
  // hooks array) is left alone rather than aborting the run, so say which
  // file it was and what was wrong with it.
  for (const entry of reports) {
    for (const note of entry.notes ?? []) {
      console.log(`  ${short(entry.target)}: ${note}`)
    }
  }

  // Ruling R9: when a Codex config merge skips a table the target already
  // declares, say so explicitly, naming the file and the table, so a user
  // whose [features] disables hooks understands why check_setup.py later
  // reports hooks as not enabled instead of dev-loop silently overriding it.
  for (const entry of reports) {
    for (const table of entry.skipped) {
      console.log(`  kept your own ${table} in ${short(entry.target)}; dev-loop's version was not applied`)
    }
  }

  console.log(`${changed.length} ${flags.dryRun ? 'would change' : 'changed'}, ${reports.length - changed.length} unchanged`)
}

main().catch((error) => {
  console.error(error.message)
  exit(1)
})
