#!/usr/bin/env bash
# Runs scripts/remote-run.sh against a stub kubectl and a stub token command, and checks the claim's
# lifecycle and that tokens only ever travel through stdin.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# Stub kubectl: logs every call's arguments to $SANDBOX/calls, one line each; `apply` saves the claim, `get` answers
# the sandbox name, `exec` saves its stdin and exits with $SANDBOX/code.
mkdir -p "$SANDBOX/bin" "$SANDBOX/config/dotagents"
cat >"$SANDBOX/bin/kubectl" <<EOF
#!/usr/bin/env bash
args="\$*"
printf '%s\n' "\${args//\$'\\n'/ }" >>"$SANDBOX/calls"  # one line per call, even with the multi-line script
case " \$* " in
  *" apply "*) cat >"$SANDBOX/claim.yaml" ;;
  *" get sandboxclaim "*) printf 'claude-runner-abc' ;;
  *" get sandbox "*) printf '' ;;
  *" exec "*) cat >"$SANDBOX/stdin"; echo "https://github.com/o/r/pull/9"; exit "\$(cat "$SANDBOX/code")" ;;
esac
EOF
cat >"$SANDBOX/bin/token" <<'EOF'
#!/usr/bin/env bash
echo "ghs-secret-for-$1"
EOF
chmod +x "$SANDBOX/bin/kubectl" "$SANDBOX/bin/token"
echo "CLAUDE_CODE_OAUTH_TOKEN=sk-claude-secret" >"$SANDBOX/config/dotagents/remote.env"

# Runs remote-run.sh with exec exiting $1; extra arguments go to the script.
run() {
  echo "$1" >"$SANDBOX/code"
  shift
  rm -f "$SANDBOX/calls" "$SANDBOX/claim.yaml" "$SANDBOX/stdin"
  env -u CLAUDE_CODE_OAUTH_TOKEN PATH="$SANDBOX/bin:$PATH" XDG_CONFIG_HOME="$SANDBOX/config" \
    REMOTE_TOKEN_CMD="$SANDBOX/bin/token" "$REPO/scripts/remote-run.sh" "$@" >"$SANDBOX/out" 2>&1
}

# A run that succeeds.
run 0 o/r "add a hello script" || fail "success: exited non-zero: $(cat "$SANDBOX/out")"
claim="$(sed -n 's/^  name: //p' "$SANDBOX/claim.yaml" | head -1)"
[[ "$claim" == run-* ]] || fail "claim not named run-*: $claim"
grep -q "name: claude-runner" "$SANDBOX/claim.yaml" || fail "claim does not use the claude-runner warm pool"
grep -q "shutdownPolicy: Delete" "$SANDBOX/claim.yaml" || fail "claim does not delete on shutdown"
grep -qE 'shutdownTime: "[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z"' "$SANDBOX/claim.yaml" || fail "claim has no shutdownTime"
grep -q "pull/9" "$SANDBOX/out" || fail "success: PR URL not printed"
grep -q "^exec -i claude-runner-abc " <(sed -E 's/^--context [^ ]+ -n [^ ]+ //' "$SANDBOX/calls") || fail "exec not in the claimed sandbox's pod"
grep -q -- " o/r rpvsilva/dotagents rpvsilva-agent remote/$claim$" "$SANDBOX/calls" || fail "exec arguments wrong: $(grep exec "$SANDBOX/calls")"
[ "$(grep -c "^--context maxibombo -n agents " "$SANDBOX/calls")" = "$(wc -l <"$SANDBOX/calls" | tr -d ' ')" ] || fail "not every call targets maxibombo/agents"
grep -q "delete sandboxclaim $claim " "$SANDBOX/calls" || fail "success: claim not deleted"

# Tokens travel through stdin, in order, before the prompt, and never as arguments.
[ "$(sed -n 1p "$SANDBOX/stdin")" = "sk-claude-secret" ] || fail "stdin line 1 is not the Claude token"
[ "$(sed -n 2p "$SANDBOX/stdin")" = "ghs-secret-for-o/r" ] || fail "stdin line 2 is not the target repo's token"
[ "$(sed -n 3p "$SANDBOX/stdin")" = "ghs-secret-for-rpvsilva/dotagents" ] || fail "stdin line 3 is not the setup repo's token"
[ "$(sed -n '4,$p' "$SANDBOX/stdin")" = "add a hello script" ] || fail "prompt does not follow the tokens"
grep -qE "sk-claude-secret|ghs-secret" "$SANDBOX/calls" "$SANDBOX/claim.yaml" "$SANDBOX/out" && fail "a token leaked into kubectl arguments, the claim or the output"

# A failed run still deletes the claim, and exits non-zero.
if run 3 o/r "do nothing"; then fail "failed run: exited 0"; fi
grep -q "delete sandboxclaim run-" "$SANDBOX/calls" || fail "failed run: claim not deleted"

# REMOTE_KEEP keeps the claim.
REMOTE_KEEP=1 run 0 o/r "keep it" || fail "keep: exited non-zero"
grep -q "delete sandboxclaim" "$SANDBOX/calls" && fail "keep: claim deleted anyway"

# The prompt can come from a file or stdin.
printf 'from a file' >"$SANDBOX/prompt.md"
run 0 o/r "@$SANDBOX/prompt.md" || fail "@file: exited non-zero"
[ "$(sed -n '4,$p' "$SANDBOX/stdin")" = "from a file" ] || fail "@file: prompt not read from the file"

# Without a Claude token, or with bad arguments, nothing reaches the cluster.
rm "$SANDBOX/config/dotagents/remote.env"
if run 0 o/r "no token"; then fail "no token: exited 0"; fi
[ ! -e "$SANDBOX/calls" ] || fail "no token: kubectl still called"
if run 0 not-a-repo "bad repo"; then fail "bad repo: exited 0"; fi
[ ! -e "$SANDBOX/calls" ] || fail "bad repo: kubectl still called"

[ "$fails" -eq 0 ] || { echo "remote-run.test.sh: $fails failure(s)"; exit 1; }
echo "remote-run.test.sh: ok"
