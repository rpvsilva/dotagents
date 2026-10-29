#!/usr/bin/env bash
# Keeps secrets out of the agent's context and out of commits, in every repo:
# - blocks a file tool on a secret file, and a shell command naming one (.env, .env.*, *.env, *.pem,
#   *.key, *.p12, *.pfx, SSH private keys, .netrc, .aws/credentials, anything under secrets/);
#   examples (.env.example, .env.sample, .env.template) pass;
# - blocks `git commit` when the changes it commits add a secret file, or a line that looks like a
#   token or private key. The reason names the file, never the secret.
# Quoted text with spaces and heredoc bodies are prose, so they are not scanned for paths.
# A guardrail against mistakes, not a security boundary: the human can still run anything themselves.
# Input: the command as $1, or a hook's JSON on stdin with .tool_input.command or a path
# (.tool_input.file_path / .path / .notebook_path, or Cursor's top-level .file_path), and optional .cwd.
# Exit 2 with the reason on stderr blocks; exit 0 lets it run.
set -euo pipefail

if [ $# -gt 0 ]; then
  cmd="$1"
  path=""
  cwd="$PWD"
else
  input="$(cat)"
  cmd="$(jq -r '.tool_input.command // .command // empty' <<<"$input")"
  path="$(jq -r '.tool_input.file_path // .tool_input.notebook_path // .tool_input.path // .file_path // empty' <<<"$input")"
  cwd="$(jq -r '.cwd // empty' <<<"$input")"
  cwd="${cwd:-$PWD}"
fi

secret_re='(^|/)(\.env(\.[^/]*)?|[^/]+\.(env|pem|key|p12|pfx)|id_(rsa|dsa|ecdsa|ed25519)|\.netrc|\.aws/credentials)$|(^|/)secrets/'
example_re='\.(example|sample|template)$'
is_secret() { [[ "$1" =~ $secret_re ]] && ! [[ "$1" =~ $example_re ]]; }

block() {
  echo "guard-secrets: $1 Ask the human to run it themselves, or to give you the value another way." >&2
  exit 2
}

if [ -n "$path" ] && is_secret "$path"; then
  block "$path looks like a secret file; agents do not read or edit it."
fi
[ -n "$cmd" ] || exit 0

# shellcheck source=SCRIPTDIR/lib.sh disable=SC1091
. "$(dirname "$0")/lib.sh"
# Heredoc bodies are data (a file being written), never a command to judge.
cmd="$(strip_heredocs "$cmd")"

s='[[:space:]]'
start="(^|[;&|(]|$s)"

# Paths named in the command, outside quoted prose.
scan="$(sed -E "s/\"[^\"]*${s}[^\"]*\"//g; s/'[^']*${s}[^']*'//g" <<<"$cmd")"
set -f
for word in $(tr '"'"'"'()<>|;&,=' ' ' <<<"$scan"); do
  if is_secret "$word"; then
    block "the command names $word, which looks like a secret file."
  fi
done

if [[ "$cmd" =~ ${start}git$s+([^;&|]*$s)?commit($s|$) ]]; then
  dir="$(target_dir "$cmd" "$cwd")"
  git -C "$dir" rev-parse --git-dir >/dev/null 2>&1 || exit 0
  # `commit -a` also commits every tracked change; otherwise only what is staged.
  base=(--cached)
  if [[ "$cmd" =~ ${start}commit$s([^;&|]*$s)?(-a[a-zA-Z]*|--all)($s|$) ]]; then base=(HEAD); fi
  while read -r f; do
    if is_secret "$f"; then block "the commit adds $f, which looks like a secret file."; fi
  done < <(git -C "$dir" diff "${base[@]}" --name-only --diff-filter=AR 2>/dev/null)
  token_re='-----BEGIN ([A-Z]+ )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,}|AKIA[0-9A-Z]{16}|xox[abprs]-[A-Za-z0-9-]{10,}|sk-ant-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{35}'
  hit="$(git -C "$dir" diff "${base[@]}" -U0 --no-color 2>/dev/null |
    awk '/^\+\+\+ / {f = substr($0, 7); next} /^\+/ {print f "\t" $0}' | grep -E -m1 -e "$token_re" | cut -f1 || true)"
  if [ -n "$hit" ]; then block "the commit adds a line in $hit that looks like a token or private key."; fi
fi
exit 0
