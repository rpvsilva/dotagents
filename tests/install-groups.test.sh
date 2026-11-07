#!/usr/bin/env bash
# Installs each group of groups.json alone into a throwaway $HOME and checks the install is whole: every link
# resolves, every required need of an installed part is installed, and nothing outside the group's closure is.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

parts() { "$REPO/scripts/parts.sh" "$@"; }

groups="$(parts groups | cut -f1)"
[ -n "$groups" ] || { echo "FAIL: parts.sh groups printed no group"; exit 1; }

# Each group gets its own home with the tool folders made, installed in the background; the checks come after `wait`.
pids=""
for g in $groups; do
  (
    home="$SANDBOX/home-$g"
    mkdir -p "$home/.claude" "$home/.codex" "$home/.cursor"
    rc=0
    HOME="$home" DOTAGENTS_SKIP_PLUGINS=1 "$REPO/install.sh" all "$g" >"$SANDBOX/$g.out" 2>&1 || rc=$?
    echo "$rc" >"$SANDBOX/$g.rc"
  ) &
  pids="$pids $!"
done

# Meanwhile: `<part> <repo-relative file>` for every file a part ships, core (`_shared`) included.
for p in _shared $(parts list | cut -f1); do
  parts files "$p" | sed "s|^|$p |"
done >"$SANDBOX/files"

for pid in $pids; do wait "$pid"; done

# Prints the repo-relative target of every link under home $1 (a link to another link in the home followed once),
# sorted, or `outside <target>` for one leading out of the repo.
targets() {
  local home="$1" l t
  find "$home" -type l ! -path '*/.dotagents-backup/*' | while IFS= read -r l; do
    t="$(readlink "$l")"
    case "$t" in "$home"/*) [ ! -L "$t" ] || t="$(readlink "$t")" ;; esac
    case "$t" in
      "$REPO"/*) echo "${t#"$REPO"/}" ;;
      *) echo "outside $t" ;;
    esac
  done | LC_ALL=C sort -u
}

# Prints `<part> <target>` for each target on stdin: the part shipping that file, or a file under that folder;
# `? <target>` when no part does.
owners() {
  awk 'NR == FNR { file[$2] = $1; n++; f[n] = $2; p[n] = $1; next }
    {
      if ($0 in file) { print file[$0], $0; next }
      for (i = 1; i <= n; i++) if (index(f[i], $0 "/") == 1) { print p[i], $0; next }
      print "?", $0
    }' "$SANDBOX/files" -
}

# Links every install makes whatever the selection: core.
targets "$SANDBOX/home-core" >"$SANDBOX/core-targets"

for g in $groups; do
  home="$SANDBOX/home-$g"
  rc="$(cat "$SANDBOX/$g.rc")"
  [ "$rc" = 0 ] || { fail "install.sh all $g exited $rc: $(cat "$SANDBOX/$g.out")"; continue; }

  dangling="$(find "$home" -type l ! -exec test -e {} \; -print)"
  [ -z "$dangling" ] || fail "$g: dangling links: $dangling"

  targets "$home" >"$SANDBOX/$g.targets"
  outside="$(sed -n 's/^outside //p' "$SANDBOX/$g.targets")"
  [ -z "$outside" ] || fail "$g: links leading out of the repo: $outside"
  grep -v '^outside ' "$SANDBOX/$g.targets" | owners >"$SANDBOX/$g.owners"
  unknown="$(sed -n 's/^? //p' "$SANDBOX/$g.owners" | LC_ALL=C comm -23 - "$SANDBOX/core-targets")"
  [ -z "$unknown" ] || fail "$g: links to files no part ships: $unknown"

  grep -v '^? ' "$SANDBOX/$g.owners" | cut -d' ' -f1 | LC_ALL=C sort -u >"$SANDBOX/$g.installed"
  # shellcheck disable=SC2046 # one word per part
  parts closure $(cat "$SANDBOX/$g.installed") >"$SANDBOX/$g.closure"
  missing="$(LC_ALL=C comm -13 "$SANDBOX/$g.installed" "$SANDBOX/$g.closure" | tr '\n' ' ')"
  [ -z "$missing" ] || fail "$g: installed parts require parts not installed: $missing"

  parts closure "$g" >"$SANDBOX/$g.wanted"
  extra="$(LC_ALL=C comm -23 "$SANDBOX/$g.installed" "$SANDBOX/$g.wanted" | tr '\n' ' ')"
  [ -z "$extra" ] || fail "$g: installed parts outside its closure: $extra"
done

if [ "$fails" -gt 0 ]; then echo "$fails failure(s)"; exit 1; fi
echo "ok"
