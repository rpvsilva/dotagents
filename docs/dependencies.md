# 📌 Dependencies

[← Back to README](../README.md)

This page records which pins [Renovate](https://docs.renovatebot.com/) keeps current and which stay hand-bumped, and why. The config is [`renovate.json`](../renovate.json). Renovate opens one PR per group, once a week (before 6am on Monday, Lisbon time), and only for a release at least 3 days old. Nothing automerges.

## Every pin

| Pin | Where | Who bumps it | Why | Group, schedule | Tier of its PR |
|---|---|---|---|---|---|
| Runner base image (`node` tag and digest) | `remote/runner/Dockerfile` | Renovate | A digest pin never moves by itself | `runner image`, weekly | medium |
| Claude Code version | `remote/runner/Dockerfile` (`CLAUDE_CODE_VERSION`) | Renovate | The `# renovate:` comment above the `ARG` marks it for the regex manager | `runner image`, weekly | medium |
| GitHub Actions SHAs | `.github/workflows/*.yml` (`uses: <owner>/<repo>@<sha>`) | Renovate | A SHA pin never moves by itself; Renovate keeps the version comment next to it | `GitHub Actions`, weekly | high |
| actionlint image | `.github/workflows/ci.yml` (`docker://rhysd/actionlint:<tag>`) | Renovate | Same as the Actions SHAs; Renovate adds a digest to the tag | `GitHub Actions`, weekly | high |
| shellcheck version | `scripts/shellcheck-version` | Renovate | Moves with the image: `tests/repo.test.sh` fails when the two disagree | `shellcheck`, weekly | high |
| shellcheck image | `scripts/shellcheck-image` | Renovate | Same PR as the version file, so the pair never splits | `shellcheck`, weekly | high |
| `agent-pr-gate` commit | `.github/workflows/agent-pr-gate.yml`, `scripts/merge-pr.sh` | by hand | It is the judge of every PR and sits in two files that must match; a bot must not change the rules that judge its own PR | none | high |
| CI node major | `node-version: 24` in `.github/workflows/ci.yml` | by hand | A major is a decision about what the suites run on, not a routine bump; Renovate's `node` rule for `github-actions` is disabled | none | high |

## Tiers

A Renovate PR is judged by [`.review-gate.yml`](../.review-gate.yml) like any other, because `scope.bots` is `true`. Its tier is the highest one among the paths it touches:

- **medium:** the runner image group (`remote/runner/`). An agent may merge it once the evidence and CI are green.
- **high:** the Actions and shellcheck groups (`.github/`, `scripts/`). It waits for the code owner's approval.

The gate wants evidence in the PR body. Every Renovate PR carries the `## Testing`, `## Risk` and `## How to revert` sections, from `prBodyTemplate` in `renovate.json`.

## Keeping the shape Renovate needs

`tests/repo.test.sh` fails when:

- `renovate.json` does not parse, or its `enabledManagers` lacks `github-actions`;
- a `uses: <owner>/<repo>@<ref>` in `.github/workflows/*.yml` is not a 40-hex SHA (a `docker://` ref is exempt).

To change a hand-bumped pin, edit it in both places where it appears and run `tests/run.sh`. To move a pin to Renovate, add it to `renovate.json` and to the table above in the same change.
