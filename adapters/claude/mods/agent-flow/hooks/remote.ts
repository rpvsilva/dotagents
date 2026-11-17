// Remote-agent runs: finding a session's run folders, reading them, knowing which are alive, and linking each run to
// the Bash call that started it. Pure: file contents in, plain values out; main.tsx does the reads. Tested by
// remote.unit.ts under node --test.
//
// parseRun, livePids and applyLiveness come from the remote-runs mod's runs.ts, which this mod replaces.

import { type FlowEvent, printable, summarize } from './flow.ts'
import type { Launch, LogLine } from './state.d.ts'
import { lineParts, oneLine, parseLines, renderEvent, type TranscriptEvent } from './transcript.ts'

/**
 * A run's state: what run.json says (`starting`, `running`, `done`, `failed`), `stopped` when it says starting or
 * running but its remote-agent is gone, `unknown` for a folder with no readable run.json.
 */
export type RunState = 'starting' | 'running' | 'done' | 'failed' | 'stopped' | 'unknown'

/** A run folder as its run.json and progress.log describe it. Times are epoch milliseconds. */
export type Run = {
  id: string
  folder: string
  name: string | null
  state: RunState
  started: number | null
  ended: number | null
  /** The last change to run.json or progress.log. */
  changed: number
  /** The setup phase: `claiming`, `sending` or `booting` until the first agent event; null after it. */
  phase: string | null
  exit: number | null
  pid: number | null
  pid_start: string | null
  /** The Claude session that started it, from run.json `caller.session`; null when it recorded none. */
  session: string | null
}

/** A run folder's files as main.tsx read them (null when missing); `changed` in epoch ms. */
export type RunFiles = { id: string; folder: string; runJson: string | null; changed: number }

const STATES: readonly RunState[] = ['starting', 'running', 'done', 'failed']
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const str = (v: unknown): string | null => (typeof v === 'string' && printable(v) !== '' ? printable(v) : null)
const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null)

/** Epoch ms from a run id `agent-YYYYmmdd-HHMMSS-N` (local time, as remote-agent's `date` wrote it). */
function idStarted(id: string): number | null {
  const m = /(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/.exec(id)
  if (!m) return null
  const [y, mo, d, h, mi, s] = m.slice(1).map(Number) as [number, number, number, number, number, number]
  const t = new Date(y, mo - 1, d, h, mi, s).getTime()
  return Number.isFinite(t) ? t : null
}

const parseJson = (text: string | null): Record<string, unknown> | null => {
  try {
    return text === null ? null : obj(JSON.parse(text))
  } catch {
    return null
  }
}

/** A run from its folder's files; `unknown` when run.json is missing or not a JSON object. */
export function parseRun(f: RunFiles): Run {
  const j = parseJson(f.runJson) ?? {}
  const state = STATES.find(s => s === j.state) ?? 'unknown'
  const sec = (v: unknown) => (num(v) === null ? null : (num(v) as number) * 1000)
  const isLive = state === 'starting' || state === 'running'
  const phase = !isLive ? null : num(j.claim_s) === null ? 'claiming' : num(j.send_s) === null ? 'sending' : num(j.first_event_s) === null ? 'booting' : null
  const caller = obj(j.caller)
  const session = caller && typeof caller.session === 'string' && /^[A-Za-z0-9_.:-]{1,128}$/.test(caller.session) ? caller.session : null
  return {
    id: f.id,
    folder: f.folder,
    name: str(j.name),
    state,
    started: sec(j.started) ?? idStarted(f.id),
    ended: sec(j.ended),
    changed: f.changed,
    phase,
    exit: num(j.exit_code),
    pid: num(j.pid),
    pid_start: str(j.pid_start),
    session,
  }
}

const isLiveRun = (r: Run) => r.state === 'starting' || r.state === 'running'

/** The pids whose liveness decides a run: those of starting and running runs, for one `ps -p` call. */
export function livePids(runs: readonly Run[]): number[] {
  return [...new Set(runs.filter(r => isLiveRun(r) && r.pid !== null).map(r => r.pid as number))]
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
    if (m) alive.set(Number(m[1]), squash(m[2]!))
  }
  return runs.map(r => {
    if (!isLiveRun(r) || r.pid === null) return r
    const start = alive.get(r.pid)
    const same = start !== undefined && (r.pid_start === null || squash(r.pid_start) === start)
    return same ? r : { ...r, state: 'stopped' }
  })
}

/** Where processes cannot be listed: a live run whose files have not changed in this long counts as stopped. */
export const STALE_MS = 5 * 60_000

/** `runs` with each live one whose files went quiet for STALE_MS marked stopped: the fallback without `ps`. */
export function applyStaleness(runs: readonly Run[], now: number): Run[] {
  return runs.map(r => (isLiveRun(r) && r.changed > 0 && now - r.changed > STALE_MS ? { ...r, state: 'stopped' } : r))
}

// Finding the run folders, from the session's directory, with file reads only.

/** `rel` resolved against the folder `base` (both POSIX paths); `rel` absolute is kept. */
export function resolvePath(base: string, rel: string): string {
  const parts = (rel.startsWith('/') ? rel : `${base}/${rel}`).split('/')
  const out: string[] = []
  for (const p of parts) {
    if (p === '' || p === '.') continue
    if (p === '..') out.pop()
    else out.push(p)
  }
  return `/${out.join('/')}`
}

/** The folders from `dir` up to the root: where a `.git` is looked for. */
export function upwards(dir: string): string[] {
  const out: string[] = []
  for (let d = resolvePath('/', dir); ; d = resolvePath(d, '..')) {
    out.push(d)
    if (d === '/') return out
  }
}

/** The git dir a `.git` file (a linked worktree's) points at, resolved against the folder holding it; null if not one. */
export function gitdirOf(dotGitFile: string, folder: string): string | null {
  const m = /^gitdir:\s*(.+?)\s*$/m.exec(dotGitFile)
  return m ? resolvePath(folder, m[1]!) : null
}

/** The common git dir of `gitdir`: its `commondir` file's path resolved against it, else `gitdir` itself. */
export function commonOf(gitdir: string, commondirFile: string | null): string {
  const rel = commondirFile?.trim()
  return rel ? resolvePath(gitdir, rel) : gitdir
}

/** The git dirs whose remote-agent/ folders hold the repo's runs: the common one, then each linked worktree's. */
export function gitDirs(common: string, worktrees: readonly string[]): string[] {
  return [common, ...[...worktrees].sort().map(w => `${common}/worktrees/${w}`)]
}

// Linking a run to the Bash call that started it.

const SUBCOMMANDS = new Set(['setup', 'check', 'resume', 'result', 'prune'])

/**
 * Whether a Bash command starts a remote run: it calls `remote-agent` (the installed command, or the repo's
 * `scripts/remote-agent.sh`), not one of its subcommands (`check`, `result`, ...) nor `--help`.
 */
export function isLaunch(command: string): boolean {
  const re = /(?:^|[\s;&|()`'"/])remote-agent(?:\.sh)?(?=[\s;&|)`'"]|$)\s*(\S*)/g
  for (const m of command.matchAll(re)) {
    const next = (m[1] ?? '').replace(/^['"]|['"]$/g, '')
    if (!SUBCOMMANDS.has(next) && next !== '-h' && next !== '--help') return true
  }
  return false
}

/** The `--name` a launch gives (`--name x`, `--name=x`, quoted or not); null without one. */
export function launchName(command: string): string | null {
  const m = /--name(?:=|\s+)(?:"([^"]*)"|'([^']*)'|(\S+))/.exec(command)
  const v = m ? (m[1] ?? m[2] ?? m[3] ?? '') : ''
  return v === '' ? null : printable(v)
}

/** The run ids a launch's output names, from remote-agent's `transcript: …/remote-agent/<id>/transcript.jsonl` line. */
export function runIdsIn(text: string): string[] {
  return [...new Set([...text.matchAll(/remote-agent\/(agent-\d{8}-\d{6}-\d+)\/transcript\.jsonl/g)].map(m => m[1]!))]
}

/** How long after a call started a run may still be its own, for a call sent to the background. */
export const LINK_MS = 120_000

/**
 * The launch that started `run`, of this session's: the one whose output named it; else the one that gave its
 * `--name`; else, by time, a launch that began before the run did (by up to LINK_MS) and was still running then
 * or went to the background, the earliest one with no run yet first. Null when none fits.
 */
export function callerOf(launches: readonly Launch[], run: Pick<Run, 'id' | 'name' | 'started'>): Launch | null {
  const exact = launches.find(l => l.runIds.includes(run.id))
  if (exact) return exact
  const t = run.started
  if (t === null) return null
  const inTime = (l: Launch) => l.at <= t + 1000 && t - l.at <= LINK_MS && (l.background || l.ended === null || l.ended >= t - 1000)
  if (run.name !== null) {
    const named = launches.filter(l => l.name === run.name && inTime(l))
    if (named.length) return named[named.length - 1]!
  }
  const fits = launches.filter(l => inTime(l) && (l.name === null || l.name === run.name))
  return fits.find(l => l.runIds.length === 0) ?? fits[0] ?? null
}

// What a run came to, and what its transcript says.

/** result.json's figures: cost in dollars, turns, files changed, the model, and why it ended badly if it did. */
export type Result = { cost: number | null; turns: number | null; files: number | null; model: string | null; why: string | null }

/** result.json's figures; null when it is missing or not an object. */
export function parseResult(text: string | null): Result | null {
  const j = parseJson(text)
  if (!j) return null
  const killed = Array.isArray(j.capped_kills) && j.capped_kills.length > 0
  const why = j.lost === true ? 'lost sandbox' : j.capped_off ? 'capped' : killed ? 'capped' : str(j.killed) ? 'killed' : null
  return {
    cost: num(j.total_cost_usd),
    turns: num(j.num_turns),
    files: Array.isArray(j.changed) ? j.changed.length : null,
    model: str(j.model),
    why,
  }
}

/** A subagent a run ran, as its transcript's task events describe it. */
export type Task = { id: string; type: string; desc: string; toolUseId: string | null; status: 'running' | 'done' | 'failed'; ended: number | null; steps: number; step: string | null }

/** What a run's transcript says: its model, its tool calls, the one running now, and the subagents it ran. */
export type Scan = { model: string | null; steps: number; step: string | null; tasks: Task[] }

const blocks = (ev: TranscriptEvent): Record<string, unknown>[] => {
  const m = obj(ev.message)
  return m && Array.isArray(m.content) ? (m.content.map(obj).filter(Boolean) as Record<string, unknown>[]) : []
}

/**
 * A run's transcript text read whole: the model from its init event; its own tool calls (events with no
 * `parent_tool_use_id`) and the one still waiting for its result; and each subagent from its task events, with the
 * tool calls its own events made.
 */
export function scanTranscript(text: string): Scan {
  const { events } = parseLines(text.endsWith('\n') ? text : `${text}\n`)
  const scan: Scan = { model: null, steps: 0, step: null, tasks: [] }
  const tasks = new Map<string, Task>()
  const byToolUse = new Map<string, Task>()
  let open: { id: string; text: string } | null = null
  for (const ev of events) {
    const parent = typeof ev.parent_tool_use_id === 'string' ? ev.parent_tool_use_id : null
    if (ev.type === 'system') {
      if (ev.subtype === 'init' && str(ev.model)) scan.model = str(ev.model)
      const id = str(ev.task_id)
      if (!id) continue
      let t = tasks.get(id)
      if (ev.subtype === 'task_started' && !t) {
        t = { id, type: str(ev.subagent_type) ?? 'agent', desc: str(ev.description) ?? '', toolUseId: str(ev.tool_use_id), status: 'running', ended: null, steps: 0, step: null }
        tasks.set(id, t)
        if (t.toolUseId) byToolUse.set(t.toolUseId, t)
      }
      if (!t) continue
      const status = str(ev.status) ?? str(obj(ev.patch)?.status)
      if (status === 'completed') t.status = 'done'
      else if (status === 'failed' || status === 'killed' || status === 'stopped') t.status = 'failed'
      const end = num(obj(ev.patch)?.end_time)
      if (end !== null) t.ended = end
      const uses = num(obj(ev.usage)?.tool_uses)
      if (uses !== null) t.steps = uses
      if (t.status !== 'running') t.step = null
      continue
    }
    const owner = parent === null ? null : (byToolUse.get(parent) ?? null)
    if (parent !== null && !owner) continue
    for (const b of blocks(ev)) {
      if (ev.type === 'assistant' && b.type === 'tool_use') {
        const line = summarize(str(b.name) ?? 'tool', obj(b.input) ?? {})
        if (owner) {
          owner.step = line
        } else {
          scan.steps++
          open = { id: str(b.id) ?? '', text: line }
          scan.step = line
        }
      }
      if (ev.type === 'user' && b.type === 'tool_result' && !owner && open && b.tool_use_id === open.id) {
        open = null
        scan.step = null
      }
    }
  }
  scan.tasks = [...tasks.values()]
  return scan
}

// The events a run gives the graph.

/** A remote run, ready for the graph: the run's own fields and what its result and transcript added. */
export type RemoteRun = {
  runId: string
  folder: string
  name: string | null
  status: 'running' | 'done' | 'failed'
  why: string | null
  started: number
  ended: number | null
  phase: string | null
  model: string | null
  steps: number
  step: string | null
  exit: number | null
  cost: number | null
  turns: number | null
  files: number | null
}

/** A run's box status: starting and running run; done is done whatever its exit code (3: no changes); else failed, with why. */
export function remoteRun(run: Run, result: Result | null, scan: Scan | null, now: number): RemoteRun {
  const status = run.state === 'starting' || run.state === 'running' ? 'running' : run.state === 'done' ? 'done' : 'failed'
  const why = status !== 'failed' ? null : run.state === 'stopped' ? 'stopped' : (result?.why ?? (run.exit !== null ? `exit ${run.exit}` : 'failed'))
  return {
    runId: run.id,
    folder: run.folder,
    name: run.name,
    status,
    why,
    started: run.started ?? now,
    ended: status === 'running' ? null : (run.ended ?? run.changed ?? now),
    phase: run.phase,
    model: scan?.model ?? result?.model ?? null,
    steps: scan?.steps ?? 0,
    step: status === 'running' ? (scan?.step ?? null) : null,
    exit: run.exit,
    cost: result?.cost ?? null,
    turns: result?.turns ?? null,
    files: result?.files ?? null,
  }
}

/** The graph events of one run: the run, then each subagent it ran. */
export function runEvents(r: RemoteRun, scan: Scan | null, at: number): FlowEvent[] {
  const evs: FlowEvent[] = [{ kind: 'remote', at, run: r }]
  for (const t of scan?.tasks ?? []) {
    // A run that ended leaves no task running.
    const status = t.status === 'running' && r.status !== 'running' ? (r.status === 'done' ? 'done' : 'failed') : t.status
    evs.push({ kind: 'remoteTask', at, runId: r.runId, folder: r.folder, task: { ...t, status, ended: status === 'running' ? null : (t.ended ?? r.ended ?? at) }, model: r.model })
  }
  return evs
}

/** One line on a finished run's box: `✓ 0:27 · $0.25 · 3 files`. */
export function costLine(cost: number | null, files: number | null): string {
  return [cost === null ? '' : `$${cost.toFixed(2)}`, files === null ? '' : `${files} file${files === 1 ? '' : 's'}`].filter(Boolean).join(' · ')
}

/**
 * A remote run's log, or one of its subagents' (`taskToolUseId`, its Agent call's id): for the run, its setup from
 * progress.log (remote-agent's lines up to its first agent event); then its own transcript events (a subagent's,
 * those with its `parent_tool_use_id`), rendered as the remote-runs mod renders them; then, for the run, its
 * summary.md once there.
 */
export function remoteLog(transcript: string, progress: string | null, summary: string | null, taskToolUseId: string | null): LogLine[] {
  const out: LogLine[] = []
  if (taskToolUseId === null) {
    for (const l of (progress ?? '').split('\n')) {
      if (!/^(remote-agent: |== )/.test(l)) continue
      out.push({ kind: 'note', text: oneLine(l) })
      if (/first agent event/.test(l)) break
    }
  }
  const names = new Map<string, string>()
  const { events } = parseLines(transcript.endsWith('\n') ? transcript : `${transcript}\n`)
  for (const ev of events) {
    const parent = typeof ev.parent_tool_use_id === 'string' ? ev.parent_tool_use_id : null
    if (parent !== taskToolUseId) continue
    for (const line of renderEvent(ev, names)) {
      const p = lineParts(line)
      if (p.kind === 'call') out.push({ kind: 'call', text: `▸ ${p.tool}${p.rest ? ` ${p.rest}` : ''}` })
      else if (p.kind === 'result') out.push({ kind: 'result', text: `⎿ ${p.rest}` })
      else if (p.kind === 'error') out.push({ kind: 'error', text: `⎿ ${p.rest}` })
      else if (p.kind === 'text') out.push({ kind: 'text', text: p.rest })
      else out.push({ kind: 'note', text: line })
    }
  }
  if (taskToolUseId === null && summary !== null && summary.trim() !== '') {
    out.push({ kind: 'note', text: '── summary ──' })
    for (const l of summary.split('\n')) if (l.trim() !== '') out.push({ kind: 'text', text: oneLine(l) })
  }
  return out
}
