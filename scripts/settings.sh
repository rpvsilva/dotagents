#!/usr/bin/env bash
# Reads and writes Settings: named values skills and scripts act on, each declared with its kind, default and meaning
# (GLOSSARY.md). install.sh links it as ~/.agents/bin/settings; skills and scripts never read the files themselves.
# Usage: settings [--dir DIR] get <section> <key> [--machine|--repo]
#        settings [--dir DIR] set <section> <key> <value> --machine|--repo
#        settings [--dir DIR] unset <section> <key> --machine|--repo
#        settings [--dir DIR] list [section]...
#        settings [--dir DIR] env <section>
#        settings [--dir DIR] check
# A section is a skill's name, `remote` (scripts/remote-agent.sh), or `shared` for the keys several skills read.
# Declarations: skills/<skill>/settings.json, skills/_shared/settings.json (shared) and remote/settings.json; each key
# has a kind (int with optional min and max, bool, string, enum with its choices, list of strings), a default (null:
# unset), a meaning, and optionally `levels` (default both; e.g. ["machine"]) and `env` (the variable scripts read).
# A skill reads a shared key only by opting in with `"<key>": {"shared": true}` in its own Declaration.
# Levels, strongest first: flag (the key's `env` variable, when set), repo (<git root of DIR>/.agents/settings.json),
# machine (${XDG_CONFIG_HOME:-~/.config}/dotagents/settings.json), default. Within a Level, a skill's own entry for a
# shared key ({"build-epic": {"max_parallel": 2}}) beats the top-level one ({"max_parallel": 4}).
# get prints the effective value (a list one item per line, nothing when unset); with --machine or --repo, only what
# that Level holds. set validates the value (a list as a comma-separated string) and writes it through a temp file;
# `set shared <key>` writes the top-level entry. list prints `<section>.<key> TAB <value> TAB <level> TAB <meaning>`
# for every key the sections read, the meaning ending with the values it takes. env prints `export VAR=value` lines
# for the section's keys that have an `env` variable and a value (a true bool as 1, a list joined by commas), and
# `unset VAR` for a false bool, since scripts read any non-empty value as true. check validates both files whole: unknown keys, bad values, keys set at a Level they do not allow.
# A bad value fails loudly, naming the file and key, instead of falling back to the default. Exit 2 on an unknown
# section or key.
# shellcheck disable=SC2016 # the single-quoted strings are jq programs, expanded by jq
set -euo pipefail

# Follow the link from ~/.agents/bin to this repo, where the Declarations live.
src="${BASH_SOURCE[0]}"
while [ -L "$src" ]; do
  dir="$(cd "$(dirname "$src")" && pwd)"
  src="$(readlink "$src")"
  [[ "$src" == /* ]] || src="$dir/$src"
done
REPO="$(cd "$(dirname "$src")/.." && pwd)"
MACHINE_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/dotagents/settings.json"

die() { echo "settings: $*" >&2; exit 1; }
# jq on Windows ends its lines with CRLF: drop the CR, so its output compares and is written as on Unix. pipefail
# keeps jq's exit status.
jq() { command jq "$@" | tr -d '\r'; }
usage() { grep -E "^# +(Usage: )?settings " "$src" | sed -E "s/^# +(Usage: )?/usage: /" >&2; exit 2; }

dir="$PWD"
if [ "${1:-}" = --dir ]; then
  [ $# -ge 2 ] || usage
  dir="$2"
  shift 2
fi
cmd="${1:-}"
[ $# -eq 0 ] || shift

REPO_FILE=""
if root="$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null)"; then REPO_FILE="$root/.agents/settings.json"; fi

# Every Declaration as one object: {"shared": {...}, "<skill>": {...}, "remote": {...}}.
decls() {
  local d f s
  # Every skill is a section, with or without Settings of its own.
  for d in "$REPO"/skills/*/ "$REPO/remote/"; do
    s="$(basename "$d")"
    [ "$s" != _shared ] || s=shared
    f="${d%/}/settings.json"
    if [ -f "$f" ]; then
      jq --arg s "$s" '{($s): .}' "$f" || die "$f: invalid JSON"
    else
      jq -n --arg s "$s" '{($s): {}}'
    fi
  done | jq -s add
}

# Prints a settings file's content, or null when it does not exist.
content() {
  [ -n "$1" ] && [ -f "$1" ] || { echo null; return; }
  jq -e 'type == "object"' "$1" >/dev/null 2>&1 || die "$1: not a JSON object"
  cat "$1"
}

LIB='
def describe($d):
  if $d.kind == "int" then "a whole number"
    + (if $d.min != null and $d.max != null then " from \($d.min) to \($d.max)"
       elif $d.min != null then " of at least \($d.min)" elif $d.max != null then " of at most \($d.max)" else "" end)
  elif $d.kind == "bool" then "true or false"
  elif $d.kind == "enum" then "one of: " + ($d.choices | join(", "))
  elif $d.kind == "list" then "a list of strings"
  else "a string" end;
def kindok($d):
  if $d.kind == "int" then type == "number" and . == floor and ($d.min == null or . >= $d.min) and ($d.max == null or . <= $d.max)
  elif $d.kind == "bool" then type == "boolean"
  elif $d.kind == "enum" then . as $v | type == "string" and any($d.choices[]; . == $v)
  elif $d.kind == "list" then type == "array" and all(.[]; type == "string")
  else type == "string" end;
def coerce($d; $s):
  (if $d.kind == "int" then (if ($s | test("^-?[0-9]+$")) then ($s | tonumber) else null end)
   elif $d.kind == "bool" then {"true": true, "yes": true, "1": true, "false": false, "no": false, "0": false}[$s | ascii_downcase]
   elif $d.kind == "list" then $s | split(",") | map(gsub("^\\s+|\\s+$"; "")) | map(select(. != ""))
   else $s end) as $v
  | if $v != null and ($v | kindok($d)) then {value: $v} else {error: "\($s | tojson) is not \(describe($d))"} end;
def allowed($d): $d.levels // ["machine", "repo"];
# The declaration of key $k read through section $s: {d, own} or null. A skill reads a shared key only when its
# Declaration opts in with {"shared": true}.
def decl($s; $k):
  if $s == "shared" then ($decls.shared[$k] | if . then {d: ., own: true} else null end)
  elif $decls[$s][$k] == null then null
  elif $decls[$s][$k].shared == true then ($decls.shared[$k] | if . then {d: ., own: false} else null end)
  else {d: $decls[$s][$k], own: true} end;
# Where a file holds $k for $s: its own entry, then (a shared key read through a skill) the top-level one.
def target($s; $k): if $s == "shared" then [$k] else [$s, $k] end;
def paths($s; $k; $own): [target($s; $k)] + (if $own or $s == "shared" then [] else [[$k]] end);
def levels: [{name: "repo", file: $repofile, data: $repo}, {name: "machine", file: $machinefile, data: $machine}]
  | map(select(.data != null and ($only == "" or .name == $only)));
def resolve($s; $k):
  decl($s; $k) as $x
  | if $x == null then {error: "unknown setting \($s).\($k)", unknown: true} else
    $x.d as $d | paths($s; $k; $x.own) as $paths
    | if $only == "" and $d.env != null and ($ENV[$d.env] // "") != "" then
        coerce($d; $ENV[$d.env]) as $c
        | if $c.error then {error: "\($d.env): \($c.error)"} else {value: $c.value, level: "flag"} end
      else
        first((levels[] as $l | $paths[] as $p | ($l.data | try getpath($p) catch null) as $v
          | select($v != null) | {l: $l, p: $p, v: $v}), null) as $hit
        | if $hit == null then {value: (if $only == "" then $d.default else null end), level: "default"}
          elif (allowed($d) | index($hit.l.name)) == null then
            {error: "\($hit.l.file): \($hit.p | join(".")) can only be set at the \(allowed($d) | join(" or ")) Level"}
          elif ($hit.v | kindok($d) | not) then
            {error: "\($hit.l.file): \($hit.p | join(".")) is \($hit.v | tojson), not \(describe($d))"}
          else {value: $hit.v, level: $hit.l.name} end
      end
    end;
def show: if type == "array" then join(",") elif . == null then "" else tostring end;
'

# Runs jq program $1 (after LIB) with the Declarations and both files; extra jq arguments follow.
q() {
  local prog="$1" d m r
  shift
  d="$(decls)" && m="$(content "$MACHINE_FILE")" && r="$(content "$REPO_FILE")" || exit 1
  jq -nr --argjson decls "$d" --argjson machine "$m" --argjson repo "$r" --arg machinefile "$MACHINE_FILE" \
    --arg repofile "$REPO_FILE" --arg only "${ONLY:-}" "$LIB $prog" "$@"
}

# Sets $level from a --machine or --repo argument, and $file from it.
level_file() {
  case "${1:-}" in
    --machine) level=machine file="$MACHINE_FILE" ;;
    --repo)
      level=repo file="$REPO_FILE"
      [ -n "$file" ] || die "not in a git repo, so there is no repo Level: $dir"
      ;;
    *) usage ;;
  esac
}

# Writes JSON on stdin to file $1 through a temp file in the same folder.
write() {
  local tmp
  mkdir -p "$(dirname "$1")"
  tmp="$(mktemp "$(dirname "$1")/.settings.json.XXXXXX")"
  if ! jq -e 'if type == "object" then . else error("not an object") end' >"$tmp"; then rm -f "$tmp"; die "could not write $1"; fi
  mv -f "$tmp" "$1"
}

# Exits 2 when $1.$2 is not declared.
known() {
  q 'decl($s; $k) != null' --arg s "$1" --arg k "$2" | grep -q true || { echo "settings: unknown setting $1.$2" >&2; exit 2; }
}

case "$cmd" in
  get)
    [ $# -ge 2 ] && [ $# -le 3 ] || usage
    ONLY=""
    if [ $# -eq 3 ]; then level_file "$3"; ONLY="$level"; fi
    r="$(ONLY="$ONLY" q 'resolve($s; $k)' --arg s "$1" --arg k "$2")"
    if [ "$(jq -r '.unknown // false' <<<"$r")" = true ]; then echo "settings: $(jq -r .error <<<"$r")" >&2; exit 2; fi
    [ "$(jq -r '.error // empty' <<<"$r")" = "" ] || die "$(jq -r .error <<<"$r")"
    jq -r '.value | if type == "array" then .[] elif . == null then empty else tostring end' <<<"$r"
    ;;
  set)
    [ $# -eq 4 ] || usage
    level_file "$4"
    known "$1" "$2"
    r="$(q 'decl($s; $k).d as $d
      | if (allowed($d) | index($level)) == null then {error: "\($s).\($k) can only be set at the \(allowed($d) | join(" or ")) Level"}
        else coerce($d; $v) end' --arg s "$1" --arg k "$2" --arg v "$3" --arg level "$level")"
    [ "$(jq -r '.error // empty' <<<"$r")" = "" ] || die "$1.$2: $(jq -r .error <<<"$r")"
    new="$(q '($old // {}) | if $s != "shared" and (.[$s] | type) != "object" and .[$s] != null then error("\($s) is not an object")
        else setpath(target($s; $k); $v) end' --arg s "$1" --arg k "$2" --argjson v "$(jq -c .value <<<"$r")" \
      --argjson old "$(content "$file")")" || die "could not write $file"
    write "$file" <<<"$new"
    ;;
  unset)
    [ $# -eq 3 ] || usage
    level_file "$3"
    known "$1" "$2"
    [ -f "$file" ] || exit 0
    new="$(q '$old | delpaths([target($s; $k)]) | if $s != "shared" and .[$s] == {} then del(.[$s]) else . end' \
      --arg s "$1" --arg k "$2" --argjson old "$(content "$file")")" || die "could not write $file"
    write "$file" <<<"$new"
    ;;
  list)
    if [ $# -eq 0 ]; then
      # shellcheck disable=SC2046 # section names hold no spaces
      set -- $(decls | jq -r 'keys_unsorted | (map(select(. == "shared")) + map(select(. != "shared")))[]')
    fi
    for s in "$@"; do q '$decls[$s] != null' --arg s "$s" | grep -q true || {
      echo "settings: unknown section $s" >&2; exit 2; }; done
    lines="$(q '$ARGS.positional[] as $s | $decls[$s] | keys_unsorted | sort[] as $k | resolve($s; $k) as $r
         | decl($s; $k).d as $d
         | "\($s).\($k)\t\(if $r.error then "ERROR: " + $r.error else ($r.value | show) end)\t\($r.level // "")\t\($d.meaning) (\(describe($d)))"' --args "$@")"
    printf '%s\n' "$lines"
    if grep -q $'\tERROR: ' <<<"$lines"; then exit 1; fi
    ;;
  env)
    [ $# -eq 1 ] || usage
    r="$(q '[$decls[$s] // {} | to_entries[] | select(.value.env != null) | .key as $k | .value.env as $e
      | resolve($s; $k) | if .error then {error} else {e: $e, v: .value} end]' --arg s "$1")"
    err="$(jq -r 'map(.error // empty) | first // empty' <<<"$r")"
    [ -z "$err" ] || die "$err"
    jq -r '.[] | select(.v != null) | if .v == false then "unset \(.e)" else "export \(.e)=\(if .v == true then "1" else (.v | if type == "array" then join(",") else tostring end) end | @sh)" end' <<<"$r"
    ;;
  check)
    [ $# -eq 0 ] || usage
    q '[levels[] as $l | $l.data | to_entries[] as $e
       | if ($e.value | type) == "object" and $decls[$e.key] != null and $e.key != "shared" then
           ($e.value | keys_unsorted[] as $k | if decl($e.key; $k) == null then "\($l.file): unknown setting \($e.key).\($k)" else empty end)
         elif $decls.shared[$e.key] != null then empty
         else "\($l.file): unknown setting \($e.key)" end] as $unknown
       | [$decls | to_entries[] | .key as $s | (.value | keys_unsorted[]) as $k | resolve($s; $k) | .error // empty] as $bad
       | ($unknown + $bad | unique) as $all | if $all == [] then "ok" else ($all[] | "settings: " + .) end' \
      | { read -r first; if [ "$first" = ok ]; then echo ok; else { echo "$first"; cat; } >&2; exit 1; fi; }
    ;;
  *) usage ;;
esac
