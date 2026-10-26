# Codex adapter

- `install.sh codex` links `global/AGENTS.md` to `~/.codex/AGENTS.md`. Codex reads skills from `~/.agents/skills`, which `install.sh` fills.
- `agents/*.toml`: custom agent definitions, linked into `~/.codex/agents/`. Each one only points at a brief in `~/.agents/skills/`.
- `hooks/*.json`: hook config snippets, same shape as Claude's. Each one calls a script in `~/.agents/hooks/`. `install.sh` merges them into `~/.codex/hooks.json` (not linked, because other tools write to it); then trust them with `/hooks` in Codex: an untrusted or changed hook is skipped.
- `rules/*.rules`: command rules, linked into `~/.codex/rules/`. `agent-identity.rules` forbids `gh pr review` and `gh auth token`, like Claude's `settings/agent-identity.json`. Check one with `codex execpolicy check --pretty --rules <file> -- <command>`.
