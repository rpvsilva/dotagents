# Plan: remove the self-hosted runners

The self-hosted runners (PR #88, squashed as commit `0f48e0d`) were a stopgap while GitHub-hosted minutes were out. This plan takes every trace of them out of the repo, GitHub and this machine, and puts CI back on GitHub-hosted runners.

**Done when** `git grep -n -e CI_RUNNER -e gh-runner -e self-hosted` finds nothing on `main` or in any open PR, the repo has no `CI_RUNNER` variable, and CI on the removal PR is green on GitHub-hosted runners.

## Before you start

Hosted minutes must be back (Settings > Billing: spending limit raised or the monthly quota reset). Without them every check fails once the runners are gone: the jobs never start.

## 1. Turn the runners off

Do this first, so no job queues for a runner that is about to disappear.

1. `gh variable delete CI_RUNNER`. Every workflow then asks for `ubuntu-latest` again, so this alone moves CI back to GitHub's runners.
2. Stop `scripts/gh-runner.sh` (Ctrl-C in its terminal), then remove what is left: `docker ps -aq --filter label=dotagents.gh-runner | xargs docker rm -f`.
3. In Settings > Actions > Runners, remove every `dotagents-*` runner still listed.
4. Optional: `docker rmi ghcr.io/actions/actions-runner:2.338.0`, and set the `rpvsilva-agent` App's "Administration" permission back to "No access" if nothing else needs it.

Done when `gh variable list` has no `CI_RUNNER`, the `docker ps` filter above lists nothing, and the Runners page is empty.

## 2. Revert #88 on a branch

```sh
git switch -c chore/remove-self-hosted-runners origin/main
git revert --no-edit 0f48e0d
```

The revert removes `scripts/gh-runner.sh` and `tests/gh-runner.test.sh`, and puts back:

- `runs-on: ubuntu-latest` in every job of `ci.yml` and `agent-pr-gate.yml`.
- In `ci.yml`, shellcheck without the install line (hosted runners ship it) and actionlint as `uses: docker://rhysd/actionlint:1.7.12` with `args: -color`.
- In `portability.yml`, the `pull_request` trigger (branches `[main]`, the `paths` list kept in its header comment) and its original header.
- In `AGENTS.md`, "Windows when shell code changes (`portability.yml`)", without the `CI_RUNNER` sentence or the `gh-runner.test.sh` entry in the Checks list.

When a later commit touched the same lines the revert conflicts; resolve every hunk to the list above and keep everything else the later commit added.

Done when the revert is committed and `git status` is clean.

## 3. Remove what other PRs added

Some PRs were rebased onto #88 and carry their own self-hosted pieces, which the revert does not touch. In 2026-10 that was #73 (check-parity): a `shellcheck (self-hosted)` step with sha256-checked downloads, an `if: ${{ !vars.CI_RUNNER }}` on its Docker shellcheck step, and the `gh-runner.test.sh` entry in its `AGENTS.md` Checks list. #79 (follow-remote-run) carried that same `AGENTS.md` entry.

1. On the branch, run `git grep -n -e CI_RUNNER -e gh-runner -e self-hosted`. For each hit, delete the self-hosted step or line and keep the hosted path as it is: a step guarded by `if: ${{ !vars.CI_RUNNER }}` loses only its `if:` line.
2. Delete this plan (`docs/plans/remove-self-hosted-runners.md`) in the same branch: its job is done.
3. For each open PR, run `git diff origin/main...origin/<branch> | grep -n -e CI_RUNNER -e gh-runner -e self-hosted` and remove what it finds from that PR. Rebase each one onto `main` after the removal PR merges.

Done when the `git grep` in step 1 finds nothing on the branch and the per-PR grep finds nothing in any open PR.

## 4. Check and ship

1. Run `tests/run.sh` and `actionlint`. Both pass.
2. Open the PR as the App, following [AGENTS.md](../../AGENTS.md) > Pull requests. Under `Testing`, list what you ran and the greps' empty output. It touches `.github/`, `scripts/` and `tests/`, so it needs Rui's approval.
3. Merge with `scripts/merge-pr.sh <number>` once CI passes on GitHub-hosted runners, the Windows job included.
