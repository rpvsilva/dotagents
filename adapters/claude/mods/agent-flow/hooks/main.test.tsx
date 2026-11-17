import { expect, mock, test } from 'claude-code/testing'

const NOW = 2_000_000_000_000 // ms
const SURFACES = ['terminal', 'desktop'] as const
const pane = (surface: (typeof SURFACES)[number], bodyColumns = 140) => ({
  plugin: 'agent-flow',
  component: 'Pane',
  requestId: 'agent-flow',
  surface,
  props: { title: 'Agent flow', isFocused: true, bodyColumns, placement: 'dock', scroll: { offset: 0, bodyRows: 40 }, view: {} },
})

// The engine beneath the mod: what it answers and what the mod asked of it.
type World = { opened: string[]; status: (string | undefined)[]; spawned: number; clock: ReturnType<typeof mock.clock> }

function engine(on: any): World {
  const w: World = { opened: [], status: [], spawned: 0, clock: mock.clock(on, { now: NOW }) }
  on('command.register', () => ({ value: { command: 'agent-flow' } }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('ui.open', (_: unknown, e: any) => {
    w.opened.push(e.id)
    return { value: { isPlaced: true } }
  })
  on('ui.status', (_: unknown, e: any) => {
    w.status.push(e.text)
    return { value: undefined }
  })
  on('agent.spawn', (_: unknown, e: any) => {
    w.spawned++
    return { model: e.model ?? 'claude-sonnet-5-5', agentId: `agent-${w.spawned}` }
  })
  on('tool.call', (_: unknown, e: any) => ({ result: { stdout: `ran ${e.command ?? e.tool}`, stderr: '' } }))
  on('tool.check', () => ({ decision: 'allow' }))
  on('turn.start', (_: unknown, e: any) => ({ turnId: e.turnId }))
  on('turn.complete', (_: unknown, e: any) => ({ text: e.text ?? '' }))
  return w
}

async function start($: any) {
  await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
  await $.turn.start({ text: 'go', turnId: 'turn-1' })
}

async function spawn($: any, description: string, subagentType = 'Explore', over: Record<string, unknown> = {}) {
  return $.agent.spawn({
    tool_use_id: `tu-${description}`, prompt: description, description, subagentType, provider: { plugin: 'engine', tier: 'core' },
    parentModel: 'claude-opus-5-5', ...over,
  })
}

async function endTurn($: any, agentId: string | undefined, reason = 'answer') {
  await $.turn.complete({
    answer: '', durationMs: 1000, isAborted: reason === 'aborted', turnId: `turn-${agentId ?? 'main'}`, reason, text: '', category: null, explanation: null,
    usage: { output_tokens: 10 }, ...(agentId ? { agentId } : {}),
  })
}

// Every text the pane draws on `surface`, joined line by line.
async function drawn($: any, surface: (typeof SURFACES)[number] = 'terminal', columns = 140): Promise<string> {
  const ui = await $.ui.mount(pane(surface, columns))
  const texts = (await ui.findAll({})).filter((el: any) => el.type === 'Text' || el.type === 'Button').map((el: any) => el.text)
  await ui.unmount()
  return texts.join('\n')
}

test('/agent-flow opens the agent-flow pane, focused', async ($, on) => {
  const w = engine(on)
  await start($)
  const { text } = await $.command.run({ command: 'agent-flow' })
  expect(text).toBe('Agent flow pane opened.')
  expect(w.opened).toEqual(['agent-flow'])
})

test('a spawned agent appears under main with its type, task and model, on every surface', async ($, on) => {
  const w = engine(on)
  await start($)
  await spawn($, 'map the auth flow')
  // In its first moments a box is entering: the connector grows before the box draws.
  expect(await drawn($)).not.toContain('Explore')
  await w.clock.advance(1000)
  for (const surface of SURFACES) {
    const out = await drawn($, surface)
    expect(out).toContain('main')
    expect(out).toContain('Explore')
    expect(out).toContain('map the auth flow')
    expect(out).toContain('sonnet 5.5')
  }
})

test('the mod passes every call on unchanged: a spawn, a tool call and a permission check get the engine answer', async ($, on) => {
  engine(on)
  await start($)
  const spawned = await spawn($, 'task one')
  expect(spawned.agentId).toBe('agent-1')
  expect(spawned.model).toBe('claude-sonnet-5-5')
  const called = await $.tool.call({ tool: 'Bash', command: 'npm test', agentId: 'agent-1' })
  expect(called.result).toEqual({ stdout: 'ran npm test', stderr: '' })
})

test("an agent's tool call shows as its current step, and its finished turn as done", async ($, on) => {
  const w = engine(on)
  await start($)
  await spawn($, 'run the tests')
  await w.clock.advance(1000)
  await $.tool.call({ tool: 'Bash', command: 'npm test', agentId: 'agent-1' })
  await endTurn($, 'agent-1')
  await w.clock.advance(1000)
  const out = await drawn($)
  expect(out).toContain('run the tests')
  expect(out).toMatch(/✓ 1 steps/)
})

test('a failed agent shows why, and the needs-you strip names it', async ($, on) => {
  const w = engine(on)
  await start($)
  await spawn($, 'draft the changelog', 'general-purpose')
  await w.clock.advance(1000)
  await endTurn($, 'agent-1', 'error')
  const out = await drawn($)
  expect(out).toContain('✗ error')
  expect(out).toContain('needs you')
  expect(out).toContain('draft the changelog')
})

test('the status line counts running and failed agents, and is set only when it changes', async ($, on) => {
  const w = engine(on)
  await start($)
  await spawn($, 'one')
  await spawn($, 'two')
  await w.clock.advance(1000)
  await endTurn($, 'agent-2', 'error')
  await w.clock.advance(500)
  expect(w.status.filter(s => s !== undefined)).toContain('● 1 running · ✗ 1 failed')
  const set = w.status.length
  await w.clock.advance(1000)
  expect(w.status.length).toBe(set)
})

test('four agents of one type draw as one squad box, and x expands it', async ($, on) => {
  const w = engine(on)
  await start($)
  for (const n of ['a', 'b', 'c', 'd']) await spawn($, `task ${n}`, 'epic-implementer')
  await w.clock.advance(1000)
  expect(await drawn($)).toContain('epic-implementer ×4')
  const ui = await $.ui.mount(pane('terminal'))
  await ui.press({ key: 'expand' })
  await ui.unmount()
  const out = await drawn($)
  expect(out).toContain('▾ epic-implementer ×4')
  expect(out).toContain('task d')
})

test('an agent started by a subagent hangs under that subagent, not under main', async ($, on) => {
  const w = engine(on)
  await start($)
  await spawn($, 'fix the flaky test', 'epic-implementer')
  await spawn($, 'try bash 3.2', 'fork', { parentAgentId: 'agent-1' })
  await w.clock.advance(1000)
  const ui = await $.ui.mount(pane('terminal'))
  await ui.press({ key: 'view' })
  await ui.unmount()
  const out = await drawn($)
  expect(out).toMatch(/└─ .*epic-implementer/)
  expect(out).toMatch(/ {3}└─ .*fork/)
})

test('t switches to the tree view and back', async ($, on) => {
  const w = engine(on)
  await start($)
  await spawn($, 'map the auth flow')
  await w.clock.advance(1000)
  const ui = await $.ui.mount(pane('terminal'))
  await ui.press({ key: 'view' })
  await ui.unmount()
  expect(await drawn($)).toMatch(/└─ .*Explore/)
  const again = await $.ui.mount(pane('terminal'))
  await again.press({ key: 'view' })
  await again.unmount()
  expect(await drawn($)).not.toMatch(/└─ .*Explore/)
})

test('a chart wider than the pane falls back to the tree', async ($, on) => {
  const w = engine(on)
  await start($)
  for (const t of ['Explore', 'Plan', 'pr-reviewer', 'general-purpose']) await spawn($, `task ${t}`, t)
  await w.clock.advance(1000)
  const out = await drawn($, 'terminal', 60)
  expect(out).toMatch(/├─ .*Explore/)
  expect(out).toContain('tree: the chart needs')
})

test('motion off: no spinner frames', { options: { motion: 'off' } } as any, async ($: any, on: any) => {
  const w = engine(on)
  await start($)
  await spawn($, 'stay still')
  await w.clock.advance(1000)
  expect(await drawn($)).not.toMatch(/[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]/)
})
