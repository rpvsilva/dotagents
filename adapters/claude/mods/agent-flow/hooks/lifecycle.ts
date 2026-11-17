// When an agent's box appears and when it leaves. Pure: the graph and the clock in, a phase out; tested by
// lifecycle.unit.ts under node --test.
//
// Rules: a running agent stays; a finished one lingers, then dims, shrinks to a chip and goes; a failed one stays
// until the next turn starts and it has lingered; a box with a child still shown stays until that child has gone;
// main never leaves.

import { type Agent, type Graph, MAIN } from './flow.ts'

/** The connector grows, then the border draws, then the content fills: over ENTER_MS. */
export const ENTER_MS = 600
/** Dim with a dotted border for half of LEAVE_MS, then a one-line chip for the other half, then gone. */
export const LEAVE_MS = 1000

export type Phase = { ph: 'enter' | 'live' | 'dim' | 'chip'; p: number }

/** Memoized leave times for one graph and clock: leaveAt(id) and goneAt(id). */
export function schedule(g: Graph, lingerMs: number): { leaveAt: (a: Agent) => number; goneAt: (a: Agent) => number } {
  const kids = new Map<string, Agent[]>()
  for (const a of g.agents) if (a.parent) kids.set(a.parent, [...(kids.get(a.parent) ?? []), a])
  const memo = new Map<string, number>()
  const leaveAt = (a: Agent): number => {
    const hit = memo.get(a.id)
    if (hit !== undefined) return hit
    memo.set(a.id, Infinity) // a cycle in bad data stays rather than loops
    const kidsGone = Math.max(0, ...(kids.get(a.id) ?? []).map(goneAt))
    let at: number
    if (a.id === MAIN || a.status === 'running' || a.ended === null) at = Infinity
    else {
      // A new turn never cuts a linger short: background agents' notifications start turns too. It only lets a
      // failed box of an earlier turn go, once it has lingered like any other.
      const natural = Math.max(a.ended + lingerMs, kidsGone)
      at = a.status !== 'failed' ? natural : a.turn < g.turn ? Math.max(natural, g.turnStarted) : Infinity
    }
    memo.set(a.id, at)
    return at
  }
  const goneAt = (a: Agent) => leaveAt(a) + LEAVE_MS
  return { leaveAt, goneAt }
}

/** The agent's phase at `now`; null when it is not shown (gone, or before it started). */
export function phaseOf(a: Agent, now: number, sched: ReturnType<typeof schedule>): Phase | null {
  if (a.id === MAIN) return { ph: 'live', p: 1 }
  if (now < a.started) return null
  const leave = sched.leaveAt(a)
  if (now >= leave + LEAVE_MS) return null
  if (now >= leave) {
    const p = (now - leave) / LEAVE_MS
    return { ph: p < 0.5 ? 'dim' : 'chip', p }
  }
  if (now - a.started < ENTER_MS) return { ph: 'enter', p: (now - a.started) / ENTER_MS }
  return { ph: 'live', p: 1 }
}

/** Whether anything will change on its own after `now`: an entry or a leave under way, or one still to come. */
export function isMoving(g: Graph, now: number, lingerMs: number): boolean {
  const sched = schedule(g, lingerMs)
  return g.agents.some(a => {
    if (a.id === MAIN) return a.status === 'running'
    if (a.status === 'running') return true
    const ph = phaseOf(a, now, sched)
    return ph !== null && (ph.ph !== 'live' || Number.isFinite(sched.leaveAt(a)))
  })
}
