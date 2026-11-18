// The agent-flow mod: `/agent-flow` opens a pane drawing the session's agents as an org chart, live. It only
// observes: every hook passes its call on with next(e), unchanged, and records what happened through flow.ts's
// reducer. Logic sits in plain .ts files (flow.ts, lifecycle.ts, layout.ts); this module only calls `$` and draws.
// A failure to record or draw is caught: it never fails, delays or changes a tool call.
import { atom, read, update } from 'claude-code'
import type { ElementConstructor, EngineInterface, Register, RenderElement, RenderSurface, TextProps } from 'claude-code'

import { type Agent, ctxColor, findAgent, fmtUsd, ink, TAB_FILL, ctxShare, emptyGraph, type FlowEvent, fmtTokens, gauge, MAIN, modelColor, modelLabel, printable, reduce, shownStatus, band, type Band, summarize } from './flow.ts'
import { addMessages, ago, backLabel, logText, EARLIER, filterCounts, FIRST_READ, goBack, type Item, type Kid, listItems, type Message, NO_LOG, openFrom, scrollLog, WAITING, windowLines } from './log.ts'
import { bar, chart, spin, clock, counts as layoutCounts, fallback, headerRow, needsRow, newTween, noFallback, type Row, type Seg, TWEEN_MS } from './layout.ts'
import { isMoving } from './lifecycle.ts'
import {
  applyLiveness,
  costLine,
  endedRuns,
  markNotified,
  notifiedKey,
  NOTIFIED_MAX,
  notifyText,
  pickOthers,
  resumeCommand,
  resumeHint,
  RESUME_WARNING,
  applyStaleness,
  commonOf,
  gitdirOf,
  gitDirs,
  isLaunch,
  launchName,
  livePids,
  parseResult,
  parseRun,
  remoteLog,
  remoteRun,
  type Result,
  type Run,
  runEvents,
  runIdsIn,
  type Scan,
  scanTranscript,
  upwards,
} from './remote.ts'
import type { Filter, Graph, Log, LogLine, View } from './state.d.ts'

const PANE = 'agent-flow'
const TICK_MS = 125
// A pane not drawn for this long is taken as closed: the timer stops redrawing it until something changes.
const DRAWN_MS = 2000
const KEYS = 'x expand squad · a agents · esc close'
const LIST_KEYS = 'click a row, a group or a filter · 1-9 open · l r n f d filter · g chart · esc close'
const LOG_KEYS = 'b back · 1-9 open a linked agent · ↑↓ scroll · c copy · esc close'
// The Agents tab's filters: hotkey and label.
const FILTERS: [Filter, string, string][] = [
  ['all', 'l', 'All'],
  ['running', 'r', 'Running'],
  ['needs', 'n', 'Needs you'],
  ['failed', 'f', 'Failed'],
  ['done', 'd', 'Done'],
]
// While the Agents tab shows a running agent, it is redrawn this often, for its clock.
const LIST_EVERY_MS = 1000
// How often the context readings are taken while an agent runs, and for how long after the last one ended (the
// engine keeps a finished agent listed for a while, so its last reading is taken too).
const CTX_EVERY_MS = 1000
const CTX_AFTER_END_MS = 10_000
// How often an open log is read again while its agent runs, and for how long after it ends (its last messages).
const LOG_EVERY_MS = 1000
const LOG_AFTER_END_MS = 5000
// The pane's rows around the log's window: the tabs and a blank row, the header card (border, two lines) and a
// blank row; the end line, a blank row and the buttons below.
const CHROME_ROWS = 11
const logRows = (bodyRows: number, l: Log) => Math.max(3, bodyRows - CHROME_ROWS - (l.truncated ? 1 : 0))

const graph = atom({ plugin: 'agent-flow', key: 'graph' } as const, emptyGraph(), { shape: 'graph-5' })
const NO_VIEW: View = { tab: 'chart', expanded: null, agentId: null, filter: 'all', groups: [], trail: [], origin: 'chart' }
const view = atom({ plugin: 'agent-flow', key: 'view' } as const, NO_VIEW, { shape: 'view-4' })
const log = atom({ plugin: 'agent-flow', key: 'log' } as const, NO_LOG, { shape: 'log-1' })
const frame = atom({ plugin: 'agent-flow', key: 'frame' } as const, 0, { shape: 'frame-1' })
// The band above the prompt redraws when this counts: on each frame while its spinners turn, else when what it says changes.
const bandFrame = atom({ plugin: 'agent-flow', key: 'bandFrame' } as const, 0, { shape: 'band-frame-1' })

// Per-process state, which a reload starts again: where boxes were drawn, what the band last said, the last draw.
let tween = newTween()
let fall = noFallback()
let lastBand = ''
let lastDraw = 0
let isTicking = false
let settings = { lingerMs: 30_000, squadMin: 4, motion: true, fixed: true }
// A colour as the pane draws it, by the `colors` option.
const paint = (c: string | undefined) => ink(c, settings.fixed)
const FAINT = 'inactive'
// The open log's reads go one at a time (the timer and a press may both ask); when it was last read; the lines its
// window showed at the last draw, which a scroll moves by.
let logQueue: Promise<void> = Promise.resolve()
let logReadAt = 0
let ctxReadAt = 0
let drawnRows = { agentId: null as string | null, rows: 10 }

// Remote runs. The scan reads the repo's run folders at most every REMOTE_EVERY_MS while a remote-agent call or a
// run is live or the pane is drawn, else every QUIET_EVERY_MS (so a run that ends is notified), rereading a folder's
// files only when they changed.
const REMOTE_EVERY_MS = 1500
const QUIET_EVERY_MS = 15_000
// A call sent to the background ends at once; its run may start up to this long after.
const LAUNCH_WAIT_MS = 120_000
// $.fs.read refuses a file over 4 MiB: a transcript under this is read whole, a bigger one through `tail`.
const FS_READ_MAX = 3 << 20
let remoteAt = 0
let remoteScanned = false
let repo: { cwd: string; common: string | null } | null = null
let sessionId: string | null = null
// Per run folder: the key of the files last read, and what they gave; `isDeep` once a finished run's transcript and
// result were read, after which its folder is not looked at again.
type Seen = { key: string; run: Run; result: Result | null; scan: Scan | null; transcriptSize: number; isFinal: boolean; isOurs: boolean; isDeep: boolean }
let seen = new Map<string, Seen>()
// The open remote log's files as last read, so an unchanged log is not read again.
let remoteLogKey = ''
// Whether this process has marked the repo's ended runs once: the first scan notifies none, so a reload repeats none.
let notifySeeded = false
let listDrawnAt = 0

async function now($: EngineInterface): Promise<number> {
  try {
    return await $.clock.now()
  } catch {
    return 0
  }
}

/** Redraws the band above the prompt: on every frame while an agent runs and motion is on, else when what it says changed. */
async function redrawBand($: EngineInterface): Promise<void> {
  const b = band(await read($, graph), await now($))
  const key = JSON.stringify(b)
  if (key === lastBand && !(settings.motion && b !== null && (b.running > 0 || b.remote > 0))) return
  lastBand = key
  await update($, bandFrame, n => n + 1)
}

/** Records one event; never throws. */
async function record($: EngineInterface, ev: FlowEvent): Promise<void> {
  try {
    await update($, graph, g => reduce(g, ev))
    await redrawBand($)
  } catch {
    // the chart misses this event; the call it came from goes on as it would
  }
}

/**
 * Reads the open log's new messages into the log atom; another agent's log (one opened, or Back) starts it again.
 * Never throws: a read the session denies says why in the log.
 */
async function readLog($: EngineInterface): Promise<void> {
  try {
    const { agentId } = await read($, view)
    const was = await read($, log)
    let l: Log = agentId === was.agentId ? was : { ...NO_LOG, agentId }
    const g = await read($, graph)
    const target = agentId === null ? undefined : findAgent(g, agentId)
    if (target?.remote) {
      logReadAt = await now($)
      l = await readRemoteLog($, l, target.remote.folder, target.remote.toolUseId)
    } else if (agentId !== null) {
      logReadAt = await now($)
      const got = await $.session.messages(l.after === null ? { agentId, last: FIRST_READ } : { agentId, after: l.after })
      if ('deny' in got) l = l.after === null ? { ...l, denied: String(got.deny) } : { ...l, after: null }
      else l = addMessages({ ...l, denied: null }, got as unknown as Message[])
    }
    if (l !== was) await update($, log, () => l)
  } catch {
    // no new lines this time
  }
}

/** A file's text; null when it is missing or unreadable. */
async function text($: EngineInterface, path: string): Promise<string | null> {
  try {
    const got = await $.fs.read(path)
    return typeof got === 'string' ? got : null
  } catch {
    return null
  }
}

/** A file's size and mtime as `size:mtime`; '' when it is missing. */
async function stamp($: EngineInterface, path: string): Promise<string> {
  try {
    const s = await $.fs.stat(path)
    return `${s.size}:${s.mtimeMs}`
  } catch {
    return ''
  }
}

/** A transcript's text: whole under FS_READ_MAX, else its last MiB through `tail` (the first, cut line dropped). */
async function transcriptText($: EngineInterface, path: string): Promise<{ text: string; truncated: boolean } | null> {
  try {
    const size = (await $.fs.stat(path)).size
    if (size < FS_READ_MAX) {
      const whole = await text($, path)
      return whole === null ? null : { text: whole, truncated: false }
    }
    const got = await $.process.run(['tail', '-c', String(1 << 20), '--', path])
    return got.exitCode === 0 ? { text: got.stdout.slice(got.stdout.indexOf('\n') + 1), truncated: true } : null
  } catch {
    return null
  }
}

/** A remote run's (or its subagent's) log, read again only when its folder's files changed. */
async function readRemoteLog($: EngineInterface, l: Log, folder: string, toolUseId: string | null): Promise<Log> {
  const key = [l.agentId, await stamp($, `${folder}/transcript.jsonl`), await stamp($, `${folder}/progress.log`), await stamp($, `${folder}/summary.md`)].join('|')
  if (key === remoteLogKey && l.lines.length) return l
  remoteLogKey = key
  const tr = await transcriptText($, `${folder}/transcript.jsonl`)
  const lines = remoteLog(tr?.text ?? '', await text($, `${folder}/progress.log`), await text($, `${folder}/summary.md`), toolUseId)
  const last = Math.max(0, lines.length - 1)
  return { ...l, lines, after: key, truncated: tr?.truncated ?? false, denied: null, top: Math.min(l.top, last) }
}

/** The repo's common git dir, found from the session's folder with file reads only; null outside a repo. */
async function commonDir($: EngineInterface): Promise<string | null> {
  const cwd = await $.session.cwd()
  if (repo?.cwd === cwd) return repo.common
  let common: string | null = null
  for (const dir of upwards(cwd)) {
    let kind: string
    try {
      kind = (await $.fs.stat(`${dir}/.git`)).kind
    } catch {
      continue
    }
    if (kind === 'dir') common = `${dir}/.git`
    else {
      const gitdir = gitdirOf((await text($, `${dir}/.git`)) ?? '', dir)
      if (gitdir) common = commonOf(gitdir, await text($, `${gitdir}/commondir`))
    }
    break
  }
  repo = { cwd, common }
  return common
}

/** The run folders under the common git dir's and each linked worktree's remote-agent/. */
async function runFolders($: EngineInterface, common: string): Promise<{ id: string; folder: string }[]> {
  const list = async (path: string) => {
    try {
      return await $.fs.list(path)
    } catch {
      return []
    }
  }
  const worktrees = (await list(`${common}/worktrees`)).filter(e => e.kind === 'dir').map(e => e.name)
  const out: { id: string; folder: string }[] = []
  for (const dir of gitDirs(common, worktrees)) {
    for (const e of await list(`${dir}/remote-agent`)) if (e.kind === 'dir') out.push({ id: e.name, folder: `${dir}/remote-agent/${e.name}` })
  }
  return out
}

/** `ps -o pid=,lstart=` for `pids`, in the C locale as remote-agent wrote pid_start; null when ps cannot run. */
async function ps($: EngineInterface, pids: number[]): Promise<string | null> {
  if (pids.length === 0) return ''
  try {
    return (await $.process.run(['env', 'LC_ALL=C', 'ps', '-o', 'pid=,lstart=', '-p', pids.join(',')])).stdout
  } catch {
    return null
  }
}

const isLiveRun = (a: Agent) => a.remote !== null && a.remote.taskId === null && a.status === 'running'

/**
 * Whether the run folders are worth reading at `t`: none was read yet, a remote-agent call or a run is live, or the
 * pane was drawn a moment ago; else every QUIET_EVERY_MS, so a run that ends is noticed and notified.
 */
function remoteDue(g: Pick<Graph, 'launches' | 'agents' | 'others'>, t: number): boolean {
  if (t - remoteAt < REMOTE_EVERY_MS) return false
  if (!remoteScanned || t - remoteAt >= QUIET_EVERY_MS || t - lastDraw < DRAWN_MS) return true
  if (g.launches.some(l => l.ended === null || (l.background && t - l.at < LAUNCH_WAIT_MS) || t - (l.ended ?? l.at) < 10_000)) return true
  return g.agents.some(isLiveRun) || g.others.some(isLiveRun)
}

/** Reads what a listed run's box and row show beyond run.json: its transcript when it grew, and its result once over. */
async function deepen($: EngineInterface, s: Seen, t: number): Promise<void> {
  const st = await stamp($, `${s.run.folder}/transcript.jsonl`)
  const size = Number(st.split(':')[0] ?? -1)
  if (st !== '' && size !== s.transcriptSize) {
    const tr = await transcriptText($, `${s.run.folder}/transcript.jsonl`)
    if (tr) s.scan = scanTranscript(tr.text)
    s.transcriptSize = size
  }
  if (s.isFinal && s.result === null) s.result = parseResult(await text($, `${s.run.folder}/result.json`))
  // A result.json may land a moment after run.json says it ended: wait a minute for it before letting the run be.
  s.isDeep = s.isFinal && (s.result !== null || t - (s.run.ended ?? s.run.changed) > 60_000)
}

/**
 * Notifies each run that ended since the last scan, once: the notified folders live in $.store under the remote-runs
 * mod's key, so a reload or a new process does not notify again. The first scan of a process marks every ended run
 * without notifying. A run is marked even when no notification went out, so a refused one is not tried every scan.
 */
async function notifyEnded($: EngineInterface, common: string, runs: readonly Run[]): Promise<void> {
  let stored: unknown
  try {
    stored = await $.store.get(notifiedKey(common))
  } catch {
    return // no store, no memory: notifying now would repeat every scan
  }
  const notified = Array.isArray(stored) ? stored.filter((f): f is string => typeof f === 'string') : []
  const ended = endedRuns(runs, new Set(notified))
  if (ended.length > 0) {
    for (const r of notifySeeded ? ended : []) {
      try {
        await $.ui.notify(notifyText(r), { title: 'remote run' })
      } catch {
        // a refused or failed notification: marked all the same
      }
    }
    await $.store.set(notifiedKey(common), markNotified(notified, ended, new Set(runs.map(r => r.folder)), NOTIFIED_MAX))
  }
  notifySeeded = true
}

/**
 * Reads the repo's remote runs: this session's into the graph (each run folder whose run.json names this session as
 * its caller, with its liveness, result, transcript and the subagents it ran), the OTHERS_MAX newest of other
 * sessions into the Agents list; notifies the runs that ended. A folder's files are reread only when they changed,
 * and a finished run's not at all once read. Never throws.
 */
async function scanRemote($: EngineInterface, t: number): Promise<void> {
  try {
    if (!remoteDue(await read($, graph), t)) return
    remoteAt = t
    remoteScanned = true
    const common = await commonDir($)
    if (!common) return
    sessionId ??= await $.session.id()
    const kept = new Map<string, Seen>()
    for (const { id, folder } of await runFolders($, common)) {
      const was = seen.get(folder)
      if (was?.isDeep) {
        kept.set(folder, was)
        continue
      }
      const files = [`${folder}/run.json`, `${folder}/progress.log`, `${folder}/transcript.jsonl`, `${folder}/session.jsonl`]
      const key = (await Promise.all(files.map(f => stamp($, f)))).join('|')
      if (was && was.key === key) {
        kept.set(folder, was)
        continue
      }
      const stamps = key.split('|')
      const changed = Math.max(...stamps.slice(0, 3).map(k => Number(k.split(':')[1] ?? 0) || 0))
      const run = parseRun({ id, folder, runJson: await text($, `${folder}/run.json`), changed, hasSession: Number(stamps[3]!.split(':')[0] ?? 0) > 0 })
      const isFinal = run.state === 'done' || run.state === 'failed'
      kept.set(folder, { key, run, result: was?.result ?? null, scan: was?.scan ?? null, transcriptSize: was?.transcriptSize ?? -1, isFinal, isOurs: run.session !== null && run.session === sessionId, isDeep: false })
    }
    seen = kept
    const all = [...kept.values()]
    const ours = all.filter(s => s.isOurs)
    const others = pickOthers(all.filter(s => !s.isOurs).map(s => s.run)).map(r => kept.get(r.folder)!)
    for (const s of [...ours, ...others]) if (!s.isDeep) await deepen($, s, t)
    // A finished run of another session too old to be listed is never read: its folder is not looked at again.
    const listed = new Set(others)
    for (const s of all) if (s.isFinal && !s.isOurs && !listed.has(s)) s.isDeep = true
    // Liveness: ps where processes run; elsewhere a run whose files went quiet for 5 minutes counts as stopped.
    const runs = all.map(s => s.run)
    const out = await ps($, livePids(runs))
    const judged = new Map((out === null ? applyStaleness(runs, t) : applyLiveness(runs, out)).map(r => [r.folder, r]))
    const now = (s: Seen) => judged.get(s.run.folder) ?? s.run
    const evs: FlowEvent[] = ours.flatMap(s => runEvents(remoteRun(now(s), s.result, s.scan, t), s.scan, t))
    evs.push({ kind: 'others', at: t, runs: others.map(s => remoteRun(now(s), s.result, s.scan, t)) })
    await update($, graph, g => evs.reduce(reduce, g))
    await notifyEnded($, common, [...judged.values()])
  } catch {
    // no runs this time
  }
}

function followLog($: EngineInterface): Promise<void> {
  logQueue = logQueue.then(() => readLog($))
  return logQueue
}

/** Whether the open log should be read again at `t`: its agent runs, or ended a moment ago. */
function logIsLive(a: Agent | undefined, t: number): boolean {
  if (!a) return false
  return a.ended === null || t - a.ended < LOG_AFTER_END_MS
}

/**
 * Takes a context reading for main and every subagent the engine lists, recorded as one graph write. Only while
 * something runs or ended a moment ago: a still session costs nothing. Never throws.
 */
async function readCtx($: EngineInterface, t: number): Promise<void> {
  try {
    const g = await read($, graph)
    const recent = g.agents.some(a => a.status === 'running' || (a.ended !== null && t - a.ended < CTX_AFTER_END_MS))
    if (!recent || t - ctxReadAt < CTX_EVERY_MS) return
    ctxReadAt = t
    const evs: FlowEvent[] = []
    try {
      for (const info of await $.agent.list()) {
        if (info.contextWindowSize && info.tokenCount > 0) evs.push({ kind: 'ctx', at: t, agent: info.id, used: info.tokenCount, window: info.contextWindowSize })
        if (info.cost) evs.push({ kind: 'cost', at: t, agent: info.id, usd: info.cost.usd })
      }
    } catch {
      // no agents listed on this surface
    }
    try {
      const { context, cost } = await $.session.usage()
      if (context.tokens !== undefined && context.window) evs.push({ kind: 'ctx', at: t, agent: MAIN, used: context.tokens, window: context.window })
      if (cost) evs.push({ kind: 'cost', at: t, agent: MAIN, usd: cost.usd })
    } catch {
      // no usage to read
    }
    if (evs.length) await update($, graph, x => evs.reduce(reduce, x))
  } catch {
    // no reading this time
  }
}

/** Opens agent `agentId`'s log, read at once; `from` says where Back returns. */
async function openLog($: EngineInterface, agentId: string, from: 'chart' | 'agents' | 'log'): Promise<void> {
  await update($, view, v => openFrom(v, agentId, from))
  await followLog($)
}

/** One step back: the log a link was followed from, else the tab the first log was opened from. */
async function back($: EngineInterface): Promise<void> {
  await update($, view, v => goBack(v))
  await followLog($)
}

/**
 * Opens what a click in the chart landed on: an agent's log; for a squad, the Agents tab with its group open; for a
 * `✓ N done` fold, the Agents tab.
 */
async function openNode($: EngineInterface, id: string): Promise<void> {
  if (id.startsWith('squad:')) {
    const group = id.slice('squad:'.length)
    await update($, view, (v): View => ({ ...v, tab: 'agents', agentId: null, trail: [], filter: 'all', groups: v.groups.includes(group) ? v.groups : [...v.groups, group] }))
  } else if (id.startsWith('fold:')) await update($, view, (v): View => ({ ...v, tab: 'agents', agentId: null, trail: [], filter: 'all' }))
  else if (id !== MAIN) await openLog($, id, 'chart')
}

/** `s` cut or padded to `n` columns: a column of a table row, a cut one ending in `…`. */
const fit = (s: string, n: number) => (s.length >= n ? `${s.slice(0, n - 2)}… ` : s.padEnd(n))

/** A tab, or a filter: either leaves any open log and forgets the way back. */
const toTab = (tab: View['tab']) => (v: View): View => ({ ...v, tab, agentId: null, trail: [] })
const toFilter = (filter: Filter) => (v: View): View => ({ ...v, tab: 'agents', filter, agentId: null, trail: [] })

/** Copies `text` on the surface that pressed, and toasts what happened. */
/** Copies `text`, toasting `copied <what>` or why not. */
async function copyText($: EngineInterface, text: string, what: string, surface: RenderSurface): Promise<void> {
  try {
    const got = await $.ui.copy({ text, surface })
    $.ui.toast(got.isCopied ? `copied ${what}` : `not copied: ${got.reason ?? 'refused'}`)
  } catch {
    $.ui.toast('not copied')
  }
}

/** Redraws the pane while something moves on it and it was drawn lately, and the band while it moves or changes. */
async function tick($: EngineInterface): Promise<void> {
  if (isTicking) return
  isTicking = true
  try {
    const t = await now($)
    await redrawBand($)
    await readCtx($, t)
    await scanRemote($, t)
    const isDrawn = t - lastDraw < DRAWN_MS
    const isGliding = t - tween.t0 < TWEEN_MS
    const v = await read($, view)
    if (isDrawn && v.agentId !== null && t - logReadAt >= LOG_EVERY_MS) {
      const g = await read($, graph)
      if (logIsLive(findAgent(g, v.agentId), t)) await followLog($)
    }
    // The list redraws once a second while an agent runs, for its clock; the chart on every frame something moves.
    if (isDrawn && v.tab === 'agents' && t - listDrawnAt >= LIST_EVERY_MS && (await read($, graph)).agents.some(a => a.id !== MAIN && a.status === 'running')) {
      listDrawnAt = t
      await update($, frame, n => n + 1)
    } else if (isDrawn && v.tab === 'chart' && v.agentId === null && (isGliding || fall.fitSince !== null || isMoving(await read($, graph), t, settings.lingerMs))) await update($, frame, n => n + 1)
  } catch {
    // no redraw this tick
  } finally {
    isTicking = false
  }
}

/** The band's parts, each one Text: the running agents by task in their model's colour with a spinner, or their count; then waiting, remote, failed. */
function bandParts(b: Band, t: number, motion: boolean, Text: ElementConstructor<TextProps>): RenderElement[] {
  const out: RenderElement[] = []
  b.names.forEach((n, i) =>
    out.push(
      <Text key={`a${n.id}`} color={paint(modelColor(n.model))}>
        {`${spin(t, motion, i * 3)} ${n.name}`}
      </Text>,
    ),
  )
  if (!b.names.length && b.running) out.push(<Text key="running" bold color={paint('claude')}>{`${spin(t, motion)} ${b.running} running`}</Text>)
  if (b.waiting) out.push(<Text key="waiting" color={paint('warning')}>{`◐ ${b.waiting} needs you`}</Text>)
  if (b.remote) out.push(<Text key="remote" color={paint('suggestion')}>{`☁ ${b.remote} remote`}</Text>)
  if (b.failed) out.push(<Text key="failed" color={paint('error')}>{`✗ ${b.failed} failed`}</Text>)
  return out
}

const numberOr = (v: unknown, d: number, min: number) => (typeof v === 'number' && Number.isFinite(v) && v >= min ? v : d)

export const register: Register = (on, options) => {
  const o = (options ?? {}) as Record<string, unknown>
  settings = {
    lingerMs: numberOr(o.linger_s, 30, 0) * 1000,
    squadMin: Math.round(numberOr(o.squad_min, 4, 2)),
    motion: o.motion !== 'off',
    fixed: o.colors !== 'theme',
  }

  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'agent-flow', description: "Show this session's agents as a live org chart in a pane" })
    $.ui.status(undefined) // the status line an earlier version set; the band above the prompt says it now
    $.clock.every(TICK_MS, () => {
      void tick($)
    })
    return next(e)
  })

  on('command.run', { command: 'agent-flow' }, async $ => {
    const { isPlaced } = await $.ui.open({ id: PANE, title: 'Agent flow', focus: true, closeOnEscape: true })
    return { text: isPlaced ? 'Agent flow pane opened.' : 'Agent flow pane opens once the terminal is wider.' }
  })

  on('turn.start', async ($, e, next) => {
    await record($, { kind: 'turnStart', at: await now($), agent: e.agentId ?? MAIN })
    // A new prompt forgets the way back through logs; an open log stays open.
    if (e.agentId === undefined) {
      try {
        await update($, view, v => (v.trail.length ? { ...v, trail: [] } : v))
      } catch {
        // nothing to forget
      }
    }
    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    const out = (e.usage as { output_tokens?: number } | undefined)?.output_tokens
    await record($, { kind: 'turnEnd', at: await now($), agent: e.agentId ?? MAIN, reason: e.reason, outputTokens: typeof out === 'number' ? out : null })
    return next(e)
  })

  on('agent.spawn', async ($, e, next) => {
    const r = await next(e)
    if (r.deny === undefined && r.agentId) {
      await record($, {
        kind: 'spawn', at: await now($), id: r.agentId, parent: e.parentAgentId ?? MAIN, type: e.subagentType, desc: e.description,
        model: r.model ?? null, parentModel: e.parentModel ?? null, toolUseId: e.tool_use_id || null,
      })
    }
    return r
  }).catch(($, e, next) => next(e))

  on('tool.call', async ($, e, next) => {
    const agent = e.agentId ?? MAIN
    await record($, { kind: 'tool', at: await now($), agent, tool: e.tool, summary: summarize(e.tool, e as unknown as Record<string, unknown>) })
    // A Bash call that starts a remote run: who made it and when, so the run hangs under that agent.
    const input = e as unknown as Record<string, unknown>
    const command = e.tool === 'Bash' && typeof input.command === 'string' ? input.command : ''
    const launch = command !== '' && e.tool_use_id && isLaunch(command) ? e.tool_use_id : null
    if (launch) {
      const desc = typeof input.description === 'string' && input.description.trim() ? printable(input.description) : null
      await record($, { kind: 'launch', at: await now($), toolUseId: launch, agent, name: launchName(command), desc, background: input.run_in_background === true })
      remoteAt = 0
    }
    const r = await next(e)
    await record($, { kind: 'toolEnd', at: await now($), agent })
    if (launch) {
      const res = (r as { result?: { stdout?: unknown; stderr?: unknown } }).result
      const out = [res?.stdout, res?.stderr].filter((x): x is string => typeof x === 'string').join('\n')
      await record($, { kind: 'launchEnd', at: await now($), toolUseId: launch, runIds: runIdsIn(out) })
      remoteAt = 0
    }
    if (e.tool === 'Agent' && e.tool_use_id && (r.deny !== undefined || r.isError)) {
      await record($, { kind: 'spawnFailed', at: await now($), toolUseId: e.tool_use_id, why: 'error' })
    }
    return r
  }).catch(($, e, next) => next(e))

  on('tool.check', async ($, e, next) => {
    const agent = e.agentId ?? MAIN
    const id = e.tool_use_id
    if (id) await record($, { kind: 'check', at: await now($), agent, toolUseId: id })
    const r = await next(e)
    if (id) await record($, { kind: 'checkEnd', at: await now($), agent, toolUseId: id })
    return r
  }).catch(($, e, next) => next(e))

  // The log draws its own window, exactly its rows: the person's scroll moves it; the chart and the list scroll as usual.
  on('ui.scroll', { component: 'Pane', requestId: PANE }, async ($, e, next) => {
    const { agentId } = await read($, view)
    if (agentId === null) return next(e)
    const l = await read($, log)
    const rows = drawnRows.agentId === agentId ? drawnRows.rows : logRows(e.bodyRows, l)
    if (l.agentId === agentId) await update($, log, () => scrollLog(l, e.by, rows))
    return {}
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    await read($, bandFrame) // redrawn on each frame the timer counts for it
    if (e.props.hasSurvey) return next(e)
    const t = await now($)
    const b = band(await read($, graph), t)
    if (b === null) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    const motion = settings.motion && e.surface === 'terminal'
    const sep = (k: string) => <Text key={k} color={paint(FAINT)}>{'  ·  '}</Text>
    const parts = bandParts(b, t, motion, Text)
    return (
      <Box>
        {parts.flatMap((p, i) => (i ? [sep(`sep${i}`), p] : [p]))}
        {b.total > 1 && <Text key="bar" color={paint(FAINT)}>{'   '}</Text>}
        {b.total > 1 && <Text key="gauge" color={paint(b.failed ? 'error' : 'success')}>{gauge(b.ended / b.total, 8)}</Text>}
        {b.total > 1 && <Text key="ended" dimColor>{` ${b.ended}/${b.total}`}</Text>}
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button, Code } = $.ui.resolve(e)
    await read($, frame) // redrawn on each frame the timer counts
    const g = await read($, graph)
    const v = await read($, view)
    const t = await now($)
    lastDraw = t
    const segText = (sg: Seg, i: number) => (
      <Text key={`s${i}`} {...(sg.color ? { color: paint(sg.color) } : {})} {...(sg.dim ? { dimColor: true } : {})} {...(sg.bold ? { bold: true } : {})}>
        {sg.text}
      </Text>
    )
    // A row: plain text, or, where it holds a box, a run of pieces, each one a node's a click opens.
    const line = (r: Row, i: number) => {
      if (!r.some(sg => sg.agent)) {
        return (
          <Text key={`r${i}`} wrap="truncate-end">
            {r.length ? r.map(segText) : ' '}
          </Text>
        )
      }
      const runs: { agent: string | undefined; segs: Seg[]; col: number }[] = []
      let col = 0
      for (const sg of r) {
        const last = runs.at(-1)
        if (last && last.agent === sg.agent) last.segs.push(sg)
        else runs.push({ agent: sg.agent, segs: [sg], col })
        col += sg.text.length
      }
      return (
        <Box key={`r${i}`} flexDirection="row">
          {runs.map(run =>
            run.agent === undefined ? (
              <Text key={`t${run.col}`}>{run.segs.map(segText)}</Text>
            ) : (
              <Button key={`open:${run.agent}:${i}:${run.col}`} plain onPress={() => openNode($, run.agent!)}>
                {run.segs.map(segText)}
              </Button>
            ),
          )}
        </Box>
      )
    }
    const counts = filterCounts(g, t)
    const width = Math.max(40, e.props.bodyColumns - 2)
    const rule = (k: string) => <Text key={k} color={paint(FAINT)}>{'─'.repeat(width)}</Text>
    // A tab or filter: the selected one filled and bold (the theme's primary button under `colors: theme`), the rest dim.
    const pill = (key: string, hotkey: string, label: string, isOn: boolean, onPress: () => void) =>
      settings.fixed ? (
        <Button key={key} hotkey={hotkey} plain onPress={onPress}>
          {isOn ? <Text backgroundColor={TAB_FILL} bold>{` ${label} `}</Text> : <Text dimColor>{` ${label} `}</Text>}
        </Button>
      ) : (
        <Button key={key} hotkey={hotkey} {...(isOn ? { variant: 'primary' } : { dimColor: true })} onPress={onPress}>
          {label}
        </Button>
      )
    const tabs = (
      <Box key="tabs" columnGap={2} marginBottom={1}>
        {pill('tab-chart', 'g', '◆ Chart', v.tab === 'chart' && v.agentId === null, () => update($, view, toTab('chart')))}
        {pill('tab-agents', 'a', `≡ Agents ${counts.all}`, v.tab === 'agents' || v.agentId !== null, () => update($, view, toTab('agents')))}
      </Box>
    )
    // An agent's status as the Agents tab and its log say it: glyph, colours, the words, the time.
    const look = (a: Agent) => {
      const st = shownStatus(a, t)
      const glyph = st === 'running' ? spin(t, settings.motion) : st === 'waiting' ? '◐' : st === 'failed' ? '✗' : '✓'
      const color = st === 'running' ? modelColor(a.model) : st === 'waiting' ? 'warning' : st === 'failed' ? 'error' : 'success'
      const took = clock((a.ended ?? t) - a.started)
      const phase = a.remote && a.remote.taskId === null && st === 'running' ? a.remote.phase : null
      const word = phase ?? (st === 'waiting' ? 'needs permission' : st)
      const status = st === 'running' || st === 'waiting' ? `${word} ${clock(t - a.started)}` : `${st} ${took} · ${ago(a.ended ?? t, t)}`
      const tone = st === 'running' ? modelColor(a.model) : st === 'waiting' ? 'warning' : st === 'failed' ? 'error' : undefined
      const model = a.remote && !a.model ? 'remote' : modelLabel(a.model)
      return { st, glyph, color, took, phase, status, tone, model }
    }

    // An agent's log, from the chart, the list or another log: a header card, a rule with the agent and its model,
    // its task, its calls and results, how it ended; under a rule, follow, back and copy.
    const open = v.agentId === null ? undefined : findAgent(g, v.agentId)
    if (open) {
      const l = await read($, log)
      const shown: Log = l.agentId === open.id ? l : { ...NO_LOG, agentId: open.id }
      const rows = logRows(e.props.scroll.bodyRows, shown)
      drawnRows = { agentId: open.id, rows }
      const k = look(open)
      const isLive = k.st === 'running' || k.st === 'waiting'
      const run = open.remote && open.remote.taskId === null ? open.remote : null
      // A finished run can be picked up with remote-agent resume when its session came back.
      const runRow = run && { id: run.runId, worktree: run.worktree, state: k.st === 'running' || k.st === 'waiting' ? ('running' as const) : open.why === 'stopped' ? ('stopped' as const) : k.st }
      const resume = runRow && run ? resumeCommand(runRow, run.hasSession) : null
      const hint = runRow && run ? resumeHint(runRow, run.hasSession) : []
      const transcriptPath = run ? `${run.folder}/transcript.jsonl` : null
      const parentAgent = open.parent === null || open.parent === MAIN ? undefined : g.agents.find(a => a.id === open.parent)
      const parent = parentAgent ? printable(parentAgent.type) : 'main'
      const badge = k.phase ? k.phase.toUpperCase() : k.st === 'running' ? 'RUNNING' : k.st === 'waiting' ? 'WAITING' : k.st === 'failed' ? 'FAILED' : 'DONE'
      const share = ctxShare(open)
      const detail = run
        ? [printable(open.desc), '☁ remote', run.runId, `caller: ${run.linked ? parent : 'none found'}`, run.turns === null ? '' : `${run.turns} turns`, run.cost === null ? '' : `$${run.cost.toFixed(2)}`]
        : open.remote
          ? [printable(open.desc), `inside ${parent}`, `${open.steps} tool calls`]
          : [printable(open.desc), `from ${parent}`, `${open.steps} steps`, share === null ? '' : `ctx ${fmtTokens(open.ctx!)}/${fmtTokens(open.window!)}`, open.cost === null ? '' : fmtUsd(open.cost)]
      // The prompt is the task line; a call still waiting for its result says so.
      const body = shown.lines.filter(x => x.kind !== 'prompt')
      const lastKind = body.at(-1)?.kind
      const pending = isLive && (lastKind === 'call' || lastKind === 'agent')
      const visible = windowLines(body, shown.follow, shown.top, rows)
      let link = 0
      const call = (text: string) => {
        const rest = text.replace(/^▸ /, '')
        const sp = rest.indexOf(' ')
        return [
          <Text key="g" color={paint(FAINT)}>{'▸ '}</Text>,
          <Text key="t" bold>{sp < 0 ? rest : rest.slice(0, sp)}</Text>,
          sp < 0 ? '' : rest.slice(sp),
        ]
      }
      const logLine = (ln: LogLine, i: number) => {
        switch (ln.kind) {
          case 'call':
            return <Text key={`l${i}`} wrap="truncate-end">{call(ln.text)}</Text>
          case 'agent': {
            link++
            const known = ln.agentId !== undefined && g.agents.find(a => a.id === ln.agentId)
            return (
              <Box key={`l${i}`} flexDirection="column">
                <Text wrap="truncate-end">{call(ln.text)}</Text>
                {known ? (
                  <Button key={`link:${ln.agentId}:${i}`} plain {...(link <= 9 ? { hotkey: String(link) } : {})} onPress={() => openLog($, ln.agentId!, 'log')}>
                    <Text dimColor>{'  ⎿ '}</Text>
                    <Text color={paint(modelColor(known.model))}>open its log →</Text>
                  </Button>
                ) : null}
              </Box>
            )
          }
          case 'result':
            return <Text key={`l${i}`} wrap="truncate-end" dimColor>{`  ${ln.text}`}</Text>
          case 'error':
            return <Text key={`l${i}`} wrap="truncate-end" color={paint('error')}>{`  ${ln.text.replace(/^⎿ (✗ )?/, '⎿ ✗ ')}`}</Text>
          case 'note':
            return <Text key={`l${i}`} wrap="truncate-end" dimColor>{ln.text}</Text>
          default:
            return <Text key={`l${i}`} wrap="truncate-end">{ln.text}</Text>
        }
      }
      const ended = !isLive
      const chip = (text: string, color: string) => <Text backgroundColor={paint(color)} color={paint('inverseText')} bold>{` ${text} `}</Text>
      const how = run
        ? ` in ${k.took}${run.cost === null ? '' : ` · $${run.cost.toFixed(2)}`}${run.turns === null ? '' : ` · ${run.turns} turns`}${run.files === null ? '' : run.files === 0 ? ' · no changes' : ` · ${run.files} files`}`
        : ` in ${k.took} · ${open.steps} steps${open.cost === null ? '' : ` · ${fmtUsd(open.cost)}`}`
      const backTo = backLabel(v, g).replace(/^← /, '').toLowerCase()
      return (
        <Box flexDirection="column" paddingX={1}>
          {tabs}
          <Box flexDirection="column" borderStyle="round" borderColor={paint(k.color)} paddingX={1}>
            <Box columnGap={1}>
              <Button key="back" hotkey="b" plain onPress={() => back($)}>
                <Text bold>{backLabel(v, g)}</Text>
              </Button>
              <Text wrap="truncate-end">
                {'  '}
                <Text color={paint(k.color)} bold>{k.glyph}</Text>
                {' '}
                <Text bold>{printable(open.type)}</Text>
                {'  '}
                {chip(badge, k.color)}
                {'  '}
                <Text color={paint(modelColor(open.model))}>{k.model}</Text>
                <Text dimColor>{`  ·  ${isLive ? clock(t - open.started) : k.took}${ended ? ` · ended ${ago(open.ended ?? t, t)}` : ''}`}</Text>
              </Text>
            </Box>
            <Text dimColor wrap="truncate-end">{detail.filter(x => x !== '').join(' · ')}</Text>
          </Box>
          <Text> </Text>
          <Text color={paint(FAINT)} wrap="truncate-end">{`── ${printable(open.type)}${k.model ? ` · ${k.model}` : ''} ──`}</Text>
          <Text wrap="truncate-end">
            <Text dimColor>task: </Text>
            {printable(open.desc)}
          </Text>
          {shown.truncated && <Text dimColor wrap="truncate-end">{EARLIER}</Text>}
          {shown.denied !== null && <Text color={paint('warning')} wrap="wrap">{`the session gives no messages for this agent: ${shown.denied}`}</Text>}
          {shown.denied === null && body.length === 0 && <Text dimColor>{WAITING}</Text>}
          {visible.map(logLine)}
          {pending && shown.follow && (
            <Text key="pending" wrap="truncate-end">
              <Text dimColor>{'  ⎿ '}</Text>
              {k.st === 'waiting' ? <Text color={paint('warning')}>◐ waiting for your permission</Text> : [<Text key="d" color={paint(modelColor(open.model))}>●</Text>, <Text key="r" dimColor> running…</Text>]}
            </Text>
          )}
          {ended && shown.follow && (
            <Box key="end" flexDirection="column" marginTop={1}>
              <Text wrap="truncate-end">
                {k.st === 'failed' ? chip('✗ failed', 'error') : chip('✓ done', 'success')}
                <Text dimColor>{k.st === 'failed' ? ` (${open.why ?? 'error'}) after ${k.took}` : how}</Text>
              </Text>
              {resume !== null ? (
                <Box flexDirection="column" borderStyle="round" borderColor={paint('warning')} paddingX={1} marginTop={1}>
                  <Code source={resume} language="bash" />
                  <Text color={paint('warning')} wrap="wrap">{RESUME_WARNING}</Text>
                </Box>
              ) : (
                hint.map((note, i) => <Text key={`hint${i}`} dimColor wrap="wrap">{note}</Text>)
              )}
            </Box>
          )}
          <Text> </Text>
          {rule('rule')}
          <Box columnGap={1}>
            {shown.follow ? (
              <Button key="end" hotkey="e" plain onPress={() => update($, log, x => ({ ...x, follow: true }))}>
                {isLive ? [<Text key="d" color={paint(modelColor(open.model))}>●</Text>, <Text key="f" dimColor> Following live</Text>] : <Text dimColor>■ End of log</Text>}
              </Button>
            ) : (
              <Button key="end" hotkey="e" variant="primary" onPress={() => update($, log, x => ({ ...x, follow: true }))}>
                ↓ Jump to end
              </Button>
            )}
            <Text dimColor>·</Text>
            <Button key="back-foot" plain dimColor onPress={() => back($)}>
              {`b back to ${backTo}`}
            </Button>
            <Text dimColor>·</Text>
            <Button key="copy" hotkey="c" plain dimColor onPress={press => copyText($, logText(shown.lines), `${shown.lines.length} lines`, press.surface)}>
              copy log
            </Button>
            <Text dimColor>·</Text>
            {transcriptPath !== null ? (
              [
                <Button key="path" hotkey="p" plain dimColor onPress={press => copyText($, transcriptPath, 'the transcript path', press.surface)}>
                  copy path
                </Button>,
                ...(resume === null
                  ? []
                  : [
                      <Text key="dot" dimColor>·</Text>,
                      <Button key="resume" hotkey="r" plain dimColor onPress={press => copyText($, resume, 'the resume command', press.surface)}>
                        copy resume
                      </Button>,
                    ]),
              ]
            ) : (
              <Button key="transcript" hotkey="v" plain dimColor onPress={async () => {
                const r = await $.ui.view({ agentId: open.id })
                if ('deny' in r && r.deny) $.ui.toast(`cannot open its transcript: ${r.deny}`)
              }}>
                open transcript
              </Button>
            )}
            <Text dimColor wrap="truncate-end">· ↑↓ scroll</Text>
          </Box>
        </Box>
      )
    }

    // The chart, unless it does not fit the pane: then its running agents, drawn as the Agents list, until it fits again.
    const columns = Math.max(40, e.props.bodyColumns - 2)
    const opts = { columns, lingerMs: settings.lingerMs, squadMin: settings.squadMin, motion: settings.motion && e.surface === 'terminal', expanded: v.expanded }
    const c = v.tab === 'chart' ? chart(g, t, opts, tween) : null
    if (c) fall = fallback(fall, c.width, columns, t)
    const narrow = c !== null && fall.narrow
    if (v.tab === 'agents' || narrow) {
      const items = listItems(g, t, narrow ? 'running' : v.filter, settings.squadMin)
      let n = 0
      // An agent's context: a 4-cell gauge coloured by how full, and its share of the window; blank without a reading.
      const ctxCell = (a: Agent, quiet: boolean) => {
        const share = ctxShare(a)
        return share === null
          ? <Text>{' '.repeat(14)}</Text>
          : [
              <Text key="gauge" dimColor={quiet} color={paint(quiet ? undefined : ctxColor(share))}>{gauge(share, 4)}</Text>,
              <Text key="pct" dimColor>{fit(` ${Math.round(share * 100)}% ctx`, 10)}</Text>,
            ]
      }
      // A row, as a table: number, glyph, type (↳ under another agent), status, model, context, task.
      // The task column: the agent's task; a remote run's (named after it already) says what it changed instead.
      const taskOf = (a: Agent) => {
        const run = a.remote && a.remote.taskId === null ? a.remote : null
        if (!run) return printable(a.desc)
        const what = run.files === null ? run.runId : run.files === 0 ? 'no changes' : `${run.files} file${run.files === 1 ? '' : 's'} changed`
        return `☁ remote · ${what}`
      }
      // The line under a live or failed agent: its step, or why it failed; '' for none.
      const underOf = (a: Agent, k: ReturnType<typeof look>) =>
        k.st === 'running' || k.st === 'waiting' ? (k.phase ? `◌ ${k.phase}…` : `⎿ ${printable(a.step ?? 'thinking')}`) : k.st === 'failed' ? `⎿ ${a.why ?? 'error'}` : ''
      const underLine = (a: Agent, k: ReturnType<typeof look>, indent: string) => {
        const under = underOf(a, k)
        return under === '' ? null : (
          <Text key={`under:${a.id}`} wrap="truncate-end">
            <Text color={paint(FAINT)}>{`${indent}${under.slice(0, 2)}`}</Text>
            <Text dimColor={k.st === 'running'} color={paint(k.st === 'waiting' ? 'warning' : k.st === 'failed' ? 'error' : undefined)}>{under.slice(2)}</Text>
          </Text>
        )
      }
      // The columns after the name, the same on a row and on an agent listed under it. No cost: the log's card has it.
      const cells = (a: Agent, k: ReturnType<typeof look>, isOld: boolean) => {
        const quiet = isOld || k.st === 'done'
        return [
          <Text key="status" dimColor={isOld || !k.tone} color={paint(isOld ? undefined : k.tone)}>{fit(k.status, 26)}</Text>,
          <Text key="model" dimColor={isOld} color={paint(isOld ? undefined : modelColor(a.model))}>{fit(k.model, 12)}</Text>,
          ctxCell(a, quiet),
          <Text key="task" dimColor={quiet}>{fit(taskOf(a), 28)}</Text>,
        ]
      }
      // A row, as a table: number, glyph, type, status, model, context, task; the agents it started under it,
      // indented, each with its own figures; a blank line after a row with something under it.
      const row = (a: Agent, isOld: boolean, kids: Kid[]) => {
        const k = look(a)
        const quiet = isOld || k.st === 'done'
        const hot = ++n <= 9 ? { hotkey: String(n) } : {}
        const name = `${a.parent !== null && a.parent !== MAIN ? '↳ ' : ''}${printable(a.type)}`
        const under = isOld ? null : underLine(a, k, '       ')
        return (
          <Box key={`row:${a.id}`} flexDirection="column" marginBottom={!isOld && (under !== null || kids.length > 0) ? 1 : 0}>
            <Button key={`agent:${a.id}`} plain {...hot} onPress={() => openLog($, a.id, 'agents')}>
              <Text color={paint(isOld ? undefined : k.color)} dimColor={isOld} bold>{k.glyph}</Text>
              {' '}
              <Text bold dimColor={quiet} color={paint(quiet ? undefined : modelColor(a.model))}>{fit(name, 20)}</Text>
              {cells(a, k, isOld)}
            </Button>
            {under}
            {kids.map(({ agent: c, depth }) => {
              const kk = look(c)
              const pad = '  '.repeat(depth - 1)
              return (
                <Box key={`kid:${c.id}`} flexDirection="column">
                  <Button key={`agent:${c.id}`} plain onPress={() => openLog($, c.id, 'agents')}>
                    <Text color={paint(FAINT)}>{`   ${pad}↳ `}</Text>
                    <Text color={paint(isOld ? undefined : kk.color)} dimColor={isOld} bold>{kk.glyph}</Text>
                    {' '}
                    <Text dimColor={isOld || kk.st === 'done'} color={paint(isOld || kk.st === 'done' ? undefined : modelColor(c.model))}>{fit(printable(c.type), 18 - pad.length)}</Text>
                    {cells(c, kk, isOld)}
                  </Button>
                  {isOld ? null : underLine(c, kk, `         ${pad}`)}
                </Box>
              )
            })}
          </Box>
        )
      }
      // A squad member, one compact line under its group.
      const member = (a: Agent) => {
        const k = look(a)
        const tail = k.st === 'running' || k.st === 'waiting' ? `⎿ ${printable(a.step ?? 'thinking')}` : k.st === 'failed' ? (a.why ?? 'error') : ''
        return (
          <Button key={`agent:${a.id}`} plain onPress={() => openLog($, a.id, 'agents')}>
            {'     '}
            <Text color={paint(k.color)} bold>{k.glyph}</Text>
            {' '}
            <Text dimColor={k.st === 'done'}>{fit(printable(a.desc), 26)}</Text>
            <Text dimColor={!k.tone} color={paint(k.tone)}>{fit(k.status, 24)}</Text>
            {ctxCell(a, k.st === 'done')}
            <Text dimColor={k.st !== 'failed' && k.st !== 'waiting'} color={paint(k.st === 'failed' ? 'error' : k.st === 'waiting' ? 'warning' : undefined)}>{tail}</Text>
          </Button>
        )
      }
      const group = (it: Extract<Item, { kind: 'group' }>) => {
        const isOpen = v.groups.includes(it.id)
        const c = layoutCounts(it.members, t)
        const lead = it.members[0]!
        const toggle = () => update($, view, (x): View => ({ ...x, groups: isOpen ? x.groups.filter(id => id !== it.id) : [...x.groups, it.id] }))
        const list = isOpen ? it.members : it.needing
        return (
          <Box key={`group:${it.id}`} flexDirection="column" marginBottom={1}>
            <Button key={`group:${it.id}`} plain onPress={toggle}>
              <Text dimColor>{isOpen ? '▾ ' : '▸ '}</Text>
              <Text color={paint(modelColor(lead.model))} bold>{fit(`${printable(it.type)} ×${it.members.length}`, 22)}</Text>
              {bar(c, it.members.length, 16).map(([text, st], j) => (
                <Text key={`b${j}`} {...(st.color ? { color: paint(st.color) } : {})} {...(st.dim ? { dimColor: true } : {})}>{text}</Text>
              ))}
              <Text dimColor>{`  ${c.done + c.failed}/${it.members.length} done · ${c.running} running`}</Text>
              {c.waiting > 0 && <Text color={paint('warning')}>{` · ◐ ${c.waiting} need you`}</Text>}
              {c.failed > 0 && <Text color={paint('error')}>{` · ✗ ${c.failed}`}</Text>}
            </Button>
            {list.map(member)}
            {!isOpen && it.members.length > list.length && (
              <Button key={`more:${it.id}`} plain onPress={toggle}>
                <Text color={paint(FAINT)}>{`       … ${it.members.length - list.length} more · click to show`}</Text>
              </Button>
            )}
          </Box>
        )
      }
      const sections: [Item['section'], string, boolean][] = [['recent', 'this turn', false], ['earlier', 'earlier this session', false], ['others', 'other sessions', false], ['older', '── older ──', true]]
      return (
        <Box flexDirection="column" paddingX={1}>
          {tabs}
          {narrow ? (
            <Text key="narrow" dimColor wrap="truncate-end">{`   the chart needs ${c!.width + 2} columns: its running agents · widen the pane for the chart, a for every agent`}</Text>
          ) : (
            <Box key="filters" columnGap={1} marginBottom={1}>
              {FILTERS.map(([f, key, label]) => (
                pill(`filter-${f}`, key, `${label} ${counts[f]}`, v.filter === f, () => update($, view, toFilter(f)))
              ))}
            </Box>
          )}
          {counts.all === 0 && <Text dimColor>No agents yet this session: they show here as they start, and stay once they finish.</Text>}
          {counts.all > 0 && items.length === 0 && <Text dimColor>{narrow ? '   nothing running' : '   nothing here'}</Text>}
          {sections.map(([sec, title, isOld]) => {
            const its = items.filter(it => it.section === sec)
            if (!its.length) return null
            return (
              <Box key={`sec:${sec}`} flexDirection="column" marginBottom={1}>
                <Text {...(isOld ? { color: paint(FAINT) } : { dimColor: true })}>{`   ${title}`}</Text>
                {its.map(it => (it.kind === 'group' ? group(it) : row(it.agent, isOld, it.kids)))}
              </Box>
            )
          })}
          {rule('rule')}
          <Text dimColor wrap="truncate-end">{` ${LIST_KEYS}`}</Text>
        </Box>
      )
    }

    const body = c!.rows
    const squad = c!.squads[0]
    return (
      <Box flexDirection="column" paddingX={1}>
        {tabs}
        <Box flexDirection="column" alignItems="center" marginBottom={1}>
          {line(headerRow(g, t), -1)}
          {line(needsRow(g, t), -2)}
        </Box>
        {body.map(line)}
        <Box columnGap={2} marginTop={1}>
          {squad && (
            <Button key="expand" hotkey="x" dimColor onPress={() => update($, view, x => ({ ...x, expanded: x.expanded === squad.id ? null : squad.id }))}>
              {v.expanded === squad.id ? `Collapse ${squad.label}` : `Expand ${squad.label}`}
            </Button>
          )}
          <Text dimColor wrap="truncate-end">{KEYS}</Text>
        </Box>
      </Box>
    )
  })
}
