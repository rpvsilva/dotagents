#!/usr/bin/env bash
# Shared by the hooks in this folder: source it, never run it.

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
