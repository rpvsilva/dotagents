// The remote-runs mod's plain logic. No claude-code import at run time, so `node --test` runs it beside the
// engine (runs.unit.ts); main.tsx keeps only the calls on `$` and the drawing.
import type { Run, RunState } from './state.d.ts'

export type { Run, RunState } from './state.d.ts'

/** What the pane says when it has no run to show, whatever the reason (no repo, no git, no folders). */
export const EMPTY = 'no remote runs in this repo'

/** How many rows the pane shows at most. */
export const MAX_ROWS = 20

/** A run folder's files as main.tsx read them: run.json's and progress.log's text (null when missing). */
export type RunFiles = {
  id: string
  folder: string
  runJson: string | null
  progress: string | null
  /** Epoch seconds of the last change to run.json or progress.log; 0 when neither is there. */
  changed: number
  /** Whether the folder holds session.jsonl (the session the run kept); missing reads as false. */
  session?: boolean
}

const STATES: readonly RunState[] = ['starting', 'running', 'done', 'failed']
const UNFINISHED: readonly RunState[] = ['starting', 'running', 'stopped']

/** The repo's common git dir from `git rev-parse --path-format=absolute --git-common-dir`, or null on failure. */
export function commonDir(exitCode: number, stdout: string): string | null {
  const dir = stdout.trim()
  return exitCode === 0 && dir.startsWith('/') ? dir.replace(/\/+$/, '') : null
}

/** The git dirs whose remote-agent/ folders hold this repo's runs: the common one, then each linked worktree's. */
export function gitDirs(common: string, worktrees: readonly string[]): string[] {
  return [common, ...[...worktrees].sort().map(w => `${common}/worktrees/${w}`)]
}

/**
 * `text` without its C0 and C1 control characters (tab kept): what the sandbox or a run folder wrote may hold
 * terminal escapes (an OSC 52 clipboard write, an OSC 8 link, cursor moves) that must never reach the terminal.
 */
export function printable(text: string): string {
  return text.replace(/[\u0000-\u0008\u000a-\u001f\u007f-\u009f]/g, '')
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const str = (v: unknown): string | null => (typeof v === 'string' && printable(v) !== '' ? printable(v) : null)

/** Epoch seconds from a run id `agent-YYYYmmdd-HHMMSS-N` (local time, as remote-agent's `date` wrote it). */
function idStarted(id: string): number | null {
  const m = /(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/.exec(id)
  if (!m) return null
  const [y, mo, d, h, mi, s] = m.slice(1).map(Number)
  const t = new Date(y, mo - 1, d, h, mi, s).getTime()
  return Number.isFinite(t) ? t / 1000 : null
}

/** The last non-empty line of a text, without control characters. */
function lastLine(text: string | null): string | null {
  const lines = (text ?? '').split('\n').map(l => printable(l).trim()).filter(Boolean)
  return lines.length ? lines[lines.length - 1] : null
}

/** A row from a run folder's files; `unknown` when run.json is missing or not a JSON object. */
export function parseRun(f: RunFiles): Run {
  let j: Record<string, unknown> = {}
  try {
    const parsed: unknown = f.runJson === null ? null : JSON.parse(f.runJson)
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) j = parsed as Record<string, unknown>
  } catch {
    // an unreadable run.json reads as none
  }
  const state = STATES.find(s => s === j.state) ?? 'unknown'
  return {
    id: f.id,
    folder: f.folder,
    name: str(j.name),
    state,
    started: num(j.started) ?? idStarted(f.id),
    ended: num(j.ended),
    changed: f.changed,
    elapsed: '',
    ago: '',
    isOld: false,
    step: lastLine(f.progress),
    claim_s: num(j.claim_s),
    send_s: num(j.send_s),
    first_event_s: num(j.first_event_s),
    exit_code: num(j.exit_code),
    pid: num(j.pid),
    pid_start: str(j.pid_start),
    worktree: str(j.worktree),
    transcript: `${f.folder}/transcript.jsonl`,
    session: f.session === true,
  }
}

const isLive = (r: Run) => r.state === 'starting' || r.state === 'running'

/** The pids whose liveness decides a row: those of starting and running runs, for one `ps -p` call. */
export function livePids(runs: readonly Run[]): number[] {
  return [...new Set(runs.filter(r => isLive(r) && r.pid !== null).map(r => r.pid as number))]
}

const squash = (s: string) => s.trim().replace(/\s+/g, ' ')

/**
 * Marks `stopped` each starting or running run whose pid is not in `ps -o pid=,lstart=` output, or is there with
 * another start time (the pid was reused). `ps` null (it could not run) changes nothing.
 */
export function applyLiveness(runs: readonly Run[], ps: string | null): Run[] {
  if (ps === null) return [...runs]
  const alive = new Map<number, string>()
  for (const line of ps.split('\n')) {
    const m = /^\s*(\d+)\s+(.*\S)\s*$/.exec(line)
    if (m) alive.set(Number(m[1]), squash(m[2]))
  }
  return runs.map(r => {
    if (!isLive(r) || r.pid === null) return r
    const start = alive.get(r.pid)
    const same = start !== undefined && (r.pid_start === null || squash(r.pid_start) === start)
    return same ? r : { ...r, state: 'stopped' }
  })
}

/** Seconds as `5s`, `1m23s`, `1h02m`; to the minute (`<1m`, `4m`, `1h02m`) when `coarse`. */
function duration(seconds: number, coarse: boolean): string {
  const s = Math.max(0, Math.floor(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (h > 0) return `${h}h${String(m).padStart(2, '0')}m`
  if (coarse) return m > 0 ? `${m}m` : '<1m'
  return m > 0 ? `${m}m${String(s % 60).padStart(2, '0')}s` : `${s}s`
}

/** The elapsed time a row shows at `now` (epoch seconds). */
function elapsed(r: Run, now: number): string {
  if (r.started === null) return ''
  if (r.state === 'done' || r.state === 'failed') return r.ended === null ? '' : duration(r.ended - r.started, false)
  if (r.state === 'stopped') return duration(r.changed - r.started, false)
  if (isLive(r)) return duration(now - r.started, true)
  return ''
}

/** How long after it ended a finished run is drawn grey, in seconds. */
export const OLD_S = 30 * 60

/** Epoch seconds a finished run ended: run.json's `ended`, else when its files last changed; null for one going on. */
function endedAt(r: Run): number | null {
  if (isLive(r)) return null
  return (r.state === 'done' || r.state === 'failed' ? r.ended : null) ?? (r.changed || null)
}

/** Seconds as a coarse age: `<1m ago`, `4m ago`, `2h ago`, `3d ago`. */
function age(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  if (s < 60) return '<1m ago'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

const newestFirst = (a: Run, b: Run) => (b.started ?? 0) - (a.started ?? 0) || (a.id < b.id ? 1 : -1)

/**
 * The rows the pane shows at `now`: every starting, running or stopped run, then the newest finished (done,
 * failed, unknown), MAX_ROWS in all, each group newest first, with its elapsed time.
 */
export function pickRows(runs: readonly Run[], now: number): Run[] {
  const open = runs.filter(r => UNFINISHED.includes(r.state)).sort(newestFirst)
  const finished = runs.filter(r => !UNFINISHED.includes(r.state)).sort(newestFirst)
  return [...open, ...finished].slice(0, MAX_ROWS).map(r => {
    const end = endedAt(r)
    return { ...r, elapsed: elapsed(r, now), ago: end === null ? '' : age(now - end), isOld: end !== null && now - end > OLD_S }
  })
}

/** A key that changes exactly when the rows would draw differently: the pane redraws only then. */
export function signature(rows: readonly Run[]): string {
  return JSON.stringify(rows)
}

/** A theme key the pane colors a row's glyph with. */
export type Tone = 'warning' | 'success' | 'error' | 'inactive'

const LOOK: Record<RunState, { glyph: string; tone: Tone }> = {
  starting: { glyph: '○', tone: 'warning' },
  running: { glyph: '●', tone: 'warning' },
  done: { glyph: '✓', tone: 'success' },
  failed: { glyph: '✗', tone: 'error' },
  stopped: { glyph: '■', tone: 'warning' },
  unknown: { glyph: '?', tone: 'inactive' },
}

/**
 * A row's words: the run's name (else its id); its state, elapsed time and, once finished, how long ago; its
 * timings and last step; and the state's glyph and the theme color to draw it in.
 */
export function formatRow(r: Run): { label: string; status: string; detail: string; glyph: string; tone: Tone } {
  const timings = ([['claim', r.claim_s], ['send', r.send_s], ['first', r.first_event_s]] as const)
    .filter(([, s]) => s !== null)
    .map(([what, s]) => `${what} ${s}s`)
  return {
    label: r.name ?? r.id,
    status: [[r.state, r.elapsed].filter(Boolean).join(' '), r.ago].filter(Boolean).join(' · '),
    detail: [...timings, ...(r.step ? [r.step] : [])].join(' · '),
    ...LOOK[r.state],
  }
}

/** How long a failed or stopped run counts in the status line after it ended, in seconds. */
export const FAILED_WINDOW_S = 60 * 60

/**
 * The status line at `now` (epoch seconds), beside the plugin's name the engine draws: `● 2 running · ✗ 1 failed`, a
 * part naming its run when it holds one (`● build running`), a zero part left out; undefined (the line cleared) when
 * both are zero. Starting runs count as running; failed and stopped runs count for FAILED_WINDOW_S after they ended
 * (a stopped run, or a failed one with no `ended`, after its files last changed), unless their folder is in
 * `opened` (the person opened that log, so it has been seen).
 */
export function statusText(runs: readonly Run[], now: number, opened: ReadonlySet<string> = new Set()): string | undefined {
  const running = runs.filter(isLive)
  const failed = runs.filter(
    r =>
      (r.state === 'failed' || r.state === 'stopped') &&
      !opened.has(r.folder) &&
      now - ((r.state === 'failed' ? r.ended : null) ?? r.changed) <= FAILED_WINDOW_S,
  )
  const part = (glyph: string, list: readonly Run[], what: string) =>
    list.length === 0 ? '' : `${glyph} ${list.length === 1 ? (list[0].name ?? list[0].id) : list.length} ${what}`
  const parts = [part('●', running, 'running'), part('✗', failed, 'failed')].filter(Boolean)
  return parts.length ? parts.join(' · ') : undefined
}

/** The runs that ended (done, failed or stopped) and whose folder is not in `notified`. */
export function endedRuns(runs: readonly Run[], notified: ReadonlySet<string>): Run[] {
  return runs.filter(r => (r.state === 'done' || r.state === 'failed' || r.state === 'stopped') && !notified.has(r.folder))
}

/**
 * The notified folders after marking `ended`, newest last. Past `max`, the oldest folders not in `present` (the
 * folders the scan found) go first; a folder still on disk is never dropped, so it is never notified twice.
 */
export function markNotified(notified: readonly string[], ended: readonly Run[], present: ReadonlySet<string>, max: number): string[] {
  const next = [...notified, ...ended.map(r => r.folder)]
  let extra = next.length - max
  return extra <= 0 ? next : next.filter(f => present.has(f) || extra-- <= 0)
}

/** A finished run's notification text: `<name> done`, `<name> failed (exit <code>)` or `<name> stopped`. */
export function notifyText(r: Run): string {
  const name = r.name ?? r.id
  if (r.state === 'failed') return r.exit_code === null ? `${name} failed` : `${name} failed (exit ${r.exit_code})`
  return `${name} ${r.state}`
}

/**
 * The runs of `runs` that are starting or running and were not in `known`, and the ids to know next time (every
 * run of `runs`). `known` null (the first scan of a process) names none, so a reload or a new session never opens
 * the pane for runs already going.
 */
export function newLiveRuns(runs: readonly Run[], known: ReadonlySet<string> | null): { started: Run[]; known: Set<string> } {
  const next = new Set(runs.map(r => r.id))
  if (known === null) return { started: [], known: next }
  return { started: runs.filter(r => isLive(r) && !known.has(r.id)), known: next }
}

/**
 * `remote.open_pane` from `settings get remote open_pane`'s exit code (null: it could not run) and output: false only
 * when it ran and printed `false`; its default, true, otherwise.
 */
export function openPaneSetting(exitCode: number | null, stdout: string): boolean {
  return !(exitCode === 0 && stdout.trim() === 'false')
}
