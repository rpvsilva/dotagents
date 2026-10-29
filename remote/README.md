# remote

Running subagents on a remote backend for compute only: a Kubernetes cluster today (Docker on this machine and an SSH host are planned). The laptop keeps the code, the git history and the orchestrating session; the backend gets what one subagent needs for one task and gives back a patch. It never touches a repo or GitHub, so it works for any project, including ones the machine's owners have no access to.

## Where the pieces live

- **The cluster side** lives in the cluster's GitOps repo: the agent-sandbox controller, the `agent-runner` image (`tools/agent-runner`) and the `claude-runner` SandboxTemplate and warm pool (`ai-infrastructure/agents`).
- **This repo is the client**: [`scripts/remote-agent.sh`](../scripts/remote-agent.sh), driving one backend script from [`scripts/remote-backends/`](../scripts/remote-backends/).

## Use

```sh
~/.agents/bin/remote-agent [--attach PATH]... "<brief>" [worktree]   # or @brief.md, or - for stdin
```

`install.sh` links the command there, so skills reach it from any project. Skills use it through [Remote subagents](../skills/_shared/conventions.md#remote-subagents): a subagent a skill marks remote-capable (today `build-epic`'s implementer) runs here whenever `remote.env` exists. `--attach` copies a file or folder from outside the worktree (a ticket, a spec) to `~/attached/<name>` in the sandbox, read-only.

1. [`remote-agent-pack.sh`](../scripts/remote-agent-pack.sh) stages this machine's setup: `CLAUDE.md`, skills, agents, commands, rules, `~/.agents/{skills,hooks}`; `settings.json` cut down to permissions, model, plugins and the hooks that call `~/.agents/hooks`; MCP servers that need no env or headers. Credentials, history, transcripts, caches and settings `env` never leave the machine; what it leaves out is printed.
2. It copies the attachments (minus secret-looking files) and lists the worktree's tracked and unignored files, minus secret-looking ones (the `guard-secrets` rule), and checks the size (`REMOTE_MAX_MB`, 200 by default).
3. It acquires a sandbox from the backend (on the cluster: claims a warm one) and puts the setup and the snapshot in as tar streams. The Claude token goes through stdin.
4. In the sandbox, [`remote-agent-sandbox.sh`](../scripts/remote-agent-sandbox.sh) reinstalls the plugins and MCP servers, commits the snapshot as a base, and runs Claude headless on the brief. Its progress shows on stderr.
5. The patch comes back and is applied to the worktree, uncommitted. The summary goes to stdout. Both, with the full stream, are kept in `<git dir>/remote-agent/<run>/`.
6. The sandbox is released on exit, also on Ctrl-C or `kill`, and with it the token (on the cluster: the claim is deleted, and with it the pod). The backend's time limit (`REMOTE_HOURS`, 2 by default; the claim's `shutdownTime` on the cluster) removes it if the script dies first.

Exit codes: 0 applied, 3 no changes, 4 the patch did not apply (the worktree changed meanwhile; the patch is kept).

## Backends

`REMOTE_BACKEND` in `remote.env` (or the environment) names the backend: `cluster` when unset. Each one is a script, `scripts/remote-backends/<name>.sh`; an unknown name stops the run before anything is staged, listing the known ones. `remote-agent` exports every `REMOTE_*` setting to it (never the Claude token) and drives it with five operations only, so packing, secret filtering, the in-sandbox run, keeping results and exit codes stay the same on every backend:

| Operation | Does |
|---|---|
| `acquire <run>` | Gets a sandbox that removes itself after `REMOTE_HOURS`; prints its handle. |
| `put <run> <handle> <dir>` | Extracts the tar stream on stdin under the sandbox's `$HOME/<dir>`. |
| `run <run> <handle> <argv>...` | Runs the command in the sandbox with stdin, stdout and stderr streamed; returns its exit code. |
| `get <run> <handle> <dir>` | Writes a tar of the sandbox's `$HOME/<dir>` on stdout. |
| `release <run>` | Removes the sandbox, even after a half-done `acquire`; with `REMOTE_KEEP=1` keeps it and says what it kept. |

Shared helpers for backends live in `scripts/remote-backends/lib.sh`, which is not a backend.

### `cluster` (default)

An agent-sandbox SandboxClaim on a warm pool, driven with `kubectl exec`; the handle is the pod. Settings: `REMOTE_CONTEXT` (kube context, `maxibombo`), `REMOTE_NAMESPACE` (`agents`), `REMOTE_POOL` (warm pool, `claude-runner`), `REMOTE_HOURS` (the claim's `shutdownTime`, 2), `REMOTE_KEEP=1` (keep the claim: `kept sandboxclaim/<run>`). Needs a kube context that can create SandboxClaims and exec into pods in the namespace.

## Setup, once

- `CLAUDE_CODE_OAUTH_TOKEN=...` (from `claude setup-token`) in `~/.config/dotagents/remote.env`.
- The backend's needs: for the default `cluster`, a kube context (`maxibombo` by default, `REMOTE_CONTEXT` to change) that can create SandboxClaims and exec into pods in `agents`. See [Backends](#backends).

No GitHub credentials are needed or sent: the PR, if any, is opened from the laptop.

## What the sandbox can reach

On the cluster: internet egress only: Anthropic and package registries work, so agents can install dependencies and run tests. Cluster services and the LAN are blocked. It holds no GitHub credentials and no git remote, so it cannot read or push to any private repo. Anyone with admin rights on the cluster can still exec into a running pod and see the snapshot and the token during a run.
