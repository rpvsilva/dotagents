# remote

Running subagents on a Kubernetes cluster for compute only. The laptop keeps the code, the git history and the orchestrating session; the cluster gets what one subagent needs for one task and gives back a patch. It never touches a repo or GitHub, so it works for any project, including ones the cluster's owners have no access to.

## Where the pieces live

- **The cluster side** lives in the cluster's GitOps repo: the agent-sandbox controller, the `agent-runner` image (`tools/agent-runner`) and the `claude-runner` SandboxTemplate and warm pool (`ai-infrastructure/agents`).
- **This repo is the client**: [`scripts/remote-agent.sh`](../scripts/remote-agent.sh).

## Use

```sh
~/.agents/bin/remote-agent [--attach PATH]... "<brief>" [worktree]   # or @brief.md, or - for stdin
```

`install.sh` links the command there, so skills reach it from any project. Skills use it through [Remote subagents](../skills/_shared/conventions.md#remote-subagents): a subagent a skill marks remote-capable (today `build-epic`'s implementer) runs here whenever `remote.env` exists. `--attach` copies a file or folder from outside the worktree (a ticket, a spec) to `~/attached/<name>` in the sandbox, read-only.

1. [`remote-agent-pack.sh`](../scripts/remote-agent-pack.sh) stages this machine's setup: `CLAUDE.md`, skills, agents, commands, rules, `~/.agents/{skills,hooks}`; `settings.json` cut down to permissions, model, plugins and the hooks that call `~/.agents/hooks`; MCP servers that need no env or headers. Credentials, history, transcripts, caches and settings `env` never leave the machine; what it leaves out is printed.
2. It copies the attachments (minus secret-looking files) and lists the worktree's tracked and unignored files, minus secret-looking ones (the `guard-secrets` rule), and checks the size (`REMOTE_MAX_MB`, 200 by default).
3. It claims a warm sandbox and streams the setup and the snapshot in with `kubectl exec`. The Claude token goes through stdin.
4. In the pod, [`remote-agent-sandbox.sh`](../scripts/remote-agent-sandbox.sh) reinstalls the plugins and MCP servers, commits the snapshot as a base, and runs Claude headless on the brief. Its progress shows on stderr.
5. The patch comes back and is applied to the worktree, uncommitted. The summary goes to stdout. Both, with the full stream, are kept in `<git dir>/remote-agent/<claim>/`.
6. The claim is deleted, and with it the pod and the token. A 2-hour `shutdownTime` deletes it if the script dies first.

Exit codes: 0 applied, 3 no changes, 4 the patch did not apply (the worktree changed meanwhile; the patch is kept).

## Setup, once

- `CLAUDE_CODE_OAUTH_TOKEN=...` (from `claude setup-token`) in `~/.config/dotagents/remote.env`.
- A kube context for the cluster (`my-cluster` by default, `REMOTE_CONTEXT` to change) that can create SandboxClaims and exec into pods in `agents`.

No GitHub credentials are needed or sent: the PR, if any, is opened from the laptop.

## What the sandbox can reach

Internet egress only: Anthropic and package registries work, so agents can install dependencies and run tests. Cluster services and the LAN are blocked. It holds no GitHub credentials and no git remote, so it cannot read or push to any private repo. Anyone with admin rights on the cluster can still exec into a running pod and see the snapshot and the token during a run.
