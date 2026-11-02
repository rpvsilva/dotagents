import assert from 'node:assert/strict'
import { test } from 'node:test'

import { mask, oneLine, parseLines, renderEvent, renderTranscript, type TranscriptEvent } from './transcript.ts'

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
    'session started (claude-opus)',
    'Looking at the repo. Then the tests.',
    '-> Bash ls -la',
    '<- Bash: total 8',
    'done in 12s, $0.03',
  ])
})

test('a tool call is summarised by command, file_path, pattern, description, url or path, as the sandbox does', () => {
  const lines = (input: Record<string, unknown>) => renderEvent(call('x', 'Tool', input) as TranscriptEvent, new Map())
  assert.deepEqual(lines({ file_path: '/a.ts', pattern: 'p' }), ['-> Tool /a.ts'])
  assert.deepEqual(lines({ pattern: 'TODO', path: 'src' }), ['-> Tool TODO'])
  assert.deepEqual(lines({ description: 'review', prompt: 'long' }), ['-> Tool review'])
  assert.deepEqual(lines({ url: 'https://example.com' }), ['-> Tool https://example.com'])
  assert.deepEqual(lines({ path: 'docs' }), ['-> Tool docs'])
  assert.deepEqual(lines({ todos: [] }), ['-> Tool'])
})

test('a result that ended in error says FAILED and its subtype', () => {
  const failed = { type: 'result', subtype: 'error_during_execution', is_error: true, duration_ms: 1000 }
  assert.deepEqual(renderTranscript(jsonl(failed)), ['FAILED error_during_execution'])
  assert.deepEqual(renderTranscript(jsonl({ type: 'result', subtype: 'error_max_turns', is_error: false })), ['FAILED error_max_turns'])
  assert.deepEqual(renderTranscript(jsonl({ type: 'result', is_error: true })), ['FAILED'])
  assert.deepEqual(renderTranscript(jsonl({ type: 'result' })), ['done'])
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
    '-> Read /x',
    '<- Read: first',
    '-> ToolSearch',
    '<- ToolSearch: WebFetch',
    '-> Bash false',
    '<- ERROR Bash: Exit code 1',
    '<- ERROR no call before it',
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
  assert.deepEqual(renderTranscript(text), ['hi', '-> tool'])
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
  assert.equal(lines[2], '<- Bash: GH_TOKEN=[masked]')
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
