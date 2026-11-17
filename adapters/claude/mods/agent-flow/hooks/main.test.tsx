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
type World = {
  opened: string[]; status: (string | undefined)[]; spawned: number; clock: ReturnType<typeof mock.clock>
  // Each agent's messages, as the session would give them; `reads` counts the reads per agent.
  messages: Record<string, any[]>; reads: Record<string, number>; viewed: unknown[]
  // Each agent's context reading, [tokens, window], as $.agent.list() gives it.
  ctx: Record<string, [number, number]>
}

function engine(on: any): World {
  const w: World = { opened: [], status: [], spawned: 0, clock: mock.clock(on, { now: NOW }), messages: {}, reads: {}, viewed: [], ctx: {} }
  on('session.messages', (_: unknown, e: any) => {
    w.reads[e.agentId] = (w.reads[e.agentId] ?? 0) + 1
    const all = w.messages[e.agentId]
    if (!all) return { value: { deny: 'unknown agent' } }
    const from = e.after ? all.findIndex(m => m.uuid === e.after) + 1 : Math.max(0, all.length - (e.last ?? all.length))
    return { value: all.slice(from) }
  })
  on('agent.list', () => ({ value: Object.entries(w.ctx).map(([id, [tokenCount, contextWindowSize]]) => ({ id, tokenCount, contextWindowSize })) }))
  on('session.usage', () => ({ value: { context: { tokens: 61_000, window: 1_000_000 } } }))
  on('ui.view', (_: unknown, e: any) => {
    w.viewed.push(e)
    return { value: {} }
  })
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

// The Agents tab: history and logs.
const msgs = (prompt: string, ...steps: string[]) => [
  { role: 'user', text: prompt, toolUses: [], uuid: 'm0' },
  ...steps.flatMap((cmd, i) => [
    { role: 'assistant', text: '', toolUses: [{ tool_use_id: `t${i}`, tool: 'Bash', input: { command: cmd } }], uuid: `m${2 * i + 1}` },
    { role: 'user', text: '', toolUses: [], toolResults: [{ tool_use_id: `t${i}`, text: `out of ${cmd}` }], uuid: `m${2 * i + 2}` },
  ]),
]

async function press($: any, key: string) {
  const ui = await $.ui.mount(pane('terminal'))
  await ui.press({ key })
  await ui.unmount()
}

test('a finished agent stays on the Agents tab after its box left the chart, and opens to its log', async ($, on) => {
  const w = engine(on)
  await start($)
  await spawn($, 'map the auth flow')
  w.messages['agent-1'] = msgs('map the auth flow', 'rg login')
  await endTurn($, 'agent-1')
  await w.clock.advance(40_000) // past the linger: gone from the chart
  expect(await drawn($)).not.toContain('map the auth flow')
  await press($, 'tab-agents')
  const list = await drawn($)
  expect(list).toContain('Agents (1)')
  expect(list).toContain('map the auth flow')
  expect(list).toMatch(/done 40s ago/)
  await press($, 'agent:agent-1')
  const out = await drawn($)
  expect(out).toContain('← Agents')
  expect(out).toContain('› map the auth flow')
  expect(out).toContain('▸ Bash rg login')
  expect(out).toContain('⎿ out of rg login')
  await press($, 'back')
  expect(await drawn($)).not.toContain('← Agents')
})

test("a running agent's open log follows its new messages; a finished one is not read again", async ($, on) => {
  const w = engine(on)
  await start($)
  await spawn($, 'run the tests')
  w.messages['agent-1'] = msgs('run the tests', 'npm test')
  await press($, 'tab-agents')
  await press($, 'agent:agent-1')
  w.messages['agent-1'] = msgs('run the tests', 'npm test', 'npm run lint')
  await drawn($)
  await w.clock.advance(1500)
  expect(await drawn($)).toContain('▸ Bash npm run lint')
  await endTurn($, 'agent-1')
  await w.clock.advance(6000)
  const reads = w.reads['agent-1']
  await drawn($)
  await w.clock.advance(5000)
  expect(w.reads['agent-1']).toBe(reads)
})

test('v opens the agent transcript in the main view; a log the session denies says why', async ($, on) => {
  const w = engine(on)
  await start($)
  await spawn($, 'one')
  await press($, 'tab-agents')
  await press($, 'agent:agent-1')
  expect(await drawn($)).toContain('the session gives no messages for this agent: unknown agent')
  await press($, 'transcript')
  expect(w.viewed).toEqual([{ agentId: 'agent-1' }])
})

test('g goes back to the chart', async ($, on) => {
  const w = engine(on)
  await start($)
  await spawn($, 'map the auth flow')
  await w.clock.advance(1000)
  await press($, 'tab-agents')
  await press($, 'tab-chart')
  expect(await drawn($)).toContain('sonnet 5.5')
  expect(await drawn($)).toContain('t tree/chart')
})

test("each box shows its context used against its model's window; main its session's; the Agents list too", async ($, on) => {
  const w = engine(on)
  await start($)
  await spawn($, 'map the auth flow')
  w.ctx['agent-1'] = [42_000, 200_000]
  await w.clock.advance(1500)
  const out = await drawn($)
  expect(out).toContain('42k/200k')
  expect(out).toContain('61k/1M')
  await press($, 'tab-agents')
  expect(await drawn($)).toContain('ctx 42k/200k (21%)')
})

// Presses the first Button whose key starts with `prefix` (keys of chart pieces and links carry their position).
async function pressFirst($: any, prefix: string) {
  const ui = await $.ui.mount(pane('terminal'))
  const all = await ui.findAll({})
  const el = all.find((x: any) => x.type === 'Button' && String(x.key ?? x.props?.key ?? '').startsWith(prefix))
  if (!el) throw new Error(`no button ${prefix}: ${all.filter((x: any) => x.type === 'Button').map((x: any) => x.key ?? x.props?.key).join(', ')}`)
  await ui.press({ key: el.key ?? el.props.key })
  await ui.unmount()
}

test('a click on a box in the chart opens its log; Back returns to the chart', async ($, on) => {
  const w = engine(on)
  await start($)
  await spawn($, 'map the auth flow')
  w.messages['agent-1'] = msgs('map the auth flow', 'rg login')
  await w.clock.advance(1000)
  await pressFirst($, 'open:agent-1:')
  const out = await drawn($)
  expect(out).toContain('← Chart')
  expect(out).toContain('▸ Bash rg login')
  await press($, 'back')
  expect(await drawn($)).toContain('t tree/chart')
})

test("a log links to the agents it started; Back goes to the parent's log, then to the list", async ($, on) => {
  const w = engine(on)
  await start($)
  await spawn($, 'fix the flaky test', 'epic-implementer')
  await spawn($, 'try bash 3.2', 'fork', { parentAgentId: 'agent-1' })
  w.messages['agent-1'] = [
    { role: 'user', text: 'fix the flaky test', toolUses: [], uuid: 'p0' },
    { role: 'assistant', text: '', toolUses: [{ tool_use_id: 'x', tool: 'Agent', input: { subagent_type: 'fork', description: 'try bash 3.2' }, agentId: 'agent-2' }], uuid: 'p1' },
  ]
  w.messages['agent-2'] = msgs('try bash 3.2', '/bin/bash --version')
  await press($, 'tab-agents')
  await press($, 'agent:agent-1')
  expect(await drawn($)).toContain('↗ open its log')
  await pressFirst($, 'link:agent-2:')
  const child = await drawn($)
  expect(child).toContain('← epic-implementer')
  expect(child).toContain('▸ Bash /bin/bash --version')
  expect(child).toContain('from epic-implementer')
  await press($, 'back')
  expect(await drawn($)).toContain('← Agents')
  await press($, 'back')
  expect(await drawn($)).toContain('Needs you')
})

test('filters keep what they say; four siblings are one group that opens to its members', async ($, on) => {
  const w = engine(on)
  await start($)
  for (const n of ['a', 'b', 'c', 'd']) await spawn($, `task ${n}`, 'epic-implementer')
  await spawn($, 'lone explorer')
  await endTurn($, 'agent-2', 'error')
  await w.clock.advance(1000)
  await press($, 'tab-agents')
  const list = await drawn($)
  expect(list).toContain('▸ epic-implementer ×4')
  expect(list).toContain('task b') // failed this turn: shown under its closed group
  expect(list).not.toContain('task c')
  await press($, 'group:main:epic-implementer')
  expect(await drawn($)).toContain('task c')
  await press($, 'filter-failed')
  const failed = await drawn($)
  expect(failed).toContain('task b')
  expect(failed).not.toContain('lone explorer')
})

test('a finished log ends with how it ended, and c copies it', async ($, on) => {
  const w = engine(on)
  const copied: string[] = []
  on('ui.toast', () => ({ value: undefined }))
  on('ui.copy', (_: unknown, e: any) => {
    copied.push(e.text)
    return { value: { isCopied: true } }
  })
  await start($)
  await spawn($, 'run the tests')
  w.messages['agent-1'] = msgs('run the tests', 'npm test')
  await w.clock.advance(3000)
  await endTurn($, 'agent-1')
  await press($, 'tab-agents')
  await press($, 'agent:agent-1')
  expect(await drawn($)).toMatch(/✓ done .* in 0:03 · 1 steps|✓ done/)
  await press($, 'copy')
  expect(copied[0]).toContain('› run the tests')
})
