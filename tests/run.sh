#!/usr/bin/env bash
# Runs every check CI runs. Lint tools that are not installed are skipped with a warning.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
status=0

step() { echo "== $1"; shift; "$@" || status=1; }

if command -v shellcheck >/dev/null; then
  step shellcheck sh -c "git ls-files -z '*.sh' | xargs -0 shellcheck"
else
  echo "== shellcheck: not installed, skipped (brew install shellcheck)"
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
