#!/usr/bin/env bash
# The SSH_ASKPASS program of the ssh backend (scripts/remote-backends/lib.sh, ssh_client): ssh runs it with its prompt
# as $1 and reads the answer from stdout, so a password or key passphrase never shows in ssh's arguments or a terminal.
# Answers a password prompt with the SSH_PASSWORD Secret and a passphrase prompt with SSH_KEY_PASSPHRASE (remote.env,
# else the environment: scripts/remote-settings.sh). Any other prompt (an unknown host key's yes/no) and a Secret not
# set get no answer and exit 1, so ssh fails as BatchMode would rather than accept a host key or hang.
# `remote-ssh-askpass.sh --any` exits 0 when either Secret is set: ssh_client then turns password logins on.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=remote-settings.sh disable=SC1091
. "$REPO/scripts/remote-settings.sh"

prompt="${1:-}"
if [ "$prompt" = --any ]; then
  [ -n "$(remote_secret SSH_PASSWORD)" ] || [ -n "$(remote_secret SSH_KEY_PASSPHRASE)" ]
  exit
fi
# Lowercased: ssh asks "Enter passphrase for key '...': ", "user@host's password: " or "(user@host) Password: ".
case "$(printf '%s' "$prompt" | tr '[:upper:]' '[:lower:]')" in
  *passphrase*) answer="$(remote_secret SSH_KEY_PASSPHRASE)" ;;
  *password*) answer="$(remote_secret SSH_PASSWORD)" ;;
  *) exit 1 ;;
esac
[ -n "$answer" ] || exit 1
printf '%s\n' "$answer"
