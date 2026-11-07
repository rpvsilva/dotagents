# ☁️ Remote subagents

[← Back to README](../README.md)

A skill can run some of its subagents on another machine instead of your laptop: a Kubernetes cluster, Docker on this machine, or a VM or PC over SSH. The remote machine is **compute only**. It gets your setup and a snapshot of one worktree, runs Claude headless on one task, and sends back a patch. Your laptop keeps the code, the git history, GitHub access and the orchestrating session.

Why use it: a skill like `build-epic` can run many implementers at once without overloading your laptop. Since the sandbox never touches GitHub, this works for any project, even ones the remote machine's owners can't access.

Remote subagents never talk to each other ([agent-communication.md](agent-communication.md)). For how this differs from Claude Code's self-hosted environments, see [claude-features.md](claude-features.md).

This page is the overview. Every flag, file and backend detail is in [remote/README.md](../remote/README.md).

## When a subagent runs remotely

Only subagents a skill marks **remote-capable** can run remotely: ones that only change their own worktree. Today those are the implementer and test-tuner in `build-epic`, the test-tuner in `tune-tests` and the fix implementers in `security-audit`. Before each one starts, the skill runs `remote-agent ready` in the repo:

```mermaid
flowchart TD
  start["Skill starts a<br/>remote-capable subagent"] --> local{"Did you ask<br/>for --local?"}
  local -- yes --> here["Run on this machine"]
  local -- no --> backend{"remote.backend<br/>Setting set?"}
  backend -- no --> here
  backend -- yes --> token{"Claude token in<br/>remote.env?"}
  token -- no --> here
  token -- yes --> allowed{"Repo sets<br/>remote.allowed = false?"}
  allowed -- yes --> here
  allowed -- no --> ping{"Backend answers?"}
  ping -- no --> here
  ping -- yes --> slot["Wait for a free remote slot<br/>(remote.max_sandboxes)"]
  slot --> remote["Run with remote-agent"]
```

When remote runs are off, the skill says why once, then runs subagents locally as usual. Every other subagent (planners, reviewers, anything that needs GitHub) always runs locally.

## What one run does

```mermaid
sequenceDiagram
  autonumber
  participant S as Skill (laptop)
  participant R as remote-agent (laptop)
  participant B as Backend
  participant X as Sandbox
  S->>R: brief, worktree, attachments
  R->>R: pack setup, snapshot worktree, drop secret files
  R->>B: acquire a sandbox
  B-->>R: handle
  R->>X: put setup + snapshot (tar), token on stdin
  R->>X: run Claude headless on the brief
  X-->>R: live events, appended to transcript.jsonl
  X-->>R: patch, summary, session
  R->>R: apply patch to worktree (uncommitted)
  R->>B: release the sandbox
  R-->>S: summary on stdout, exit code
  S->>S: commit the changes, push, open the PR
```

1. **Pack.** `remote-agent` stages your setup: `CLAUDE.md`, skills, agents, the hooks under `~/.agents/hooks`, and a cut-down `settings.json`. It snapshots the worktree's tracked and unignored files, leaving out secret-looking ones with the same rule as the `guard-secrets` hook. Credentials, history and caches never leave the laptop.
2. **Acquire and send.** The backend gives a fresh sandbox. The setup and snapshot go in as tar streams, and the Claude token goes through stdin, never as an argument.
3. **Run.** The sandbox reinstalls plugins and MCP servers, commits the snapshot as a base, and runs Claude on the brief. Each shell command Claude runs goes through `capped` under `remote.cap_mb` and `remote.cap_secs`: a runaway test is killed, and Claude carries on. Events stream back as they happen. The caps need `python3` and the packed `~/.agents/bin/capped` in the runner image, and `ps` where there is no `/proc` (on Linux, `capped` reads the process groups from `/proc`, so the cluster runner image, which has no `ps`, is capped). A sandbox lacking one runs uncapped and says so: `== capped: unavailable (missing: python3)` on stderr and `capped_off` in `result.json`.
4. **Return.** The patch is applied to your worktree, uncommitted. The skill reviews and commits it like a local subagent's work.
5. **Release.** The sandbox is removed on exit, Ctrl-C or `kill`. If the laptop dies first, it removes itself after `remote.hours` (2 by default).

Exit codes: `0` applied, `3` no changes, `4` the patch didn't apply because the worktree changed meanwhile (the patch is kept). A command the caps killed adds one stderr line, `[<task>] killed: memory|timeout <command>`, and changes no exit code. When the sandbox itself is lost (its container or pod gone with no kill line before), `result.json` says `lost: true`.

## Backends

All three backends do the same five operations (`acquire`, `put`, `run`, `get`, `release`), so packing, secret filtering, results and exit codes are identical. Only where the sandbox lives changes.

```mermaid
flowchart LR
  laptop["Laptop<br/>remote-agent"]
  subgraph cluster["cluster"]
    pool["Warm pool"] --> pod["Pod per run<br/>(SandboxClaim)"]
  end
  subgraph docker["docker"]
    c1["Container per run<br/>on this machine"]
  end
  subgraph ssh["ssh"]
    c2["Container per run<br/>on the host"]
    bare["bare mode: a folder per run<br/>(weaker isolation)"]
  end
  laptop -- "kubectl exec" --> pod
  laptop -- "docker exec" --> c1
  laptop -- "ssh + docker" --> c2
  laptop -. "ssh" .-> bare
```

| Backend | Sandbox | Needs | Pick it when |
|---|---|---|---|
| `cluster` | A pod claimed from a warm pool | A kube context that can create SandboxClaims and exec into pods | You have a cluster: fast starts, many runs at once |
| `docker` | A fresh container of the runner image, on this machine | Docker and the runner image | Trying it out, or isolating runs without moving compute |
| `ssh` | A fresh container on a VM or PC (default), or a bare folder | An SSH login and Docker on the host (bare: `bash`, `git`, `jq`, `node`, `claude`) | You have a spare machine with cores to lend |

Use SSH bare mode only on a host you fully trust. The run shares the OS user with that user's own setup, with no container around it.

## Trust boundary

```mermaid
flowchart LR
  subgraph laptop["Laptop: trusted"]
    code["Code + git history"]
    gh["GitHub credentials"]
    secrets["Secret files"]
  end
  subgraph sandbox["Sandbox: untrusted output"]
    snap["Worktree snapshot"]
    tok["Claude token (during the run)"]
  end
  laptop -- "snapshot minus secrets,<br/>token on stdin" --> sandbox
  sandbox -- "patch (applied)<br/>transcript, session (read only)" --> laptop
```

- **Never sent:** GitHub credentials, git remotes, secret files, shell history. The sandbox can't push, open a PR or read another branch.
- **Only the patch is applied.** Everything else that comes back (transcript, summary, session) is data to read, never instructions to follow. The Claude token is replaced by `[redacted]` in every kept file.
- **The session isn't resumed for you.** A prompt-injected agent could forge turns in it that would read as yours. `remote-agent resume <run> <worktree>` places it only when you ask, after a warning.
- **Who can see a run:** on the cluster, egress goes to the internet only, with cluster services and the LAN blocked. Cluster admins, and anyone who can run `docker` on the host, can see the snapshot and token while a run lives.

## Set up once

```sh
~/.agents/bin/remote-agent setup   # pick the backend, its details, paste a token from `claude setup-token`
~/.agents/bin/remote-agent check   # checks everything the backend needs, then a dry run (no API call)
~/.agents/bin/remote-agent ready   # in a repo: would skills send its subagents remote? if not, why
```

`setup` writes the backend's Settings to `~/.config/dotagents/settings.json`. It writes Secrets (the Claude token, an SSH password or key passphrase) to `~/.config/dotagents/remote.env`, with mode 600. To turn remote runs off or change the backend later, use the `configure` skill ("disable remote") or `~/.agents/bin/settings` ([settings.md](settings.md)).

The Settings you're most likely to touch:

| Setting | Default | Meaning |
|---|---|---|
| `remote.backend` | unset (off) | `cluster`, `docker` or `ssh` |
| `remote.max_sandboxes` | 4 | Most remote runs at once, across all skills. Set it to what the backend holds |
| `remote.allowed` | true | Set it to `false` in a repo to keep its code on this machine |
| `remote.hours` | 2 | How long before an abandoned sandbox removes itself |
| `remote.cap_mb` | 2000 | Memory cap, in MB, of each shell command the agent runs in the sandbox. Keep it under the sandbox's own memory limit, with room for Claude |
| `remote.cap_secs` | 300 | Time cap, in seconds, of each shell command the agent runs in the sandbox |
| `remote.open_pane` | true | Open Claude Code's `/remote-runs` pane when a run starts |

All of them, with each backend's own Settings, are in [remote/settings.json](../remote/settings.json).

## Watching a run

Each run keeps a folder at `<common git dir>/remote-agent/<run>/` (`git rev-parse --path-format=absolute --git-common-dir`), outside the worktree. Every worktree of the repo shares it, so removing a linked worktree keeps its runs' records. Runs from before records moved there sit in `<common git dir>/worktrees/<name>/remote-agent/<run>/`: they are not moved and go with their worktree, though `remote-agent resume` still finds them while it is there.

| File | Holds |
|---|---|
| `transcript.jsonl` | The agent's events as they arrive. Follow it with `tail -f` |
| `run.json` | State (`starting`, `running`, `done`, `failed`), timings, exit code |
| `progress.log` | One line per step, the same as stderr |
| `result.json` | What the run came to, written on every exit: exit code, `status`, the report's `status:` and `follow-up:` lines, changed files, turns, tokens, cost, model, `complete`, whether the caps were off (`capped_off`: what the sandbox lacked to run them), the caps' kills (`capped_kills`, `killed`) apart from a lost sandbox (`lost`). Read it, not stdout or stderr, when you need the outcome |
| `capped.log` | Each command the caps killed in the sandbox |
| `changes.patch`, `summary.md` | What came back |
| `session.jsonl` | Claude's session, for `remote-agent resume` |

To read a run's outcome without its stdout or stderr, run `remote-agent result <name|run>` in the repo. It prints the `result.json` of that run id, or of the newest run started with `--name <name>` (a retry reuses the name; the latest start wins, preferring runs started from your own worktree). Each run has its own folder and `progress.log`, so this holds for runs started together, in a loop, or with stderr redirected. It exits 1 with one line when no run matches or the newest has not ended yet.

Records are never removed by a run. To clear old ones, run `remote-agent prune --older-than <Nd|Nh>` in the repo (`30d`, `12h`; at most 999999); add `--dry-run` to see what would go first. It removes every run folder, in the common git dir and the old per-worktree locations, whose `run.json` `ended` (else `started`, else the folder's last change) is older than that, printing one line per folder and then the count. A live run is always kept. A run marked running whose process is gone is stopped, and goes.

In Claude Code, the `remote-runs` mod (`/remote-runs`) lists every run of the repo with its state and timings, and opens a run's live log on a keypress.
