#!/usr/bin/env bash
# Passes when pass A wrote a non-empty areas.md in the run folder.
set -euo pipefail
[ -s "$EVAL_SCRATCH/areas.md" ]
