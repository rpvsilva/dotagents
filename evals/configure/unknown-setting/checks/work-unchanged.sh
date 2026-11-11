#!/usr/bin/env bash
# Passes when the repo is as the fixture left it: nothing changed, added or committed.
set -euo pipefail
[ -z "$(git -C "$EVAL_WORK" status --porcelain)" ]
[ "$(git -C "$EVAL_WORK" rev-list --count HEAD)" = 1 ]
