#!/usr/bin/env bash
# Runs every check CI runs. Lint tools that are not installed are skipped with a warning.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
status=0

step() { echo "== $1"; shift; "$@" || status=1; }

# Lint at the shellcheck version pinned in scripts/shellcheck-version, the one CI runs: the local binary when it
# matches, else the pinned docker image.
pin="$(tr -d '\r\n' <scripts/shellcheck-version)"
local_version=""
if command -v shellcheck >/dev/null; then
  local_version="$(shellcheck --version | sed -n 's/^version: //p' | tr -d '\r')"
fi
if [ "$local_version" = "$pin" ]; then
  step shellcheck sh -c "git ls-files -z '*.sh' | xargs -0 shellcheck"
elif command -v docker >/dev/null; then
  step "shellcheck (koalaman/shellcheck:v$pin)" sh -c \
    "git ls-files -z '*.sh' | xargs -0 docker run --rm -v \"\$PWD:/mnt\" -w /mnt koalaman/shellcheck:v$pin"
elif [ -n "$local_version" ]; then
  echo "== shellcheck: found $local_version, need $pin (scripts/shellcheck-version)."
  echo "   Install $pin from https://github.com/koalaman/shellcheck/releases/tag/v$pin, or install docker to run its image."
  status=1
else
  echo "== shellcheck: not installed, skipped (install $pin, or docker to run its image)"
fi

if command -v actionlint >/dev/null; then
  step actionlint actionlint
else
  echo "== actionlint: not installed, skipped (brew install actionlint)"
fi

for t in tests/*.test.sh; do step "$t" "$t"; done

# macOS: run the suites again under /bin/bash 3.2. A `bash -> /bin/bash` shim first on PATH,
# for this pass only, makes the scripts they call through `#!/usr/bin/env bash` run under 3.2 too.
if [ "$(uname -s)" = Darwin ] && [ -x /bin/bash ]; then
  shim="$(mktemp -d)"
  trap 'rm -rf "$shim"' EXIT
  ln -s /bin/bash "$shim/bash"
  for t in tests/*.test.sh; do step "$t (bash 3.2)" env PATH="$shim:$PATH" /bin/bash "$t"; done
fi

exit "$status"
