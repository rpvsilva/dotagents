import assert from 'node:assert/strict'
import { test } from 'node:test'

import { emptyGraph, type FlowEvent, type Graph, MAIN, reduce } from './flow.ts'
import { ENTER_MS, isMoving, LEAVE_MS, phaseOf, schedule } from './lifecycle.ts'

const T = 1_800_000_000_000
const LINGER = 30_000
const run = (evs: FlowEvent[], g: Graph = emptyGraph()) => evs.reduce(reduce, g)
const spawn = (id: string, at: number, parent = MAIN): FlowEvent => ({ kind: 'spawn', at, id, parent, type: 'Explore', desc: id, model: null, parentModel: null, toolUseId: null })
const end = (id: string, at: number, reason = 'answer'): FlowEvent => ({ kind: 'turnEnd', at, agent: id, reason, outputTokens: null })
const phase = (g: Graph, id: string, now: number) => phaseOf(g.agents.find(a => a.id === id)!, now, schedule(g, LINGER))?.ph ?? null

test('a box enters, stays live while running, lingers, dims, shrinks to a chip, then goes', () => {
  const g = run([{ kind: 'turnStart', at: T, agent: MAIN }, spawn('a', T), end('a', T + 5000)])
  assert.equal(phase(g, 'a', T - 1), null)
  assert.equal(phase(g, 'a', T + ENTER_MS / 2), 'enter')
  assert.equal(phase(g, 'a', T + ENTER_MS), 'live')
  assert.equal(phase(g, 'a', T + 5000 + LINGER - 1), 'live')
  assert.equal(phase(g, 'a', T + 5000 + LINGER), 'dim')
  assert.equal(phase(g, 'a', T + 5000 + LINGER + LEAVE_MS / 2), 'chip')
  assert.equal(phase(g, 'a', T + 5000 + LINGER + LEAVE_MS), null)
})

test('a running agent never leaves; a failed one stays until the next prompt', () => {
  const g = run([{ kind: 'turnStart', at: T, agent: MAIN }, spawn('run', T), spawn('bad', T), end('bad', T + 1, 'error')])
  const later = T + 10 * LINGER
  assert.equal(phase(g, 'run', later), 'live')
  assert.equal(phase(g, 'bad', later), 'live')
  const next = reduce(g, { kind: 'turnStart', at: later, agent: MAIN })
  assert.equal(phase(next, 'bad', later), 'dim')
  assert.equal(phase(next, 'bad', later + LEAVE_MS), null)
  assert.equal(phase(next, 'run', later + LEAVE_MS), 'live')
})

test('a parent stays while a child is shown, and leaves only after it', () => {
  const g = run([spawn('p', T), spawn('k', T + 1, 'p'), end('p', T + 2), end('k', T + 20_000)])
  // p would leave at T+2+LINGER; its child is shown until T+20000+LINGER+LEAVE_MS.
  assert.equal(phase(g, 'p', T + 2 + LINGER + 10), 'live')
  const kidGone = T + 20_000 + LINGER + LEAVE_MS
  assert.equal(phase(g, 'k', kidGone), null)
  assert.equal(phase(g, 'p', kidGone), 'dim')
})

test('a new turn never cuts a linger short: finished boxes still leave deepest first, each after its linger', () => {
  const P = T + 10_000 // the next turn, long before either would leave on its own
  const g = run([{ kind: 'turnStart', at: T, agent: MAIN }, spawn('p', T), spawn('k', T + 1, 'p'), end('k', T + 2000), end('p', T + 2500), { kind: 'turnStart', at: P, agent: MAIN }])
  assert.equal(phase(g, 'k', P), 'live')
  assert.equal(phase(g, 'p', P), 'live')
  assert.equal(phase(g, 'k', T + 2000 + LINGER), 'dim')
  // p's own linger ends first, but it waits for its child to go.
  assert.equal(phase(g, 'p', T + 2500 + LINGER), 'live')
  assert.equal(phase(g, 'p', T + 2000 + LINGER + LEAVE_MS), 'dim')
})

test('a failed box leaves once the next turn has started and it has lingered', () => {
  const g = run([{ kind: 'turnStart', at: T, agent: MAIN }, spawn('bad', T), end('bad', T + 1, 'error'), { kind: 'turnStart', at: T + 5000, agent: MAIN }])
  assert.equal(phase(g, 'bad', T + 5000), 'live')
  assert.equal(phase(g, 'bad', T + 1 + LINGER), 'dim')
})

test('main never leaves', () => {
  const g = run([{ kind: 'turnStart', at: T, agent: MAIN }, end(MAIN, T + 1)])
  assert.equal(phase(g, MAIN, T + 10 * LINGER), 'live')
})

test('isMoving: something runs or will leave on its own; a chart of failed and idle boxes is still', () => {
  const idle = run([{ kind: 'turnStart', at: T, agent: MAIN }, end(MAIN, T)])
  assert.equal(isMoving(idle, T + 1, LINGER), false)
  const running = run([spawn('a', T)])
  assert.equal(isMoving(running, T + 1, LINGER), true)
  const lingering = run([spawn('a', T), end('a', T + 1)])
  assert.equal(isMoving(lingering, T + 2 * ENTER_MS, LINGER), true)
  assert.equal(isMoving(lingering, T + 1 + LINGER + LEAVE_MS, LINGER), false)
  const failed = run([{ kind: 'turnStart', at: T, agent: MAIN }, spawn('a', T), end('a', T + 1, 'error'), end(MAIN, T + 2)])
  assert.equal(isMoving(failed, T + 2 * ENTER_MS, LINGER), false)
})
