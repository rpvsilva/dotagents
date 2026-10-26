#!/usr/bin/env bash
# Runs scripts/gh-app-token.sh against a stub GitHub API (curl) and a throwaway App key.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

openssl genrsa -out "$SANDBOX/app.pem" 2048 2>/dev/null
openssl rsa -in "$SANDBOX/app.pem" -pubout -out "$SANDBOX/app.pub" 2>/dev/null

# Stub curl: checks the JWT signature against app.pub, logs each request, answers like GitHub.
# $SANDBOX/installed lists the repos the App is installed on.
mkdir "$SANDBOX/bin"
cat >"$SANDBOX/bin/curl" <<EOF
#!/usr/bin/env bash
method=GET body= auth=
while [ \$# -gt 1 ]; do
  case "\$1" in
    -X) method="\$2"; shift ;;
    -d) body="\$2"; shift ;;
    -H) case "\$2" in "Authorization: Bearer "*) auth="\${2#Authorization: Bearer }" ;; esac; shift ;;
  esac
  shift
done
url="\$1"
unb64() { local s; s="\$(tr -- '-_' '+/')"; while [ \$((\${#s} % 4)) -ne 0 ]; do s="\$s="; done; printf '%s' "\$s" | openssl base64 -d -A; }
IFS=. read -r h p s <<<"\$auth"
printf '%s' "\$s" | unb64 >"$SANDBOX/sig"
printf '%s.%s' "\$h" "\$p" | openssl dgst -sha256 -verify "$SANDBOX/app.pub" -signature "$SANDBOX/sig" >/dev/null || { echo "bad signature" >&2; exit 22; }
printf '%s' "\$p" | unb64 >"$SANDBOX/claims"
echo "\$method \$url \$body" >>"$SANDBOX/requests"
case "\$method \$url" in
  "GET https://api.test/repos/"*/installation)
    r="\${url#https://api.test/repos/}"; grep -qx "\${r%/installation}" "$SANDBOX/installed" || exit 22
    echo '{"id":42}' ;;
  "POST https://api.test/app/installations/42/access_tokens") echo '{"token":"ghs_test"}' ;;
  *) exit 22 ;;
esac
EOF
chmod +x "$SANDBOX/bin/curl"
echo "rpvsilva/dotagents" >"$SANDBOX/installed"

run() {
  rm -f "$SANDBOX/requests"
  env -u DOTAGENTS_APP_ID PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" GITHUB_API_URL=https://api.test \
    DOTAGENTS_APP_KEY="$SANDBOX/app.pem" "$@" "$REPO/scripts/gh-app-token.sh" rpvsilva/dotagents 2>"$SANDBOX/err"
}

# Signs a valid JWT for the App, finds the installation, and prints a token narrowed to the repo.
out="$(run DOTAGENTS_APP_ID=123)" || fail "valid setup exited non-zero: $(cat "$SANDBOX/err")"
[ "$out" = "ghs_test" ] || fail "printed '$out', want ghs_test"
grep -q '"iss":"123"' "$SANDBOX/claims" || fail "JWT iss is not the App ID: $(cat "$SANDBOX/claims")"
grep -q 'access_tokens {"repositories":\["dotagents"\]}' "$SANDBOX/requests" || fail "token not narrowed to the repo"

# Reads the App ID from the config file when the environment has none.
mkdir -p "$SANDBOX/config/dotagents"
echo "DOTAGENTS_APP_ID=456" >"$SANDBOX/config/dotagents/github-app.env"
run >/dev/null || fail "config file setup exited non-zero: $(cat "$SANDBOX/err")"
grep -q '"iss":"456"' "$SANDBOX/claims" || fail "App ID not read from github-app.env"
rm "$SANDBOX/config/dotagents/github-app.env"

# Fails, printing no token, when something is missing.
if run >/dev/null; then fail "no App ID exited 0"; fi
if run DOTAGENTS_APP_ID=123 DOTAGENTS_APP_KEY="$SANDBOX/missing.pem" >/dev/null; then fail "missing key exited 0"; fi
: >"$SANDBOX/installed"
if out="$(run DOTAGENTS_APP_ID=123)"; then fail "not installed exited 0"; fi
[ -z "$out" ] || fail "not installed still printed '$out'"
grep -q "not installed" "$SANDBOX/err" || fail "not installed gave no clear error: $(cat "$SANDBOX/err")"

[ "$fails" -eq 0 ] || { echo "gh-app-token.test.sh: $fails failure(s)"; exit 1; }
echo "gh-app-token.test.sh: ok"
