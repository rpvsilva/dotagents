// Moved from the remote-runs mod's transcript.unit.ts with the reader: its rendering, masking and line kinds.
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { lineParts, lineStyle, mask, oneLine, parseLines, renderEvent, renderTranscript, sessionRule, tidy, type TranscriptEvent } from './transcript.ts'

// A transcript's text: one JSON line per event, each ended with a newline.
const jsonl = (...events: unknown[]) => events.map(e => `${JSON.stringify(e)}\n`).join('')

const init = { type: 'system', subtype: 'init', session_id: 's1', model: 'claude-opus', cwd: '/w' }
const say = (text: string) => ({ type: 'assistant', message: { content: [{ type: 'text', text }] } })
const call = (id: string, name: string, input: Record<string, unknown>) => ({
  type: 'assistant',
  message: { content: [{ type: 'tool_use', id, name, input }] },
})
const answer = (id: string, content: unknown, isError?: boolean) => ({
  type: 'user',
  message: { content: [{ type: 'tool_result', tool_use_id: id, content, ...(isError === undefined ? {} : { is_error: isError }) }] },
})
const success = { type: 'result', subtype: 'success', is_error: false, duration_ms: 12_400, total_cost_usd: 0.0349, num_turns: 3, result: 'ok' }

// Token-shaped strings, built from parts so no secret scanner flags this file.
const SK = ['sk', 'ant', 'oat01', 'AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'].join('-')
const GHP = ['ghp', 'AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'].join('_')
const PAT = ['github', 'pat', '11ABCDEFG0123456789', 'abcdefghijklmnopqrstuvwxyz'].join('_')
const JWT = ['eyJhbGciOiJIUzI1NiJ9', 'eyJzdWIiOiIxMjM0NTY3ODkwIn0', 'SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV'].join('.')
const BEARER_VALUE = ['abc', 'def', '123456'].join('')
const PEM = ['-----BEGIN', 'RSA', 'PRIVATE', 'KEY-----'].join(' ')
const ENV_VALUE = ['s3cr3t', 'v4lue'].join('-')
const SECRETS = [SK, GHP, PAT, JWT, BEARER_VALUE, ENV_VALUE, 'MIIEowIBAAKCAQEA']

test('a successful run renders its init, text, tool call, named result and done line, in order', () => {
  const text = jsonl(
    init,
    say('Looking at the repo.\nThen the tests.'),
    call('t1', 'Bash', { command: 'ls -la', description: 'list' }),
    answer('t1', 'total 8\ndrwxr-xr-x  3 me  staff'),
    success,
  )
  assert.deepEqual(renderTranscript(text), [
    '── session · claude-opus ──',
    'Looking at the repo. Then the tests.',
    '▸ Bash ls -la',
    '  ⎿ total 8',
    '✓ done in 12s · $0.03',
  ])
})

test('a tool call is summarised by command, file_path, pattern, description, url or path, as the sandbox does', () => {
  const lines = (input: Record<string, unknown>) => renderEvent(call('x', 'Tool', input) as TranscriptEvent, new Map())
  assert.deepEqual(lines({ file_path: '/a.ts', pattern: 'p' }), ['▸ Tool /a.ts'])
  assert.deepEqual(lines({ pattern: 'TODO', path: 'src' }), ['▸ Tool TODO'])
  assert.deepEqual(lines({ description: 'review', prompt: 'long' }), ['▸ Tool review'])
  assert.deepEqual(lines({ url: 'https://example.com' }), ['▸ Tool https://example.com'])
  assert.deepEqual(lines({ path: 'docs' }), ['▸ Tool docs'])
  assert.deepEqual(lines({ todos: [] }), ['▸ Tool'])
})

test('a result that ended in error says FAILED and its subtype', () => {
  const failed = { type: 'result', subtype: 'error_during_execution', is_error: true, duration_ms: 1000 }
  assert.deepEqual(renderTranscript(jsonl(failed)), ['✗ failed (error_during_execution)'])
  assert.deepEqual(renderTranscript(jsonl({ type: 'result', subtype: 'error_max_turns', is_error: false })), ['✗ failed (error_max_turns)'])
  assert.deepEqual(renderTranscript(jsonl({ type: 'result', is_error: true })), ['✗ failed'])
  assert.deepEqual(renderTranscript(jsonl({ type: 'result' })), ['✓ done'])
})

test('a tool result renders from a string, an array of blocks, and with is_error', () => {
  const text = jsonl(
    call('a', 'Read', { file_path: '/x' }),
    answer('a', '\n\nfirst\nsecond'),
    call('b', 'ToolSearch', { query: 'q' }),
    answer('b', [{ type: 'tool_reference', tool_name: 'WebFetch' }, { type: 'text', text: 'more' }], false),
    call('c', 'Bash', { command: 'false' }),
    answer('c', [{ type: 'text', text: 'Exit code 1\nboom' }], true),
    answer('unknown', 'no call before it', true),
  )
  assert.deepEqual(renderTranscript(text), [
    '▸ Read /x',
    '  ⎿ first',
    '▸ ToolSearch',
    '  ⎿ WebFetch',
    '▸ Bash false',
    '  ⎿ ✗ Exit code 1',
    '  ⎿ ✗ no call before it',
  ])
})

test('thinking, other system events and unknown types are skipped', () => {
  const text = jsonl(
    { type: 'assistant', message: { content: [{ type: 'thinking', thinking: 'hmm' }, { type: 'text', text: 'hi' }] } },
    { type: 'system', subtype: 'compact_boundary' },
    { type: 'stream_event', event: {} },
    { type: 'user', text: 'stub', message: 'not an object' },
    { type: 'assistant', message: { content: 'a string' } },
    { type: 'assistant', message: { content: [null, 3, { type: 'tool_use' }] } },
  )
  assert.deepEqual(renderTranscript(text), ['hi', '▸ tool'])
})

test('a partial last line is carried, not rendered, and completes on the next read', () => {
  const whole = jsonl(say('one'), say('two'))
  const cut = whole.length - 5
  const first = parseLines(whole.slice(0, cut))
  assert.equal(first.events.length, 1)
  assert.equal(first.carry, whole.slice(0, cut).split('\n')[1])
  const second = parseLines(whole.slice(cut), first.carry)
  assert.equal(second.events.length, 1)
  assert.equal(second.carry, '')
  assert.deepEqual(renderTranscript(whole.slice(0, cut)), ['one'])
})

test('a line starting with { that is not JSON, and non-object JSON, are skipped', () => {
  const text = `{ this is a banner\n${jsonl(say('kept'))}[1,2]\n"str"\n{"no":"type"}\nplain text\n`
  assert.deepEqual(renderTranscript(text), ['kept'])
})

test('an empty transcript renders no line', () => {
  assert.deepEqual(renderTranscript(''), [])
  assert.deepEqual(parseLines(''), { events: [], carry: '' })
})

test('token-shaped strings never reach a rendered line', () => {
  const text = jsonl(
    say(`the key is ${SK} and ${GHP}`),
    call('t', 'Bash', { command: `curl -H "Authorization: Bearer ${BEARER_VALUE}" https://u:${ENV_VALUE}@host/x` }),
    answer('t', `GH_TOKEN=${ENV_VALUE}\n`),
    answer('t', `${PAT} ${JWT}`),
    say(`${PEM}\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----`),
    say(`{"api_key": "${ENV_VALUE}"}`),
  )
  const lines = renderTranscript(text)
  assert.equal(lines.length, 6)
  for (const line of lines) for (const s of SECRETS) assert.ok(!line.includes(s), `${line} shows ${s}`)
  assert.equal(lines[2], '  ⎿ GH_TOKEN=[masked]')
  assert.match(lines[4], /^\[masked private key\]$/)
})

test('a secret at the cut is masked before the line is cut', () => {
  const line = oneLine(`${'x'.repeat(150)} ${GHP}`)
  assert.ok(!line.includes(GHP.slice(0, 8)), line)
  assert.ok(line.length <= 160)
})

test('mask is best effort over a whole line and leaves plain text alone', () => {
  assert.equal(mask('nothing to see here'), 'nothing to see here')
  assert.equal(mask(`a ${SK} b`), 'a [masked] b')
  assert.equal(mask(mask(`TOKEN=${ENV_VALUE}`)), 'TOKEN=[masked]')
})

test('a line has ANSI escapes and control characters stripped and fits one row', () => {
  const osc52 = '\u001b]52;c;aGVsbG8=\u0007'
  const line = oneLine(`\u001b[31mred\u001b[0m\ttab${osc52}\r\nnext \u0007\u009b2Jend`)
  assert.equal(line, 'red tab next end')
  const long = oneLine('y'.repeat(500))
  assert.equal(long.length, 160)
  assert.ok(long.endsWith('…'))
})

const bytes = (s: string) => new TextEncoder().encode(s).length
const n = (count: number, from = 0) => Array.from({ length: count }, (_, i) => `line ${from + i}`)

test('mask covers every token shape the README lists', () => {
  const tail = 'AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'
  const shapes = [
    ...['gho', 'ghu', 'ghs', 'ghr'].map(p => [p, tail].join('_')),
    ['xoxb', '123456789012', 'abcdefABCDEF'].join('-'),
    ['AKIA', 'IOSFODNN7EXAMPLE'].join(''),
  ]
  for (const s of shapes) assert.equal(mask(`x ${s} y`), 'x [masked] y', s)
  assert.equal(mask(`Authorization: Basic ${BEARER_VALUE}`), 'Authorization: Basic [masked]')
  assert.equal(mask(`AWS_SECRET_ACCESS_KEY=${ENV_VALUE} DB_PASSWORD: ${ENV_VALUE}`), 'AWS_SECRET_ACCESS_KEY=[masked] DB_PASSWORD: [masked]')
})

test('a top-level tool_result, an init with no model and a non-string tool input render too', () => {
  const names = new Map([['t', 'Bash']])
  assert.deepEqual(renderEvent({ type: 'tool_result', tool_use_id: 't', content: 'out\nmore', is_error: true }, names), ['  ⎿ ✗ out'])
  assert.deepEqual(renderEvent({ type: 'system', subtype: 'init' }, new Map()), ['── session ──'])
  assert.deepEqual(renderEvent(call('x', 'Glob', { path: ['a', 'b'] }) as TranscriptEvent, new Map()), ['▸ Glob ["a","b"]'])
  assert.deepEqual(renderTranscript(jsonl({ type: 'result', total_cost_usd: 1.5 })), ['✓ done $1.50'])
})

test('oneLine keeps up to max characters when given a wider max', () => {
  const path = `/repo/.git/${'d/'.repeat(100)}transcript.jsonl`
  assert.equal(oneLine(path, 1000), path)
  assert.equal(oneLine(path).length, 160)
})

test('a crafted 1 MiB line of secret-like repeats is masked and cut in well under a second', () => {
  const MiB = 1 << 20
  for (const unit of ['KEY', 'eyJ-', 'TOKEN_']) {
    const crafted = `GH_TOKEN=${ENV_VALUE} ${unit.repeat(Math.ceil(MiB / unit.length))}`
    const started = performance.now()
    const line = oneLine(crafted)
    const took = performance.now() - started
    assert.ok(took < 500, `${unit}: ${Math.round(took)} ms`)
    assert.ok(line.startsWith('GH_TOKEN=[masked] '), line)
    assert.ok(!line.includes(ENV_VALUE), line)
    assert.equal(line.length, 160)
  }
  // Masking shortens a long secret: the line still says it was cut.
  assert.equal(oneLine(`a sk-ant-${'x'.repeat(1000)} more`), 'a [masked]…')
})

test('lineStyle colors a log line by its kind: tool call, result, error, session, end', () => {
  const got = [
    '▸ Bash ls -1A',
    '  ⎿ total 3',
    '  ⎿ ✗ no such file',
    '── session · claude-opus-5-5 ──',
    '✓ done in 21s · $0.04',
    '✓ done',
    '✗ failed (error_max_turns)',
    'I created the file.',
    'done with the refactor, moving on',
  ].map(lineStyle)
  assert.deepEqual(got, [
    { color: 'suggestion' },
    { dimColor: true },
    { color: 'error' },
    { dimColor: true },
    { color: 'success' },
    { color: 'success' },
    { color: 'error' },
    {},
    {},
  ])
})

test('tidy drops <br> and <tool_use_error> markup and shows sandbox paths relative to its working copy', () => {
  const work = '/home/azureadmin/.remote-agent/runs/agent-20261103-151221-20578/work'
  assert.equal(tidy(`${work}/sleep-done.txt`), 'sleep-done.txt')
  assert.equal(tidy(`cd ${work} && ls`), 'cd . && ls')
  assert.equal(tidy(`File created successfully at: ${work}/a/b.ts`), 'File created successfully at: a/b.ts')
  assert.equal(tidy(`${work}space/x`), `${work}space/x`)
  assert.equal(tidy('<tool_use_error>Blocked: sleep 30</tool_use_error>'), 'Blocked: sleep 30')
  assert.equal(tidy('I will wait. <br>'), 'I will wait.  ')
})

test('a tool call and its result render as a call row and an indented result row, without markup', () => {
  const work = '/home/u/.remote-agent/runs/agent-1/work'
  const text = jsonl(
    { type: 'assistant', message: { content: [{ type: 'tool_use', id: 't1', name: 'Write', input: { file_path: `${work}/x.txt` } }] } },
    { type: 'user', message: { content: [{ type: 'tool_result', tool_use_id: 't1', content: `File created successfully at: ${work}/x.txt` }] } },
    { type: 'user', message: { content: [{ type: 'tool_result', tool_use_id: 't2', is_error: true, content: '<tool_use_error>Blocked</tool_use_error>' }] } },
    { type: 'user', message: { content: [{ type: 'tool_result', tool_use_id: 't3', content: '' }] } },
  )
  assert.deepEqual(renderTranscript(text), ['▸ Write x.txt', '  ⎿ File created successfully at: x.txt', '  ⎿ ✗ Blocked', '  ⎿ (no output)'])
})

test('lineParts splits a line by its kind for drawing', () => {
  assert.deepEqual(lineParts('▸ Bash ls -la'), { kind: 'call', tool: 'Bash', rest: 'ls -la' })
  assert.deepEqual(lineParts('▸ TodoWrite'), { kind: 'call', tool: 'TodoWrite', rest: '' })
  assert.deepEqual(lineParts('  ⎿ ✗ boom'), { kind: 'error', rest: 'boom' })
  assert.deepEqual(lineParts('  ⎿ ok'), { kind: 'result', rest: 'ok' })
  assert.deepEqual(lineParts('── session · opus ──'), { kind: 'session', rest: '── session · opus' })
  assert.deepEqual(lineParts('✓ done in 4s · $0.12'), { kind: 'done', rest: 'in 4s · $0.12' })
  assert.deepEqual(lineParts('✗ failed (error_max_turns)'), { kind: 'failed', rest: '(error_max_turns)' })
  assert.deepEqual(lineParts('✓ done with the refactor'), { kind: 'text', rest: '✓ done with the refactor' })
})

test('sessionRule fills the width with the rule', () => {
  assert.equal(sessionRule('── session · opus ──', 24), '── session · opus ──────')
  assert.equal(sessionRule('── session ──', 5), '── session ──')
})
