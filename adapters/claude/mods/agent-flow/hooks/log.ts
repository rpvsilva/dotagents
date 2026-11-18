// An agent's log: its messages, as `$.session.messages({ agentId })` returns them, turned into lines the pane draws,
// and the window the pane shows of them. Pure: messages in, lines out; tested by log.unit.ts under node --test.
//
// The scrolling (scrollLog, windowLines, appendLines) follows the remote-runs mod's transcript.ts; it is copied, not
// imported, since each mod is installed as its own plugin.

import { type Agent, findAgent, type Graph, MAIN, printable, shownStatus, summarize, withoutJson } from './flow.ts'
import type { Filter, Log, LogLine, View } from './state.d.ts'

export type { Filter, Log, LogLine }

/** The most lines a log keeps; past it the oldest go and the log says so. */
export const LINES_MAX = 2000
/** The longest line drawn; a longer one ends in `…`. */
export const LINE_MAX = 200
/** How many messages the first read of a log asks for; an agent with more shows only its newest. */
export const FIRST_READ = 400
export const EARLIER = '(earlier messages not shown: v opens the whole transcript)'
export const WAITING = 'no messages yet'

export const NO_LOG: Log = { agentId: null, lines: [], after: null, follow: true, top: 0, truncated: false, denied: null }

/** The message fields the log reads; anything else on a message is ignored. */
export type Message = {
  role: 'user' | 'assistant'
  text?: string
  toolUses?: { tool?: string; input?: Record<string, unknown>; agentId?: string }[]
  toolResults?: { text?: string; isError?: boolean }[]
  uuid?: string
}

/**
 * Secret-shaped strings and what replaces them, as the remote-runs mod masks them. Best effort: a tool result may
 * print a token; a false positive hides some text, and a token of a shape not listed here still shows.
 */
const SECRETS: readonly [RegExp, string][] = [
  [/-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*/g, '[masked private key]'],
  [/\bsk-ant-[A-Za-z0-9_-]+/g, '[masked]'],
  [/\bgithub_pat_[A-Za-z0-9_]+/g, '[masked]'],
  [/\bgh[pousr]_[A-Za-z0-9]{20,}/g, '[masked]'],
  [/\bxox[abprs]-[A-Za-z0-9-]+/g, '[masked]'],
  [/\bAKIA[0-9A-Z]{16}\b/g, '[masked]'],
  [/\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, '[masked]'],
  [/\b(Bearer|Basic)\s+[^\s"',]+/gi, '$1 [masked]'],
  [/(:\/\/)[^\s/:@]+:[^\s/@]+@/g, '$1[masked]@'],
  [/\b(\w*(?:KEY|TOKEN|SECRET|PASSWORD)\w*["']?\s*[=:]\s*["']?)[^\s"',;]+/gi, '$1[masked]'],
]

/** `line` with every secret-shaped string replaced by `[masked]`. */
export function mask(line: string): string {
  let out = line
  for (const [re, by] of SECRETS) out = out.replace(re, by)
  return out
}

/**
 * One line of at most `max` characters: whitespace runs to one space, control characters dropped, secrets masked,
 * then cut, so a cut never leaves part of a secret unmasked. Masking runs on the first `4 * max` characters only.
 */
export function oneLine(s: string, max = LINE_MAX): string {
  const flat = printable(s.replace(/\s+/g, ' ').trim())
  const masked = mask(flat.slice(0, 4 * max))
  if (masked.length > max) return `${masked.slice(0, max - 1)}…`
  return flat.length > 4 * max ? `${masked}…` : masked
}

/** The non-empty lines of `text`, each made one printable line. */
const linesOf = (text: string) =>
  text
    .split('\n')
    .map(l => oneLine(l))
    .filter(l => l !== '')

/**
 * The lines one message draws as. A user message: the agent's prompt (`isFirst`) or a later message to it, and the
 * first line of each tool result (`⎿`, an error's in red), with how many lines it held. An assistant message: its
 * text less any JSON object in it (an outcome a skill's brief asks for, which the end line already says), then each
 * tool call (`▸ Bash npm test`).
 */
export function messageLines(m: Message, isFirst: boolean): LogLine[] {
  const out: LogLine[] = []
  if (m.role === 'user') {
    for (const l of linesOf(m.text ?? '')) out.push({ kind: isFirst ? 'prompt' : 'text', text: l })
    for (const r of m.toolResults ?? []) {
      const ls = linesOf(r.text ?? '')
      const more = ls.length > 1 ? `  (+${ls.length - 1} lines)` : ''
      out.push({ kind: r.isError ? 'error' : 'result', text: `⎿ ${ls[0] ?? '(no output)'}${more}` })
    }
    return out
  }
  for (const l of linesOf(withoutJson(m.text ?? ''))) out.push({ kind: 'text', text: l })
  for (const u of m.toolUses ?? []) {
    const text = `▸ ${oneLine(summarize(u.tool ?? 'tool', u.input ?? {}))}`
    // An Agent call that started an agent links to that agent's log.
    out.push(u.agentId ? { kind: 'agent', text, agentId: u.agentId } : { kind: 'call', text })
  }
  return out
}

/**
 * `log` with the messages of one read added: their lines at the end, `after` moved to the last one's uuid. The
 * first read of a log (`after` null) that came back full marks the log truncated: older messages were left out.
 */
export function addMessages(log: Log, msgs: readonly Message[], asked = FIRST_READ): Log {
  if (msgs.length === 0) return log
  const isStart = log.after === null && log.lines.length === 0
  const added = msgs.flatMap((m, i) => messageLines(m, isStart && i === 0 && !(msgs.length >= asked)))
  const after = msgs[msgs.length - 1]?.uuid ?? log.after
  const next = appendLines({ ...log, after, truncated: log.truncated || (isStart && msgs.length >= asked) }, added)
  return next === log ? { ...log, after } : next
}

/** A log's lines as the pane draws them, one per row, for the clipboard. */
export function logText(lines: readonly LogLine[]): string {
  return lines.map(l => (l.kind === 'prompt' ? `› ${l.text}` : l.kind === 'result' || l.kind === 'error' ? `  ${l.text}` : l.text)).join('\n')
}

/**
 * `log` with `added` at its end, keeping the last `max` lines. A window scrolled up stays on the same lines: its
 * top moves back by the lines dropped.
 */
export function appendLines(log: Log, added: readonly LogLine[], max = LINES_MAX): Log {
  if (added.length === 0) return log
  const all = [...log.lines, ...added]
  const dropped = Math.max(0, all.length - max)
  return {
    ...log,
    lines: dropped ? all.slice(dropped) : all,
    top: Math.max(0, log.top - dropped),
    truncated: log.truncated || dropped > 0,
  }
}

/** The last line a window of `rows` may start at. */
const lastTop = (n: number, rows: number) => Math.max(0, n - rows)

/** `log` with its window of `rows` moved `by` lines (negative: up). It follows the end again once it reaches it. */
export function scrollLog(log: Log, by: number, rows: number): Log {
  const last = lastTop(log.lines.length, rows)
  const now = log.follow ? last : Math.min(Math.max(log.top, 0), last)
  const top = Math.min(Math.max(now + by, 0), last)
  return { ...log, top, follow: top >= last }
}

/** The `rows` lines a window shows: the last ones while following, else those from `top`. */
export function windowLines<T>(lines: readonly T[], follow: boolean, top: number, rows: number): T[] {
  const last = lastTop(lines.length, rows)
  const start = follow ? last : Math.min(Math.max(top, 0), last)
  return lines.slice(start, start + rows)
}

/** How long ago `then` was, as people say it: `just now`, `42s ago`, `3m ago`, `2h ago`. */
export function ago(then: number, now: number): string {
  const s = Math.max(0, Math.floor((now - then) / 1000))
  if (s < 5) return 'just now'
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  return `${Math.floor(s / 3600)}h ago`
}

/** An agent counts as old once it ended this long ago: it moves under `── older ──`. */
export const OLD_MS = 30 * 60_000

export type Section = 'recent' | 'earlier' | 'others' | 'older'

/** An agent listed under the one that started it, `depth` levels down (1: its child). */
export type Kid = { agent: Agent; depth: number }

/** One entry of the Agents list: an agent, or a squad's group (its members, and those that need you). */
export type Item =
  | { kind: 'agent'; agent: Agent; section: Section; kids: Kid[] }
  | { kind: 'group'; id: string; type: string; members: Agent[]; needing: Agent[]; section: Section }

const isLiveAt = (a: Agent, now: number) => {
  const st = shownStatus(a, now)
  return st === 'running' || st === 'waiting'
}

/** Whether `a` passes `filter` at `now`: needs you is waiting, or failed this turn. */
export function passes(a: Agent, filter: Filter, g: Graph, now: number): boolean {
  const st = shownStatus(a, now)
  switch (filter) {
    case 'all':
      return true
    case 'running':
      return st === 'running'
    case 'needs':
      return st === 'waiting' || (st === 'failed' && a.turn === g.turn)
    case 'failed':
      return st === 'failed'
    case 'done':
      return st === 'done'
  }
}

/** How many agents each filter keeps. */
export function filterCounts(g: Graph, now: number): Record<Filter, number> {
  const all = [...g.agents.filter(a => a.id !== MAIN), ...g.others]
  const n = (f: Filter) => all.filter(a => passes(a, f, g, now)).length
  return { all: all.length, running: n('running'), needs: n('needs'), failed: n('failed'), done: n('done') }
}

/**
 * The Agents list, in three sections: `recent`, this turn's agents and any still running or waiting (running first,
 * newest first, then the finished by when they ended); `earlier`, the finished agents of earlier turns; `older`,
 * those that ended over OLD_MS ago. Main is not listed: its log is the conversation itself. With the filter `all`,
 * `squadMin` or more siblings of one type under one parent are one group, in the section of its newest member;
 * another filter lists the agents that pass it, ungrouped.
 */
export function listItems(g: Graph, now: number, filter: Filter, squadMin: number): Item[] {
  const listed = g.agents.filter(a => a.id !== MAIN && passes(a, filter, g, now))
  const ids = new Set(listed.map(a => a.id))
  // An agent whose parent is listed too goes under it, not on a row of its own.
  const all = listed.filter(a => a.parent === null || a.parent === MAIN || !ids.has(a.parent))
  const kidsOf = (id: string, depth: number): Kid[] =>
    listed
      .filter(a => a.parent === id)
      .sort((x, y) => x.started - y.started)
      .flatMap(a => [{ agent: a, depth }, ...kidsOf(a.id, depth + 1)])
  // This turn: running, or ended since the prompt started it; a run found late, long over, is not this turn's.
  const sectionOf = (a: Agent): Section =>
    isLiveAt(a, now) || (a.ended !== null && a.ended >= g.turnStarted) ? 'recent' : a.ended !== null && now - a.ended > OLD_MS ? 'older' : 'earlier'
  const key = (a: Agent) => (isLiveAt(a, now) ? [0, -a.started] : [1, -(a.ended ?? a.started)])
  const order = (x: Agent, y: Agent) => {
    const [p, q] = [key(x), key(y)]
    return p[0]! - q[0]! || p[1]! - q[1]!
  }
  const groups = new Map<string, Agent[]>()
  if (filter === 'all') {
    for (const a of all) {
      const id = `${a.parent}:${a.type}`
      groups.set(id, [...(groups.get(id) ?? []), a])
    }
    for (const [id, ms] of groups) if (ms.length < squadMin) groups.delete(id)
  }
  const grouped = new Set([...groups.values()].flat().map(a => a.id))
  type Ranked = { item: Item; lead: Agent }
  const ranked: Ranked[] = all.filter(a => !grouped.has(a.id)).map(a => ({ item: { kind: 'agent', agent: a, section: sectionOf(a), kids: kidsOf(a.id, 1) }, lead: a }))
  for (const [id, ms] of groups) {
    const members = ms.slice().sort((x, y) => x.started - y.started)
    const lead = members.slice().sort(order)[0]!
    const needing = members.filter(a => passes(a, 'needs', g, now))
    ranked.push({ item: { kind: 'group', id, type: lead.type, members, needing, section: sectionOf(lead) }, lead })
  }
  // The runs other sessions started, after this session's own: live first, then newest.
  for (const a of g.others.filter(x => passes(x, filter, g, now))) ranked.push({ item: { kind: 'agent', agent: a, section: 'others', kids: [] }, lead: a })
  const rank: Record<Section, number> = { recent: 0, earlier: 1, others: 2, older: 3 }
  return ranked.sort((x, y) => rank[x.item.section] - rank[y.item.section] || order(x.lead, y.lead)).map(r => r.item)
}

// Navigation: where a log was opened from, and where Back goes. Pure, on the View.
type Nav = Pick<View, 'tab' | 'agentId' | 'trail' | 'origin'>

/**
 * `v` with agent `id`'s log open, opened from the chart, the list, or another log (a link to an agent it started):
 * from a log, Back returns to that log; else to the tab it was opened from.
 */
export function openFrom<V extends Nav>(v: V, id: string, from: 'chart' | 'agents' | 'log'): V {
  if (from === 'log' && v.agentId !== null) return { ...v, agentId: id, trail: [...v.trail, v.agentId] }
  return { ...v, agentId: id, trail: [], origin: from === 'log' ? v.origin : from }
}

/** `v` one step back: the last log in the trail, or the tab the first log was opened from. */
export function goBack<V extends Nav>(v: V): V {
  const last = v.trail.at(-1)
  if (last !== undefined) return { ...v, agentId: last, trail: v.trail.slice(0, -1) }
  return { ...v, agentId: null, tab: v.origin }
}

/** What Back says: `← ` and the agent type of the log it returns to, else `← Chart` or `← Agents`. */
export function backLabel(v: Nav, g: Graph): string {
  const last = v.trail.at(-1)
  if (last !== undefined) return `← ${printable(findAgent(g, last)?.type ?? 'back')}`
  return v.origin === 'chart' ? '← Chart' : '← Agents'
}
