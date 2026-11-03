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

# Sets ssh_cmd to the ssh client and options for every call to the SSH backend's host (the host and command follow):
# `ssh -T`, then REMOTE_SSH_KEY's key file alone when set (`-i <file> -o IdentitiesOnly=yes`; a leading ~/ is this
# machine's home). With no SSH_PASSWORD or SSH_KEY_PASSPHRASE Secret, `-o BatchMode=yes`: keys from the agent or
# ~/.ssh/config only, never a prompt. With either, keys are still tried first, then one password try, every prompt
# answered by scripts/remote-ssh-askpass.sh (SSH_ASKPASS_REQUIRE=force: never the terminal, needs OpenSSH 8.4+), which
# answers nothing else, so an unknown host key still fails.
ssh_client() {
  local askpass key="${REMOTE_SSH_KEY:-}"
  askpass="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/remote-ssh-askpass.sh"
  ssh_cmd=(ssh -T)
  if "$askpass" --any; then
    ssh_cmd=(env SSH_ASKPASS="$askpass" SSH_ASKPASS_REQUIRE=force DISPLAY="${DISPLAY:-:0}" ssh -T -o BatchMode=no
      -o NumberOfPasswordPrompts=1)
  else
    ssh_cmd+=(-o BatchMode=yes)
  fi
  # shellcheck disable=SC2088 # a literal ~/, expanded here
  case "$key" in "~/"*) key="$HOME/${key#\~/}" ;; esac
  [ -z "$key" ] || ssh_cmd+=(-i "$key" -o IdentitiesOnly=yes)
}
