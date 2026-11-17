// The agent-flow mod: `/agent-flow` opens a pane drawing the session's agents as an org chart, live. It only
// observes: every hook passes its call on with next(e), unchanged, and records what happened through flow.ts's
// reducer. Logic sits in plain .ts files (flow.ts, lifecycle.ts, layout.ts); this module only calls `$` and draws.
// A failure to record or draw is caught: it never fails, delays or changes a tool call.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, RenderSurface } from 'claude-code'

import { type Agent, ctxShare, emptyGraph, type FlowEvent, fmtTokens, MAIN, modelColor, modelLabel, printable, reduce, shownStatus, statusText, summarize } from './flow.ts'
import { addMessages, ago, backLabel, logText, EARLIER, filterCounts, FIRST_READ, goBack, type Item, listItems, type Message, NO_LOG, openFrom, scrollLog, WAITING, windowLines } from './log.ts'
import { chart, clock, fallback, headerRow, needsRow, newTween, noFallback, type Row, type Seg, TWEEN_MS, tree } from './layout.ts'
import { isMoving } from './lifecycle.ts'
import {
  applyLiveness,
  costLine,
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
import type { Filter, Log, LogLine, View } from './state.d.ts'

const PANE = 'agent-flow'
const TICK_MS = 125
// A pane not drawn for this long is taken as closed: the timer stops redrawing it until something changes.
const DRAWN_MS = 2000
const KEYS = 't tree/chart · x expand squad · a agents · esc close'
const LIST_KEYS = '1-9 open · l r n f d filter · g chart · esc close'
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

const graph = atom({ plugin: 'agent-flow', key: 'graph' } as const, emptyGraph(), { shape: 'graph-3' })
const NO_VIEW: View = { tab: 'chart', tree: false, expanded: null, agentId: null, filter: 'all', groups: [], trail: [], origin: 'chart' }
const view = atom({ plugin: 'agent-flow', key: 'view' } as const, NO_VIEW, { shape: 'view-3' })
const log = atom({ plugin: 'agent-flow', key: 'log' } as const, NO_LOG, { shape: 'log-1' })
const frame = atom({ plugin: 'agent-flow', key: 'frame' } as const, 0, { shape: 'frame-1' })

// Per-process state, which a reload starts again: where boxes were drawn, the status line last set, the last draw.
let tween = newTween()
let fall = noFallback()
let lastStatus: string | undefined | null = null
let lastDraw = 0
let isTicking = false
let settings = { lingerMs: 30_000, squadMin: 4, motion: true }
// The open log's reads go one at a time (the timer and a press may both ask); when it was last read; the lines its
// window showed at the last draw, which a scroll moves by.
let logQueue: Promise<void> = Promise.resolve()
let logReadAt = 0
let ctxReadAt = 0
let drawnRows = { agentId: null as string | null, rows: 10 }

// Remote runs. The scan reads the repo's run folders at most every REMOTE_EVERY_MS while a remote-agent call or a
// run of this session is live (and once after the mod loads), rereading a folder's files only when they changed.
const REMOTE_EVERY_MS = 1500
// A call sent to the background ends at once; its run may start up to this long after.
const LAUNCH_WAIT_MS = 120_000
// $.fs.read refuses a file over 4 MiB: a transcript under this is read whole, a bigger one through `tail`.
const FS_READ_MAX = 3 << 20
let remoteAt = 0
let remoteScanned = false
let repo: { cwd: string; common: string | null } | null = null
let sessionId: string | null = null
// Per run folder: the key of the files last read, and what they gave; a finished run of another session is skipped.
type Seen = { key: string; run: Run; result: Result | null; scan: Scan | null; transcriptSize: number; isFinal: boolean; isOurs: boolean }
let seen = new Map<string, Seen>()
// The open remote log's files as last read, so an unchanged log is not read again.
let remoteLogKey = ''
let listDrawnAt = 0

async function now($: EngineInterface): Promise<number> {
  try {
    return await $.clock.now()
  } catch {
    return 0
  }
}

/** Sets the status line only when its text changed. */
async function status($: EngineInterface): Promise<void> {
  const text = statusText(await read($, graph), await now($))
  if (text === lastStatus) return
  lastStatus = text
  $.ui.status(text)
}

/** Records one event; never throws. */
async function record($: EngineInterface, ev: FlowEvent): Promise<void> {
  try {
    await update($, graph, g => reduce(g, ev))
    await status($)
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
    const target = agentId === null ? undefined : g.agents.find(a => a.id === agentId)
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

/** Whether the run folders are worth reading at `t`: a remote-agent call or a run of this session is live, or none was read yet. */
function remoteDue(g: { launches: { ended: number | null; at: number; background: boolean }[]; agents: Agent[] }, t: number): boolean {
  if (t - remoteAt < REMOTE_EVERY_MS) return false
  if (!remoteScanned) return true
  if (g.launches.some(l => l.ended === null || (l.background && t - l.at < LAUNCH_WAIT_MS) || t - (l.ended ?? l.at) < 10_000)) return true
  return g.agents.some(a => a.remote !== null && a.remote.taskId === null && a.status === 'running')
}

/**
 * Reads this session's remote runs into the graph: each run folder whose run.json names this session as its caller,
 * its liveness, result and transcript. A folder's files are reread only when they changed; a finished run of
 * another session is not listed again. Never throws.
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
    const ours: Seen[] = []
    for (const { id, folder } of await runFolders($, common)) {
      const was = seen.get(folder)
      if (was?.isFinal && !was.isOurs) {
        kept.set(folder, was)
        continue
      }
      const key = [await stamp($, `${folder}/run.json`), await stamp($, `${folder}/progress.log`), await stamp($, `${folder}/transcript.jsonl`)].join('|')
      let s = was
      if (!was || was.key !== key) {
        const changed = Math.max(...key.split('|').map(k => Number(k.split(':')[1] ?? 0) || 0))
        const run = parseRun({ id, folder, runJson: await text($, `${folder}/run.json`), changed })
        const isOurs = run.session !== null && run.session === sessionId
        const isFinal = run.state === 'done' || run.state === 'failed'
        let scan = was?.scan ?? null
        let size = was?.transcriptSize ?? -1
        if (isOurs) {
          const st = await stamp($, `${folder}/transcript.jsonl`)
          const now = Number(st.split(':')[0] ?? -1)
          if (st !== '' && now !== size) {
            const tr = await transcriptText($, `${folder}/transcript.jsonl`)
            if (tr) scan = scanTranscript(tr.text)
            size = now
          }
        }
        const result = isOurs && isFinal ? parseResult(await text($, `${folder}/result.json`)) : null
        s = { key, run, result, scan, transcriptSize: size, isFinal, isOurs }
      }
      kept.set(folder, s!)
      if (s!.isOurs) ours.push(s!)
    }
    seen = kept
    if (!ours.length) return
    // Liveness: ps where processes run; elsewhere a run whose files went quiet for 5 minutes counts as stopped.
    const runs = ours.map(s => s.run)
    const out = await ps($, livePids(runs))
    const judged = out === null ? applyStaleness(runs, t) : applyLiveness(runs, out)
    const evs = ours.flatMap((s, i) => runEvents(remoteRun(judged[i]!, s.result, s.scan, t), s.scan, t))
    await update($, graph, g => evs.reduce(reduce, g))
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
      }
    } catch {
      // no agents listed on this surface
    }
    try {
      const { context } = await $.session.usage()
      if (context.tokens !== undefined && context.window) evs.push({ kind: 'ctx', at: t, agent: MAIN, used: context.tokens, window: context.window })
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

/** A tab, or a filter: either leaves any open log and forgets the way back. */
const toTab = (tab: View['tab']) => (v: View): View => ({ ...v, tab, agentId: null, trail: [] })
const toFilter = (filter: Filter) => (v: View): View => ({ ...v, tab: 'agents', filter, agentId: null, trail: [] })

/** Copies `text` on the surface that pressed, and toasts what happened. */
async function copyText($: EngineInterface, text: string, lines: number, surface: RenderSurface): Promise<void> {
  try {
    const got = await $.ui.copy({ text, surface })
    $.ui.toast(got.isCopied ? `copied ${lines} lines` : `not copied: ${got.reason ?? 'refused'}`)
  } catch {
    $.ui.toast('not copied')
  }
}

/** Redraws the pane while something moves on it and it was drawn lately; keeps the status line current. */
async function tick($: EngineInterface): Promise<void> {
  if (isTicking) return
  isTicking = true
  try {
    const t = await now($)
    await status($)
    await readCtx($, t)
    await scanRemote($, t)
    const isDrawn = t - lastDraw < DRAWN_MS
    const isGliding = t - tween.t0 < TWEEN_MS
    const v = await read($, view)
    if (isDrawn && v.agentId !== null && t - logReadAt >= LOG_EVERY_MS) {
      const g = await read($, graph)
      if (logIsLive(g.agents.find(a => a.id === v.agentId), t)) await followLog($)
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

const numberOr = (v: unknown, d: number, min: number) => (typeof v === 'number' && Number.isFinite(v) && v >= min ? v : d)

export const register: Register = (on, options) => {
  const o = (options ?? {}) as Record<string, unknown>
  settings = {
    lingerMs: numberOr(o.linger_s, 30, 0) * 1000,
    squadMin: Math.round(numberOr(o.squad_min, 4, 2)),
    motion: o.motion !== 'off',
  }

  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'agent-flow', description: "Show this session's agents as a live org chart in a pane" })
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

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    await read($, frame) // redrawn on each frame the timer counts
    const g = await read($, graph)
    const v = await read($, view)
    const t = await now($)
    lastDraw = t
    const segText = (sg: Seg, i: number) => (
      <Text key={`s${i}`} {...(sg.color ? { color: sg.color } : {})} {...(sg.dim ? { dimColor: true } : {})} {...(sg.bold ? { bold: true } : {})}>
        {sg.text}
      </Text>
    )
    // A row: plain text, or, where it holds a box or tree line, a run of pieces, each one a node's a click opens.
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
    const tabs = (
      <Box key="tabs" columnGap={2} marginBottom={1}>
        <Button key="tab-chart" hotkey="g" {...(v.tab === 'chart' ? { variant: 'primary' } : { dimColor: true })} onPress={() => update($, view, toTab('chart'))}>
          Chart
        </Button>
        <Button key="tab-agents" hotkey="a" {...(v.tab === 'agents' ? { variant: 'primary' } : { dimColor: true })} onPress={() => update($, view, toTab('agents'))}>
          {`Agents (${counts.all})`}
        </Button>
      </Box>
    )
    const look = (a: Agent) => {
      const st = shownStatus(a, t)
      const glyph = st === 'running' ? '●' : st === 'waiting' ? '◐' : st === 'failed' ? '✗' : '✓'
      const color = st === 'running' ? modelColor(a.model) : st === 'waiting' ? 'warning' : st === 'failed' ? 'error' : 'success'
      const when =
        st === 'running' || st === 'waiting'
          ? `${st === 'waiting' ? 'needs you' : 'running'} ${clock(t - a.started)}`
          : `${st === 'failed' ? `failed (${a.why ?? 'error'})` : 'done'} ${ago(a.ended ?? t, t)} · took ${clock((a.ended ?? t) - a.started)}`
      const share = ctxShare(a)
      const ctx = share === null ? '' : `ctx ${fmtTokens(a.ctx!)}/${fmtTokens(a.window!)} (${Math.round(share * 100)}%)`
      const run = a.remote && a.remote.taskId === null ? a.remote : null
      const spent = run ? costLine(run.cost, run.files) : ''
      const meta = [run ? `☁ remote${modelLabel(a.model) ? ` ${modelLabel(a.model)}` : ''}` : modelLabel(a.model), when, `${a.steps} steps`, ctx, spent].filter(x => x !== '').join(' · ')
      return { st, glyph, color, meta }
    }

    // An agent's log, from the chart, the list or another log.
    const open = v.agentId === null ? undefined : g.agents.find(a => a.id === v.agentId)
    if (open) {
      const l = await read($, log)
      const shown: Log = l.agentId === open.id ? l : { ...NO_LOG, agentId: open.id }
      const rows = logRows(e.props.scroll.bodyRows, shown)
      drawnRows = { agentId: open.id, rows }
      const k = look(open)
      const isLive = k.st === 'running' || k.st === 'waiting'
      const parent =
        (open.parent === null || open.parent === MAIN ? 'main' : printable(g.agents.find(a => a.id === open.parent)?.type ?? 'an agent')) +
        (open.remote && open.remote.taskId === null && !open.remote.linked ? ' (no remote-agent call found for it)' : '')
      const badge = k.st === 'running' ? 'RUNNING' : k.st === 'waiting' ? 'NEEDS YOU' : k.st === 'failed' ? 'FAILED' : 'DONE'
      const visible = windowLines(shown.lines, shown.follow, shown.top, rows)
      let link = 0
      const logLine = (ln: LogLine, i: number) => {
        switch (ln.kind) {
          case 'prompt':
            return <Text key={`l${i}`} wrap="truncate-end" color="suggestion">{`› ${ln.text}`}</Text>
          case 'call':
            return <Text key={`l${i}`} wrap="truncate-end" bold>{ln.text}</Text>
          case 'agent': {
            // A link to the agent this call started: its log, with Back returning here.
            link++
            const known = ln.agentId !== undefined && g.agents.some(a => a.id === ln.agentId)
            if (!known) return <Text key={`l${i}`} wrap="truncate-end" bold>{ln.text}</Text>
            return (
              <Button key={`link:${ln.agentId}:${i}`} plain {...(link <= 9 ? { hotkey: String(link) } : {})} onPress={() => openLog($, ln.agentId!, 'log')}>
                <Text bold>{ln.text}</Text>
                <Text color="suggestion">{'  ↗ open its log'}</Text>
              </Button>
            )
          }
          case 'result':
            return <Text key={`l${i}`} wrap="truncate-end" dimColor>{`  ${ln.text}`}</Text>
          case 'error':
            return <Text key={`l${i}`} wrap="truncate-end" color="error">{`  ${ln.text}`}</Text>
          case 'note':
            return <Text key={`l${i}`} wrap="truncate-end" dimColor>{ln.text}</Text>
          default:
            return <Text key={`l${i}`} wrap="truncate-end">{ln.text}</Text>
        }
      }
      const took = clock((open.ended ?? t) - open.started)
      return (
        <Box flexDirection="column" paddingX={1}>
          {tabs}
          <Box flexDirection="column" borderStyle="round" borderColor={k.color} paddingX={1} marginBottom={1}>
            <Box columnGap={1}>
              <Button key="back" hotkey="b" onPress={() => back($)}>
                {backLabel(v, g)}
              </Button>
              <Text wrap="truncate-end">
                <Text color={k.color}>{k.glyph}</Text>
                {' '}
                <Text bold>{printable(open.type)}</Text>
                {'  '}
                {printable(open.desc)}
                {'  '}
                <Text backgroundColor={k.color} color="inverseText" bold>{` ${badge} `}</Text>
              </Text>
            </Box>
            <Text dimColor wrap="truncate-end">{`${k.meta} · from ${parent}`}</Text>
          </Box>
          {shown.truncated && <Text dimColor wrap="truncate-end">{EARLIER}</Text>}
          {shown.denied !== null && <Text color="warning" wrap="wrap">{`the session gives no messages for this agent: ${shown.denied}`}</Text>}
          {shown.denied === null && shown.lines.length === 0 && <Text dimColor>{WAITING}</Text>}
          {visible.map(logLine)}
          {!isLive && shown.follow && (
            <Text key="end" wrap="truncate-end">
              <Text backgroundColor={k.st === 'failed' ? 'error' : 'success'} color="inverseText" bold>{k.st === 'failed' ? ' ✗ failed ' : ' ✓ done '}</Text>
              <Text dimColor>{k.st === 'failed' ? ` ${open.why ?? 'error'} after ${took} · ${open.steps} steps` : ` in ${took} · ${open.steps} steps`}</Text>
            </Text>
          )}
          <Box columnGap={2} marginTop={1}>
            {shown.follow ? (
              <Button key="end" hotkey="e" dimColor onPress={() => update($, log, x => ({ ...x, follow: true }))}>
                {isLive ? '● Following live' : '■ End of log'}
              </Button>
            ) : (
              <Button key="end" hotkey="e" variant="primary" onPress={() => update($, log, x => ({ ...x, follow: true }))}>
                ↓ Jump to end
              </Button>
            )}
            <Button key="copy" hotkey="c" dimColor onPress={press => copyText($, logText(shown.lines), shown.lines.length, press.surface)}>
              Copy log
            </Button>
            <Button key="transcript" hotkey="v" dimColor onPress={async () => {
              const r = await $.ui.view({ agentId: open.id })
              if ('deny' in r && r.deny) $.ui.toast(`cannot open its transcript: ${r.deny}`)
            }}>
              Open transcript
            </Button>
            <Text dimColor wrap="truncate-end">{LOG_KEYS}</Text>
          </Box>
        </Box>
      )
    }

    if (v.tab === 'agents') {
      const items = listItems(g, t, v.filter, settings.squadMin)
      let n = 0
      const hot = () => (++n <= 9 ? { hotkey: String(n) } : {})
      const row = (a: Agent, isOld: boolean) => {
        const k = look(a)
        const detail = k.st === 'running' || k.st === 'waiting' ? (a.step ? `⎿ ${printable(a.step)}` : '⎿ thinking') : k.st === 'failed' ? `⎿ ${a.why ?? 'error'}` : ''
        return (
          <Box key={`row:${a.id}`} flexDirection="column">
            <Button key={`agent:${a.id}`} plain dimColor={isOld} {...hot()} onPress={() => openLog($, a.id, 'agents')}>
              <Text {...(isOld ? { dimColor: true } : { color: k.color })}>{k.glyph}</Text> <Text bold={!isOld}>{printable(a.type)}</Text>{'  '}{printable(a.desc)}{'  '}
              <Text dimColor>{k.meta}</Text>
            </Button>
            {!isOld && detail !== '' && <Text dimColor wrap="truncate-end">{`  ${detail}`}</Text>}
          </Box>
        )
      }
      // A squad member, one compact line under its group.
      const member = (a: Agent) => {
        const k = look(a)
        return (
          <Button key={`agent:${a.id}`} plain {...hot()} onPress={() => openLog($, a.id, 'agents')}>
            {'   '}
            <Text color={k.color}>{k.glyph}</Text> {printable(a.desc)}{'  '}
            <Text dimColor>{k.meta}</Text>
          </Button>
        )
      }
      const group = (it: Extract<Item, { kind: 'group' }>) => {
        const isOpen = v.groups.includes(it.id)
        const c = { running: 0, waiting: 0, done: 0, failed: 0 }
        for (const m of it.members) c[shownStatus(m, t)]++
        const lead = it.members[0]!
        return (
          <Box key={`group:${it.id}`} flexDirection="column">
            <Button key={`group:${it.id}`} plain {...hot()} onPress={() => update($, view, (x): View => ({ ...x, groups: isOpen ? x.groups.filter(id => id !== it.id) : [...x.groups, it.id] }))}>
              <Text bold>{`${isOpen ? '▾' : '▸'} ${printable(it.type)} ×${it.members.length}`}</Text>{'  '}
              <Text color={modelColor(lead.model)}>{modelLabel(lead.model)}</Text>{'  '}
              <Text color={modelColor(lead.model)}>{`● ${c.running}`}</Text>
              <Text dimColor>{' · '}</Text>
              <Text color="warning">{`◐ ${c.waiting}`}</Text>
              <Text dimColor>{' · '}</Text>
              <Text color="success">{`✓ ${c.done}`}</Text>
              <Text dimColor>{' · '}</Text>
              <Text color="error">{`✗ ${c.failed}`}</Text>
            </Button>
            {(isOpen ? it.members : it.needing).map(member)}
          </Box>
        )
      }
      const sections: [Item['section'], string | null][] = [['recent', null], ['earlier', '── earlier this session ──'], ['older', '── older ──']]
      return (
        <Box flexDirection="column" paddingX={1}>
          {tabs}
          <Box key="filters" columnGap={1} marginBottom={1}>
            {FILTERS.map(([f, key, label]) => (
              <Button key={`filter-${f}`} hotkey={key} {...(v.filter === f ? { variant: 'primary' } : { dimColor: true })} onPress={() => update($, view, toFilter(f))}>
                {`${label} ${counts[f]}`}
              </Button>
            ))}
          </Box>
          <Box flexDirection="column" rowGap={1}>
            {counts.all === 0 && <Text dimColor>No agents yet this session: they show here as they start, and stay once they finish.</Text>}
            {counts.all > 0 && items.length === 0 && <Text dimColor>None match this filter.</Text>}
            {sections.map(([sec, title]) => {
              const its = items.filter(it => it.section === sec)
              if (!its.length) return null
              const isOld = sec !== 'recent'
              const body = its.map(it => (it.kind === 'group' ? group(it) : row(it.agent, isOld)))
              return title === null ? (
                body
              ) : (
                <Box key={`sec:${sec}`} flexDirection="column">
                  <Text dimColor>{title}</Text>
                  {body}
                </Box>
              )
            })}
            <Text dimColor>{LIST_KEYS}</Text>
          </Box>
        </Box>
      )
    }

    const columns = Math.max(40, e.props.bodyColumns - 2)
    const opts = { columns, lingerMs: settings.lingerMs, squadMin: settings.squadMin, motion: settings.motion && e.surface === 'terminal', expanded: v.expanded }
    const c = chart(g, t, opts, tween)
    fall = fallback(fall, c.width, columns, t)
    const asTree = v.tree || fall.tree
    const body = asTree ? tree(g, t, opts) : c.rows
    const squad = c.squads[0]
    return (
      <Box flexDirection="column" paddingX={1}>
        {tabs}
        <Box flexDirection="column" alignItems="center" marginBottom={1}>
          {line(headerRow(g, t), -1)}
          {line(needsRow(g, t), -2)}
        </Box>
        {body.map(line)}
        <Box columnGap={2} marginTop={1}>
          {fall.tree && !v.tree ? (
            <Text key="view" dimColor>{`tree: the chart needs ${c.width + 2} columns`}</Text>
          ) : (
            <Button key="view" hotkey="t" dimColor onPress={() => update($, view, x => ({ ...x, tree: !x.tree }))}>
              {asTree ? 'Chart view' : 'Tree view'}
            </Button>
          )}
          {squad && !asTree && (
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
