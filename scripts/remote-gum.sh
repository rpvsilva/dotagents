#!/usr/bin/env bash
# gum (https://github.com/charmbracelet/gum) for `remote-agent setup`'s menus.
# Usage: remote-gum.sh path      prints the gum to run (on PATH, else ~/.agents/bin/gum); exit 1 when there is none.
#        remote-gum.sh install   installs it when there is none: with brew when brew is on PATH, else on Linux or macOS
#                                (x86_64, arm64) the release pinned in remote/gum.txt, downloaded with curl or wget,
#                                checked against its pinned sha256 and put at ~/.agents/bin/gum. Elsewhere (Windows) it
#                                names the command to run. Prints one line, indented, as install.sh's lines are; exit 0
#                                when gum is there after, 1 when not.
# DOTAGENTS_GUM_PINS (a pins file) and DOTAGENTS_GUM_URL (the download folder, e.g. file:///...) replace remote/gum.txt
# and the release URL, for tests.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PINS="${DOTAGENTS_GUM_PINS:-$REPO/remote/gum.txt}"
OURS="$HOME/.agents/bin/gum"

where() {
  if command -v gum >/dev/null 2>&1; then command -v gum
  elif [ -x "$OURS" ]; then printf '%s\n' "$OURS"
  else return 1; fi
}

sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

fetch() {
  if command -v curl >/dev/null 2>&1; then curl -fsSL -o "$2" "$1"
  elif command -v wget >/dev/null 2>&1; then wget -qO "$2" "$1"
  else return 2; fi
}

# Downloads the pinned release for this machine to $OURS; prints why not on failure.
download() {
  local os arch version asset want url tmp got
  case "$(uname -s)" in Linux) os=Linux ;; Darwin) os=Darwin ;; *) return 1 ;; esac
  case "$(uname -m)" in x86_64 | amd64) arch=x86_64 ;; aarch64 | arm64) arch=arm64 ;; *) echo "no pinned gum for $os $(uname -m)"; return 1 ;; esac
  version="$(awk '$1 == "version" { print $2 }' "$PINS")"
  asset="gum_${version}_${os}_${arch}.tar.gz"
  want="$(awk -v a="$asset" '$1 == a { print $2 }' "$PINS")"
  [ -n "$want" ] || { echo "no pinned gum for $os $arch"; return 1; }
  url="${DOTAGENTS_GUM_URL:-https://github.com/charmbracelet/gum/releases/download/v$version}/$asset"
  tmp="$(mktemp -d)"
  # It runs in a subshell ($(download)): the folder goes when it exits.
  trap 'rm -rf "$tmp"' EXIT
  fetch "$url" "$tmp/gum.tgz" || { echo "could not download $url (needs curl or wget)"; return 1; }
  got="$(sha256 "$tmp/gum.tgz")"
  [ "$got" = "$want" ] || { echo "$asset: sha256 $got, want $want"; return 1; }
  tar -xzf "$tmp/gum.tgz" -C "$tmp" "${asset%.tar.gz}/gum" || { echo "$asset holds no gum"; return 1; }
  mkdir -p "$(dirname "$OURS")"
  mv -f "$tmp/${asset%.tar.gz}/gum" "$OURS"
  chmod 755 "$OURS"
}

install() {
  local log why
  if where >/dev/null; then echo "  gum: already installed"; return 0; fi
  if command -v brew >/dev/null 2>&1; then
    log="$(mktemp)"
    if brew install gum >"$log" 2>&1 && where >/dev/null; then rm -f "$log"; echo "  gum: installed with brew"; return 0; fi
    echo "  gum: brew could not install it, remote-agent setup asks plain questions:"
    tail -n 5 "$log" | sed 's/^/    /'
    rm -f "$log"
    return 1
  fi
  case "$(uname -s)" in
    Linux | Darwin)
      if why="$(download)"; then echo "  gum: installed $OURS ($(awk '$1 == "version" { print $2 }' "$PINS"))"; return 0; fi
      echo "  gum: not installed ($why), skipped: remote-agent setup asks plain questions"
      ;;
    MINGW* | MSYS* | CYGWIN*)
      echo "  gum: not installed, skipped: for menus in remote-agent setup, run \`winget install charmbracelet.gum\`" ;;
    *)
      echo "  gum: not installed, skipped: for menus in remote-agent setup, see https://github.com/charmbracelet/gum#installation" ;;
  esac
  return 1
}

case "${1:-}" in
  path) where ;;
  install) install ;;
  *) echo "usage: $0 path|install" >&2; exit 2 ;;
esac
