#!/bin/bash
# Usage: run by scripts/mutation_canary.sh in the built repo; installs the pinned tools of web/, no lock file.
set -eu
cd web
npm install --no-audit --no-fund
