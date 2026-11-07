#!/usr/bin/env bash
# Runs scripts/capped on real commands: bad arguments, the exit codes, the memory and time caps, the closed stdin, how
# soon the runner ends after its command, SIGTERM to the runner, a missing `ps`, and `capped stop` in throwaway
# worktrees (forged, planted and oddly named registry entries included). On Linux
# the cases run with `ps` (when there is one), again with it hidden from PATH and again with a `ps` that fails (as
# BusyBox's does on these options), so capped reads the groups from /proc. Git Bash skips every case that needs process
# groups.
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
  while [ ! -s "$1" ] && [ "$i" -lt 250 ]; do sleep 0.02; i=$((i + 1)); done
  cat "$1"
}

# True when pid $1 is a live process (not gone, not a zombie): from ps where it works, else from /proc.
alive() {
  local s
  if ps -o stat= -p $$ >/dev/null 2>&1; then
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

# A PATH with what the cases run but no ps, and one whose ps fails.
nops="$SANDBOX/nops"
badps="$SANDBOX/badps"
mkdir "$nops" "$badps"
if [ "$gitbash" -eq 0 ]; then
  for t in python3 bash sh sleep cat git grep ls sed mkdir rm ln true find tail; do
    ln -s "$(type -P "$t")" "$nops/$t"
    ln -s "$(type -P "$t")" "$badps/$t"
  done
  printf '#!/bin/sh\necho "ps: unrecognized option" >&2\nexit 1\n' >"$badps/ps"
  chmod +x "$badps/ps"
fi

# Prints the start time capped registers for pid $1: ps's lstart in the C locale, else /proc's start in clock ticks.
cat >"$SANDBOX/start_of.py" <<'PY'
import os, subprocess, sys
try:
    out = subprocess.run(["ps", "-o", "lstart=", "-p", sys.argv[1]], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         universal_newlines=True, env=dict(os.environ, LC_ALL="C"))
    ok = out.returncode == 0
except OSError:
    ok = False
if ok:
    print(out.stdout.strip())
else:
    print("proc %s" % open("/proc/%s/stat" % sys.argv[1]).read().rsplit(")", 1)[1].split()[19])
PY
start_of() { python3 "$SANDBOX/start_of.py" "$1"; }

# Runs `capped stop` (the rest of argv) in a session of its own, beside a sentinel sleep in its group whose pid goes
# to file $2, after forging an entry for its own group in registry folder $1. Exits with the stop's code.
cat >"$SANDBOX/stopper.py" <<'PY'
import os, subprocess, sys
os.setsid()
sentinel = subprocess.Popen(["sleep", "30"])
open(sys.argv[2], "w").write(str(sentinel.pid))
me = subprocess.run([sys.executable, os.path.join(os.path.dirname(sys.argv[0]), "start_of.py"), str(os.getpid())],
                    stdout=subprocess.PIPE, universal_newlines=True).stdout.strip()
open(os.path.join(sys.argv[1], str(os.getpgrp())), "w").write("x\n1\n%s\n%s\n" % (me, me))
sys.exit(subprocess.call(sys.argv[3:]))
PY

# Without ps and without /proc (macOS) the runner refuses to run uncapped (Windows needs neither: it has no memory cap).
if [ "$gitbash" -eq 0 ] && [ "$procfs" -eq 0 ]; then
  code=0
  env PATH="$nops" "$CAPPED" --secs 5 -- true >/dev/null 2>"$SANDBOX/err" || code=$?
  [ "$code" -eq 125 ] || fail "no ps: exit $code, want 125"
  grep -q "capped: .*ps" "$SANDBOX/err" || fail "no ps: no message naming ps"
fi

# Bad arguments get the usage and exit 2; a command that cannot start exits 127 saying why.
for args in "--secs 0 -- true" "--mb x -- true" "--secs" "--secs 5 true" "--" "stop" "stop a b"; do
  code=0
  # shellcheck disable=SC2086 # the words of $args are the arguments
  "$CAPPED" $args >/dev/null 2>"$SANDBOX/err" || code=$?
  [ "$code" -eq 2 ] || fail "usage ($args): exit $code, want 2"
  grep -q "^usage: capped" "$SANDBOX/err" || fail "usage ($args): no usage on stderr"
done
run --secs 5 -- "$SANDBOX/no-such-command"
[ "$code" -eq 127 ] || fail "missing command: exit $code, want 127"
grep -q "^capped: cannot run .*no-such-command" "$SANDBOX/err" || fail "missing command: not said: $(cat "$SANDBOX/err")"
# A command ended by signal N exits 128+N.
if [ "$gitbash" -eq 0 ]; then
  # shellcheck disable=SC2016 # expanded by the capped sh
  run --secs 5 -- sh -c 'kill -TERM $$'
  [ "$code" -eq 143 ] || fail "signal: exit $code, want 143"
fi

# Every case, under the PATH it is called with; $1 names the run in failures and its throwaway folder.
cases() {
  local how="$1" dir="$SANDBOX/$1" jc code ms start runner leader gc main w i plain s1 s2 s3 stale key target reg
  local orphan sentinel l other base capd
  mkdir "$dir"

  # Over the time cap: killed with 124 and the timeout line, not before the cap.
  run --secs 0.5 -- sleep 5
  [ "$code" -eq 124 ] || fail "timeout: exit $code, want 124"
  grep -qx "capped: killed: timeout" "$SANDBOX/err" || fail "timeout: no 'capped: killed: timeout' on stderr"
  [ "$ms" -ge 500 ] || fail "timeout: killed after ${ms} ms, before the 0.5 s cap"
  [ "$ms" -lt 2500 ] || fail "timeout: took ${ms} ms"

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

  # A command that ends ends the runner at once, not at the next poll (0.2 s). Each capped run of a 50 ms sleep is set
  # against capped refusing its arguments (its startup, no poll) plus the same sleep, run alternately so both see the
  # same load; the fastest of each is compared, as load only adds time. Waiting out the poll adds ~150 ms; 100 ms is
  # allowed, since under ps the first poll runs ps.
  if [ "$gitbash" -eq 0 ]; then
    base=""; capd=""
    for i in 1 2 3 4 5; do
      start="$(now_ms)"
      "$CAPPED" --bogus 2>/dev/null || true
      sleep 0.05
      ms=$(($(now_ms) - start))
      if [ -z "$base" ] || [ "$ms" -lt "$base" ]; then base="$ms"; fi
      start="$(now_ms)"
      "$CAPPED" --mb 500 --secs 5 -- sleep 0.05 || fail "quick exit: run $i failed"
      ms=$(($(now_ms) - start))
      if [ -z "$capd" ] || [ "$ms" -lt "$capd" ]; then capd="$ms"; fi
    done
    [ $((capd - base)) -lt 100 ] || fail "quick exit: fastest run took ${capd} ms, ${base} ms without the poll loop"
  fi

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

    # A shell with job control (Claude Code's Bash tool) puts each command in a group of its own: the time cap still kills
    # it, and does not wait for it to end.
    # shellcheck disable=SC2016 # expanded by the capped bash
    run --secs 0.5 -- bash -c 'set -m; sleep 30 & echo $! >"$1/jc"; wait' bash "$dir"
    [ "$code" -eq 124 ] || fail "job control timeout: exit $code, want 124"
    [ "$ms" -lt 3000 ] || fail "job control timeout: took ${ms} ms"
    jc="$(pid_in "$dir/jc")"
    [ -n "$jc" ] || fail "job control timeout: the command was killed before it started its sleep"
    pids+=("$jc")
    if alive "$jc"; then fail "job control timeout: a command in a group of its own outlived the kill"; fi

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
    while alive "$s1" && [ "$i" -lt 250 ]; do sleep 0.02; i=$((i + 1)); done
    if alive "$s1"; then fail "stop wt1: the capped sleeper of wt1 survived"; fi
    alive "$s2" || fail "stop wt1: killed the capped sleeper of wt2"
    alive "$plain" || fail "stop wt1: killed a plain sleep it did not start"
    alive "$stale" || fail "stop wt1: killed a group under a stale entry"
    reg="$main/.git/capped/$key"

    # An entry whose name is digits outside ASCII ("²") is skipped, not a crash: the stop still stops the rest.
    # shellcheck disable=SC2016 # expanded by the capped sh
    (cd "$dir/wt1" && exec "$CAPPED" -- sh -c 'echo $$ >"$1"; exec sleep 30' sh "$dir/s3.pid" 2>/dev/null) &
    pids+=("$!")
    s3="$(pid_in "$dir/s3.pid")"
    pids+=("$s3")
    printf '%s\n1\nx\nx\n' "$dir/wt1" >"$reg/²"
    code=0
    "$CAPPED" stop "$dir/wt1" >"$SANDBOX/out" 2>"$SANDBOX/err" || code=$?
    [ "$code" -eq 0 ] || fail "stop with a ² entry: exit $code ($(tail -1 "$SANDBOX/err"))"
    i=0
    while alive "$s3" && [ "$i" -lt 250 ]; do sleep 0.02; i=$((i + 1)); done
    if alive "$s3"; then fail "stop with a ² entry: the capped sleeper survived"; fi
    rm -f "$reg/²"

    # A forged entry for a live group whose leader is gone, naming this shell as its runner, kills nothing.
    python3 -c 'import os, subprocess, sys; os.setsid(); c = subprocess.Popen(["sleep", "30"])
open(sys.argv[1], "w").write("%d %d" % (os.getpid(), c.pid))' "$dir/orphan"
    orphan="$(cat "$dir/orphan")"
    pids+=("${orphan#* }")
    printf '%s\n%s\n%s\ngone\n' "$dir/wt1" "$$" "$(start_of $$)" >"$reg/${orphan% *}"
    "$CAPPED" stop "$dir/wt1" >"$SANDBOX/out" 2>"$SANDBOX/err" || fail "leaderless entry: the stop failed"
    alive "${orphan#* }" || fail "leaderless entry: killed a group whose leader is gone"

    # Nor does one for group 0 (the stopper's own, to killpg) naming a live runner, nor one for the stopper's group
    # with its leader's true start time.
    s1="$(start_of 1)"
    printf 'x\n1\n%s\n%s\n' "$s1" "$s1" >"$reg/0"
    code=0
    python3 "$SANDBOX/stopper.py" "$reg" "$dir/sentinel" "$CAPPED" stop "$dir/wt1" >"$SANDBOX/out" 2>"$SANDBOX/err" || code=$?
    sentinel="$(cat "$dir/sentinel")"
    pids+=("$sentinel")
    [ "$code" -eq 0 ] || fail "forged entries: the stop exited $code (143: it killed its own group)"
    alive "$sentinel" || fail "forged entries: killed the stopper's group"
    grep -q "stopped 0 group" "$SANDBOX/out" || fail "forged entries: stopped some: $(cat "$SANDBOX/out")"

    # A link planted at the entry's name is not written through: links at the next few hundred pids (made in one
    # process, so they use none up), then a capped run in wt1.
    printf 'victim\n' >"$dir/victim"
    python3 -c 'import os, sys
for n in range(os.getpid() + 1, os.getpid() + 300): os.symlink(sys.argv[1], os.path.join(sys.argv[2], str(n)))' \
      "$dir/victim" "$reg"
    (cd "$dir/wt1" && "$CAPPED" -- true) || fail "planted link: the capped run failed"
    [ "$(cat "$dir/victim")" = victim ] || fail "planted link: the entry was written through it: $(cat "$dir/victim")"
    find "$reg" -type l -exec rm -f {} +

    # The start times are read in the C locale: a group registered under another locale is still stopped.
    other=""
    if command -v ps >/dev/null && LC_ALL=C ps -o lstart= -p $$ >/dev/null 2>&1; then
      for l in $(locale -a 2>/dev/null | grep -Ei '^(de_DE|fr_FR|es_ES|pt_PT|pt_BR|it_IT)\.utf-?8$' || true); do
        if [ "$(LC_ALL="$l" ps -o lstart= -p $$ 2>/dev/null)" != "$(LC_ALL=C ps -o lstart= -p $$)" ]; then other="$l"; break; fi
      done
    fi
    if [ -z "$other" ]; then
      echo "capped.test.sh ($how): no locale whose ps lstart differs from C's, locale case skipped"
    else
      # shellcheck disable=SC2016 # expanded by the capped sh
      (cd "$dir/wt1" && LC_ALL="$other" exec "$CAPPED" -- sh -c 'echo $$ >"$1"; exec sleep 30' sh "$dir/s4.pid" 2>/dev/null) &
      pids+=("$!")
      s3="$(pid_in "$dir/s4.pid")"
      pids+=("$s3")
      LC_ALL=C "$CAPPED" stop "$dir/wt1" >/dev/null 2>&1 || fail "locale: the stop failed"
      i=0
      while alive "$s3" && [ "$i" -lt 250 ]; do sleep 0.02; i=$((i + 1)); done
      if alive "$s3"; then fail "locale: a group registered under $other survived a stop in C"; fi
    fi
  fi
}

if command -v ps >/dev/null || [ "$procfs" -eq 0 ]; then cases ps; fi
if [ "$procfs" -eq 1 ]; then
  PATH="$nops" cases proc
  PATH="$badps" cases failps
fi

[ "$fails" -eq 0 ] || { echo "capped.test.sh: $fails failure(s)"; exit 1; }
echo "capped.test.sh: ok"
