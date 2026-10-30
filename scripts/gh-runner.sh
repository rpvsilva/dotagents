#!/usr/bin/env bash
# Serves a repo's GitHub Actions jobs from ephemeral self-hosted runners in this machine's Docker, so CI spends no
# hosted minutes. Each runner is a fresh container of GitHub's runner image: it registers, takes one job, exits, and
# a new one with a new registration token takes its place. Runs in the foreground until Ctrl-C (or TERM), which
# removes its containers and deregisters their runners.
# Usage: scripts/gh-runner.sh [--once] [owner/repo]   (default repo: this checkout's origin)
#   --once: each runner serves one job and the script exits, instead of starting a new runner after each job.
# ci.yml runs on these runners only while the repo variable CI_RUNNER holds their label:
#   gh variable set CI_RUNNER --body dotagents     (unset it to go back to GitHub-hosted runners)
# Needs docker, and the App behind scripts/gh-app-token.sh with the "Administration: write" repository permission
# (to create registration tokens and remove runners). Without that permission, set GH_RUNNER_TOKEN to a token from
# the repo's Settings > Actions > Runners > New self-hosted runner: it registers runners for 1 hour, after which new
# runners fail until you restart with a fresh one; runners stopped before their job are then left for GitHub to drop.
# Settings: RUNNER_COUNT (3: one per ci.yml job), RUNNER_LABEL (dotagents),
# RUNNER_IMAGE (ghcr.io/actions/actions-runner:2.338.0), RUNNER_RETRY_SECONDS (30: wait after a failed start).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API="${GITHUB_API_URL:-https://api.github.com}"
count="${RUNNER_COUNT:-3}"
label="${RUNNER_LABEL:-dotagents}"
image="${RUNNER_IMAGE:-ghcr.io/actions/actions-runner:2.338.0}"
retry="${RUNNER_RETRY_SECONDS:-30}"

once=""
[ "${1:-}" != "--once" ] || { once=1; shift; }
repo="${1:-$(git -C "$HERE" remote get-url origin | sed -E 's#^.*github\.com[:/]##; s#\.git$##')}"
[[ "$repo" == */* ]] || { echo "gh-runner: not an owner/repo: $repo" >&2; exit 1; }
[[ "$count" =~ ^[1-9][0-9]*$ ]] || { echo "gh-runner: RUNNER_COUNT must be a positive number: $count" >&2; exit 1; }
command -v docker >/dev/null || { echo "gh-runner: docker not found" >&2; exit 1; }

# Runner and container names start with this, so stopping can find this run's runners and nothing else.
prefix="$label-$(hostname -s | tr -c 'A-Za-z0-9-\n' -)-$$"
say() { echo "gh-runner: $*" >&2; }

api() {
  local token
  token="$("$HERE/gh-app-token.sh" "$repo")" || return 1
  curl -fsS -H "Authorization: Bearer $token" -H "Accept: application/vnd.github+json" "$@"
}

# One runner slot: start a container, wait for its job, repeat (once with --once).
slot() {
  local i="$1" n=0 name token
  while :; do
    n=$((n + 1))
    name="$prefix-$i-$n"
    if [ -n "${GH_RUNNER_TOKEN:-}" ]; then
      token="$GH_RUNNER_TOKEN"
    elif ! token="$(api -X POST "$API/repos/$repo/actions/runners/registration-token" | jq -er .token)"; then
      say "$name: no registration token (does the App have Administration: write on $repo?); retrying in ${retry}s"
      sleep "$retry"
      continue
    fi
    say "$name: waiting for a job"
    # The token goes in through the environment, so it never shows in this machine's process list.
    if ! RUNNER_TOKEN="$token" docker run --rm --name "$name" --label "dotagents.gh-runner=$prefix" -e RUNNER_TOKEN \
      "$image" bash -c '
        ./config.sh --unattended --ephemeral --url "$1" --token "$RUNNER_TOKEN" --name "$2" --labels "$3" &&
          exec ./run.sh' _ "https://github.com/$repo" "$name" "$label" </dev/null; then
      say "$name: exited with an error${GH_RUNNER_TOKEN:+ (GH_RUNNER_TOKEN lasts 1 hour; has it expired?)}; retrying in ${retry}s"
      sleep "$retry"
    fi
    [ -z "$once" ] || return 0
  done
}

stop() {
  trap - INT TERM EXIT
  # shellcheck disable=SC2046 # one word per job
  kill $(jobs -p) 2>/dev/null || true
  docker ps -aq --filter "label=dotagents.gh-runner=$prefix" | xargs docker rm -f >/dev/null 2>&1 || true
  # A runner stopped before its job stays registered (offline); remove this run's.
  api "$API/repos/$repo/actions/runners?per_page=100" 2>/dev/null |
    jq -r --arg p "$prefix-" '.runners[] | select(.name | startswith($p)) | .id' 2>/dev/null |
    while read -r id; do api -X DELETE "$API/repos/$repo/actions/runners/$id" >/dev/null || true; done
}
trap 'stop; exit 130' INT TERM
trap stop EXIT

say "$count runner(s) labelled $label for $repo ($image); Ctrl-C to stop"
for i in $(seq "$count"); do slot "$i" & done
wait
