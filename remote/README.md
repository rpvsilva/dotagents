# remote

Running this setup's agents remotely: Claude Code headless in a sandbox pod (agent-sandbox on Kubernetes), working on a task and opening a PR.

## Where the pieces live

- **The image and the cluster side** live in the cluster's GitOps repo, not here: the `agent-runner` image (`tools/agent-runner`, with what a runner can rely on in its README), the `claude-runner` SandboxTemplate and warm pool (`ai-infrastructure/agents`). The image holds tools only, no one's setup, so anyone can use it.
- **This repo is the client**: [`scripts/remote-run.sh`](../scripts/remote-run.sh) claims a sandbox, execs in, sends the tokens and runs [`scripts/remote-run-sandbox.sh`](../scripts/remote-run-sandbox.sh) there, which installs this setup, lets Claude work and opens the PR.

## Use

```sh
scripts/remote-run.sh <owner/repo> "<task>"       # or @task.md, or - for stdin
```

It prints Claude's summary and the PR URL, opened as `rpvsilva-agent` on branch `remote/run-<timestamp>-<n>`. It exits 3 when Claude committed nothing. The claim is deleted at the end; its 2-hour `shutdownTime` deletes it if the script dies first. `REMOTE_KEEP=1` keeps it for debugging; the script's header lists the other overrides.

Setup, once:

- `CLAUDE_CODE_OAUTH_TOKEN=...` (from `claude setup-token`) in `~/.config/dotagents/remote.env`.
- The GitHub App (`scripts/gh-app-token.sh`) installed on the target repo; it already covers this one.
- A kube context for the cluster (`maxibombo` by default) that can create SandboxClaims and exec into pods in `agents`.

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
