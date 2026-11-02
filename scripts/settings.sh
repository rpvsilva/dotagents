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

# Each command is one jq run: starting a process costs ~100 ms on Windows, and scripts call this often. So this
# script forks as little as it can: the Declaration files go to jq as input files, the settings files are read with
# the `read` builtin, and jq answers with the command's output or one `!<exit code> <message>` line.

# Every skill is a section, with or without Settings of its own; `sections` names them, `decl_files` holds the
# Declarations that exist.
sections=""
decl_files=()
for d in "$REPO"/skills/*/ "$REPO/remote/"; do
  d="${d%/}"
  s="${d##*/}"
  [ "$s" != _shared ] || s=shared
  sections="$sections $s"
  [ ! -f "$d/settings.json" ] || decl_files+=("$d/settings.json")
done

# Each settings file's text, and whether it exists (`read` returns non-zero at end of file, which is expected).
machine_raw="" has_machine=0 repo_raw="" has_repo=0
if [ -f "$MACHINE_FILE" ]; then IFS= read -r -d '' machine_raw <"$MACHINE_FILE" || true; has_machine=1; fi
if [ -n "$REPO_FILE" ] && [ -f "$REPO_FILE" ]; then IFS= read -r -d '' repo_raw <"$REPO_FILE" || true; has_repo=1; fi

# Binds $decls ({"shared": {...}, "<skill>": {...}, "remote": {...}}), $machine and $repo (a settings file's object,
# or null when it does not exist) for LIB, or stops on a settings file that is not a JSON object.
PRE='
def parsefile($raw; $has):
  if $has == 0 then null else ($raw | try fromjson catch null) as $j | if ($j | type) == "object" then $j else "bad" end end;
(reduce inputs as $d ({}; . + {(input_filename | gsub("\\\\"; "/") | split("/") | .[-2]
  | if . == "_shared" then "shared" else . end): $d})) as $loaded
| (($sections | split(" ") | map(select(. != "") | {(.): {}}) | add // {}) + $loaded) as $decls
| parsefile($machine_raw; $has_machine) as $machine | parsefile($repo_raw; $has_repo) as $repo
| if $machine == "bad" then "!1 settings: \($machinefile): not a JSON object"
  elif $repo == "bad" then "!1 settings: \($repofile): not a JSON object"
  else ('

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

# Runs jq program $1 (after PRE and LIB), extra jq arguments following, and leaves its output in $out. On a
# `!<code> <message>` answer, prints the message and exits with the code.
q() {
  local prog="$1"
  shift
  out="$(jq -nr --arg sections "$sections" --arg machine_raw "$machine_raw" --argjson has_machine "$has_machine" \
    --arg repo_raw "$repo_raw" --argjson has_repo "$has_repo" --arg machinefile "$MACHINE_FILE" \
    --arg repofile "$REPO_FILE" --arg only "${ONLY:-}" "$@" "$PRE $LIB $prog) end" ${decl_files[@]+"${decl_files[@]}"})" \
    || exit 1
  # jq on Windows ends its lines with CRLF: drop the CR, so the output compares and is written as on Unix.
  out="${out//$'\r'/}"
  if [[ "$out" == '!'* ]]; then
    printf '%s\n' "${out:3}" >&2
    exit "${out:1:1}"
  fi
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

# Writes $out, a JSON object, to file $1 through a temp file in the same folder.
write() {
  local tmp
  mkdir -p "${1%/*}"
  tmp="$(mktemp "${1%/*}/.settings.json.XXXXXX")"
  if ! printf '%s\n' "$out" >"$tmp"; then rm -f "$tmp"; die "could not write $1"; fi
  mv -f "$tmp" "$1"
}

# The file at the Level being written, as LIB binds it.
CUR='(if $level == "machine" then $machine else $repo end)'
UNKNOWN='"!2 settings: unknown setting \($s).\($k)"'

case "$cmd" in
  get)
    [ $# -ge 2 ] && [ $# -le 3 ] || usage
    ONLY=""
    if [ $# -eq 3 ]; then level_file "$3"; ONLY="$level"; fi
    q 'resolve($s; $k) as $r
      | if $r.unknown then "!2 settings: \($r.error)" elif $r.error then "!1 settings: \($r.error)"
        else $r.value | if type == "array" then .[] elif . == null then empty else tostring end end' --arg s "$1" --arg k "$2"
    [ -z "$out" ] || printf '%s\n' "$out"
    ;;
  set)
    [ $# -eq 4 ] || usage
    level_file "$4"
    q "decl(\$s; \$k) as \$x
      | if \$x == null then $UNKNOWN
        elif (allowed(\$x.d) | index(\$level)) == null then
          \"!1 settings: \(\$s).\(\$k): can only be set at the \(allowed(\$x.d) | join(\" or \")) Level\"
        else coerce(\$x.d; \$v) as \$c
          | if \$c.error then \"!1 settings: \(\$s).\(\$k): \(\$c.error)\"
            else ($CUR // {}) | if \$s != \"shared\" and (.[\$s] | type) != \"object\" and .[\$s] != null
              then \"!1 settings: \(\$file): \(\$s) is not an object\" else setpath(target(\$s; \$k); \$c.value) end end end" \
      --arg s "$1" --arg k "$2" --arg v "$3" --arg level "$level" --arg file "$file"
    write "$file"
    ;;
  unset)
    [ $# -eq 3 ] || usage
    level_file "$3"
    q "if decl(\$s; \$k) == null then $UNKNOWN elif $CUR == null then empty
      else $CUR | delpaths([target(\$s; \$k)]) | if \$s != \"shared\" and .[\$s] == {} then del(.[\$s]) else . end end" \
      --arg s "$1" --arg k "$2" --arg level "$level"
    [ -z "$out" ] || write "$file"
    ;;
  list)
    # The named sections, else every one: shared first.
    q '($wanted | split(" ") | map(select(. != ""))) as $w
      | (if $w == [] then ["shared"] + ($decls | keys_unsorted | map(select(. != "shared"))) else $w end) as $ss
      | ($ss | map(select($decls[.] == null))) as $bad
      | if $bad != [] then "!2 settings: unknown section \($bad[0])" else
        $ss[] as $s | $decls[$s] | keys_unsorted | sort[] as $k | resolve($s; $k) as $r | decl($s; $k).d as $d
        | "\($s).\($k)\t\(if $r.error then "ERROR: " + $r.error else ($r.value | show) end)\t\($r.level // "")\t\($d.meaning) (\(describe($d)))"
        end' --arg wanted "$*"
    printf '%s\n' "$out"
    if [[ "$out" == *$'\tERROR: '* ]]; then exit 1; fi
    ;;
  env)
    [ $# -eq 1 ] || usage
    q '[$decls[$s] // {} | to_entries[] | select(.value.env != null) | .key as $k | .value.env as $e
        | resolve($s; $k) | if .error then {error} else {e: $e, v: .value} end] as $all
      | (first($all[] | .error // empty) // null) as $err
      | if $err then "!1 settings: \($err)" else
        $all[] | select(.v != null) | if .v == false then "unset \(.e)"
          else "export \(.e)=\(if .v == true then "1" else (.v | if type == "array" then join(",") else tostring end) end | @sh)" end
        end' --arg s "$1"
    [ -z "$out" ] || printf '%s\n' "$out"
    ;;
  check)
    [ $# -eq 0 ] || usage
    q '[levels[] as $l | $l.data | to_entries[] as $e
       | if ($e.value | type) == "object" and $decls[$e.key] != null and $e.key != "shared" then
           ($e.value | keys_unsorted[] as $k | if decl($e.key; $k) == null then "\($l.file): unknown setting \($e.key).\($k)" else empty end)
         elif $decls.shared[$e.key] != null then empty
         else "\($l.file): unknown setting \($e.key)" end] as $unknown
       | [$decls | to_entries[] | .key as $s | (.value | keys_unsorted[]) as $k | resolve($s; $k) | .error // empty] as $bad
       | ($unknown + $bad | unique) as $all
       | if $all == [] then "ok" else "!1 " + ($all | map("settings: " + .) | join("\n")) end'
    printf '%s\n' "$out"
    ;;
  *) usage ;;
esac
