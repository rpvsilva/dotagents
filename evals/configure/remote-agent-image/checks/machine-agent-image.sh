#!/usr/bin/env bash
# Passes when the machine Level file gives epic-implementer its own image in remote.images, and leaves remote.image
# (every other agent's) unset.
set -euo pipefail
machine="$EVAL_HOME/.config/dotagents/settings.json"
jq -e '.remote.images == ["epic-implementer=ghcr.io/example/runner-py:1"] and (.remote.image == null)' "$machine" >/dev/null
