#!/usr/bin/env bash
# Prints a 1-hour GitHub App installation token for one repo, so agents act as the App's bot and Rui can approve their PRs.
# Usage: GH_TOKEN="$(scripts/gh-app-token.sh [owner/repo])" gh pr create ...   (default repo: this checkout's origin)
# Config: DOTAGENTS_APP_ID and DOTAGENTS_APP_KEY (private key .pem), from the environment or
# ~/.config/dotagents/github-app.env. Never commit either value's file.
set -euo pipefail

CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/dotagents"
# shellcheck disable=SC1091
[ -f "$CONFIG/github-app.env" ] && . "$CONFIG/github-app.env"
APP_ID="${DOTAGENTS_APP_ID:?set DOTAGENTS_APP_ID (in $CONFIG/github-app.env)}"
KEY="${DOTAGENTS_APP_KEY:-$CONFIG/github-app.pem}"
API="${GITHUB_API_URL:-https://api.github.com}"
[ -r "$KEY" ] || { echo "gh-app-token: private key not readable: $KEY" >&2; exit 1; }

repo="${1:-$(git remote get-url origin | sed -E 's#^.*github\.com[:/]##; s#\.git$##')}"
[[ "$repo" == */* ]] || { echo "gh-app-token: not an owner/repo: $repo" >&2; exit 1; }

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

# App JWT: RS256, backdated a minute for clock drift, valid 9 of GitHub's allowed 10 minutes.
now="$(date +%s)"
header="$(printf '{"alg":"RS256","typ":"JWT"}' | b64url)"
payload="$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' $((now - 60)) $((now + 540)) "$APP_ID" | b64url)"
signature="$(printf '%s.%s' "$header" "$payload" | openssl dgst -sha256 -sign "$KEY" | b64url)"
jwt="$header.$payload.$signature"

api() { curl -fsS -H "Authorization: Bearer $jwt" -H "Accept: application/vnd.github+json" "$@"; }

installation="$(api "$API/repos/$repo/installation" | jq -r .id)" \
  || { echo "gh-app-token: the App is not installed on $repo" >&2; exit 1; }
# Narrow the token to this one repo, whatever else the installation covers.
api -X POST -d "{\"repositories\":[\"${repo#*/}\"]}" "$API/app/installations/$installation/access_tokens" | jq -er .token
