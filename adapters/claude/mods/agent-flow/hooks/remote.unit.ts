import assert from 'node:assert/strict'
import { test } from 'node:test'

import { emptyGraph, type FlowEvent, type Graph, MAIN, reduce } from './flow.ts'
import { chart, type ChartOpts, newTween } from './layout.ts'
import {
  applyLiveness,
  applyStaleness,
  callerOf,
  commonOf,
  gitdirOf,
  gitDirs,
  isLaunch,
  launchName,
  parseResult,
  parseRun,
  remoteLog,
  remoteRun,
  resolvePath,
  type Run,
  runEvents,
  runIdsIn,
  scanTranscript,
  STALE_MS,
  upwards,
} from './remote.ts'
import type { Launch } from './state.d.ts'

const T = 1_800_000_000_000
const ID = 'agent-20261117-114447-18521'
const FOLDER = `/repo/.git/remote-agent/${ID}`
const runJson = (over: Record<string, unknown> = {}) =>
  JSON.stringify({ run: ID, name: 'probe', started: T / 1000, pid: 4242, pid_start: 'Tue Nov 17 11:44:47 2026', state: 'running', claim_s: 1, send_s: 1, first_event_s: 11, caller: { session: 'sess-1', claude_pid: 99 }, ...over })
const run = (over: Record<string, unknown> = {}, changed = T) => parseRun({ id: ID, folder: FOLDER, runJson: runJson(over), changed })
const launch = (over: Partial<Launch> = {}): Launch => ({ toolUseId: 'tu-1', agent: MAIN, at: T - 500, ended: null, name: null, desc: 'run the probe', background: false, runIds: [], ...over })

// A transcript like a real run's: init, a tool call and its result, a subagent with its own call, the end.
const jsonl = (...evs: unknown[]) => evs.map(e => `${JSON.stringify(e)}\n`).join('')
const TRANSCRIPT = jsonl(
  { type: 'system', subtype: 'init', model: 'claude-opus-5-5' },
  { type: 'assistant', message: { content: [{ type: 'tool_use', id: 'c1', name: 'Bash', input: { command: 'ls skills | wc -l' } }] } },
  { type: 'user', message: { content: [{ type: 'tool_result', tool_use_id: 'c1', content: '15' }] } },
  { type: 'assistant', message: { content: [{ type: 'tool_use', id: 'agent1', name: 'Agent', input: { description: 'Find settings' } }] } },
  { type: 'system', subtype: 'task_started', task_id: 'tk1', tool_use_id: 'agent1', description: 'Find skills with settings.json', subagent_type: 'Explore' },
  { type: 'assistant', parent_tool_use_id: 'agent1', message: { content: [{ type: 'tool_use', id: 'k1', name: 'Bash', input: { command: 'ls -d skills/*/' } }] } },
  { type: 'user', parent_tool_use_id: 'agent1', message: { content: [{ type: 'tool_result', tool_use_id: 'k1', content: 'skills/a/' }] } },
  { type: 'system', subtype: 'task_progress', task_id: 'tk1', usage: { tool_uses: 1 } },
  { type: 'system', subtype: 'task_updated', task_id: 'tk1', patch: { status: 'completed', end_time: T + 20_000 } },
  { type: 'assistant', message: { content: [{ type: 'text', text: '14 skills.' }] } },
)

test("a run reads from run.json: its caller session, times in ms, and its setup phase until the first agent event", () => {
  const r = run()
  assert.deepEqual([r.session, r.started, r.phase, r.state, r.name], ['sess-1', T, null, 'running', 'probe'])
  assert.equal(run({ first_event_s: null }).phase, 'booting')
  assert.equal(run({ send_s: null, first_event_s: null }).phase, 'sending')
  assert.equal(run({ claim_s: null, send_s: null, first_event_s: null }).phase, 'claiming')
  assert.equal(run({ caller: null }).session, null)
  assert.equal(run({ caller: { session: 'bad session; rm' } }).session, null)
  assert.equal(parseRun({ id: ID, folder: FOLDER, runJson: 'not json', changed: 0 }).state, 'unknown')
})

test('a live run whose remote-agent is gone, or whose pid was reused, is stopped; without ps, one quiet for 5 minutes', () => {
  const r = run()
  assert.equal(applyLiveness([r], '4242 Tue Nov 17 11:44:47 2026\n')[0]!.state, 'running')
  assert.equal(applyLiveness([r], '4242 Wed Nov 18 09:00:00 2026\n')[0]!.state, 'stopped')
  assert.equal(applyLiveness([r], '')[0]!.state, 'stopped')
  assert.equal(applyLiveness([r], null)[0]!.state, 'running')
  assert.equal(applyStaleness([r], T + STALE_MS - 1)[0]!.state, 'running')
  assert.equal(applyStaleness([r], T + STALE_MS + 1)[0]!.state, 'stopped')
})

test('the run folders are found with file reads: a .git folder, or a worktree .git file through commondir', () => {
  assert.equal(resolvePath('/a/b', '../c/./d'), '/a/c/d')
  assert.equal(resolvePath('/a/b', '/x/y'), '/x/y')
  assert.deepEqual(upwards('/a/b'), ['/a/b', '/a', '/'])
  const gitdir = gitdirOf('gitdir: /repo/.git/worktrees/scylla\n', '/work/scylla')
  assert.equal(gitdir, '/repo/.git/worktrees/scylla')
  assert.equal(commonOf(gitdir!, '../..\n'), '/repo/.git')
  assert.equal(commonOf('/repo/.git', null), '/repo/.git')
  assert.equal(gitdirOf('not a gitdir', '/w'), null)
  assert.deepEqual(gitDirs('/repo/.git', ['b', 'a']), ['/repo/.git', '/repo/.git/worktrees/a', '/repo/.git/worktrees/b'])
})

test('a launch is a Bash call to remote-agent, not one of its subcommands; its --name and the run ids it printed', () => {
  for (const c of ['~/.agents/bin/remote-agent "map auth"', 'scripts/remote-agent.sh --name probe @brief.md', 'cd x && remote-agent - < b.md', 'remote-agent --agent pr-reviewer "review"'])
    assert.ok(isLaunch(c), c)
  for (const c of ['remote-agent check', '~/.agents/bin/remote-agent result agent-1', 'remote-agent --help', 'cat scripts/remote-agent-pack.sh', 'echo remote-agents', 'ls remote/'])
    assert.ok(!isLaunch(c), c)
  assert.deepEqual(['--name probe x', "--name='two words' x", '--name=p2 x', 'none'].map(launchName), ['probe', 'two words', 'p2', null])
  assert.deepEqual(runIdsIn(`remote-agent: transcript: /r/.git/remote-agent/${ID}/transcript.jsonl\nrun /r/.git/remote-agent/${ID}/transcript.jsonl`), [ID])
})

test('a run links to the call that printed its id, else the one with its --name, else the earliest in time with no run yet', () => {
  const r = { id: ID, name: 'probe', started: T }
  const a = launch({ toolUseId: 'a', agent: 'x', at: T - 900 })
  const b = launch({ toolUseId: 'b', agent: 'y', at: T - 800, name: 'probe' })
  const c = launch({ toolUseId: 'c', agent: 'z', at: T - 700, runIds: [ID] })
  assert.equal(callerOf([a, b, c], r)?.toolUseId, 'c')
  assert.equal(callerOf([a, b], r)?.toolUseId, 'b')
  assert.equal(callerOf([a, launch({ toolUseId: 'd', at: T - 100 })], { ...r, name: null })?.toolUseId, 'a')
  assert.equal(callerOf([launch({ toolUseId: 'e', at: T - 900, runIds: ['other'] }), a], { ...r, name: null })?.toolUseId, 'a')
  // Too early, ended before the run started, or started after it: none.
  assert.equal(callerOf([launch({ at: T - 200_000 })], { ...r, name: null }), null)
  assert.equal(callerOf([launch({ at: T - 9000, ended: T - 5000 })], { ...r, name: null }), null)
  assert.equal(callerOf([launch({ at: T + 5000 })], { ...r, name: null }), null)
  // A call sent to the background ended at once: its run still links.
  assert.equal(callerOf([launch({ at: T - 9000, ended: T - 8990, background: true })], { ...r, name: null })?.toolUseId, 'tu-1')
})

test('result.json gives cost, turns, files and why a run ended badly', () => {
  const r = parseResult(JSON.stringify({ total_cost_usd: 0.25, num_turns: 4, changed: ['a', 'b'], model: 'claude-opus-5-5', lost: false, capped_kills: [] }))
  assert.deepEqual(r, { cost: 0.25, turns: 4, files: 2, model: 'claude-opus-5-5', why: null })
  assert.equal(parseResult(JSON.stringify({ lost: true }))!.why, 'lost sandbox')
  assert.equal(parseResult(JSON.stringify({ capped_kills: [{ pid: 1 }] }))!.why, 'capped')
  assert.equal(parseResult('nope'), null)
})

test("a transcript gives the run's model, its own steps, and each subagent with its own calls and status", () => {
  const s = scanTranscript(TRANSCRIPT)
  assert.equal(s.model, 'claude-opus-5-5')
  assert.equal(s.steps, 2)
  assert.equal(s.step, 'Agent Find settings')
  assert.deepEqual(s.tasks, [{ id: 'tk1', type: 'Explore', desc: 'Find skills with settings.json', toolUseId: 'agent1', status: 'done', ended: T + 20_000, steps: 1, step: null }])
})

test("a remote log: the run's setup, its own events, then its summary; a subagent's log only its own events", () => {
  const progress = 'remote-agent-pack: setup staged\nremote-agent: claimed in 1s\n== capped: shell prefix set\nremote-agent: first agent event after 11s\n  → Bash ls\nremote-agent: no changes\n'
  const lines = remoteLog(TRANSCRIPT, progress, '14 skills.\n', null)
  assert.deepEqual(lines.slice(0, 3).map(l => l.text), ['remote-agent: claimed in 1s', '== capped: shell prefix set', 'remote-agent: first agent event after 11s'])
  assert.ok(lines.some(l => l.kind === 'call' && l.text === '▸ Bash ls skills | wc -l'))
  assert.ok(!lines.some(l => l.text.includes('skills/*/')))
  assert.deepEqual(lines.slice(-2), [{ kind: 'note', text: '── summary ──' }, { kind: 'text', text: '14 skills.' }])
  const child = remoteLog(TRANSCRIPT, progress, '14 skills.\n', 'agent1')
  assert.deepEqual(child.map(l => l.text), ['▸ Bash ls -d skills/*/', '⎿ skills/a/'])
})

// The graph: a run hangs under the agent whose Bash call started it, its subagents under it.
const graphWith = (r: Run, extra: FlowEvent[] = []) => {
  const scan = scanTranscript(TRANSCRIPT)
  const evs: FlowEvent[] = [
    { kind: 'turnStart', at: T - 2000, agent: MAIN },
    { kind: 'spawn', at: T - 1500, id: 'impl', parent: MAIN, type: 'epic-implementer', desc: 'fix it', model: null, parentModel: null, toolUseId: null },
    { kind: 'launch', at: T - 1000, toolUseId: 'b1', agent: 'impl', name: null, desc: 'run the probe remotely', background: true },
    ...extra,
    ...runEvents(remoteRun(r, null, scan, T), scan, T),
  ]
  return evs.reduce(reduce, emptyGraph())
}
const agent = (g: Graph, id: string) => g.agents.find(a => a.id === id)!

test("a run hangs under the subagent that launched it, named by the call's description; its subagent under it", () => {
  const g = graphWith(run({ name: null }))
  const r = agent(g, `remote:${ID}`)
  assert.deepEqual([r.parent, r.type, r.desc, r.status, r.model, r.remote!.linked], ['impl', '☁ remote-agent', 'run the probe remotely', 'running', 'claude-opus-5-5', true])
  const k = agent(g, `remote:${ID}:tk1`)
  assert.deepEqual([k.parent, k.type, k.status], [`remote:${ID}`, 'Explore', 'done'])
})

test('a run with no call found hangs under main, unlinked; the call naming it later moves it under its agent', () => {
  const r = run({ started: (T + 300_000) / 1000 })
  const g = graphWith(r)
  assert.deepEqual([agent(g, `remote:${ID}`).parent, agent(g, `remote:${ID}`).remote!.linked], [MAIN, false])
  const moved = reduce(g, { kind: 'launchEnd', at: T + 400_000, toolUseId: 'b1', runIds: [ID] })
  assert.deepEqual([agent(moved, `remote:${ID}`).parent, agent(moved, `remote:${ID}`).remote!.linked], ['impl', true])
})

test('a run done is done whatever its exit code; failed says its exit or why; stopped says stopped', () => {
  const at = (over: Record<string, unknown>) => remoteRun(run(over), null, null, T)
  assert.deepEqual([at({ state: 'done', exit_code: 3, ended: T / 1000 + 27 }).status, at({ state: 'done', exit_code: 3 }).why], ['done', null])
  assert.deepEqual([at({ state: 'failed', exit_code: 2 }).status, at({ state: 'failed', exit_code: 2 }).why], ['failed', 'exit 2'])
  assert.equal(remoteRun({ ...run(), state: 'stopped' }, null, null, T).why, 'stopped')
  assert.equal(remoteRun(run({ state: 'failed' }), { cost: null, turns: null, files: null, model: null, why: 'lost sandbox' }, null, T).why, 'lost sandbox')
})

test("a run's box: ☁ and its name, its setup phase while it boots, its cost and files once done", () => {
  const OPTS: ChartOpts = { columns: 160, lingerMs: 60_000, squadMin: 4, motion: false, expanded: null }
  const draw = (r: Run, at: number, result = null as Parameters<typeof remoteRun>[1]) => {
    const g = [{ kind: 'turnStart', at: T - 1, agent: MAIN } as FlowEvent, ...runEvents(remoteRun(r, result, null, T), null, T)].reduce(reduce, emptyGraph())
    const tw = newTween()
    let c = chart(g, at, OPTS, tw)
    for (let t = T; t <= at; t += 125) c = chart(g, t, OPTS, tw)
    return c.rows.map(row => row.map(sg => sg.text).join('')).join('\n')
  }
  const booting = draw(run({ first_event_s: null }), T + 2000)
  assert.match(booting, /☁ probe/)
  assert.match(booting, /◌ booting…/)
  assert.match(booting, /remote/)
  const done = draw(run({ state: 'done', exit_code: 0, ended: T / 1000 + 27 }), T + 30_000, { cost: 0.25, turns: 4, files: 3, model: 'claude-opus-5-5', why: null })
  assert.match(done, /opus 5\.5 +0:27/)
  assert.match(done, /✓ \$0\.25 · 3 files/)
})
