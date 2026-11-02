# remote-runs

A Claude Code mod: `/remote-runs` opens a pane listing the remote-agent runs of the session's repo. It only reads: each run is a folder `remote-agent/<run>/` in the repo's common git dir (`git rev-parse --path-format=absolute --git-common-dir`) or in a linked worktree's git dir under it (`worktrees/<name>/`). With no runs, outside a git repo, or where Claude Code runs no processes (the desktop app, VS Code), the pane says `no remote runs in this repo`.

One row per run: its name (else the run id), state, elapsed time, the claim / send / first-event timings and the last step (the last line of `progress.log`), all from `run.json` and `progress.log`. States are those `run.json` holds (`starting`, `running`, `done`, `failed`), plus `stopped` when it says starting or running but its pid is gone or belongs to a later process (`ps -o pid=,lstart=`; a `kill -9` or a reboot), and `unknown` for a folder with no `run.json`. Rows: every starting, running or stopped run, then the newest finished, 20 in all, newest first; the first nine have hotkeys `1`-`9`.

A timer reads the folders every 1.5 s, from session start, so the pane is current without a command; it rereads `run.json` and `progress.log` only when their size or mtime changed, never rereads a done or failed run, runs one `ps` per tick for the live runs, and redraws only when a row would show something new. Elapsed time is shown to the minute while a run goes on, so a running run does not redraw every tick.

Installed by `./install.sh claude` from the `dotagents-mods` marketplace (`../.claude-plugin/marketplace.json`) and read from this folder: after editing it, run `/reload-plugins` in a session.

## Files

- `hooks/main.tsx`: the hooks module. Registers `/remote-runs`, starts the timer, opens the pane, reads the run folders, draws the pane. Every `$.fs` and `$.process` call is caught and means "no data", so a missing folder or a failing `git` never prints a failed-hook line.
- `hooks/runs.ts`: plain logic (parsing a run folder, liveness, which rows, their words) with no `claude-code` import at run time, tested by `hooks/runs.unit.ts` under `node --test`.
- `hooks/state.d.ts`: the state contract (`plugin.json` `types`). Change an atom's `shape` tag when its fields change.
- `hooks/main.test.tsx`: engine tests under `claude plugin test`, with the engine's answers faked.

## Tests

```sh
bash tests/mod.test.sh                                     # all of the below, for every mod
node --test adapters/claude/mods/remote-runs/hooks/*.unit.ts
claude plugin validate adapters/claude/mods/remote-runs
claude plugin test adapters/claude/mods/remote-runs
```
