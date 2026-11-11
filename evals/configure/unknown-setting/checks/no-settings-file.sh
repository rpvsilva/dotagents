#!/usr/bin/env bash
# Passes when neither Level's settings file was written, and no file in the repo names the made-up key.
set -euo pipefail
machine="$EVAL_HOME/.config/dotagents/settings.json"
[ ! -e "$EVAL_WORK/.agents/settings.json" ]
[ ! -e "$machine" ]
! grep -rq --exclude-dir=.git coffee_breaks "$EVAL_WORK"
