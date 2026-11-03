#!/usr/bin/env bash
# Shared by the hooks in this folder and scripts/remote-agent*.sh: source it, never run it.

# The secret rules come from secret-patterns.txt beside this file (see its header):
# secret_re (secret file paths), example_re (examples of them, which pass) and token_re (tokens and
# private keys in text). Without them it stops (exit 2): an empty regex would match every path.
# Read in plain bash: every hook call sources this file, and each extra process costs it time.
# A trailing CR is dropped (a CRLF checkout on Windows), else every rule would end in it.
case "${BASH_SOURCE[0]}" in */*) secret_patterns="${BASH_SOURCE[0]%/*}" ;; *) secret_patterns=. ;; esac
secret_patterns="$secret_patterns/secret-patterns.txt"
secret_re="" example_re="" token_re=""
if [ -f "$secret_patterns" ] && [ -r "$secret_patterns" ]; then
  while IFS= read -r _secret_line || [ -n "$_secret_line" ]; do
    _secret_line="${_secret_line%$'\r'}"
    case "$_secret_line" in
      'path '*) secret_re="${secret_re:+$secret_re|}${_secret_line#path }" ;;
      'example '*) example_re="${example_re:+$example_re|}${_secret_line#example }" ;;
      'token '*) token_re="${token_re:+$token_re|}${_secret_line#token }" ;;
    esac
  done <"$secret_patterns"
  unset _secret_line
fi
if [ -z "$secret_re" ] || [ -z "$example_re" ] || [ -z "$token_re" ]; then
  echo "lib.sh: cannot read secret-patterns.txt ($secret_patterns)" >&2
  exit 2
fi

# True when path $1 looks like a secret file, unless it is an example.
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
