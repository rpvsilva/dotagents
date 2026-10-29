#!/usr/bin/env bash
# Helpers sourced by the remote-agent backends in this folder; not a backend itself.

say() { echo "remote-agent: $*" >&2; }

# Quotes one word for a POSIX shell: wrapped in single quotes, each ' written as '\''.
sq() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }
