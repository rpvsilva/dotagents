#!/usr/bin/env bash
# Prints the checks that cover the changed files, one per line, as mapped in tests/suites.txt.
# Usage: tests/affected.sh [--all | --base <ref> | -- <path>...]
#   (none)       files changed since the merge base with origin/main: committed, uncommitted and untracked
#   --base <ref> the same, against <ref>
#   -- <path>... exactly these files (e.g. the ones a merge conflict touched)
#   --all        every check
# With no merge base to compare against (no origin/main, a shallow clone), it prints every check.
# Pure bash and git, no grep or sort: tests/run.test.sh runs it with a minimal PATH.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

checks=() patterns=()
while read -r check rest; do
  case "$check" in '' | '#'*) continue ;; esac
  checks+=("$check")
  patterns+=("${rest:-}")
done <tests/suites.txt

all() {
  local c
  for c in "${checks[@]}"; do [ "$c" = all ] || echo "$c"; done
  exit 0
}

base=origin/main files=() given=""
case "${1:-}" in
  --all) all ;;
  --base) base="${2:?--base needs a ref}" ;;
  --) shift; files=("$@") given=1 ;;
  '') ;;
  *) echo "usage: tests/affected.sh [--all | --base <ref> | -- <path>...]" >&2; exit 2 ;;
esac

if [ -z "$given" ]; then
  since="$(git merge-base "$base" HEAD 2>/dev/null)" || all
  while IFS= read -r f; do files+=("$f"); done < <(git diff --name-only "$since"; git ls-files --others --exclude-standard)
fi

# Patterns are split unquoted below; no filename expansion, or `*.sh` would match files in the repo root.
set -f
selected=" "
for f in ${files[@]+"${files[@]}"}; do
  for i in "${!checks[@]}"; do
    check="${checks[$i]}"
    hit=""
    [ "$f" = "tests/$check" ] && hit=1
    # shellcheck disable=SC2053
    for p in ${patterns[$i]}; do [[ $f == $p ]] && hit=1; done
    [ -n "$hit" ] || continue
    [ "$check" = all ] && all
    selected+="$check "
  done
done

for c in "${checks[@]}"; do
  case "$selected" in *" $c "*) echo "$c" ;; esac
done
