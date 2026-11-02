// The mod's state contract, named in plugin.json `types`: self-contained, no import.

/**
 * A run's state: what `run.json` says (`starting`, `running`, `done`, `failed`), `stopped` when it says starting
 * or running but its `remote-agent` is gone, `unknown` for a folder with no readable `run.json`.
 */
export type RunState = 'starting' | 'running' | 'done' | 'failed' | 'stopped' | 'unknown'

/** One row of the pane: a run folder of this repo, `<git dir>/remote-agent/<id>/`, as its files describe it. */
export type Run = {
  id: string
  folder: string
  /** The `--name` given to remote-agent; null without one (the row then shows the id). */
  name: string | null
  state: RunState
  /** Epoch seconds: from run.json, else from the id's timestamp; null when neither has one. */
  started: number | null
  ended: number | null
  /** Epoch seconds of the last change to run.json or progress.log; 0 when neither is there. */
  changed: number
  /** The elapsed time as the row shows it: to the minute while the run goes on, so a tick does not redraw it. */
  elapsed: string
  /** The last line of progress.log. */
  step: string | null
  claim_s: number | null
  send_s: number | null
  first_event_s: number | null
  exit_code: number | null
  pid: number | null
  pid_start: string | null
  worktree: string | null
  transcript: string
  /** Whether the run folder holds session.jsonl, which `remote-agent resume` places for /resume. */
  session: boolean
}

/**
 * What the timer keeps for the session (survives a hot reload, not a new process): the common git dirs whose
 * finished runs it has marked notified without notifying, and the status line it last set (null: none).
 */
export type Ticker = { seeded: string[]; status: string | null }

/** What the pane shows: the list (`runId` null), or the log of the run with that id. */
export type View = { runId: string | null }

/**
 * The open run's log, which the timer reads on: the transcript's bytes read so far (`offset`, always just past a
 * newline), the lines they render to (the last 2000), and the window over them: at the end while `follow`, else
 * from line `top`. `truncated` when earlier events are not in `lines`. `runId` null: no log open.
 */
export type Log = {
  runId: string | null
  offset: number
  lines: string[]
  follow: boolean
  top: number
  truncated: boolean
}

declare module 'claude-code' {
  interface PluginState {
    // Change an atom's shape tag when its fields change: it declines what an earlier version stored.
    'remote-runs': { runs: Shaped<Run[]>; ticker: Shaped<Ticker>; view: Shaped<View>; log: Shaped<Log> }
  }
}
