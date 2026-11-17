// The agent graph: what the session's agents are and how each stands, built from Claude Code's events by one pure
// reducer. No `claude-code` import at run time: tested by flow.unit.ts under node --test.

import type { Agent, Graph, Launch, Remote, Status } from './state.d.ts'
import { callerOf, type RemoteRun, type Task } from './remote.ts'

export type { Agent, Graph, Launch, Remote, Status }

export type FlowEvent =
  | { kind: 'spawn'; at: number; id: string; parent: string; type: string; desc: string; model: string | null; parentModel: string | null; toolUseId: string | null }
  | { kind: 'tool'; at: number; agent: string; tool: string; summary: string }
  | { kind: 'toolEnd'; at: number; agent: string }
  | { kind: 'check'; at: number; agent: string; toolUseId: string }
  | { kind: 'checkEnd'; at: number; agent: string; toolUseId: string }
  | { kind: 'turnStart'; at: number; agent: string }
  | { kind: 'turnEnd'; at: number; agent: string; reason: string; outputTokens: number | null }
  | { kind: 'spawnFailed'; at: number; toolUseId: string; why: string }
  /** A Bash call that runs remote-agent began, and ended (with the run ids its output named). */
  | { kind: 'launch'; at: number; toolUseId: string; agent: string; name: string | null; desc: string | null; background: boolean }
  | { kind: 'launchEnd'; at: number; toolUseId: string; runIds: string[] }
  /** A remote run as its folder reads now, and a subagent it ran. */
  | { kind: 'remote'; at: number; run: RemoteRun }
  | { kind: 'remoteTask'; at: number; runId: string; folder: string; task: Task; model: string | null }
  /** A context reading: `used` tokens of a `window` (both from the engine). */
  | { kind: 'ctx'; at: number; agent: string; used: number; window: number }

/** Agents kept at most: past it the oldest finished ones go (main never). */
export const MAX_AGENTS = 100
/** A permission check pending this long shows the agent as waiting for you; quicker ones are the classifier's. */
export const WAIT_MS = 1000

export const MAIN = 'main'

export function emptyGraph(): Graph {
  return {
    agents: [{ id: MAIN, parent: null, type: 'main', desc: '', model: null, status: 'done', why: null, started: 0, ended: null, steps: 0, tokens: 0, step: null, toolUseId: null, turn: 0, checks: {}, ctx: null, window: null, remote: null }],
    turn: 0,
    turnStarted: 0,
    lastTurn: null,
    launches: [],
  }
}

const isFinished = (a: Agent) => a.status !== 'running'

/** Tokens as people say them: `950`, `42k`, `1.2M`, `1M`. */
export function fmtTokens(n: number): string {
  if (n >= 1_000_000) {
    const m = n / 1_000_000
    return `${Number.isInteger(Math.round(m * 10) / 10) ? Math.round(m) : (Math.round(m * 10) / 10).toFixed(1)}M`
  }
  if (n >= 1000) return `${Math.round(n / 1000)}k`
  return String(Math.round(n))
}

/** The share of its window an agent has used, 0 to 1; null without a reading. */
export function ctxShare(a: Pick<Agent, 'ctx' | 'window'>): number | null {
  return a.ctx === null || !a.window ? null : Math.min(1, Math.max(0, a.ctx / a.window))
}

/** The theme colour of a context share: under half `success`, to 80% `warning`, past it `error`. */
export const ctxColor = (share: number) => (share >= 0.8 ? 'error' : share >= 0.5 ? 'warning' : 'success')

/** A gauge `w` cells wide: ▰ for the share used (at least one once any is), ▱ for the rest. */
export function gauge(share: number, w: number): string {
  const f = Math.min(w, Math.max(share > 0 ? 1 : 0, Math.round(share * w)))
  return '▰'.repeat(f) + '▱'.repeat(w - f)
}

/** Applies one event; returns the same graph when nothing changed, so a caller can skip a write. */
export function reduce(g: Graph, ev: FlowEvent): Graph {
  const at = ev.at
  const patch = (id: string, fn: (a: Agent) => Agent): Graph => {
    const i = g.agents.findIndex(a => a.id === id)
    const was = g.agents[i]
    if (!was) return g
    const next = fn(was)
    if (next === was) return g
    const agents = g.agents.slice()
    agents[i] = next
    return { ...g, agents }
  }
  switch (ev.kind) {
    case 'spawn': {
      if (g.agents.some(a => a.id === ev.id)) return g
      const parent = g.agents.some(a => a.id === ev.parent) ? ev.parent : MAIN
      const agent: Agent = {
        id: ev.id, parent, type: ev.type, desc: ev.desc, model: ev.model, status: 'running', why: null, started: at, ended: null,
        steps: 0, tokens: 0, step: null, toolUseId: ev.toolUseId, turn: g.turn, checks: {}, ctx: null, window: null, remote: null,
      }
      const withMain = ev.parentModel && parent === MAIN ? patch(MAIN, m => (m.model ? m : { ...m, model: ev.parentModel })) : g
      return cap({ ...withMain, agents: [...withMain.agents, agent] })
    }
    case 'tool':
      return patch(ev.agent, a => ({ ...a, steps: a.steps + 1, step: ev.summary, ...(a.id !== MAIN && isFinished(a) ? reopen(a) : {}) }))
    case 'toolEnd':
      return patch(ev.agent, a => (a.step === null ? a : { ...a, step: null }))
    case 'check':
      return patch(ev.agent, a => ({ ...a, checks: { ...a.checks, [ev.toolUseId]: at } }))
    case 'checkEnd':
      return patch(ev.agent, a => {
        if (!(ev.toolUseId in a.checks)) return a
        const checks = { ...a.checks }
        delete checks[ev.toolUseId]
        return { ...a, checks }
      })
    case 'turnStart': {
      if (ev.agent !== MAIN) return patch(ev.agent, a => (isFinished(a) ? { ...a, ...reopen(a) } : a))
      const prev = g.agents.filter(a => a.id !== MAIN && a.turn === g.turn)
      const lastTurn = g.turn === 0 ? null : { done: prev.filter(a => a.status === 'done').length, failed: prev.filter(a => a.status === 'failed').length }
      return { ...patch(MAIN, a => ({ ...a, status: 'running', why: null, started: a.started || at, ended: null })), turn: g.turn + 1, turnStarted: at, lastTurn }
    }
    case 'turnEnd': {
      const failed = ev.reason === 'aborted' ? 'stopped' : ev.reason === 'refusal' || ev.reason === 'error' ? ev.reason : null
      return patch(ev.agent, a => ({
        ...a,
        status: ev.agent === MAIN ? 'done' : failed ? 'failed' : 'done',
        why: ev.agent === MAIN ? null : failed,
        ended: at,
        step: null,
        checks: {},
        tokens: a.tokens + (ev.outputTokens ?? 0),
      }))
    }
    case 'ctx':
      return patch(ev.agent, a => (a.ctx === ev.used && a.window === ev.window ? a : { ...a, ctx: ev.used, window: ev.window }))
    case 'launch': {
      if (g.launches.some(l => l.toolUseId === ev.toolUseId)) return g
      const launch: Launch = { toolUseId: ev.toolUseId, agent: ev.agent, at, ended: null, name: ev.name, desc: ev.desc, background: ev.background, runIds: [] }
      return { ...g, launches: [...g.launches, launch].slice(-MAX_LAUNCHES) }
    }
    case 'launchEnd': {
      const i = g.launches.findIndex(l => l.toolUseId === ev.toolUseId)
      const was = g.launches[i]
      if (!was) return g
      const launch = { ...was, ended: at, runIds: [...new Set([...was.runIds, ...ev.runIds])] }
      const launches = g.launches.slice()
      launches[i] = launch
      // A run its output named hangs under the call's agent from now on.
      const agents = g.agents.map(a =>
        a.remote && a.remote.taskId === null && launch.runIds.includes(a.remote.runId) && (a.parent !== launch.agent || !a.remote.linked) && g.agents.some(p => p.id === launch.agent)
          ? { ...a, parent: launch.agent, desc: launch.desc ?? a.desc, remote: { ...a.remote, linked: true } }
          : a,
      )
      return { ...g, launches, agents }
    }
    case 'remote': {
      const r = ev.run
      const id = `remote:${r.runId}`
      const caller = callerOf(g.launches, { id: r.runId, name: r.name, started: r.started })
      const linked = caller !== null && g.agents.some(a => a.id === caller.agent)
      const was = g.agents.find(a => a.id === id)
      const remote: Remote = {
        runId: r.runId, folder: r.folder, taskId: null, toolUseId: null, phase: r.phase, exit: r.exit, cost: r.cost, turns: r.turns, files: r.files, linked,
      }
      const fields = {
        parent: linked ? caller!.agent : MAIN, desc: (linked ? caller!.desc : null) ?? was?.desc ?? r.name ?? 'remote run', model: r.model, status: r.status,
        why: r.why, ended: r.ended, steps: r.steps, step: r.step, remote,
      }
      if (!was) {
        const agent: Agent = {
          id, type: `☁ ${r.name ?? 'remote-agent'}`, started: r.started, tokens: 0, toolUseId: null, turn: g.turn, checks: {}, ctx: null, window: null, ...fields,
        }
        return cap({ ...g, agents: [...g.agents, agent] })
      }
      return patch(id, a => (same(a, fields) ? a : { ...a, ...fields }))
    }
    case 'remoteTask': {
      const parent = `remote:${ev.runId}`
      if (!g.agents.some(a => a.id === parent)) return g
      const t = ev.task
      const id = `${parent}:${t.id}`
      const remote: Remote = {
        runId: ev.runId, folder: ev.folder, taskId: t.id, toolUseId: t.toolUseId, phase: null, exit: null, cost: null, turns: null, files: null, linked: true,
      }
      const fields = {
        parent, type: printable(t.type), desc: printable(t.desc), model: ev.model, status: t.status, why: t.status === 'failed' ? 'failed' : null,
        ended: t.ended, steps: t.steps, step: t.step, remote,
      }
      const was = g.agents.find(a => a.id === id)
      if (!was) {
        const agent: Agent = { id, started: at, tokens: 0, toolUseId: null, turn: g.turn, checks: {}, ctx: null, window: null, ...fields }
        return cap({ ...g, agents: [...g.agents, agent] })
      }
      return patch(id, a => (same(a, fields) ? a : { ...a, ...fields }))
    }
    case 'spawnFailed': {
      const a = g.agents.find(x => x.toolUseId === ev.toolUseId)
      if (!a || isFinished(a)) return g
      return patch(a.id, x => ({ ...x, status: 'failed', why: ev.why, ended: at, step: null, checks: {} }))
    }
  }
}

/** The most remote-agent calls the graph keeps; the oldest go. */
export const MAX_LAUNCHES = 50

/** Whether `a` already has every field of `fields`, compared by value. */
function same(a: Agent, fields: Partial<Agent>): boolean {
  return Object.entries(fields).every(([k, v]) => JSON.stringify(a[k as keyof Agent]) === JSON.stringify(v))
}

const reopen = (_a: Agent): Partial<Agent> => ({ status: 'running', why: null, ended: null })

/** Past MAX_AGENTS, drops the oldest finished agents that are no one's parent. */
function cap(g: Graph): Graph {
  if (g.agents.length <= MAX_AGENTS) return g
  const parents = new Set(g.agents.map(a => a.parent))
  const drop = g.agents
    .filter(a => a.id !== MAIN && isFinished(a) && !parents.has(a.id))
    .sort((x, y) => (x.ended ?? 0) - (y.ended ?? 0))
    .slice(0, g.agents.length - MAX_AGENTS)
    .map(a => a.id)
  const gone = new Set(drop)
  return { ...g, agents: g.agents.filter(a => !gone.has(a.id)) }
}

/** Since when the agent waits for a person: its oldest check pending at least WAIT_MS; null when it does not. */
export function waitingSince(a: Agent, now: number): number | null {
  const oldest = Math.min(...Object.values(a.checks))
  return Number.isFinite(oldest) && now - oldest >= WAIT_MS ? oldest : null
}

/** What the agent shows: its own status, or `waiting` while a check is pending for a person. */
export function shownStatus(a: Agent, now: number): Status | 'waiting' {
  return a.status === 'running' && waitingSince(a, now) !== null ? 'waiting' : a.status
}

/** A tool call as one line: the tool and the input field that says what it does, control characters dropped. */
export function summarize(tool: string, input: Record<string, unknown>): string {
  const pick = (k: string) => (typeof input[k] === 'string' ? (input[k] as string) : null)
  let what: string | null
  if (tool === 'Agent') what = [pick('subagent_type'), pick('description')].filter(Boolean).join(' · ')
  else what = pick('command') ?? pick('file_path') ?? pick('pattern') ?? pick('url') ?? pick('path') ?? pick('description') ?? pick('query')
  const line = what ? `${tool} ${what}` : tool
  return printable(line.split('\n')[0] ?? '').slice(0, 120)
}

/** Text from outside with control characters (escapes included) removed, so it can never move the terminal. */
export function printable(s: string): string {
  return s.replace(/[\u0000-\u001f\u007f-\u009f]/g, '')
}

/** A model id as people say it: `claude-opus-5-5` → `opus 5.5`; an id it cannot read, as given. */
export function modelLabel(id: string | null): string {
  if (!id) return ''
  const m = /(opus|sonnet|haiku|fable)[-_ ]?(\d+)(?:[-_.](\d+))?/i.exec(id)
  return m?.[1] && m[2] ? `${m[1].toLowerCase()} ${m[2]}${m[3] ? `.${m[3]}` : ''}` : printable(id).slice(0, 24)
}

/** The color of a model's family: Claude's own for opus, the theme's blue for sonnet; teal and violet for haiku, fable. */
export function modelColor(id: string | null): string {
  const fam = /(opus|sonnet|haiku|fable)/i.exec(id ?? '')?.[1]?.toLowerCase()
  switch (fam) {
    case 'opus':
      return 'claude'
    case 'sonnet':
      return 'suggestion'
    case 'haiku':
      return '#5cc9a7'
    case 'fable':
      return 'merged'
    default:
      return 'text'
  }
}

/** The status line: `● 3 running · ◐ 1 needs you · ✗ 1 failed`; undefined when no agent runs, waits or failed this turn. */
export function statusText(g: Graph, now: number): string | undefined {
  const mine = g.agents.filter(a => a.id !== MAIN && (a.status === 'running' || a.turn === g.turn))
  const waiting = mine.filter(a => shownStatus(a, now) === 'waiting').length
  const running = mine.filter(a => a.status === 'running').length - waiting
  const failed = mine.filter(a => a.status === 'failed').length
  const parts = [running && `● ${running} running`, waiting && `◐ ${waiting} needs you`, failed && `✗ ${failed} failed`].filter(Boolean)
  return parts.length ? parts.join(' · ') : undefined
}
