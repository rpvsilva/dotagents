#!/usr/bin/env bash
# Passes when no repo Level file was written and the repo is as the fixture left it.
set -euo pipefail
[ ! -e "$EVAL_WORK/.agents/settings.json" ]
[ -z "$(git -C "$EVAL_WORK" status --porcelain)" ]
[ "$(git -C "$EVAL_WORK" rev-list --count HEAD)" = 1 ]
