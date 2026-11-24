# ⚖️ Compared with Claude features

[← Back to the docs](README.md)

> **TL;DR** Self-hosted environments move *whole Claude sessions* into your network. Cowork is for knowledge work, not code. This setup sends *single subagent tasks* to spare compute and keeps GitHub off the remote side. Different jobs, no overlap worth switching for.

Two Claude features look like this setup's [remote subagents](remote.md): Claude Code's **self-hosted environments** and **Claude Cowork**. They solve different problems, and neither replaces this setup.

## ⚡ In one line each

- **This setup (`remote-agent`):** a local session sends some subagent tasks to other compute (a cluster, Docker or an SSH host) and gets a patch back.
- **[Self-hosted environments](https://code.claude.com/docs/en/self-hosted-environments):** Claude Code's cloud sessions run on runners in your network instead of Anthropic's.
- **[Claude Cowork](https://support.claude.com/en/articles/13345190-getting-started-with-cowork):** an agent mode for knowledge work (documents, browsing, apps), with no terminal needed, running on Anthropic's cloud.

## 🆚 Side by side

| | [This setup](remote.md) | [Self-hosted environments](https://code.claude.com/docs/en/self-hosted-environments) | [Claude Cowork](https://support.claude.com/en/articles/13345190-getting-started-with-cowork) |
|---|---|---|---|
| What runs remotely | One **subagent task** | A whole **session** | A whole **task** |
| Who's in charge | Your local session | Anthropic's control plane queues it; your runner claims it | Anthropic's cloud; the Desktop app reaches your files and browser |
| Started from | A skill, during a local run | claude.ai, desktop, mobile, `claude --cloud`, routines | Desktop, web, mobile, Chrome, schedules |
| Where compute runs | Your cluster, Docker or an SSH host | Your runners (e.g. on Kubernetes) | Anthropic's servers |
| Code and git | Snapshot minus secrets, **no GitHub access**; a patch comes back and the laptop commits | The runner **clones with git credentials** and can push and open PRs | Folders you connect, plus connectors (MCP) |
| What leaves your machines | The conversation goes to the Anthropic API | The conversation and transcript go to Anthropic | Files and work live in your Claude account |
| Plans | Any (a `claude setup-token` token) | Team and Enterprise, public beta, an Owner turns it on | Paid plans |
| Agents talking | No, hub-and-spoke ([agent-communication.md](agent-communication.md)) | One session per task; nothing between sessions | Runs subagents in parallel; its docs don't say whether they talk |
| Other tools | Yes, the skills are portable | Claude only | Claude only |
| You maintain | The runner image and backend | The runner image, fleet and network | Nothing |

## 💭 What it means for us

**Self-hosted environments** change *where Claude's cloud sessions run*. They suit a team that starts sessions from the web or phone and needs them inside its network, close to internal services. It's not a drop-in for us:

- The runner clones the repo and holds git credentials, so the "no GitHub access in the sandbox" guarantee is gone.
- It runs whole sessions, not subagent tasks handed out by a local orchestrator.
- It's Team or Enterprise only, in beta, and needs an org Owner to turn it on.

It could become a fourth `remote-agent` backend (dispatched with `claude --environment`) if the org enables it, as long as we accept the weaker trust boundary for those runs.

**Cowork** is for knowledge work, not code in worktrees. It runs on Anthropic's servers, reaches your machine only through the Desktop app, and has no worktree, patch or review flow. It doesn't overlap with remote subagents.

**This setup** is the only one of the three that:

- fans out from a local session, which keeps the code, git history and GitHub access;
- applies only a patch from the remote side, treating everything else as data;
- works on any plan and in any tool, the **portable first** principle.

## 📎 Sources

Facts about the Claude features are from their docs as of November 2026 and may change:

- Self-hosted environments: [overview](https://code.claude.com/docs/en/self-hosted-environments), [quickstart](https://code.claude.com/docs/en/self-hosted-environments-quickstart), [deploy to production](https://code.claude.com/docs/en/self-hosted-environments-deploy) (git credentials, Kubernetes recipes)
- Claude Cowork: [getting started](https://support.claude.com/en/articles/13345190-getting-started-with-cowork)
- This setup: [remote.md](remote.md) (overview) and [remote/README.md](../remote/README.md) (full reference)
