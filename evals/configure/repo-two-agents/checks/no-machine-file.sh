#!/usr/bin/env bash
# Passes when no machine Level file was written.
set -euo pipefail
machine="$EVAL_HOME/.config/dotagents/settings.json"
[ ! -e "$machine" ]
