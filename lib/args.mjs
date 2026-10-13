const COMMANDS = ['init', 'upgrade', 'uninstall-global']
const HARNESSES = ['claude', 'codex', 'both']

export function parseArgs(argv) {
  const flags = {
    command: null, global: false, yes: false, dryRun: false, link: false, force: false, harness: null, vault: null, notes: null,
  }
  for (const arg of argv) {
    if (COMMANDS.includes(arg)) {
      if (flags.command !== null) throw new Error('one command at a time')
      flags.command = arg
    } else if (arg === '--global') flags.global = true
    else if (arg === '--force') flags.force = true
    else if (arg.startsWith('--notes=')) flags.notes = arg.slice('--notes='.length)
    else if (arg === '--yes' || arg === '-y') flags.yes = true
    else if (arg === '--dry-run') flags.dryRun = true
    else if (arg === '--link') flags.link = true
    else if (arg.startsWith('--harness=')) flags.harness = arg.slice('--harness='.length)
    else if (arg.startsWith('--vault=')) flags.vault = arg.slice('--vault='.length)
    else throw new Error(`unknown flag: ${arg}`)
  }
  if (flags.command !== null && flags.global) throw new Error('--global cannot be combined with a command')
  if (flags.command !== null && flags.link) throw new Error('--link works only with --global')
  if (flags.harness !== null && !HARNESSES.includes(flags.harness)) {
    throw new Error('harness must be claude, codex or both')
  }
  return flags
}
