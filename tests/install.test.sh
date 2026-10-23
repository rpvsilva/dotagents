#!/usr/bin/env bash
# Runs install.sh against a throwaway $HOME and checks the links it leaves behind.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

assert_link() {
  local dest="$1" want="$2" got
  if [ ! -L "$dest" ]; then fail "$dest is not a symlink"; return; fi
  got="$(readlink "$dest")"
  [ "$got" = "$want" ] || fail "$dest -> $got, want $want"
}

# Prints the target of every symlink under $1, so two runs can be compared.
snapshot() { find "$1" -type l | sort | while read -r l; do echo "$l -> $(readlink "$l")"; done; }

run() { HOME="$1" "$REPO/install.sh" "${@:2}"; }

skills=()
for d in "$REPO"/skills/*/; do skills+=("$(basename "$d")"); done

# all: a pre-existing real file is backed up, then everything is linked.
home="$SANDBOX/all"
mkdir -p "$home/.claude" "$home/.codex"
echo "old instructions" >"$home/.claude/CLAUDE.md"
run "$home" all >/dev/null

for s in "${skills[@]}"; do
  assert_link "$home/.agents/skills/$s" "$REPO/skills/$s"
  assert_link "$home/.claude/skills/$s" "$home/.agents/skills/$s"
  [ -f "$home/.claude/skills/$s/SKILL.md" ] || fail "$s/SKILL.md not reachable through ~/.claude/skills"
done
assert_link "$home/.claude/CLAUDE.md" "$REPO/global/AGENTS.md"
assert_link "$home/.codex/AGENTS.md" "$REPO/global/AGENTS.md"
grep -rqx "old instructions" "$home/.dotagents-backup" 2>/dev/null || fail "replaced CLAUDE.md was not backed up"

# Second run: same links, nothing backed up.
before="$(snapshot "$home")"
out="$(run "$home" all)"
[ "$before" = "$(snapshot "$home")" ] || fail "second run changed the links"
if grep -q "backed up" <<<"$out"; then fail "second run backed something up"; fi

# claude: no codex links; codex: skipped cleanly when ~/.codex is missing.
home="$SANDBOX/claude"
mkdir -p "$home"
run "$home" claude >/dev/null
assert_link "$home/.claude/CLAUDE.md" "$REPO/global/AGENTS.md"
[ ! -e "$home/.codex" ] || fail "install.sh claude touched ~/.codex"
run "$home" codex | grep -q "skipping" || fail "codex without ~/.codex did not skip"

# Unknown target exits non-zero.
if run "$SANDBOX/bad" nope >/dev/null 2>&1; then fail "unknown target exited 0"; fi

[ "$fails" -eq 0 ] || { echo "install.test.sh: $fails failure(s)"; exit 1; }
echo "install.test.sh: ok"
