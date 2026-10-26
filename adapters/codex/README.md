# Codex adapter

- `install.sh codex` links `global/AGENTS.md` to `~/.codex/AGENTS.md`. Codex reads skills from `~/.agents/skills`, which `install.sh` fills.
- `agents/*.toml`: custom agent definitions, linked into `~/.codex/agents/`. Each one only points at a brief in `~/.agents/skills/`.
- Hooks go in `~/.codex/hooks.json`, calling scripts in `../../scripts/hooks/`.
