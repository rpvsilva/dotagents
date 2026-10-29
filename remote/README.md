# remote

The runner image for remote agents: a sandbox pod (agent-sandbox on Kubernetes) that runs Claude Code headless on a task and opens a PR.

## What is in it

- Claude Code (pinned by `CLAUDE_CODE_VERSION` in the [Dockerfile](Dockerfile)), git, `gh`, `jq`, `python3`, node.
- This repo at `/home/node/dotagents`, with `install.sh claude` already run: skills, agents, the `guard-merge` and `guard-secrets` hooks, plugins and MCP servers.
- No credentials. A run brings them:
  - `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`), set on the pod from a Kubernetes Secret.
  - `GH_TOKEN`, a `rpvsilva-agent` App token from `scripts/gh-app-token.sh`, passed per run. git uses it through `gh auth git-credential`.

It runs as `node` (uid 1000) and idles (`sleep infinity`) until a run execs into it.

## Build and publish

[`runner-image.yml`](../.github/workflows/runner-image.yml) builds it on PRs that touch it, and pushes `ghcr.io/rpvsilva/dotagents-runner:latest` and `:<sha>` on every push to `main`. The package is private; the cluster pulls it with an image pull secret.

To build locally (from the repo root): `docker build -f remote/Dockerfile -t dotagents-runner:dev .`

## Refresh at run start

The image holds a snapshot of this repo. Skills and hooks are links into it, so a run updates them in place before it starts work:

```sh
git -C ~/dotagents fetch --depth 1 origin main && git -C ~/dotagents reset --hard FETCH_HEAD
~/dotagents/install.sh claude   # only when a skill, agent, plugin or MCP server was added
```

Rebuild the image (merge to `main`, or run the workflow) only when tools change: Claude Code, node, apt packages.
