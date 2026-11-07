#!/usr/bin/env bash
# Runs the checks CI runs that cover the changed files, as tests/affected.sh picks them; its arguments pass through
# (--all for every check, -- <path>... for given files). Lint tools that are not installed are skipped with a warning.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
status=0

step() { echo "== $1"; shift; "$@" || status=1; }

checks=" $(tests/affected.sh "$@" | tr '\n' ' ')" || exit 1
skipped=""
want() { case "$checks" in *" $1 "*) ;; *) skipped="$skipped $1"; return 1 ;; esac; }

# Lint at the shellcheck version pinned in scripts/shellcheck-version, the one CI runs: the local binary when it
# matches, else the image pinned by digest in scripts/shellcheck-image, with the checkout mounted read-only.
if want shellcheck; then
  pin="$(tr -d '\r\n' <scripts/shellcheck-version)"
  image="$(tr -d '\r\n' <scripts/shellcheck-image)"
  local_version=""
  if command -v shellcheck >/dev/null; then
    local_version="$(shellcheck --version | sed -n 's/^version: //p' | tr -d '\r')"
  fi
  if [ "$local_version" = "$pin" ]; then
    step shellcheck sh -c "git ls-files -z '*.sh' | xargs -0 shellcheck"
  elif command -v docker >/dev/null && docker info >/dev/null 2>&1; then
    step "shellcheck ($image)" sh -c \
      "git ls-files -z '*.sh' | xargs -0 docker run --rm -v \"\$PWD:/mnt:ro\" -w /mnt $image"
  elif command -v docker >/dev/null; then
    echo "== shellcheck: $pin not installed and the docker daemon is not running, skipped (start docker, or install $pin)"
  elif [ -n "$local_version" ]; then
    echo "== shellcheck: found $local_version, need $pin (scripts/shellcheck-version)."
    echo "   Install $pin from https://github.com/koalaman/shellcheck/releases/tag/v$pin, or install docker to run its image."
    status=1
  else
    echo "== shellcheck: not installed, skipped (install $pin, or docker to run its image)"
  fi
fi

if ! want actionlint; then
  :
elif command -v actionlint >/dev/null; then
  step actionlint actionlint
else
  echo "== actionlint: not installed, skipped (brew install actionlint)"
fi

# python.test.sh runs only the test folders holding a changed file (every folder when a changed file is in none).
if [ "${1:-}" != --all ]; then
  PYTHON_TEST_FILES="$(tests/affected.sh --files "$@")" || exit 1
  export PYTHON_TEST_FILES
fi

suites=()
for t in tests/*.test.sh; do want "${t##*/}" && suites+=("$t"); done
# At once, through tests/parallel.sh (TEST_JOBS caps how many).
[ "${#suites[@]}" -eq 0 ] || step "tests/*.test.sh" tests/parallel.sh "${suites[@]}"

# macOS: run the suites again under /bin/bash 3.2. A `bash -> /bin/bash` shim first on PATH,
# for this pass only, makes the scripts they call through `#!/usr/bin/env bash` run under 3.2 too.
if [ "$(uname -s)" = Darwin ] && [ -x /bin/bash ] && [ "${#suites[@]}" -gt 0 ]; then
  shim="$(mktemp -d)"
  trap 'rm -rf "$shim"' EXIT
  ln -s /bin/bash "$shim/bash"
  step "tests/*.test.sh (bash 3.2)" env PATH="$shim:$PATH" /bin/bash tests/parallel.sh "${suites[@]}"
fi

[ -z "$skipped" ] || echo "== not run, no changed file they cover (tests/suites.txt; --all runs them):$skipped"
exit "$status"
