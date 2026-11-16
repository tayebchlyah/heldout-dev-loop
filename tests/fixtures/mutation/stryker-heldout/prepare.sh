#!/bin/bash
# Usage: run by scripts/mutation_canary.sh in the built repo; installs the pinned tools, moves the held-out test.
set -eu
npm install --no-audit --no-fund
mkdir -p tests/acceptance
git mv tests/heldout/price.test.ts tests/acceptance/price.test.ts
git commit -q -m held-out
