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
  // Each agent's cost in dollars, as $.agent.list() gives it.
  cost: Record<string, number>
  // What the mod wrote, by path, last write winning.
  files: Record<string, string>
}

function engine(on: any): World {
  const w: World = { opened: [], status: [], spawned: 0, clock: mock.clock(on, { now: NOW }), messages: {}, reads: {}, viewed: [], ctx: {}, cost: {}, files: {} }
  on('session.messages', (_: unknown, e: any) => {
    w.reads[e.agentId] = (w.reads[e.agentId] ?? 0) + 1
    const all = w.messages[e.agentId]
    if (!all) return { value: { deny: 'unknown agent' } }
    const from = e.after ? all.findIndex(m => m.uuid === e.after) + 1 : Math.max(0, all.length - (e.last ?? all.length))
    return { value: all.slice(from) }
  })
  on('agent.list', () => ({ value: [...new Set([...Object.keys(w.ctx), ...Object.keys(w.cost)])].map(id => ({ id, tokenCount: w.ctx[id]?.[0] ?? 0, contextWindowSize: w.ctx[id]?.[1] ?? 0, ...(id in w.cost ? { cost: { usd: w.cost[id] } } : {}) })) }))
  on('session.usage', () => ({ value: { context: { tokens: 61_000, window: 1_000_000 }, cost: { usd: 1.84 } } }))
  on('ui.view', (_: unknown, e: any) => {
    w.viewed.push(e)
    return { value: {} }
  })
  on('session.info', () => ({ value: { transcriptPath: '', scratchpadDir: '/scratch' } }))
  on('fs.write', (_: unknown, e: any) => {
    w.files[e.path] = e.text
    return { value: undefined }
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

test('the band is written for the status line script: a line per spinner frame naming the agent, the failed count and the ended bar; no status line', async ($, on) => {
  const w = engine(on)
  await start($)
  const band = () => w.files['/scratch/agent-flow/band.txt']
  expect(band() ?? '').toBe('')
  await spawn($, 'one')
  await spawn($, 'two')
  await w.clock.advance(1000)
  await endTurn($, 'agent-2', 'error')
  await w.clock.advance(500)
  const lines = band()!.trimEnd().split('\n').map(l => l.replace(/\x1b\[[0-9;]*m/g, ''))
  expect(lines.length).toBe(10)
  expect(lines[0]).toBe('⠋ one  ·  ✗ 1 failed   ▰▰▰▰▱▱▱▱ 1/2')
  expect(lines[1]).toBe('⠙ one  ·  ✗ 1 failed   ▰▰▰▰▱▱▱▱ 1/2')
  await endTurn($, 'agent-1')
  await w.clock.advance(500)
  expect(band()!.split('\n')[0]).toContain('✗ 1 failed')
  expect(w.status.filter(s => s !== undefined)).toEqual([])
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
  await press($, 'tab-agents')
  const out = await drawn($)
  expect(out).toContain('epic-implementer')
  expect(out).toMatch(/   ↳ . fork/)
})

test('a chart wider than the pane lists its running agents instead', async ($, on) => {
  const w = engine(on)
  await start($)
  for (const t of ['Explore', 'Plan', 'pr-reviewer', 'general-purpose']) await spawn($, `task ${t}`, t)
  await w.clock.advance(1000)
  const out = await drawn($, 'terminal', 60)
  expect(out).toContain('the chart needs')
  expect(out).toContain('pr-reviewer')
  expect(out).not.toContain('╭')
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
  expect(list).toContain('≡ Agents 1')
  expect(list).toContain('map the auth flow')
  expect(list).toMatch(/done 0:00 · 40s ago/)
  await press($, 'agent:agent-1')
  const out = await drawn($)
  expect(out).toContain('← Agents')
  expect(out).toContain('task: map the auth flow')
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
  expect(await drawn($)).toContain('x expand squad')
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
  expect(await drawn($)).toContain('21% ctx')
})

test("a squad's members show their context: the expanded squad under the chart, and the group opened in the Agents list", async ($, on) => {
  const w = engine(on)
  await start($)
  for (const n of ['a', 'b', 'c', 'd']) await spawn($, `task ${n}`, 'epic-implementer')
  w.ctx['agent-3'] = [60_000, 200_000]
  await w.clock.advance(1500)
  expect(await drawn($)).toContain('ctx avg 30%')
  await press($, 'expand')
  expect(await drawn($)).toMatch(/task c .* 30%/)
  await press($, 'tab-agents')
  await pressFirst($, 'group:')
  expect(await drawn($)).toMatch(/task c .*30% ctx/)
})

test("each agent shows what it cost: its box, main's the session's, its squad's total, its log; the list has no cost column", async ($, on) => {
  const w = engine(on)
  await start($)
  await spawn($, 'map the auth flow')
  for (const n of ['a', 'b', 'c', 'd']) await spawn($, `task ${n}`, 'epic-implementer')
  w.cost['agent-1'] = 0.123
  w.cost['agent-2'] = 0.5
  w.cost['agent-3'] = 0.25
  w.messages['agent-1'] = msgs('map the auth flow', 'rg login')
  await w.clock.advance(1500)
  const chartOut = await drawn($)
  expect(chartOut).toContain(' $0.12 ')
  expect(chartOut).toContain(' $1.84 ')
  expect(chartOut).toContain(' $0.75 ')
  await press($, 'tab-agents')
  const list = await drawn($)
  expect(list).not.toContain('$0.12')
  expect(list).not.toContain('$0.75')
  await pressFirst($, 'agent:agent-1')
  expect(await drawn($)).toMatch(/from main · .*· \$0\.12/)
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
  expect(await drawn($)).toContain('x expand squad')
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
  expect(await drawn($)).toContain('open its log →')
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

// Remote runs: a fake repo on a fake file system, as remote-agent leaves it.
function repoFs(on: any, files: Record<string, string>, dirs: Record<string, string[]>, ps: () => string) {
  const calls = { ps: 0, lists: 0, stats: 0 }
  on('session.cwd', () => ({ value: '/repo' }))
  on('session.id', () => ({ value: 'sess-1' }))
  on('fs.stat', (_: unknown, e: any) => {
    calls.stats++
    if (dirs[e.path]) return { value: { kind: 'dir', size: 0, mtimeMs: NOW, isLink: false } }
    const f = files[e.path]
    if (f === undefined) throw new Error(`ENOENT: ${e.path}`)
    return { value: { kind: 'file', size: f.length, mtimeMs: NOW + f.length, isLink: false } }
  })
  on('fs.read', (_: unknown, e: any) => {
    const f = files[e.path]
    if (f === undefined) throw new Error(`ENOENT: ${e.path}`)
    return { value: f }
  })
  on('fs.list', (_: unknown, e: any) => {
    calls.lists++
    const names = dirs[e.path]
    if (!names) throw new Error(`ENOENT: ${e.path}`)
    return { value: names.map(name => ({ name, kind: 'dir', size: 0, mtimeMs: 0, isLink: false })) }
  })
  on('process.run', (_: unknown, e: any) => {
    if (e.argv.includes('ps')) {
      calls.ps++
      return { value: { exitCode: 0, stdout: ps(), stderr: '' } }
    }
    return { value: { exitCode: 1, stdout: '', stderr: '' } }
  })
  return calls
}

const RUN = 'agent-20261117-114447-18521'
const RUNDIR = `/repo/.git/remote-agent/${RUN}`
const runFile = (over: Record<string, unknown>) =>
  JSON.stringify({ run: RUN, name: 'probe', started: NOW / 1000, pid: 4242, pid_start: 'Tue Nov 17 11:44:47 2026', state: 'running', claim_s: 1, send_s: 1, first_event_s: 2, caller: { session: 'sess-1' }, ...over })
const TRANSCRIPT = [
  { type: 'system', subtype: 'init', model: 'claude-opus-5-5' },
  { type: 'assistant', message: { content: [{ type: 'tool_use', id: 'c1', name: 'Bash', input: { command: 'ls skills' } }] } },
].map(e => `${JSON.stringify(e)}\n`).join('')

test('a remote run started by a subagent hangs under it in the chart, and opens to its transcript log', async ($, on) => {
  const w = engine(on)
  const files: Record<string, string> = { [`${RUNDIR}/run.json`]: runFile({}), [`${RUNDIR}/transcript.jsonl`]: TRANSCRIPT, [`${RUNDIR}/progress.log`]: 'remote-agent: claimed in 1s\n' }
  const calls = repoFs(on, files, { '/repo/.git': ['remote-agent'], '/repo/.git/remote-agent': [RUN] }, () => '4242 Tue Nov 17 11:44:47 2026\n')
  await start($)
  await spawn($, 'fix it', 'epic-implementer')
  await $.tool.call({ tool: 'Bash', command: `~/.agents/bin/remote-agent --name probe @b.md`, description: 'probe remotely', run_in_background: true, agentId: 'agent-1', tool_use_id: 'b1' })
  await w.clock.advance(3000)
  expect(await drawn($)).toContain('☁ probe')
  expect(calls.ps).toBeGreaterThan(0)
  await pressFirst($, `open:remote:${RUN}`)
  const log = await drawn($)
  expect(log).toContain('remote-agent: claimed in 1s')
  expect(log).toContain('▸ Bash ls skills')
  expect(log).toContain('caller: epic-implementer')
})

test("a remote run's box carries a context gauge from its transcript, once its model's window is known", async ($, on) => {
  const w = engine(on)
  const usage = { input_tokens: 2, cache_read_input_tokens: 40_000, cache_creation_input_tokens: 1_998, output_tokens: 0 }
  const tr = TRANSCRIPT + `${JSON.stringify({ type: 'assistant', message: { model: 'claude-opus-5-5', usage, content: [] } })}\n`
  repoFs(on, { [`${RUNDIR}/run.json`]: runFile({}), [`${RUNDIR}/transcript.jsonl`]: tr }, { '/repo/.git': ['remote-agent'], '/repo/.git/remote-agent': [RUN] }, () => '4242 Tue Nov 17 11:44:47 2026\n')
  // A window an earlier session learned, as $.store keeps it.
  on('store.get', (_: unknown, e: any) => ({ value: e.key === 'windows' ? { 'claude-opus-5-5': 1_000_000 } : undefined }))
  on('store.set', () => ({ value: undefined }))
  await start($)
  await $.tool.call({ tool: 'Bash', command: `~/.agents/bin/remote-agent --name probe @b.md`, description: 'probe remotely', run_in_background: true, agentId: 'main', tool_use_id: 'b1' })
  await w.clock.advance(3000)
  const chart = await drawn($)
  expect(chart).toContain('☁ probe')
  expect(chart).toContain('42k/1M')
})

test("another session's run is listed under other sessions, never on the chart; its log offers resume", async ($, on) => {
  const w = engine(on)
  const files = {
    [`${RUNDIR}/run.json`]: runFile({ caller: { session: 'someone-else' }, state: 'done', worktree: '/repo', ended: NOW / 1000 + 1 }),
    [`${RUNDIR}/session.jsonl`]: '{}\n',
  }
  repoFs(on, files, { '/repo/.git': ['remote-agent'], '/repo/.git/remote-agent': [RUN] }, () => '')
  const copied: string[] = []
  on('ui.toast', () => ({ value: undefined }))
  on('ui.copy', (_: unknown, e: any) => {
    copied.push(e.text)
    return { value: { isCopied: true } }
  })
  await start($)
  await w.clock.advance(3000)
  expect(await drawn($)).not.toContain('probe')
  await press($, 'tab-agents')
  const list = await drawn($)
  expect(list).toContain('other sessions')
  expect(list).toContain('☁ probe')
  await pressFirst($, `agent:remote:${RUN}`)
  const log = await drawn($)
  expect(log).toContain('the sandbox wrote this session')
  expect(log).toContain('copy path')
  await press($, 'resume')
  await press($, 'path')
  expect(copied).toEqual([`remote-agent resume ${RUN} /repo`, `${RUNDIR}/transcript.jsonl`])
})

test("a finished run's log ends with one end line, whole, at a narrow pane's width", async ($, on) => {
  const w = engine(on)
  const files = {
    [`${RUNDIR}/run.json`]: runFile({ state: 'done', exit_code: 0, ended: NOW / 1000 + 107 }),
    [`${RUNDIR}/transcript.jsonl`]: TRANSCRIPT + JSON.stringify({ type: 'result', subtype: 'success', is_error: false, duration_ms: 59_000, total_cost_usd: 0.24, num_turns: 12 }) + '\n',
    [`${RUNDIR}/result.json`]: JSON.stringify({ total_cost_usd: 0.24, num_turns: 12, changed: ['a.py', 'b.py'], model: 'claude-opus-5-5' }),
  }
  repoFs(on, files, { '/repo/.git': ['remote-agent'], '/repo/.git/remote-agent': [RUN] }, () => '')
  await start($)
  await w.clock.advance(2000)
  await press($, 'tab-agents')
  await pressFirst($, `agent:remote:${RUN}`)
  const wide = await drawn($)
  expect(wide).toContain('12 turns')
  const out = await drawn($, 'terminal', 70)
  expect(out).toContain('$0.24 · 12 turns · 2 files')
  expect(out).not.toContain('in 59s')
})

test('quiet (not drawn, nothing live), the run folders are read every 15 s', async ($, on) => {
  const w = engine(on)
  const calls = repoFs(on, { [`${RUNDIR}/run.json`]: runFile({ caller: { session: 'someone-else' }, state: 'done' }) }, { '/repo/.git': ['remote-agent'], '/repo/.git/remote-agent': [RUN] }, () => '')
  await start($)
  await w.clock.advance(1000)
  const lists = calls.lists
  await w.clock.advance(12_000)
  expect(calls.lists).toBe(lists)
  await w.clock.advance(4000)
  expect(calls.lists).toBeGreaterThan(lists)
})

test('finished runs of other sessions past the listed 20 are read once, then never looked at again', async ($, on) => {
  const w = engine(on)
  const ids = Array.from({ length: 30 }, (_, i) => `agent-20261117-1000${String(i).padStart(2, '0')}-1`)
  const files: Record<string, string> = {}
  for (const [i, id] of ids.entries()) files[`/repo/.git/remote-agent/${id}/run.json`] = runFile({ run: id, name: `old-${i}`, state: 'done', caller: { session: 'someone-else' }, started: NOW / 1000 - 1000 + i, ended: NOW / 1000 - 900 + i })
  const calls = repoFs(on, files, { '/repo/.git': ['remote-agent'], '/repo/.git/remote-agent': ids }, () => '')
  await start($)
  await w.clock.advance(1000)
  await press($, 'tab-agents')
  const stats = calls.stats
  // Drawn, so read every 1.5 s: only the folder listings and the open pane, no folder's files again.
  await press($, 'tab-agents')
  await w.clock.advance(1500)
  await press($, 'tab-agents')
  await w.clock.advance(1500)
  expect(calls.stats - stats).toBeLessThan(5)
  expect(await drawn($)).toContain('old-29')
  expect(await drawn($)).not.toContain('old-9 ')
})

test('a run that ends is notified once; runs already over when the mod loads are not', async ($, on) => {
  const w = engine(on)
  const OLD = 'agent-20261117-100000-1'
  const files: Record<string, string> = {
    [`${RUNDIR}/run.json`]: runFile({ caller: { session: 'someone-else' } }),
    [`/repo/.git/remote-agent/${OLD}/run.json`]: runFile({ run: OLD, name: 'old', state: 'done' }),
  }
  repoFs(on, files, { '/repo/.git': ['remote-agent'], '/repo/.git/remote-agent': [RUN, OLD] }, () => '4242 Tue Nov 17 11:44:47 2026\n')
  const kept: Record<string, unknown> = {}
  on('store.get', (_: unknown, e: any) => ({ value: kept[e.key] }))
  on('store.set', (_: unknown, e: any) => {
    kept[e.key] = e.value
    return { value: undefined }
  })
  const notes: string[] = []
  on('ui.notify', (_: unknown, e: any) => {
    notes.push(e.text)
    return { value: { isSent: true } }
  })
  await start($)
  await w.clock.advance(3000)
  expect(notes).toEqual([])
  files[`${RUNDIR}/run.json`] = runFile({ caller: { session: 'someone-else' }, state: 'failed', exit_code: 2, ended: NOW / 1000 + 5 })
  await w.clock.advance(16_000)
  expect(notes).toEqual(['probe failed (exit 2)'])
  await w.clock.advance(16_000)
  expect(notes).toEqual(['probe failed (exit 2)'])
  expect(kept['notified:/repo/.git']).toEqual([`/repo/.git/remote-agent/${OLD}`, RUNDIR])
})
test('a graph stored before remote runs (graph-2, no launches) is declined: the remote run still shows', async ($, on) => {
  const w = engine(on)
  // What the version before remote runs stored, kept through the reload.
  const old = { agents: [{ id: 'main', parent: null, type: 'main', desc: '', model: null, status: 'done', why: null, started: 0, ended: null, steps: 0, tokens: 0, step: null, toolUseId: null, turn: 1, checks: {}, ctx: null, window: null }], turn: 1, turnStarted: NOW, lastTurn: null }
  // It stays what the store holds until the mod writes over it.
  let isWritten = false
  on('state.get', (_: unknown, e: any, next: any) => (e.key !== 'graph' || isWritten ? next(e) : { value: { value: { shape: 'graph-2', value: old }, version: 0 } }))
  on('state.set', (_: unknown, e: any, next: any) => {
    if (e.key === 'graph') isWritten = true
    return next(e)
  })
  const files: Record<string, string> = { [`${RUNDIR}/run.json`]: runFile({}), [`${RUNDIR}/transcript.jsonl`]: TRANSCRIPT }
  repoFs(on, files, { '/repo/.git': ['remote-agent'], '/repo/.git/remote-agent': [RUN] }, () => '4242 Tue Nov 17 11:44:47 2026\n')
  await start($)
  await $.tool.call({ tool: 'Bash', command: '~/.agents/bin/remote-agent --name probe @b.md', run_in_background: true, tool_use_id: 'b1' })
  await w.clock.advance(3000)
  await press($, 'tab-agents')
  expect(await drawn($)).toContain('☁ probe')
})
