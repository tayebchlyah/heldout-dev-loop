#!/usr/bin/env node
import { createInterface } from 'node:readline/promises'
import { stdin, stdout, argv, env, exit } from 'node:process'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { homedir } from 'node:os'
import { parseArgs } from '../lib/args.mjs'
import { buildConfig, writeConfig } from '../lib/config.mjs'
import { buildPlan, runPlan, assertLinkSource } from '../lib/plan.mjs'

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')

async function main() {
  const flags = parseArgs(argv.slice(2))
  const home = env.HOME ?? homedir()

  const repoRoot = flags.link ? process.cwd() : packageRoot
  if (flags.link) assertLinkSource(repoRoot)

  const rl = flags.yes ? null : createInterface({ input: stdin, output: stdout })
  const ask = async (question, fallback) => {
    const answer = await rl.question(`${question} [${fallback}]: `)
    return answer.trim() === '' ? fallback : answer.trim()
  }

  const config = await buildConfig({ flags, home, env, ask })
  rl?.close()

  const configResult = writeConfig(home, config, { dryRun: flags.dryRun })
  const actions = buildPlan({ repoRoot, home, harnesses: config.harnesses, mode: flags.link ? 'link' : 'copy' })
  const reports = runPlan(actions, { dryRun: flags.dryRun, home })

  report(
    [{ target: configResult.path, action: configResult.changed ? 'write' : 'skip', backup: null, added: [], skipped: [] }, ...reports],
    flags,
    home,
    config,
  )
}

function report(reports, flags, home, config) {
  const short = (path) => path.replace(home, '~')
  const changed = reports.filter((entry) => entry.action !== 'skip')

  console.log(flags.dryRun ? 'dry run, nothing written' : 'installed')
  console.log(`mode: ${flags.link ? 'link' : 'copy'}`)

  // Ruling R1: config.vault can be null. The installer still succeeds, but
  // says how to set it, since a silent null is easy to miss.
  if (config.vault === null) {
    console.log('vault not set; pass --vault=<path> or set DEV_LOOP_VAULT to set it')
  }

  for (const entry of changed) {
    console.log(`  ${entry.action.padEnd(7)} ${short(entry.target)}`)
    if (entry.backup) console.log(`          backup ${short(entry.backup)}`)
    for (const added of entry.added) console.log(`          + ${added}`)
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

  console.log(`${changed.length} changed, ${reports.length - changed.length} unchanged`)
}

main().catch((error) => {
  console.error(error.message)
  exit(1)
})
