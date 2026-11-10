#!/usr/bin/env bash
# Passes when the repo Level file is changed or new but not committed, and nothing was committed on top of the fixture.
set -euo pipefail
[ -n "$(git -C "$EVAL_WORK" status --porcelain -- .agents/settings.json)" ]
[ "$(git -C "$EVAL_WORK" rev-list --count HEAD)" = 1 ]
