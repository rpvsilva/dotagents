# remote

Running this setup's agents remotely: Claude Code headless in a sandbox pod (agent-sandbox on Kubernetes), working on a task and opening a PR.

## Where the pieces live

- **The image and the cluster side** live in the cluster's GitOps repo, not here: the `agent-runner` image (`tools/agent-runner`, with what a runner can rely on in its README), the `claude-runner` SandboxTemplate and warm pool (`ai-infrastructure/agents`). The image holds tools only, no one's setup, so anyone can use it.
- **This repo is the client**: it claims a sandbox, execs in, sends the tokens and installs this setup there before the task.

## Run start

Credentials go in through stdin on `kubectl exec`, never as arguments or claim env:

- `CLAUDE_CODE_OAUTH_TOKEN`, from `claude setup-token`.
- `GH_TOKEN`, a `rpvsilva-agent` App token from `scripts/gh-app-token.sh`. git in the image uses it through `gh auth git-credential`.

Then the run installs this repo, so skills and hooks are always current (about 13 s):

```sh
git clone -q --depth 1 https://github.com/rpvsilva/dotagents.git ~/dotagents
~/dotagents/install.sh claude
```

Deleting the sandbox drops both tokens.
