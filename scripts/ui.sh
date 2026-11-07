#!/usr/bin/env bash
# Draws install.sh's or uninstall.sh's plain output as a live view: one line per tool with a spinner and a progress bar,
# the lines worth reading kept (backups, restores, warnings, failures, dry-run plans) and a summary at the end.
# Usage: <plain output> | scripts/ui.sh <install|uninstall> [the script's arguments]
# The scripts pipe through it only when stdout is a terminal; DOTAGENTS_PLAIN=1 keeps the plain lines. NO_COLOR drops
# the colours. It never changes the exit code: the scripts return their own.
set -uo pipefail

mode="${1:-install}"
shift || true
target=all dry=0 picked=""
for a in "$@"; do
  case "$a" in
    all | shared | claude | codex | cursor) target="$a" ;;
    --dry-run) dry=1 ;;
    -*) ;;
    *) picked+=" $a" ;; # a group or part: it picks what to install, not the tools
  esac
done
# How many tool sections the run prints, for the progress bar.
case "$mode:$target" in
  *:all) total=4 ;;
  install:shared) total=1 ;;
  install:*) total=2 ;;
  *) total=1 ;;
esac

e=$'\033'
if [ -z "${NO_COLOR:-}" ]; then
  B="${e}[1m" D="${e}[2m" R="${e}[0m" GR="${e}[32m" YE="${e}[33m" RD="${e}[31m" CY="${e}[36m" MA="${e}[35m"
else
  B="" D="" R="" GR="" YE="" RD="" CY="" MA=""
fi
CL=$'\r'"${e}[K"
cols="$(tput cols 2>/dev/null || echo 80)"
case "$cols" in '' | *[!0-9]*) cols=80 ;; esac
frames=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)
frame=0
# Bash 3.2 (macOS) takes whole seconds only, so its spinner turns once a second.
if [ "${BASH_VERSINFO[0]}" -ge 4 ]; then tick=0.08; else tick=1; fi

tilde='~'
short() { printf '%s' "${1//"$HOME"/$tilde}"; }

# Per section, and for the whole run.
section="" steps=0 notes=""
# Each kind of line counted, with its label; s_<kind> per section, t_<kind> for the run.
kinds="linked:linked changed:updated removed:removed backups:backed_up restored:restored same:unchanged planned:planned failed:failed"
# shellcheck disable=SC2034 # read indirectly, through counts and eval
s_linked=0 s_changed=0 s_removed=0 s_backups=0 s_restored=0 s_same=0 s_planned=0 s_failed=0
# shellcheck disable=SC2034
t_linked=0 t_changed=0 t_removed=0 t_backups=0 t_restored=0 t_same=0 t_planned=0 t_failed=0
last=""
trailer=""

bar() {
  local w=18 f i on="" off=""
  f=$((steps * w / total))
  [ "$f" -le "$w" ] || f=$w
  for ((i = 0; i < w; i++)); do
    if [ "$i" -lt "$f" ]; then on+="━"; else off+="─"; fi
  done
  printf '%s%s%s%s%s' "$MA" "$on" "$D" "$off" "$R"
}

# "12 linked · 2 updated" from the s_ (section) or t_ (run) counters $1.
counts() {
  local k v label out=""
  for k in $kinds; do
    v="$1${k%%:*}" label="${k#*:}"
    [ "${!v}" -eq 0 ] || out+=" · ${!v} ${label//_/ }"
  done
  out="${out# · }"
  printf '%s' "${out:-nothing to do}"
}

add() { eval "s_$1=\$((s_$1 + 1))"; }

draw() {
  [ -n "$section" ] || return 0
  local room act
  room=$((cols - 48))
  act="$(short "$last")"
  [ "$room" -gt 3 ] || act=""
  [ "${#act}" -le "$room" ] || act="…${act:${#act}-room+1}"
  printf '%s %s%s%s %s%-7s%s %s %s%d/%d%s  %s%s%s' "$CL" "$CY" "${frames[frame]}" "$R" "$B" "$section" "$R" \
    "$(bar)" "$D" "$steps" "$total" "$R" "$D" "$act" "$R"
}

end_section() {
  [ -n "$section" ] || return 0
  local icon="${GR}✓$R"
  [ "$s_failed" -eq 0 ] || icon="${RD}✗$R"
  printf '%s %s %s%-7s%s %s%s%s\n' "$CL" "$icon" "$B" "$section" "$R" "$D" "$(counts s_)" "$R"
  [ -z "$notes" ] || printf '%s' "$notes"
  local k
  for k in $kinds; do k="${k%%:*}"; eval "t_$k=\$((t_$k + s_$k)) s_$k=0"; done
  last="" notes="" section=""
  steps=$((steps + 1))
}

note() { notes+="$(printf '     %s %s' "$1" "$(short "$2")")"$'\n'; }

line() {
  local l="$1" body
  case "$l" in
    "  "*) ;;
    "") return ;;
    *" not found, skipping")
      end_section
      printf '%s %s○ %-7s not found, skipped%s\n' "$CL" "$D" "${l%%:*}" "$R"
      steps=$((steps + 1))
      return
      ;;
    shared | claude | codex | cursor)
      end_section
      section="$l"
      draw
      return
      ;;
    merge-json:* | *"not a JSON object"*) ;; # stderr from a helper: an indented failure, below
    "✘"*)
      # A CLI's own error (claude plugin ...): kept under the tool it belongs to.
      if [ -n "$section" ]; then note "  ${RD}↳${R}" "${l#✘ }"; draw; else trailer+="$l"$'\n'; fi
      return
      ;;
    *)
      trailer+="$l"$'\n'
      return
      ;;
  esac
  body="${l#  }"
  last="$body"
  case "$body" in
    *failed* | *"not a JSON object"* | merge-json:*) add failed; note "${RD}✗$R" "$body" ;;
    "would "*) add planned; note "${YE}→$R" "${body#would }" ;;
    "backed up "*) add backups; note "${CY}↺$R" "$body" ;;
    "restored "*) add restored; note "${CY}↺$R" "$body" ;;
    *" -> "*) add linked ;;
    *already*) add same ;;
    *skipped* | *skipping* | *"add it to"* | *trust*) note "${YE}!$R" "$body" ;;
    "deleted "*) add removed; note "${D}-$R" "$body" ;;
    *"entries removed"*) add changed ;;
    *removed* | *uninstalled*) add removed ;;
    *merged* | *installed* | *added*) add changed ;;
    *) note "${D}·$R" "$body" ;;
  esac
  draw
}

printf '\033[?25l'
trap 'printf "\033[?25h"' EXIT
trap 'printf "%s\033[?25h\n" "$CL"; exit 130' INT TERM

start=$SECONDS
title="$mode · $target"
[ -z "$picked" ] || title+=" ·$picked"
[ "$dry" -eq 0 ] || title+=" · dry run"
printf '\n %sdotagents%s %s%s%s\n\n' "$B" "$R" "$D" "$title" "$R"

buf=""
while :; do
  if IFS= read -r -t "$tick" l; then
    line "$buf$l"
    buf=""
  elif [ $? -gt 128 ]; then
    buf+="$l"
    frame=$(((frame + 1) % ${#frames[@]}))
    draw
  else
    [ -z "$buf$l" ] || line "$buf$l"
    break
  fi
done
end_section

took=$((SECONDS - start))
sum=" · $(counts t_)"
printf '\n'
if [ "$steps" -eq 0 ]; then
  printf ' %s✗ Stopped%s\n' "${RD}$B" "$R"
elif [ "$t_failed" -gt 0 ]; then
  printf ' %s✗ Finished with problems%s %sin %ds%s%s\n' "${RD}$B" "$R" "$D" "$took" "${sum}" "$R"
elif [ "$dry" -eq 1 ]; then
  printf ' %s✓ Dry run, nothing changed%s %sin %ds%s%s\n' "${GR}$B" "$R" "$D" "$took" "${sum}" "$R"
elif [ "$mode" = uninstall ]; then
  printf ' %s✓ Uninstalled%s %sin %ds%s%s\n' "${GR}$B" "$R" "$D" "$took" "${sum}" "$R"
else
  printf ' %s✓ Installed%s %sin %ds%s%s\n' "${GR}$B" "$R" "$D" "$took" "${sum}" "$R"
fi
[ -z "$trailer" ] || printf '%s' "$trailer" | while IFS= read -r l; do printf '   %s%s%s\n' "$D" "$(short "$l")" "$R"; done
printf '\n'
