import assert from 'node:assert/strict'
import { test } from 'node:test'

import { costSum, ctxColor, fmtUsd, ctxShare, emptyGraph, fmtTokens, type FlowEvent, gauge, type Graph, ink, MAIN, MAX_AGENTS, modelColor, modelLabel, printable, reduce, shownStatus, band, summarize, WAIT_MS } from './flow.ts'

const T = 1_800_000_000_000
const run = (evs: FlowEvent[], g: Graph = emptyGraph()) => evs.reduce(reduce, g)
const spawn = (id: string, at: number, over: Partial<Extract<FlowEvent, { kind: 'spawn' }>> = {}): FlowEvent => ({
  kind: 'spawn', at, id, parent: MAIN, type: 'Explore', desc: `task ${id}`, model: 'claude-sonnet-5-5', parentModel: 'claude-opus-5-5', toolUseId: `tu-${id}`, ...over,
})
const agent = (g: Graph, id: string) => g.agents.find(a => a.id === id)!

test('a new prompt starts main; a spawn adds a running agent under main, with its model, and main learns its own', () => {
  const g = run([{ kind: 'turnStart', at: T, agent: MAIN }, spawn('a1', T + 10)])
  assert.equal(agent(g, MAIN).status, 'running')
  assert.equal(agent(g, MAIN).model, 'claude-opus-5-5')
  assert.equal(g.turn, 1)
  const a = agent(g, 'a1')
  assert.deepEqual([a.parent, a.status, a.model, a.turn, a.started], [MAIN, 'running', 'claude-sonnet-5-5', 1, T + 10])
})

test('a spawn under an agent hangs under it; an unknown parent falls back to main; a second spawn of one id is ignored', () => {
  const g = run([spawn('a1', T), spawn('a2', T + 1, { parent: 'a1' }), spawn('a3', T + 2, { parent: 'nobody' }), spawn('a1', T + 3, { desc: 'again' })])
  assert.equal(agent(g, 'a2').parent, 'a1')
  assert.equal(agent(g, 'a3').parent, MAIN)
  assert.equal(agent(g, 'a1').desc, 'task a1')
  assert.equal(g.agents.length, 4)
})

test('tool calls count steps and show the current one, cleared when it ends', () => {
  let g = run([spawn('a1', T), { kind: 'tool', at: T + 1, agent: 'a1', tool: 'Bash', summary: 'Bash npm test' }])
  assert.equal(agent(g, 'a1').step, 'Bash npm test')
  assert.equal(agent(g, 'a1').steps, 1)
  g = reduce(g, { kind: 'toolEnd', at: T + 2, agent: 'a1' })
  assert.equal(agent(g, 'a1').step, null)
})

test('an agent waits for you only once a check has been pending WAIT_MS; a quick classifier answer never shows', () => {
  let g = run([spawn('a1', T), { kind: 'check', at: T + 100, agent: 'a1', toolUseId: 't1' }])
  assert.equal(shownStatus(agent(g, 'a1'), T + 100 + WAIT_MS - 1), 'running')
  assert.equal(shownStatus(agent(g, 'a1'), T + 100 + WAIT_MS), 'waiting')
  g = reduce(g, { kind: 'checkEnd', at: T + 2000, agent: 'a1', toolUseId: 't1' })
  assert.equal(shownStatus(agent(g, 'a1'), T + 5000), 'running')
})

test('an agent ends with its turn: answer is done; aborted, refusal and error fail, with why', () => {
  const ends = ['answer', 'aborted', 'refusal', 'error'].map((reason, i) =>
    run([spawn(`a${i}`, T), { kind: 'turnEnd', at: T + 500, agent: `a${i}`, reason, outputTokens: 42 }]),
  )
  assert.deepEqual(ends.map((g, i) => [agent(g, `a${i}`).status, agent(g, `a${i}`).why]), [
    ['done', null], ['failed', 'stopped'], ['failed', 'refusal'], ['failed', 'error'],
  ])
  assert.equal(agent(ends[0]!, 'a0').ended, T + 500)
  assert.equal(agent(ends[0]!, 'a0').tokens, 42)
})

test('a finished agent that is sent a message runs again', () => {
  let g = run([spawn('a1', T), { kind: 'turnEnd', at: T + 1, agent: 'a1', reason: 'answer', outputTokens: null }])
  g = reduce(g, { kind: 'turnStart', at: T + 2, agent: 'a1' })
  assert.equal(agent(g, 'a1').status, 'running')
  assert.equal(agent(g, 'a1').ended, null)
})

test('a failed Agent call fails the agent it started', () => {
  const g = run([spawn('a1', T), { kind: 'spawnFailed', at: T + 1, toolUseId: 'tu-a1', why: 'error' }])
  assert.deepEqual([agent(g, 'a1').status, agent(g, 'a1').why], ['failed', 'error'])
})

test('a new prompt counts the last turn and bumps the turn; main ends idle with its own turn', () => {
  let g = run([
    { kind: 'turnStart', at: T, agent: MAIN }, spawn('a1', T + 1), spawn('a2', T + 2),
    { kind: 'turnEnd', at: T + 3, agent: 'a1', reason: 'answer', outputTokens: null },
    { kind: 'turnEnd', at: T + 4, agent: 'a2', reason: 'error', outputTokens: null },
    { kind: 'turnEnd', at: T + 5, agent: MAIN, reason: 'answer', outputTokens: null },
  ])
  assert.equal(agent(g, MAIN).status, 'done')
  g = reduce(g, { kind: 'turnStart', at: T + 10, agent: MAIN })
  assert.deepEqual(g.lastTurn, { done: 1, failed: 1 })
  assert.deepEqual([g.turn, g.turnStarted], [2, T + 10])
})

test('past MAX_AGENTS the oldest finished agents go, never main, a running one or a parent', () => {
  let g = run([spawn('keep', T), spawn('kid', T + 1, { parent: 'keep' }), { kind: 'turnEnd', at: T + 2, agent: 'keep', reason: 'answer', outputTokens: null }])
  for (let i = 0; i < MAX_AGENTS + 5; i++) {
    g = reduce(g, spawn(`x${i}`, T + 10 + i))
    g = reduce(g, { kind: 'turnEnd', at: T + 10 + i, agent: `x${i}`, reason: 'answer', outputTokens: null })
  }
  g = reduce(g, spawn('live', T + 1000))
  assert.ok(g.agents.length <= MAX_AGENTS)
  for (const id of [MAIN, 'keep', 'kid', 'live']) assert.ok(g.agents.some(a => a.id === id), id)
  assert.ok(!g.agents.some(a => a.id === 'x0'))
})

test('an event that changes nothing returns the same graph, so a caller skips the write', () => {
  const g = run([spawn('a1', T)])
  assert.equal(reduce(g, { kind: 'toolEnd', at: T, agent: 'a1' }), g)
  assert.equal(reduce(g, { kind: 'checkEnd', at: T, agent: 'a1', toolUseId: 'none' }), g)
  assert.equal(reduce(g, { kind: 'tool', at: T, agent: 'ghost', tool: 'Bash', summary: 'Bash ls' }), g)
})

test('summarize names the tool and what it acts on, on one line, without control characters', () => {
  assert.equal(summarize('Bash', { command: 'npm test\nmore' }), 'Bash npm test')
  assert.equal(summarize('Read', { file_path: '/a/b.ts' }), 'Read /a/b.ts')
  assert.equal(summarize('Agent', { subagent_type: 'Explore', description: 'map auth' }), 'Agent Explore · map auth')
  assert.equal(summarize('TodoWrite', {}), 'TodoWrite')
  assert.equal(summarize('Bash', { command: 'echo \u001b[31mred' }), 'Bash echo [31mred')
  assert.equal(printable('a\u0007b\u009bc'), 'abc')
})

test('models read as people say them, coloured by family, Bedrock ids too', () => {
  assert.equal(modelLabel('claude-opus-5-5'), 'opus 5.5')
  assert.equal(modelLabel('us.anthropic.claude-sonnet-5-5-v1:0'), 'sonnet 5.5')
  assert.equal(modelLabel('claude-haiku-5-5'), 'haiku 5.5')
  assert.equal(modelLabel('some-other-model'), 'some-other-model')
  assert.equal(modelLabel(null), '')
  assert.deepEqual(['claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5', 'claude-fable-5-2', 'x'].map(modelColor), ['claude', 'suggestion', '#5cc9a7', 'merged', 'text'])
})

test('the band counts running, waiting and failed agents of this turn and how many ended; null when there is nothing to say', () => {
  assert.equal(band(emptyGraph(), T), null)
  const g = run([
    { kind: 'turnStart', at: T, agent: MAIN }, spawn('a1', T), spawn('a2', T), spawn('a3', T),
    { kind: 'check', at: T, agent: 'a2', toolUseId: 't' },
    { kind: 'turnEnd', at: T + 1, agent: 'a3', reason: 'error', outputTokens: null },
  ])
  assert.deepEqual(band(g, T + WAIT_MS), {
    names: [{ id: 'a1', name: 'task a1', model: 'claude-sonnet-5-5' }], running: 1, waiting: 1, remote: 0, failed: 1, ended: 1, total: 3,
  })
})

test('the band names up to 4 running agents by task, one line each cut at 32 columns; past 4 it counts them', () => {
  const one = run([{ kind: 'turnStart', at: T, agent: MAIN }, spawn('a1', T, { desc: 'review the\nlogin   flow, its tests and the docs' })])
  assert.equal(band(one, T)!.names[0]!.name, 'review the login flow, its test…')
  const ids = ['a1', 'a2', 'a3', 'a4', 'a5']
  const four = run([{ kind: 'turnStart', at: T, agent: MAIN }, ...ids.slice(0, 4).map(id => spawn(id, T))])
  assert.deepEqual(band(four, T)!.names.map(n => n.name), ['task a1', 'task a2', 'task a3', 'task a4'])
  const five = run([
    { kind: 'turnStart', at: T, agent: MAIN }, ...ids.map(id => spawn(id, T)), spawn('a6', T), spawn('a7', T),
    { kind: 'turnEnd', at: T + 1, agent: 'a6', reason: 'answer', outputTokens: null },
    { kind: 'turnEnd', at: T + 1, agent: 'a7', reason: 'answer', outputTokens: null },
  ])
  assert.deepEqual(band(five, T + 1), { names: [], running: 5, waiting: 0, remote: 0, failed: 0, ended: 2, total: 7 })
})

test('a context reading sets used and window; the same reading again changes nothing; an unknown agent is ignored', () => {
  const g = run([spawn('a1', T), { kind: 'ctx', at: T + 1, agent: 'a1', used: 42_000, window: 200_000 }])
  assert.deepEqual([agent(g, 'a1').ctx, agent(g, 'a1').window], [42_000, 200_000])
  assert.equal(ctxShare(agent(g, 'a1')), 0.21)
  assert.equal(reduce(g, { kind: 'ctx', at: T + 2, agent: 'a1', used: 42_000, window: 200_000 }), g)
  assert.equal(reduce(g, { kind: 'ctx', at: T + 2, agent: 'ghost', used: 1, window: 2 }), g)
  assert.equal(ctxShare(agent(run([spawn('a2', T)]), 'a2')), null)
})

test('context reads as people say it: tokens, a gauge, and a colour by how full', () => {
  assert.deepEqual([950, 42_000, 199_600, 1_000_000, 1_200_000].map(fmtTokens), ['950', '42k', '200k', '1M', '1.2M'])
  assert.deepEqual([0, 0.01, 0.5, 1].map(s => gauge(s, 6)), ['▱▱▱▱▱▱', '▰▱▱▱▱▱', '▰▰▰▱▱▱', '▰▰▰▰▰▰'])
  assert.deepEqual([0.2, 0.5, 0.79, 0.8].map(ctxColor), ['success', 'warning', 'warning', 'error'])
})

test('ink draws the plan palette when fixed, and the theme keys otherwise', () => {
  assert.equal(ink(modelColor('claude-opus-5-5'), true), '#e0835f')
  assert.equal(ink(modelColor('claude-sonnet-5-5'), true), '#7fa8f2')
  assert.equal(ink(modelColor('claude-haiku-5-5'), true), '#5cc9a7')
  assert.equal(ink('warning', true), '#e8b34f')
  assert.equal(ink(ctxColor(0.9), true), '#f0727a')
  assert.equal(ink('warning', false), 'warning')
  assert.equal(ink(modelColor(null), true), '#d8d9df')
  assert.equal(ink('permission', true), 'permission')
  assert.equal(ink(undefined, true), undefined)
})

test('fmtUsd and costSum: cents, under a cent, from a hundred; null when no agent has a cost', () => {
  assert.equal(fmtUsd(0.123), '$0.12')
  assert.equal(fmtUsd(0.001), '<$0.01')
  assert.equal(fmtUsd(0), '$0.00')
  assert.equal(fmtUsd(123.4), '$123')
  assert.equal(costSum([{ cost: 0.5, remote: null }, { cost: null, remote: null }, { cost: 0.25, remote: null }]), 0.75)
  assert.equal(costSum([{ cost: null, remote: null }]), null)
})
