import { execFileSync } from 'node:child_process'

// Parses `text` with Python's tomllib and throws if it is not valid TOML.
// The installer's own merge cannot prove a file still parses; only a real
// TOML parser can, and every machine that runs this suite already has
// python3 (the skill's own test suite needs it).
export function assertValidToml(text) {
  execFileSync('python3', ['-c', 'import tomllib, sys; tomllib.loads(sys.stdin.read())'], { input: text })
}
