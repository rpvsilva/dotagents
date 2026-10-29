# remote

Running subagents on a remote backend for compute only: a Kubernetes cluster, Docker on this machine, or a VM or PC over SSH. The laptop keeps the code, the git history and the orchestrating session; the backend gets what one subagent needs for one task and gives back a patch. It never touches a repo or GitHub, so it works for any project, including ones the machine's owners have no access to.

## Where the pieces live

- **The cluster side** lives in the cluster's GitOps repo: the agent-sandbox controller, the `agent-runner` image (`tools/agent-runner`, also used by the `docker` and `ssh` backends) and the `claude-runner` SandboxTemplate and warm pool (`ai-infrastructure/agents`).
- **This repo is the client**: [`scripts/remote-agent.sh`](../scripts/remote-agent.sh), driving one backend script from [`scripts/remote-backends/`](../scripts/remote-backends/).

## Use

```sh
~/.agents/bin/remote-agent [--attach PATH]... [--name TASK] "<brief>" [worktree]   # or @brief.md, or - for stdin
```

`install.sh` links the command there, so skills reach it from any project. Skills use it through [Remote subagents](../skills/_shared/conventions.md#remote-subagents): a subagent a skill marks remote-capable (today `build-epic`'s implementer) runs here whenever `remote.env` exists. `--attach` copies a file or folder from outside the worktree (a task, a spec) to `~/attached/<name>` in the sandbox, read-only. `--name` names the task (e.g. a task's `TT-NN-<slug>`), made a valid label value (lowercase, `[a-z0-9-]`, at most 63 characters): every progress line on stderr starts with `[<task>]` (`[<run>]` without it), so several runs in parallel stay readable, and the backend labels the sandbox with it.

1. [`remote-agent-pack.sh`](../scripts/remote-agent-pack.sh) stages this machine's setup: `CLAUDE.md`, skills, agents, commands, rules, `~/.agents/{skills,hooks}`; `settings.json` cut down to permissions, model, plugins and the hooks that call `~/.agents/hooks`; MCP servers that need no env or headers. Credentials, history, transcripts, caches and settings `env` never leave the machine; what it leaves out is printed.
2. It copies the attachments (minus secret-looking files) and lists the worktree's tracked and unignored files, minus secret-looking ones (the `guard-secrets` rule), and checks the size (`REMOTE_MAX_MB`, 200 by default).
3. It acquires a sandbox from the backend (on the cluster: claims a warm one) and puts the setup and the snapshot in as tar streams. The Claude token goes through stdin.
4. In the sandbox, [`remote-agent-sandbox.sh`](../scripts/remote-agent-sandbox.sh) reinstalls the plugins and MCP servers, commits the snapshot as a base, and runs Claude headless on the brief. Its progress shows on stderr, and in the sandbox's own log with every step of `remote-agent` (`kubectl logs <pod>`; `progress.log` in the sandbox's home where there is no such log). The token never goes there.
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
| `check` | Names, one line each, what this backend needs and is missing; fails when anything is. Used by `remote-agent check`. |

Shared helpers for backends live in `scripts/remote-backends/lib.sh`, which is not a backend.

### `cluster` (default)

An agent-sandbox SandboxClaim on a warm pool, driven with `kubectl exec`; the handle is the pod. Settings: `REMOTE_CONTEXT` (kube context, `maxibombo`), `REMOTE_NAMESPACE` (`agents`), `REMOTE_POOL` (warm pool, `claude-runner`), `REMOTE_HOURS` (the claim's `shutdownTime`, 2), `REMOTE_KEEP=1` (keep the claim: `kept sandboxclaim/<run>`), `REMOTE_CLAIM_TIMEOUT` (seconds to wait for the claim to be Ready, 180; on a timeout the claim's conditions are printed, and a claim the controller fails stops the run at once with its reason). The claim and its pod carry the labels `sandbox.users.io/remote-agent-run=<run>` and, with `--name`, `sandbox.users.io/remote-agent-task=<task>`: `kubectl get pods -L sandbox.users.io/remote-agent-task` tells the runs apart, and `kubectl logs -f <pod>` follows one. The labels sit under `sandbox.users.io` because agent-sandbox rejects pod labels outside its allowed domains (`AllowedLabelDomains`, default `sandbox.users.io`). Needs a kube context that can create SandboxClaims and exec into pods in the namespace.

### `docker`

A fresh container of the runner image on this machine for each run, driven with `docker exec`; the handle is the container, named after the run. It runs `sleep <REMOTE_HOURS*3600>` under `--rm`, so it removes itself after `REMOTE_HOURS` if `remote-agent` dies; otherwise `docker rm -f` removes it at the end, also on failure, Ctrl-C or `kill`. Like the cluster's pods, it carries the labels `sandbox.users.io/remote-agent-run=<run>` and, with `--name`, `sandbox.users.io/remote-agent-task=<task>`: `docker ps --filter label=sandbox.users.io/remote-agent-task` lists the named runs, and `docker logs -f <run>` follows one. Needs only Docker and the image. Settings, in `remote.env`:

- `REMOTE_BACKEND=docker`.
- `REMOTE_IMAGE`: the runner image, `ghcr.io/xgeekshq/agent-runner:main` by default. It is private: log in once with `docker login ghcr.io` (your GitHub user and a token with `read:packages`), or build it from `tools/agent-runner` in the cluster's GitOps repo (`docker build -t agent-runner:local tools/agent-runner`) and set `REMOTE_IMAGE=agent-runner:local`.
- `REMOTE_PLATFORM`: passed to `docker run --platform` when set (e.g. `linux/amd64`); unset, Docker picks this machine's architecture.
- `REMOTE_HOURS` (2) and `REMOTE_KEEP=1` (keep the container: `kept container <run>`; it still removes itself after `REMOTE_HOURS`).

**Apple Silicon** needs the image published for `linux/arm64` as well as `linux/amd64`, or built locally as above. An amd64-only image runs under emulation with `REMOTE_PLATFORM=linux/amd64`, but slowly.

The container gets Docker's default network: internet egress, and whatever this machine's network reaches. Anyone who can run `docker` here can see the snapshot and the token during a run.

### `ssh`

A VM or PC reached with `ssh`. In container mode (the default) it is the `docker` backend run on that host: a fresh container of the runner image per run, each `docker` command sent as one `ssh -T -o BatchMode=yes <host> '<command>'` call with every word single-quoted, so any login shell (bash, zsh, dash) runs it as written. Files travel as tar streams through the connection, and the token through stdin: no `scp`, no temp files on either side, and the token never in `ssh` arguments. Containers are named after the run and labelled as for `docker`, so several runs can share the host; each is removed at the end, and `--rm` with its `sleep` removes it after `REMOTE_HOURS` if `remote-agent` dies. Settings, in `remote.env`:

- `REMOTE_BACKEND=ssh`.
- `REMOTE_SSH_HOST`: the ssh destination, `user@host` or an alias from `~/.ssh/config` (where a port, key or jump host go).
- `REMOTE_SSH_MODE`: `container` (the default) or `bare` (see [Bare mode](#bare-mode-trusted-hosts-only)). Any other value stops the run, naming both.
- `REMOTE_IMAGE`, `REMOTE_PLATFORM`, `REMOTE_HOURS` and `REMOTE_KEEP=1` as for [`docker`](#docker), applied on the host.

In container mode the host needs:

- **Key-based SSH** that works without a prompt (`BatchMode=yes` never asks for a password or passphrase: use an agent or an unencrypted key). Check with `ssh -o BatchMode=yes <host> true`.
- **Docker**, with the SSH user in the `docker` group (`sudo usermod -aG docker <user>`, then log in again). Check with `ssh <host> docker info`.
- **The image**: log in once on the host (`ssh -t <host> docker login ghcr.io`) or build it there, and set `REMOTE_IMAGE` to its tag. Pick a `REMOTE_PLATFORM` or image that matches the host's architecture.

**Who can see the code and the token during a run** in container mode: the host's root and every member of its `docker` group, since they can `docker exec` into the container or inspect it. Use a host only you (and people you trust with the token) administer. The container gets Docker's default network on the host: internet egress, and whatever the host's network reaches.

#### Bare mode (trusted hosts only)

With `REMOTE_SSH_MODE=bare` the run happens directly on the host, as the SSH user, with no container. **This is weaker isolation**: the run shares the OS user with that user's own setup and with other runs, has no network limits beyond the host's own, and the SSH user (and the host's root) can read the code and the Claude token while a run lives. Prefer container mode whenever the host has Docker or is shared with anyone else.

- Each run gets its own folder, `$REMOTE_SSH_DIR/<run>` (mode 700), used as its `HOME`, with `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME` and Claude Code's config (`CLAUDE_CONFIG_DIR`) inside it: the setup, plugins, MCP servers, token and files stay apart from the host user's `~/.claude` and from other runs. The folder is removed at the end; `REMOTE_KEEP=1` keeps it (`kept run folder ...`).
- The host needs the same key-based SSH as container mode, and no Docker.
- Before anything is sent, the host is checked for `bash`, `tar`, `git`, `jq`, `node` and `claude` on the SSH user's `PATH` (non-interactive login: set it in the shell's non-interactive startup file); each missing one is named and the run stops.
- A client that dies leaves its folder behind: the next run's acquire removes run folders older than `REMOTE_HOURS`.
- The sandbox log is `progress.log` in the run folder (there is no container log), never the host's `/proc/1/fd/1`, even as root.
- Settings: `REMOTE_SSH_DIR`, the run folders' root on the host, absolute or relative to the SSH user's home (`.remote-agent/runs` by default); `REMOTE_HOURS` and `REMOTE_KEEP=1` as above. `REMOTE_IMAGE` and `REMOTE_PLATFORM` do not apply.

## Setup, once

```sh
~/.agents/bin/remote-agent setup   # asks for the backend, its settings and a Claude token; writes remote.env
~/.agents/bin/remote-agent check   # checks the backend's needs, then a dry run
```

`setup` asks for the backend and its details (cluster: kube context, by default the saved one or else the current one, namespace and pool; SSH: host and mode; Docker: nothing more; the runner image for Docker and SSH container mode). It then asks you to run `claude setup-token` in another terminal and paste the token, which it reads without echo and never prints; enter keeps the token already there. It writes `~/.config/dotagents/remote.env` readable only by you (mode 600), keeping settings it does not ask about (`REMOTE_HOURS`, `REMOTE_KEEP`, ...). Stopping it midway (Ctrl-C, end of input) writes nothing.

`check` proves the setup without spending tokens. It names each missing thing on its own line, then ends with a dry run, and exits 0 only when everything passes (it prints `ok` last):

- `remote.env` exists (without it, it says so and points to `setup`) and sets `CLAUDE_CODE_OAUTH_TOKEN`; the token is checked as set, never printed, and not sent anywhere.
- The backend's needs. `cluster`: `kubectl` on `PATH`, the kube context, and `kubectl auth can-i` to create SandboxClaims and `pods/exec` in the namespace. `docker`: the Docker daemon (`docker info`), the image present or pullable, and a note when the image's architecture differs from the machine's (it would run emulated). `ssh`: the host reachable with `ssh -o BatchMode=yes`, then the `docker` checks run on the host (container mode) or the tools bare mode needs (bare mode).
- The dry run, only when nothing is missing: acquire a sandbox, put a one-file snapshot in `~/work`, run a shell command that copies it to `~/out` and finds `claude`, get `~/out` back and compare, release. No Claude call. The sandbox is released even when a step fails, and the failing step is named.

By hand instead: put `CLAUDE_CODE_OAUTH_TOKEN=...` (from `claude setup-token`) and the backend's settings (see [Backends](#backends)) in `~/.config/dotagents/remote.env`, then `chmod 600` it. For the default `cluster`, that needs a kube context (`maxibombo` by default, `REMOTE_CONTEXT` to change) that can create SandboxClaims and exec into pods in `agents`; for `docker`, Docker and the runner image; for `ssh`, key-based SSH to a host with Docker and the image (bare mode: the tools listed under [Bare mode](#bare-mode-trusted-hosts-only) instead).

Without `remote.env`, skills run their subagents on this machine, as before.

No GitHub credentials are needed or sent: the PR, if any, is opened from the laptop.

## What the sandbox can reach

On the cluster: internet egress only: Anthropic and package registries work, so agents can install dependencies and run tests. Cluster services and the LAN are blocked. It holds no GitHub credentials and no git remote, so it cannot read or push to any private repo. Anyone with admin rights on the cluster can still exec into a running pod and see the snapshot and the token during a run.
