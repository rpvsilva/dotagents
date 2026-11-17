import assert from 'node:assert/strict'
import { test } from 'node:test'

import { emptyGraph, type FlowEvent, MAIN, reduce } from './flow.ts'
import { addMessages, ago, appendLines, backLabel, filterCounts, goBack, type Item, LINES_MAX, listItems, mask, type Message, messageLines, NO_LOG, OLD_MS, oneLine, openFrom, scrollLog, windowLines } from './log.ts'
import type { View } from './state.d.ts'

const T = 1_800_000_000_000
const texts = (ls: { text: string }[]) => ls.map(l => l.text)

test('a log reads as the agent worked: its prompt, its text, each tool call and the first line of each result', () => {
  const msgs: Message[] = [
    { role: 'user', text: 'map the auth flow', uuid: 'u1' },
    { role: 'assistant', text: 'Looking at the routes.', toolUses: [{ tool: 'Bash', input: { command: 'rg login\nmore' } }], uuid: 'u2' },
    { role: 'user', text: '', toolResults: [{ text: 'src/a.ts\nsrc/b.ts\nsrc/c.ts' }], uuid: 'u3' },
    { role: 'assistant', toolUses: [{ tool: 'Read', input: { file_path: '/x.ts' } }], uuid: 'u4' },
    { role: 'user', toolResults: [{ text: 'no such file', isError: true }], uuid: 'u5' },
  ]
  const l = addMessages({ ...NO_LOG, agentId: 'a1' }, msgs)
  assert.deepEqual(l.lines, [
    { kind: 'prompt', text: 'map the auth flow' },
    { kind: 'text', text: 'Looking at the routes.' },
    { kind: 'call', text: '▸ Bash rg login' },
    { kind: 'result', text: '⎿ src/a.ts  (+2 lines)' },
    { kind: 'call', text: '▸ Read /x.ts' },
    { kind: 'error', text: '⎿ no such file' },
  ])
  assert.equal(l.after, 'u5')
  assert.equal(l.truncated, false)
})

test('a later read adds after the last message; only the first message of the first read is the prompt', () => {
  let l = addMessages({ ...NO_LOG, agentId: 'a1' }, [{ role: 'user', text: 'go', uuid: 'u1' }])
  l = addMessages(l, [{ role: 'user', text: 'and also this', uuid: 'u2' }])
  assert.deepEqual(l.lines.map(x => x.kind), ['prompt', 'text'])
  assert.equal(l.after, 'u2')
  assert.equal(addMessages(l, []), l)
})

test('a first read that came back full says older messages were left out, and has no prompt line', () => {
  const msgs: Message[] = Array.from({ length: 3 }, (_, i) => ({ role: 'assistant', text: `step ${i}`, uuid: `u${i}` }))
  const l = addMessages({ ...NO_LOG, agentId: 'a1' }, msgs, 3)
  assert.equal(l.truncated, true)
  assert.ok(l.lines.every(x => x.kind !== 'prompt'))
})

test('lines are one printable line each, control characters dropped', () => {
  assert.deepEqual(texts(messageLines({ role: 'assistant', text: 'a\u001b[31mred\n\n  b  ' }, false)), ['a[31mred', 'b'])
  assert.deepEqual(texts(messageLines({ role: 'user', toolResults: [{ text: '' }] }, false)), ['⎿ (no output)'])
})

test('the window follows the end; scrolling up stops following, reaching the end follows again; old lines drop past the cap', () => {
  let l = appendLines({ ...NO_LOG, agentId: 'a' }, Array.from({ length: 20 }, (_, i) => ({ kind: 'text' as const, text: `${i}` })))
  assert.deepEqual(texts(windowLines(l.lines, l.follow, l.top, 5)), ['15', '16', '17', '18', '19'])
  l = scrollLog(l, -3, 5)
  assert.equal(l.follow, false)
  assert.deepEqual(texts(windowLines(l.lines, l.follow, l.top, 5)), ['12', '13', '14', '15', '16'])
  l = scrollLog(l, 10, 5)
  assert.equal(l.follow, true)
  const big = appendLines(l, Array.from({ length: LINES_MAX }, () => ({ kind: 'text' as const, text: 'x' })))
  assert.equal(big.lines.length, LINES_MAX)
  assert.equal(big.truncated, true)
})

const sp = (id: string, at: number, type = 'Explore', parent = MAIN): FlowEvent => ({ kind: 'spawn', at, id, parent, type, desc: id, model: null, parentModel: null, toolUseId: null })
const end = (id: string, at: number, reason = 'answer'): FlowEvent => ({ kind: 'turnEnd', at, agent: id, reason, outputTokens: null })
const ids = (items: Item[]) => items.map(it => (it.kind === 'agent' ? `${it.section}:${it.agent.id}` : `${it.section}:group ${it.id} ×${it.members.length}`))

test('the Agents list: running first, then this turn by when it ended, then earlier turns, then older ones; main never', () => {
  const g = [
    { kind: 'turnStart', at: T, agent: MAIN } as FlowEvent, sp('ancient', T), end('ancient', T + 1),
    { kind: 'turnStart', at: T + OLD_MS, agent: MAIN } as FlowEvent, sp('old', T + OLD_MS), end('old', T + OLD_MS + 1),
    { kind: 'turnStart', at: T + OLD_MS + 10, agent: MAIN } as FlowEvent, sp('a', T + OLD_MS + 11), sp('b', T + OLD_MS + 12), sp('c', T + OLD_MS + 13),
    end('a', T + OLD_MS + 20), end('c', T + OLD_MS + 30),
  ].reduce(reduce, emptyGraph())
  assert.deepEqual(ids(listItems(g, T + OLD_MS + 40, 'all', 99)), ['recent:b', 'recent:c', 'recent:a', 'earlier:old', 'older:ancient'])
})

test('four siblings of one type are one group, listing the members that need you; a filter lists agents ungrouped', () => {
  const evs: FlowEvent[] = [{ kind: 'turnStart', at: T, agent: MAIN }]
  for (let i = 0; i < 5; i++) evs.push(sp(`e${i}`, T + i, 'epic-implementer'))
  evs.push(sp('x', T + 9), end('e1', T + 10), end('e2', T + 11, 'error'), { kind: 'check', at: T + 12, agent: 'e3', toolUseId: 'k' })
  const g = evs.reduce(reduce, emptyGraph())
  const now = T + 5000
  const items = listItems(g, now, 'all', 4)
  assert.deepEqual(ids(items), ['recent:x', 'recent:group main:epic-implementer ×5'])
  const grp = items[1] as Extract<Item, { kind: 'group' }>
  assert.deepEqual(grp.needing.map(a => a.id), ['e2', 'e3'])
  assert.deepEqual(ids(listItems(g, now, 'needs', 4)), ['recent:e3', 'recent:e2'])
  assert.deepEqual(ids(listItems(g, now, 'done', 4)), ['recent:e1'])
  assert.deepEqual(filterCounts(g, now), { all: 6, running: 3, needs: 2, failed: 1, done: 1 })
})

test('Back goes the way the logs were opened: to the log a link was followed from, then to the tab', () => {
  const g = [sp('p', T, 'epic-implementer'), sp('k', T + 1, 'fork', 'p')].reduce(reduce, emptyGraph())
  const v0: View = { tab: 'agents', tree: false, expanded: null, agentId: null, filter: 'all', groups: [], trail: [], origin: 'chart' }
  const fromChart = openFrom({ ...v0, tab: 'chart' }, 'p', 'chart')
  assert.equal(backLabel(fromChart, g), '← Chart')
  const viaLink = openFrom(fromChart, 'k', 'log')
  assert.deepEqual([viaLink.agentId, viaLink.trail], ['k', ['p']])
  assert.equal(backLabel(viaLink, g), '← epic-implementer')
  const back1 = goBack(viaLink)
  assert.deepEqual([back1.agentId, back1.trail], ['p', []])
  const back2 = goBack(back1)
  assert.deepEqual([back2.agentId, back2.tab], [null, 'chart'])
  assert.equal(backLabel(openFrom(v0, 'p', 'agents'), g), '← Agents')
})

test('an Agent call that started an agent is a link to its log', () => {
  const ls = messageLines({ role: 'assistant', toolUses: [{ tool: 'Agent', input: { subagent_type: 'Explore', description: 'map auth' }, agentId: 'a9' }, { tool: 'Agent', input: {} }] }, false)
  assert.deepEqual(ls, [{ kind: 'agent', text: '▸ Agent Explore · map auth', agentId: 'a9' }, { kind: 'call', text: '▸ Agent' }])
})

test('secrets are masked before a line is cut, as remote-runs masks them', () => {
  assert.equal(mask('GH_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz123456'), 'GH_TOKEN=[masked]')
  assert.equal(mask('nothing here'), 'nothing here')
  assert.equal(oneLine('key sk-ant-api03-abcdef plus more'), 'key [masked] plus more')
  assert.ok(!oneLine(`x ${'a'.repeat(190)} sk-ant-api03-abcdefghijklmnop`).includes('sk-ant'))
  assert.equal(messageLines({ role: 'user', toolResults: [{ text: 'Authorization: Bearer abc.def' }] }, false)[0]!.text, '⎿ Authorization: Bearer [masked]')
})

test('ago reads as people say it', () => {
  assert.deepEqual([0, 42_000, 180_000, 7_200_000].map(d => ago(T, T + d)), ['just now', '42s ago', '3m ago', '2h ago'])
})
