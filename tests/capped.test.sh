#!/usr/bin/env bash
# Runs scripts/capped on real commands: the memory and time caps, the closed stdin, SIGTERM to the runner, a missing
# `ps`, and `capped stop` in throwaway worktrees. On Linux the cases run with `ps` (when there is one) and again with
# it hidden from PATH, so capped reads the groups from /proc. Git Bash skips every case that needs process groups.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CAPPED="$REPO/scripts/capped"
SANDBOX="$(mktemp -d)"
pids=()
cleanup() {
  exec 2>/dev/null # no "Killed" notices for the sleepers killed here
  for p in ${pids[@]+"${pids[@]}"}; do kill -9 "$p" 2>/dev/null || true; done
  wait || true
  rm -rf "$SANDBOX"
}
trap cleanup EXIT
fails=0
how=""

fail() { echo "FAIL${how:+ ($how)}: $*"; fails=$((fails + 1)); }
now_ms() { python3 -c 'import time; print(int(time.time() * 1000))'; }

# Runs capped with args $@, stdout and stderr to $SANDBOX/out and $SANDBOX/err; sets $code and $ms (elapsed).
run() {
  local start
  start="$(now_ms)"
  code=0
  "$CAPPED" "$@" >"$SANDBOX/out" 2>"$SANDBOX/err" || code=$?
  ms=$(($(now_ms) - start))
}

# Waits up to 5 s for file $1 to hold a pid, then prints it.
pid_in() {
  local i=0
  while [ ! -s "$1" ] && [ "$i" -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
  cat "$1"
}

# True when pid $1 is a live process (not gone, not a zombie): from ps, else from /proc.
alive() {
  local s
  if command -v ps >/dev/null; then
    s="$(ps -o stat= -p "$1" 2>/dev/null || true)"
  else
    s="$(sed 's/.*) //' "/proc/$1/stat" 2>/dev/null || true)"
  fi
  [ -n "$s" ] && [[ "$s" != Z* ]]
}

gitbash=0
if [[ "$OSTYPE" == msys* || "$OSTYPE" == cygwin* ]]; then gitbash=1; fi

procfs=0
if [ "$(uname -s)" = Linux ] && [ -r /proc/self/stat ]; then procfs=1; fi

# A PATH with what the cases run but no ps.
nops="$SANDBOX/nops"
mkdir "$nops"
if [ "$gitbash" -eq 0 ]; then
  for t in python3 sh sleep cat git grep ls sed mkdir; do ln -s "$(command -v "$t")" "$nops/$t"; done
fi

# Without ps and without /proc (macOS) the runner refuses to run uncapped (Windows needs neither: it has no memory cap).
if [ "$gitbash" -eq 0 ] && [ "$procfs" -eq 0 ]; then
  code=0
  env PATH="$nops" "$CAPPED" --secs 5 -- true >/dev/null 2>"$SANDBOX/err" || code=$?
  [ "$code" -eq 125 ] || fail "no ps: exit $code, want 125"
  grep -q "capped: .*ps" "$SANDBOX/err" || fail "no ps: no message naming ps"
fi

# Every case, under the PATH it is called with; $1 names the run in failures and its throwaway folder.
cases() {
  local how="$1" dir="$SANDBOX/$1" code ms start runner leader gc main w i plain s1 s2 stale key target
  mkdir "$dir"

  # Over the time cap: killed with 124 and the timeout line.
  run --secs 1 -- sleep 5
  [ "$code" -eq 124 ] || fail "timeout: exit $code, want 124"
  grep -qx "capped: killed: timeout" "$SANDBOX/err" || fail "timeout: no 'capped: killed: timeout' on stderr"
  [ "$ms" -lt 3000 ] || fail "timeout: took ${ms} ms"

  # Stdin is closed: a command waiting on input returns at once, though the caller's stdin stays open.
  code=0
  start="$(now_ms)"
  "$CAPPED" -- cat < <(sleep 5) >"$SANDBOX/out" 2>"$SANDBOX/err" || code=$?
  ms=$(($(now_ms) - start))
  [ "$code" -eq 0 ] || fail "cat: exit $code"
  [ "$ms" -lt 2000 ] || fail "cat: waited ${ms} ms on stdin"

  # The command's own exit code passes through.
  run --secs 5 -- sh -c 'exit 3'
  [ "$code" -eq 3 ] || fail "exit code: got $code, want 3"

  if [ "$gitbash" -eq 1 ]; then
    echo "capped.test.sh: memory, SIGTERM and stop cases skipped on Git Bash"
  else
    # Over the memory cap: a command growing to about 200 MB under --mb 50 is killed with 137 within 2 s.
    run --mb 50 --secs 30 -- python3 -c "import time
b = []
for _ in range(20):
    b.append(' ' * 10**7)
time.sleep(30)"
    [ "$code" -eq 137 ] || fail "memory: exit $code, want 137"
    grep -qx "capped: killed: memory" "$SANDBOX/err" || fail "memory: no 'capped: killed: memory' on stderr"
    [ "$ms" -lt 3000 ] || fail "memory: took ${ms} ms"
    # Under the cap it runs to the end.
    run --mb 500 --secs 10 -- python3 -c "b = ' ' * 10**7"
    [ "$code" -eq 0 ] || fail "under the memory cap: exit $code"

    # SIGTERM to the runner kills its whole group (the command and its children) before it exits.
    # shellcheck disable=SC2016 # expanded by the capped sh
    "$CAPPED" -- sh -c 'sleep 30 & echo $! >"$1/gc"; echo $$ >"$1/leader"; wait' sh "$dir" 2>/dev/null &
    runner=$!
    pids+=("$runner")
    leader="$(pid_in "$dir/leader")"
    gc="$(pid_in "$dir/gc")"
    pids+=("$leader" "$gc")
    kill -TERM "$runner"
    wait "$runner" 2>/dev/null || true
    if alive "$leader"; then fail "SIGTERM: the command outlived the runner"; fi
    if alive "$gc"; then fail "SIGTERM: the command's child outlived the runner"; fi

    # capped stop: throwaway repo with two worktrees.
    main="$dir/main"
    git init -q "$main"
    git -C "$main" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init
    git -C "$main" worktree add -q "$dir/wt1"
    git -C "$main" worktree add -q "$dir/wt2"
    # A capped sleeper in each worktree, and a plain sleep in wt1.
    for w in wt1 wt2; do
      # shellcheck disable=SC2016 # expanded by the capped sh
      (cd "$dir/$w" && exec "$CAPPED" -- sh -c 'echo $$ >"$1"; exec sleep 30' sh "$dir/$w.pid" 2>/dev/null) &
      pids+=("$!")
    done
    (cd "$dir/wt1" && exec sleep 30) &
    plain=$!
    pids+=("$plain")
    s1="$(pid_in "$dir/wt1.pid")"
    s2="$(pid_in "$dir/wt2.pid")"
    pids+=("$s1" "$s2")
    # A stale entry: a group capped never started, under a pid it once registered. Stop must leave it alone.
    python3 -c 'import os, sys, time; os.setsid(); open(sys.argv[1], "w").write(str(os.getpid())); time.sleep(30)' \
      "$dir/stale.pid" &
    pids+=("$!")
    stale="$(pid_in "$dir/stale.pid")"
    pids+=("$stale")
    key="$(python3 -c 'import hashlib, os, sys; print(hashlib.sha1(os.path.realpath(sys.argv[1]).encode()).hexdigest())' "$dir/wt1")"
    printf '%s\n1\nlong ago\nlong ago\n' "$dir/wt1" >"$main/.git/capped/$key/$stale"
    [ -n "$(ls "$main/.git/capped" 2>/dev/null)" ] || fail "stop: nothing registered under the git common dir"

    # Refusals: /, the home folder and the main checkout; nothing dies.
    for target in / "$main"; do
      code=0
      "$CAPPED" stop "$target" >/dev/null 2>"$SANDBOX/err" || code=$?
      [ "$code" -ne 0 ] || fail "stop $target: not refused"
    done
    code=0
    HOME="$dir/wt1" "$CAPPED" stop "$dir/wt1" >/dev/null 2>"$SANDBOX/err" || code=$?
    [ "$code" -ne 0 ] || fail "stop \$HOME: not refused"
    alive "$s1" || fail "a refused stop killed the wt1 sleeper"

    code=0
    "$CAPPED" stop "$dir/wt1" >"$SANDBOX/out" 2>"$SANDBOX/err" || code=$?
    [ "$code" -eq 0 ] || fail "stop wt1: exit $code ($(cat "$SANDBOX/err"))"
    # Bounded: the sleepers end on their own after 30 s, which must not pass for a stop.
    i=0
    while alive "$s1" && [ "$i" -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
    if alive "$s1"; then fail "stop wt1: the capped sleeper of wt1 survived"; fi
    alive "$s2" || fail "stop wt1: killed the capped sleeper of wt2"
    alive "$plain" || fail "stop wt1: killed a plain sleep it did not start"
    alive "$stale" || fail "stop wt1: killed a group under a stale entry"
  fi
}

if command -v ps >/dev/null || [ "$procfs" -eq 0 ]; then cases ps; fi
if [ "$procfs" -eq 1 ]; then PATH="$nops" cases proc; fi

[ "$fails" -eq 0 ] || { echo "capped.test.sh: $fails failure(s)"; exit 1; }
echo "capped.test.sh: ok"
