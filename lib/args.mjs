const HARNESSES = ['claude', 'codex', 'both']

export function parseArgs(argv) {
  const flags = { yes: false, dryRun: false, link: false, harness: null, vault: null }
  for (const arg of argv) {
    if (arg === '--yes' || arg === '-y') flags.yes = true
    else if (arg === '--dry-run') flags.dryRun = true
    else if (arg === '--link') flags.link = true
    else if (arg.startsWith('--harness=')) flags.harness = arg.slice('--harness='.length)
    else if (arg.startsWith('--vault=')) flags.vault = arg.slice('--vault='.length)
    else throw new Error(`unknown flag: ${arg}`)
  }
  if (flags.harness !== null && !HARNESSES.includes(flags.harness)) {
    throw new Error('harness must be claude, codex or both')
  }
  return flags
}
