# 🏷️ Releases

[← Back to the docs](README.md)

dotagents is released with [semantic versioning](https://semver.org/). Each release is a git tag `v<x.y.z>`, a GitHub release with its changelog, a section in [`CHANGELOG.md`](../CHANGELOG.md), the version in [`VERSION`](../VERSION), and runner images tagged with the version.

## Install a specific version

A release is a git tag, so installing one is cloning that tag:

```sh
git clone --branch v1.2.0 --depth 1 https://github.com/rpvsilva/dotagents.git && cd dotagents
./install.sh remote pr-loop epics security safety-hooks agent-flow small-tools
```

The [releases page](https://github.com/rpvsilva/dotagents/releases) lists every version and what changed in each.

- **Check what you run**: `./install.sh --version` prints `dotagents 1.2.0` on a release, or `dotagents 1.2.0+<sha>` on a checkout with commits past it (such as `main`).
- **Move to another version**: in the clone, `git fetch --tags && git checkout v1.3.0`, then run `./install.sh` again with the same groups. Its links point into the clone, so the checkout decides what every tool runs. A `--depth 1` clone needs `git fetch --depth 1 origin tag v1.3.0` first.
- **Follow the latest code instead**: clone without `--branch` and `git pull` on `main`. `main` holds every merged change, released or not.
- **Read the major version before upgrading**: a major bump (`1.x` to `2.0.0`) says something you rely on changed. Its changelog section lists the breaking changes.

### Pin the runner image too

The `remote.image` Setting's default is `ghcr.io/rpvsilva/agent-runner:main`, which follows `main`. On a release, pin the image built from the same tag:

```sh
~/.agents/bin/settings set remote image ghcr.io/rpvsilva/agent-runner:1.2.0 --machine
```

## Runner image tags

`.github/workflows/runner-image.yml` publishes `ghcr.io/rpvsilva/agent-runner` for `linux/amd64` and `linux/arm64`. Only `main` builds it; a PR does not.

| Tag | Points at | Moves |
|---|---|---|
| `1.2.0` | The image built from tag `v1.2.0` | Never |
| `1.2`, `1` | The latest release in that minor or major line | On each release in the line |
| `latest` | The latest release | On each release |
| `main` | The latest build of `main` | On a push to `main` that changes `remote/runner/`, and every Monday's rebuild for Debian's security fixes |
| `<yyyymmdd>-<sha>` | One build of `main` | Never |

A release's image is built once and not rebuilt, so `1.2.0` stays the image that release was tested with. Security fixes land in `main` weekly and in the next release.

## How a version is picked

The version comes from the [Conventional Commits](https://www.conventionalcommits.org/) merged since the last release. A PR merges as one squashed commit, so its title is the commit that counts.

| Commit | Bump | Example |
|---|---|---|
| `feat:` | Minor: `1.2.0` to `1.3.0` | `feat(skills): add renovate-prs` |
| `fix:`, `perf:` | Patch: `1.2.0` to `1.2.1` | `fix(remote): retry a claim` |
| `!` after the type, or a `BREAKING CHANGE:` footer | Major: `1.2.0` to `2.0.0` | `feat(install)!: drop the shared tool word` |
| `docs:`, `chore:`, `ci:`, `test:`, `refactor:` | None, and left out of the changelog | `docs: fix a link` |

The highest bump wins: a release holding one `feat:` and three `fix:` commits is a minor release.

## How a release is cut

`.github/workflows/release.yml` runs [release-please](https://github.com/googleapis/release-please) on every push to `main`:

1. It keeps one open PR, `chore(main): release <x.y.z>`, updated with each merge. The PR bumps `VERSION` and `.release-please-manifest.json` and adds the release's section to `CHANGELOG.md`. Nothing else edits those three files.
2. Merging that PR releases: the workflow tags `v<x.y.z>` on the merge commit, creates the GitHub release with the changelog section, then calls `runner-image.yml` to build the image from the tag and publish its version tags.

The release PR is an ordinary medium-tier PR: CI runs on it and `scripts/merge-pr.sh <number>` merges it. Merge it when the changes in it are worth releasing; until then, it waits and grows.

Its configuration is [`release-please-config.json`](../release-please-config.json). `bootstrap-sha` there is where the first release's changelog starts; release-please ignores it after the first release.

### Maintainer setup

The workflow runs as the maintainer's agent GitHub App, not as `GITHUB_TOKEN`: GitHub starts no workflows on a PR opened with `GITHUB_TOKEN`, so the release PR would never get the CI that `main`'s ruleset requires.

1. Give the App **Contents** and **Pull requests** read and write on the repo (it has them for opening and merging PRs).
2. In the repo's **Settings → Secrets and variables → Actions**, add the variable `DOTAGENTS_APP_ID` (the App's ID) and the secret `DOTAGENTS_APP_PRIVATE_KEY` (its private key, the contents of `~/.config/dotagents/github-app.pem`).

Without `DOTAGENTS_APP_ID` the release job is skipped, so a fork never cuts releases by accident.

### Fix a release

- **Force a version**: merge a commit whose body has a `Release-As: 2.0.0` footer. The next release PR takes that version.
- **A bad release**: release a fix as the next patch. Never move or reuse a published tag: people may have installed it. To withdraw one, mark the GitHub release as a pre-release and say why in its notes.
