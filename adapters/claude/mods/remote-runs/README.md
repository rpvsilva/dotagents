# remote-runs

A Claude Code mod: `/remote-runs` opens a pane listing the remote-agent runs of the session's repo. It only reads: each run is a folder `remote-agent/<run>/` in the repo's common git dir (`git rev-parse --path-format=absolute --git-common-dir`) or in a linked worktree's git dir under it (`worktrees/<name>/`). With no runs, outside a git repo, or where Claude Code runs no processes (the desktop app, VS Code), the pane says `no remote runs in this repo`.

Installed by `./install.sh claude` from the `dotagents-mods` marketplace (`../.claude-plugin/marketplace.json`) and read from this folder: after editing it, run `/reload-plugins` in a session.

## Files

- `hooks/main.tsx`: the hooks module. Registers `/remote-runs`, opens the pane, finds the run folders, draws the pane. Every `$.fs` and `$.process` call is caught and means "no data", so a missing folder or a failing `git` never prints a failed-hook line.
- `hooks/runs.ts`: plain logic with no `claude-code` import at run time, tested by `hooks/runs.unit.ts` under `node --test`.
- `hooks/state.d.ts`: the state contract (`plugin.json` `types`). Change an atom's `shape` tag when its fields change.
- `hooks/main.test.tsx`: engine tests under `claude plugin test`, with the engine's answers faked.

## Tests

```sh
bash tests/mod.test.sh                                     # all of the below, for every mod
node --test adapters/claude/mods/remote-runs/hooks/*.unit.ts
claude plugin validate adapters/claude/mods/remote-runs
claude plugin test adapters/claude/mods/remote-runs
```
