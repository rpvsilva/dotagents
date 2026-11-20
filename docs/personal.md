# 👤 Keep your own values out of a fork

[← Back to README](../README.md)

Anyone can install this repo, and anyone can fork it. This page shows how to keep your own values (instructions, guardrails, plugins, gate identity, cluster) out of a fork. The maintainer's values are the worked example: the table lists each one and how it stays out of a public install.

Each value gets one of three treatments:

- **Setting**: the Declaration's default is neutral (unset, or a documented public value). A personal value is saved at a Level, never in the default. See [settings.md](settings.md).
- **`personal` group**: the part ships only when the `personal` group is installed. A public install names its groups and leaves `personal` out.
- **Repo only**: the file governs this repo. No install links or runs it.

## The values

| Value | Where it lives | Treatment |
|---|---|---|
| Merge gate for build-epic PRs (`scripts/merge-pr.sh {pr}`) | `build-epic.merge_command`, declared in [`skills/build-epic/settings.json`](../skills/build-epic/settings.json) | Setting, default unset: plain `gh pr merge`, refused only in a repo with a `.review-gate.yml`. The maintainer's repo sets it in `.agents/settings.json` |
| GitHub App identity for build-epic (`scripts/gh-app-token.sh`) | `build-epic.identity_command`, same file | Setting, default unset: the current `gh` login. The maintainer's repo sets it in `.agents/settings.json` |
| Cluster kube context | `remote.context`, declared in [`remote/settings.json`](../remote/settings.json) | Setting, default unset: the cluster backend stops with a "not set" error until you save one |
| Cluster warm pool | `remote.pool`, same file | Setting, default unset, as `context` |
| Runner image (`ghcr.io/rpvsilva/agent-runner:main`) | `remote.image`, same file, and [`remote/README.md`](../remote/README.md) | Setting whose default stays, documented as the default. The image is public and built from [`remote/runner/Dockerfile`](../remote/runner/Dockerfile); to use your own, build it and save it with `settings set remote image <image> --machine` |
| The maintainer's global instructions | [`global/AGENTS.md`](../global/AGENTS.md), linked as `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md` | `personal` group (part `global`) |
| Claude guardrail: no `gh pr review`, no `gh auth token` | [`adapters/claude/settings/agent-identity.json`](../adapters/claude/settings/agent-identity.json) | `personal` group (part `global`) |
| Codex guardrail: the agent never acts as the maintainer's login | [`adapters/codex/rules/agent-identity.rules`](../adapters/codex/rules/agent-identity.rules) | `personal` group (part `global`) |
| Claude plugins the maintainer chose | [`adapters/claude/plugins.txt`](../adapters/claude/plugins.txt) | `personal` group (part `global`) |
| `add-plugin` skill (edits this repo's `plugins.txt`) | [`skills/add-plugin/`](../skills/add-plugin/SKILL.md) | `personal` group |
| Gate rules and maintainers | [`.review-gate.yml`](../.review-gate.yml) | Repo only |
| Code owners | [`CODEOWNERS`](../CODEOWNERS) | Repo only |
| This repo's Setting values | [`.agents/settings.json`](../.agents/settings.json) | Repo only (read at the repo Level, only inside this repo) |
| CI and gate workflows | [`.github/workflows/`](../.github/workflows/) | Repo only |
| Rules for working on this repo | [`AGENTS.md`](../AGENTS.md) (`CLAUDE.md` links to it) | Repo only |
| App token script | [`scripts/gh-app-token.sh`](../scripts/gh-app-token.sh), with its ID and key in `~/.config/dotagents/github-app.{env,pem}` | Repo only: no install links it; `identity_command` runs it from the repo root |
| Merge script and its `agent-pr-gate` pin | [`scripts/merge-pr.sh`](../scripts/merge-pr.sh) | Repo only: no install links it; `merge_command` runs it from the repo root |
| The README and these docs | [`README.md`](../README.md), `docs/` | Repo only |

## The repo check

`tests/repo.test.sh` scans every file a part of a public group ships (every group but `personal`) and every core file, as `scripts/parts.sh files` lists them. It skips files under a `fixtures/` folder, `test_*` files and `*.test.*` files: they hold sample data. It fails on `rpvsilva` or `0xgui` anywhere in a line, and on the maintainer's first name as a whole word. `rpvsilva`, the image's owner, is allowed only in `remote/settings.json` and `remote/README.md`, where the image is the documented default.

## Keep today's setup: the maintainer's example

On the maintainer's machine, three things keep everything as it was:

1. Install everything: a full `./install.sh`, with no group named, includes `personal`.
2. Save the cluster once, at the machine Level, since `context` and `pool` no longer default to it:

   ```sh
   ~/.agents/bin/settings set remote context <your-kube-context> --machine
   ~/.agents/bin/settings set remote pool claude-runner --machine
   ```

3. Nothing to do for build-epic: the maintainer's repo's `.agents/settings.json` keeps `merge_command` (`scripts/merge-pr.sh {pr}`) and `identity_command` (`scripts/gh-app-token.sh`).
