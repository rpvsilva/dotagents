#!/usr/bin/env bash
# Shared by the hooks in this folder and scripts/remote-agent*.sh: source it, never run it.

# True when path $1 looks like a secret file (.env, keys, certificates, credentials, secrets/),
# unless it is an example (.example, .sample, .template).
secret_re='(^|/)(\.env(\.[^/]*)?|[^/]+\.(env|pem|key|p12|pfx)|id_(rsa|dsa|ecdsa|ed25519)|\.netrc|\.aws/credentials)$|(^|/)secrets/'
example_re='\.(example|sample|template)$'
is_secret() { [[ "$1" =~ $secret_re ]] && ! [[ "$1" =~ $example_re ]]; }

# Prints the directory command $1 runs in, starting from $2: a command that moves into another
# repo (`cd <dir> && ...`) is judged by that dir. Only one cd to an existing dir counts;
# anything less clear stays at $2.
target_dir() {
  local cmd="$1" cwd="$2" s='[[:space:]]' dir
  local cd_re="(^|[;&|(]|$s)cd$s+([^;&|[:space:]]+)"
  if [ "$(grep -cE "$cd_re" <<<"${cmd//[;&|]/$'\n'}")" -eq 1 ] && [[ "$cmd" =~ $cd_re ]]; then
    dir="${BASH_REMATCH[2]//[\"\']/}"
    dir="${dir/#\~/$HOME}"
    dir="$(cd "$cwd" 2>/dev/null && cd "$dir" 2>/dev/null && pwd)" && cwd="$dir"
  fi
  echo "$cwd"
}

# Prints command $1 without heredoc bodies: the lines after `<<WORD` (also `<<-WORD`, `<<'WORD'`,
# `<<"WORD"`) up to the line that is WORD. Their text is data written somewhere, not a command.
# A here-string (`<<<`) is not a heredoc. Only the first heredoc on a line is recognised.
strip_heredocs() {
  awk -v q="'" '
    BEGIN { re = "(^|[^<])<<-?[ \t]*[\"" q "]?[A-Za-z_][A-Za-z0-9_]*[\"" q "]?" }
    end != "" { line = $0; sub(/^\t+/, "", line); if (line == end) end = ""; next }
    {
      print
      if (match($0, re)) {
        w = substr($0, RSTART, RLENGTH)
        sub(/^[^<]?<<-?[ \t]*/, "", w)
        gsub(/["\047]/, "", w)
        end = w
      }
    }' <<<"$1"
}
