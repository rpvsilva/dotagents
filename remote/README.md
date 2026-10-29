# remote

The runner image for remote agents: a sandbox pod (agent-sandbox on Kubernetes) that runs Claude Code headless on a task and opens a PR.

## What is in it

- Claude Code (pinned by `CLAUDE_CODE_VERSION` in the [Dockerfile](Dockerfile)), git, `gh`, `jq`, `python3`, node.
- Nothing private and no credentials, so the image is public and the cluster pulls it without a pull secret. No secret lives on the cluster: a run sends its credentials through stdin when it execs into the sandbox:
  - `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`).
  - `GH_TOKEN`, a `rpvsilva-agent` App token from `scripts/gh-app-token.sh`. git uses it through `gh auth git-credential`.

It runs as `node` (uid 1000) and idles (`sleep infinity`) until a run execs into it. Deleting the sandbox drops both tokens.

## Run start

A run installs this repo before it starts work, so skills and hooks are always current:

```sh
git clone -q --depth 1 https://github.com/rpvsilva/dotagents.git ~/dotagents
~/dotagents/install.sh claude
```

## Build and publish

[`runner-image.yml`](../.github/workflows/runner-image.yml) builds it on PRs that change the Dockerfile, and pushes `ghcr.io/rpvsilva/dotagents-runner:latest` and `:<sha>` when that change reaches `main`.

To build locally: `docker build -t dotagents-runner:dev remote`
