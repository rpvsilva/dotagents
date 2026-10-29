#!/usr/bin/env bash
# Helpers sourced by the remote-agent backends in this folder; not a backend itself.

say() { echo "remote-agent: $*" >&2; }

# Quotes one word for a POSIX shell: wrapped in single quotes, each ' written as '\''.
sq() {
  local q="'"
  printf "'%s'" "${1//$q/$q\\$q$q}"
}

# Quotes each word with sq and joins them with spaces: one command line for a remote shell of any flavour.
sq_argv() {
  local line="" w
  for w in "$@"; do line="$line${line:+ }$(sq "$w")"; done
  printf '%s' "$line"
}
