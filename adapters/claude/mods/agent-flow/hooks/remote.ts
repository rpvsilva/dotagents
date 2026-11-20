// Remote-agent runs: finding a session's run folders, reading them, knowing which are alive, and linking each run to
// the Bash call that started it. Pure: file contents in, plain values out; main.tsx does the reads. Tested by
// remote.unit.ts under node --test.
//
// parseRun, livePids and applyLiveness come from the remote-runs mod's runs.ts, which this mod replaces.

import { type FlowEvent, printable, summarize, withoutJson } from './flow.ts'
import type { Launch, LogLine } from './state.d.ts'
import { lineParts, oneLine, parseLines, renderEvent, tidy, type TranscriptEvent } from './transcript.ts'

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
  /** The worktree it ran from, from run.json; what `remote-agent resume` takes. */
  worktree: string | null
  /** Whether its folder holds a session.jsonl that is not empty: the session remote-agent can resume. */
  hasSession: boolean
}

/** A run folder's files as main.tsx read them (null when missing); `changed` in epoch ms. */
export type RunFiles = { id: string; folder: string; runJson: string | null; changed: number; hasSession?: boolean }

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
    worktree: str(j.worktree),
    hasSession: f.hasSession ?? false,
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
export type Task = {
  id: string; type: string; desc: string; toolUseId: string | null; status: 'running' | 'done' | 'failed'; started: number | null; ended: number | null; steps: number; step: string | null
  /** The model its own requests named, and the context its latest one used; null before its first. */
  model: string | null; ctx: number | null
  /** What its own requests cost so far, in dollars, priced by PRICES; null before its first or when a model is not priced. */
  cost: number | null
}

/**
 * What a run's transcript says: its model, its tool calls, the one running now, the subagents it ran, the context
 * its latest request used (null before one), and each model's context window as its final result names them.
 */
export type Scan = {
  model: string | null; steps: number; step: string | null; tasks: Task[]; ctx: number | null; windows: Record<string, number>
  /** What the whole run cost so far, its subagents' requests included, priced by PRICES; null as Task.cost. */
  cost: number | null
}

/** A model's list prices in dollars per million tokens: input, output, cache read, cache write (5 min), cache write (1 h). */
export type Price = readonly [input: number, output: number, cacheRead: number, cacheWrite5m: number, cacheWrite1h: number]

/**
 * List prices by model id, copied from the telemetry skill's pricing.json (as of 2026-10-23). They price a live run
 * only: once its result.json lands, the run shows the cost it names instead. A model missing here shows no cost
 * until then.
 */
export const PRICES: Readonly<Record<string, Price>> = {
  'claude-fable-5-1': [10, 50, 0.25, 12.5, 20],
  'claude-mythos-5-1': [10, 50, 0.25, 12.5, 20],
  'claude-fable-5': [10, 50, 1, 12.5, 20],
  'claude-mythos-5': [10, 50, 1, 12.5, 20],
  'claude-opus-5-5': [4, 20, 0.2, 5, 8],
  'claude-opus-5': [5, 25, 0.5, 6.25, 10],
  'claude-opus-4-8': [5, 25, 0.5, 6.25, 10],
  'claude-opus-4-7': [5, 25, 0.5, 6.25, 10],
  'claude-opus-4-6': [5, 25, 0.5, 6.25, 10],
  'claude-sonnet-5-5': [2, 10, 0.1, 2.5, 4],
  'claude-sonnet-5': [2, 10, 0.2, 2.5, 4],
  'claude-sonnet-4-6': [3, 15, 0.3, 3.75, 6],
  'claude-haiku-5-5': [0.1, 0.5, 0.01, 0.125, 0.2],
  'claude-haiku-4-5': [1, 5, 0.1, 1.25, 2],
}

/**
 * A model id's prices: the id without a provider prefix (`us.anthropic.`), a `[1m]` tag, a date or a `-v1:0`
 * version, then shortened a `-<n>` at a time until PRICES has it (`claude-opus-5-1` → `claude-opus-5`); null when
 * none matches.
 */
export function priceOf(model: string | null): Price | null {
  if (!model) return null
  let id = model.toLowerCase().replace(/\[[^\]]*\]/g, '').replace(/^.*?(?=claude-)/, '').replace(/[@-]\d{8}.*$/, '').replace(/-v\d+(:\d+)?$/, '')
  for (;;) {
    const p = PRICES[id]
    if (p) return p
    const cut = id.replace(/-\d+$/, '')
    if (cut === id) return null
    id = cut
  }
}

/**
 * What one request cost in dollars, from its `usage` and its model's prices: cache writes by their split into 5 min
 * and 1 h when the usage gives it, else all at the 5 min rate. 0 for a request with no tokens (a synthetic message),
 * priced or not; null when its model has no price or it has no usage.
 */
export function usdOf(usage: unknown, model: string | null): number | null {
  const u = obj(usage)
  if (!u) return null
  const n = (v: unknown) => num(v) ?? 0
  const write = n(u.cache_creation_input_tokens)
  const split = obj(u.cache_creation)
  const write1h = split ? Math.min(write, n(split.ephemeral_1h_input_tokens)) : 0
  const counts = [n(u.input_tokens), n(u.output_tokens), n(u.cache_read_input_tokens), write - write1h, write1h]
  if (counts.every(c => c === 0)) return 0
  const p = priceOf(model)
  return p ? counts.reduce((sum, c, i) => sum + (c * p[i]!) / 1e6, 0) : null
}

/** The context a request used, as the engine's tokenCount counts it: its whole input and its output. */
function usedBy(usage: unknown): number | null {
  const u = obj(usage)
  if (!u) return null
  const parts = [u.input_tokens, u.cache_read_input_tokens, u.cache_creation_input_tokens, u.output_tokens].map(num)
  return parts.some(n => n !== null) ? parts.reduce<number>((sum, n) => sum + (n ?? 0), 0) : null
}

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
  const scan: Scan = { model: null, steps: 0, step: null, tasks: [], ctx: null, windows: {}, cost: null }
  const tasks = new Map<string, Task>()
  // Each request's cost by its message id, with the subagent that made it: a message of several content blocks comes
  // as several events with the same id, and the last one's usage counts.
  const spent = new Map<string, { owner: Task | null; usd: number | null }>()
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
        t = { id, type: str(ev.subagent_type) ?? 'agent', desc: str(ev.description) ?? '', toolUseId: str(ev.tool_use_id), status: 'running', started: null, ended: null, steps: 0, step: null, model: null, ctx: null, cost: null }
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
    if (ev.type === 'result') {
      for (const [model, u] of Object.entries(obj(ev.modelUsage) ?? {})) {
        const w = num(obj(u)?.contextWindow)
        if (w && w > 0) scan.windows[model] = w
      }
      continue
    }
    const owner = parent === null ? null : (byToolUse.get(parent) ?? null)
    if (parent !== null && !owner) continue
    if (ev.type === 'assistant') {
      const m = obj(ev.message)
      const used = usedBy(m?.usage)
      if (m?.usage !== undefined) spent.set(str(m.id) ?? `#${spent.size}`, { owner, usd: usdOf(m.usage, str(m.model) ?? owner?.model ?? scan.model) })
      if (owner) {
        owner.model = str(m?.model) ?? owner.model
        if (used !== null) owner.ctx = used
      } else if (used !== null) scan.ctx = used
    }
    // A subagent starts at its first event's time (its prompt), which the task events do not carry.
    const at = typeof ev.timestamp === 'string' ? Date.parse(ev.timestamp) : NaN
    if (owner && owner.started === null && Number.isFinite(at)) owner.started = at
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
  const total = (rows: { usd: number | null }[]) => (rows.length === 0 || rows.some(r => r.usd === null) ? null : rows.reduce((sum, r) => sum + r.usd!, 0))
  const rows = [...spent.values()]
  scan.cost = total(rows)
  for (const t of scan.tasks) t.cost = total(rows.filter(r => r.owner === t))
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
  /** The session that started it (null: none recorded), its worktree, and whether it can be resumed. */
  session: string | null
  worktree: string | null
  hasSession: boolean
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
    // result.json's figure once there; until then the transcript's priced tokens.
    cost: result?.cost ?? scan?.cost ?? null,
    turns: result?.turns ?? null,
    files: result?.files ?? null,
    session: run.session,
    worktree: run.worktree,
    hasSession: run.hasSession,
  }
}

/**
 * The graph events of one run: the run, then each subagent it ran, each with a context reading when its transcript
 * has one and `windowOf` knows its model's window, and a subagent with its cost once its requests are priced.
 */
export function runEvents(r: RemoteRun, scan: Scan | null, at: number, windowOf: (model: string) => number | null = () => null): FlowEvent[] {
  const evs: FlowEvent[] = [{ kind: 'remote', at, run: r }]
  const reading = (agent: string, used: number | null, model: string | null) => {
    const window = model === null ? null : (scan?.windows[model] ?? windowOf(model))
    if (used !== null && window) evs.push({ kind: 'ctx', at, agent, used, window })
  }
  reading(`remote:${r.runId}`, scan?.ctx ?? null, r.model)
  for (const t of scan?.tasks ?? []) {
    // A run that ended leaves no task running.
    const status = t.status === 'running' && r.status !== 'running' ? (r.status === 'done' ? 'done' : 'failed') : t.status
    evs.push({ kind: 'remoteTask', at, runId: r.runId, folder: r.folder, task: { ...t, status, ended: status === 'running' ? null : (t.ended ?? r.ended ?? at) }, model: r.model })
    reading(`remote:${r.runId}:${t.id}`, t.ctx, t.model ?? r.model)
    if (t.cost !== null) evs.push({ kind: 'cost', at, agent: `remote:${r.runId}:${t.id}`, usd: t.cost })
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
  // The agent's last message: its text blocks, and the span of `out` its event drew.
  let said: { texts: string[]; at: number; end: number } | null = null
  for (const ev of events) {
    const parent = typeof ev.parent_tool_use_id === 'string' ? ev.parent_tool_use_id : null
    if (parent !== taskToolUseId) continue
    const texts = ev.type === 'assistant' ? blocks(ev).filter(b => b.type === 'text' && typeof b.text === 'string' && b.text.trim() !== '').map(b => b.text as string) : []
    const at = out.length
    for (const line of renderEvent(ev, names)) {
      const p = lineParts(line)
      if (p.kind === 'call') out.push({ kind: 'call', text: `▸ ${p.tool}${p.rest ? ` ${p.rest}` : ''}` })
      else if (p.kind === 'result') out.push({ kind: 'result', text: `⎿ ${p.rest}` })
      else if (p.kind === 'error') out.push({ kind: 'error', text: `⎿ ${p.rest}` })
      else if (p.kind === 'text') out.push({ kind: 'text', text: p.rest })
      // The run's own end line goes: the log's end line says how it ended, with the whole run's time.
      else if ((p.kind === 'done' || p.kind === 'failed') && taskToolUseId === null) continue
      else out.push({ kind: 'note', text: line })
    }
    if (texts.length) said = { texts, at, end: out.length }
  }
  // The last message's JSON (a task's outcome, for the skill that started the run) is not drawn: its prose is.
  if (said) {
    let k = 0
    const kept: LogLine[] = []
    for (const l of out.slice(said.at, said.end)) {
      if (l.kind !== 'text') kept.push(l)
      else {
        const prose = oneLine(tidy(withoutJson(said.texts[k++] ?? l.text)))
        if (prose !== '') kept.push({ ...l, text: prose })
      }
    }
    out.splice(said.at, said.end - said.at, ...kept)
  }
  // summary.md is the agent's last message, which the transcript already shows: read only when it shows none.
  if (taskToolUseId === null && said === null && summary !== null && summary.trim() !== '') {
    out.push({ kind: 'note', text: '── summary ──' })
    for (const l of summary.split('\n')) if (l.trim() !== '') out.push({ kind: 'text', text: oneLine(l) })
  }
  return out
}

// Moved from the remote-runs mod, which this mod replaces: resuming a run, and notifying each run that ends.

/** At most this many runs of other sessions are listed, the newest. */
export const OTHERS_MAX = 20
/** The folders of notified runs kept in the store per repo; past it, only folders gone from disk are dropped. */
export const NOTIFIED_MAX = 1000
/** The store key of a repo's notified run folders: the remote-runs mod's, so a run it notified is not notified again. */
export const notifiedKey = (common: string) => `notified:${common}`

function shellQuote(s: string): string {
  return /^[A-Za-z0-9_@%+=:,./-]+$/.test(s) ? s : `'${s.replace(/'/g, `'\\''`)}'`
}

const isEnded = (state: RunState) => state === 'done' || state === 'failed' || state === 'stopped'

/**
 * The command that picks a finished (done, failed or stopped) run with a session up: `remote-agent resume <run>
 * <worktree>` (the worktree from run.json, shell-quoted); null for a run still going or one with no session.jsonl.
 */
export function resumeCommand(row: Pick<Run, 'id' | 'state' | 'worktree'>, hasSession: boolean): string | null {
  if (!isEnded(row.state) || !hasSession) return null
  return ['remote-agent', 'resume', shellQuote(printable(row.id)), ...(row.worktree === null ? [] : [shellQuote(row.worktree)])].join(' ')
}

/** The warning under the resume command: it places a session the sandbox wrote. */
export const RESUME_WARNING = "⚠ the sandbox wrote this session and it may hold forged turns: read the run folder's session.jsonl first"

/**
 * How to pick a finished run up: `resume: <resumeCommand>` and RESUME_WARNING; with no session.jsonl (`hasSession`
 * false), that it cannot be resumed. None for a run still going. Mirrors the session line remote-agent prints at the
 * end of a run.
 */
export function resumeHint(row: Pick<Run, 'id' | 'state' | 'worktree'>, hasSession: boolean): string[] {
  if (!isEnded(row.state)) return []
  const command = resumeCommand(row, hasSession)
  if (command === null) return ['no session came back: this run cannot be resumed']
  return [`resume: ${command}`, RESUME_WARNING]
}

/** The runs that ended (done, failed or stopped) and whose folder is not in `notified`. */
export function endedRuns(runs: readonly Run[], notified: ReadonlySet<string>): Run[] {
  return runs.filter(r => isEnded(r.state) && !notified.has(r.folder))
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
  if (r.state === 'failed') return r.exit === null ? `${name} failed` : `${name} failed (exit ${r.exit})`
  return `${name} ${r.state}`
}

/** The runs of other sessions to list: the OTHERS_MAX newest, live ones first. */
export function pickOthers(runs: readonly Run[]): Run[] {
  const live = (r: Run) => (r.state === 'starting' || r.state === 'running' ? 0 : 1)
  return runs
    .slice()
    .sort((x, y) => live(x) - live(y) || (y.started ?? 0) - (x.started ?? 0))
    .slice(0, OTHERS_MAX)
}
