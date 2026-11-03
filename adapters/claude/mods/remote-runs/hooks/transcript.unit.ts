import assert from 'node:assert/strict'
import { test } from 'node:test'

import { parseRun } from './runs.ts'
import {
  appendLines,
  byteSlice,
  CHUNK,
  copiedText,
  type Log,
  mask,
  nextOffset,
  NO_LOG,
  oneLine,
  parseLines,
  readChunk,
  readFrom,
  renderEvent,
  renderTranscript,
  resumeHint,
  scrollLog,
  type TranscriptEvent,
  windowLines,
  wrappedRows,
} from './transcript.ts'

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

const bytes = (s: string) => new TextEncoder().encode(s).length
const n = (count: number, from = 0) => Array.from({ length: count }, (_, i) => `line ${from + i}`)

test('nextOffset moves past the whole lines only, counting multi-byte characters as their UTF-8 bytes', () => {
  assert.equal(nextOffset('ab\ncd\n', 10), 16)
  assert.equal(nextOffset('é→😀\n', 0), bytes('é→😀\n'))
  assert.equal(nextOffset('é→😀\n', 0), 10)
  assert.equal(nextOffset('one\nhalf é wr', 5), 9)
  assert.equal(nextOffset('no newline yet', 7), 7)
  assert.equal(nextOffset('', 3), 3)
})

test('byteSlice cuts text by UTF-8 bytes, never inside a character', () => {
  assert.equal(byteSlice('aé😀b', 1), 'é😀b')
  assert.equal(byteSlice('aé😀b', 3), '😀b')
  assert.equal(byteSlice('aé😀b', 0, 3), 'aé')
  assert.equal(byteSlice('aé😀b', 0, 4), 'aé')
  assert.equal(byteSlice('aé😀b', 3, 4), '😀')
  assert.equal(byteSlice('aé😀b', 2), '😀b')
  assert.equal(byteSlice('aé😀b', 4), 'b')
  assert.equal(byteSlice('abc', 9), '')
})

test('windowLines shows the last rows while following, and the rows from top otherwise', () => {
  const lines = n(10)
  assert.deepEqual(windowLines(lines, true, 0, 4), n(4, 6))
  assert.deepEqual(windowLines(lines, false, 2, 4), n(4, 2))
  assert.deepEqual(windowLines(lines, false, 9, 4), n(4, 6))
  assert.deepEqual(windowLines(lines, false, -3, 4), n(4))
  assert.deepEqual(windowLines(n(2), true, 0, 4), n(2))
  assert.deepEqual(windowLines([], true, 0, 4), [])
})

const log = (over: Partial<Log> = {}): Log => ({ ...NO_LOG, runId: 'r', ...over })

test('scrolling up stops following; scrolling back to the end follows again', () => {
  const up = scrollLog(log({ lines: n(10) }), -3, 4)
  assert.deepEqual([up.top, up.follow], [3, false])
  assert.deepEqual(windowLines(up.lines, up.follow, up.top, 4), n(4, 3))
  const top = scrollLog(up, -100, 4)
  assert.deepEqual([top.top, top.follow], [0, false])
  const back = scrollLog(top, 6, 4)
  assert.deepEqual([back.top, back.follow], [6, true])
  const short = scrollLog(log({ lines: n(2) }), -1, 4)
  assert.equal(short.follow, true)
})

test('new lines leave a scrolled-up window where it was, and follow the end otherwise', () => {
  const up = appendLines(scrollLog(log({ lines: n(10) }), -3, 4), n(5, 10))
  assert.deepEqual(windowLines(up.lines, up.follow, up.top, 4), n(4, 3))
  const following = appendLines(log({ lines: n(10) }), n(5, 10))
  assert.deepEqual(windowLines(following.lines, following.follow, following.top, 4), n(4, 11))
})

test('lines past the cap drop the oldest, keep a scrolled window on the same lines, and mark the log truncated', () => {
  const up = scrollLog(log({ lines: n(10) }), -3, 4)
  const capped = appendLines(up, n(4, 10), 12)
  assert.equal(capped.lines.length, 12)
  assert.equal(capped.lines[0], 'line 2')
  assert.equal(capped.truncated, true)
  assert.deepEqual(windowLines(capped.lines, capped.follow, capped.top, 4), n(4, 3))
  assert.equal(appendLines(log({ lines: n(3) }), n(1, 3), 12).truncated, false)
})

test('a first read starts at 0, or one byte before the last chunk of a file over a chunk; a shrunk file starts again', () => {
  assert.deepEqual(readFrom(NO_LOG, 500), { from: 0, length: CHUNK, isTail: false, isReset: false })
  assert.deepEqual(readFrom(NO_LOG, CHUNK), { from: 0, length: CHUNK, isTail: false, isReset: false })
  assert.deepEqual(readFrom(NO_LOG, 5 * CHUNK), { from: 4 * CHUNK - 1, length: CHUNK + 1, isTail: true, isReset: false })
  assert.deepEqual(readFrom(log({ offset: 300 }), 5 * CHUNK), { from: 300, length: CHUNK, isTail: false, isReset: false })
  assert.deepEqual(readFrom(log({ offset: 300 }), 100), { from: 0, length: CHUNK, isTail: false, isReset: true })
  assert.deepEqual(readFrom(log({ offset: 9 * CHUNK }), 5 * CHUNK), { from: 4 * CHUNK - 1, length: CHUNK + 1, isTail: true, isReset: true })
})

const event = (text: string) => jsonl(say(text))

test('readChunk renders the whole lines and leaves a half-written last line to be read again', () => {
  const whole = event('one') + event('twö')
  const half = event('three').slice(0, 20)
  const first = readChunk(log(), whole + half, { from: 0, length: CHUNK, isTail: false, isFull: false }, new Map())
  assert.deepEqual(first.lines, ['one', 'twö'])
  assert.equal(first.offset, bytes(whole))
  const again = readChunk(first, half + event('three').slice(20), { from: first.offset, length: CHUNK, isTail: false, isFull: false }, new Map())
  assert.deepEqual(again.lines, ['one', 'twö', 'three'])
  assert.equal(again.offset, bytes(whole + event('three')))
  assert.equal(readChunk(first, half, { from: first.offset, length: CHUNK, isTail: false, isFull: false }, new Map()), first)
})

test('a tail read drops the line cut at its start and marks the log truncated', () => {
  const got = readChunk(log(), `t","x":1}\n${event('kept')}`, { from: 1000, length: CHUNK + 1, isTail: true, isFull: false }, new Map())
  assert.deepEqual(got.lines, ['kept'])
  assert.equal(got.truncated, true)
  assert.equal(got.offset, 1000 + bytes(`t","x":1}\n${event('kept')}`))
  const exact = readChunk(log(), `\n${event('first whole')}`, { from: 999, length: CHUNK + 1, isTail: true, isFull: false }, new Map())
  assert.deepEqual(exact.lines, ['first whole'])
})

test('a full chunk with no newline is skipped whole, so one huge line never stalls the log', () => {
  const huge = 'x'.repeat(50)
  const got = readChunk(log({ offset: 200 }), huge, { from: 200, length: CHUNK, isTail: false, isFull: true }, new Map())
  assert.equal(got.offset, 200 + CHUNK)
  assert.deepEqual(got.lines, [])
  const notFull = readChunk(log({ offset: 200 }), huge, { from: 200, length: CHUNK, isTail: false, isFull: false }, new Map())
  assert.equal(notFull.offset, 200)
})

test('a tail read with no newline skips its bytes when the file had them all, and waits for more otherwise', () => {
  const huge = 'x'.repeat(50)
  const full = readChunk(log(), huge, { from: 1000, length: CHUNK + 1, isTail: true, isFull: true }, new Map())
  assert.deepEqual([full.offset, full.lines, full.truncated], [1000 + CHUNK + 1, [], true])
  const start = log()
  assert.equal(readChunk(start, huge, { from: 1000, length: CHUNK + 1, isTail: true, isFull: false }, new Map()), start)
})

// A row of the pane in `state`, for the run folder `/repo/.git/remote-agent/<id>/`, with `worktree` from run.json.
const row = (state: string, worktree: string | null = '/repo', id = 'agent-20261102-100000-7') =>
  parseRun({ id, folder: `/repo/.git/remote-agent/${id}`, runJson: JSON.stringify({ state, worktree }), progress: null, changed: 0 })

test('a finished run with a session says how to resume it, and warns the sandbox wrote the session', () => {
  for (const state of ['done', 'failed']) {
    const [command, warning, ...rest] = resumeHint(row(state), true)
    assert.equal(command, 'to resume: remote-agent resume agent-20261102-100000-7 /repo')
    assert.match(warning, /sandbox wrote/)
    assert.match(warning, /forged turns/)
    assert.ok(warning.includes('/repo/.git/remote-agent/agent-20261102-100000-7/session.jsonl'), warning)
    assert.deepEqual(rest, [])
  }
  const stopped = { ...row('running'), state: 'stopped' as const }
  assert.equal(resumeHint(stopped, true)[0], 'to resume: remote-agent resume agent-20261102-100000-7 /repo')
})

test('a run still going, or one with no run.json, shows no resume hint', () => {
  for (const state of ['starting', 'running', 'not-a-state']) {
    assert.deepEqual(resumeHint(row(state), true), [])
    assert.deepEqual(resumeHint(row(state), false), [])
  }
})

test('a finished run with no session.jsonl says it cannot be resumed', () => {
  for (const state of ['done', 'failed']) assert.deepEqual(resumeHint(row(state), false), ['no session came back: this run cannot be resumed'])
})

test('the worktree in the resume command is shell-quoted', () => {
  const command = (wt: string | null) => resumeHint(row('done', wt), true)[0]
  assert.equal(command('/Users/me/My Projects/repo'), "to resume: remote-agent resume agent-20261102-100000-7 '/Users/me/My Projects/repo'")
  assert.equal(command("/tmp/it's"), "to resume: remote-agent resume agent-20261102-100000-7 '/tmp/it'\\''s'")
  assert.equal(command('/tmp/$(rm -rf ~)'), "to resume: remote-agent resume agent-20261102-100000-7 '/tmp/$(rm -rf ~)'")
  assert.equal(command(null), 'to resume: remote-agent resume agent-20261102-100000-7')
})

test('copying the path toasts copied, or why not', () => {
  assert.equal(copiedText(null), 'copied')
  assert.match(copiedText('no-surface'), /^not copied \(this surface has no clipboard\)/)
  assert.match(copiedText('no-clipboard'), /^not copied \(no clipboard tool\)/)
  assert.match(copiedText('refused'), /^not copied \(refused\)/)
})

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
  assert.deepEqual(renderEvent({ type: 'tool_result', tool_use_id: 't', content: 'out\nmore', is_error: true }, names), ['<- ERROR Bash: out'])
  assert.deepEqual(renderEvent({ type: 'system', subtype: 'init' }, new Map()), ['session started'])
  assert.deepEqual(renderEvent(call('x', 'Glob', { path: ['a', 'b'] }) as TranscriptEvent, new Map()), ['-> Glob ["a","b"]'])
  assert.deepEqual(renderTranscript(jsonl({ type: 'result', total_cost_usd: 1.5 })), ['done $1.50'])
})

test('oneLine keeps up to max characters when given a wider max', () => {
  const path = `/repo/.git/${'d/'.repeat(100)}transcript.jsonl`
  assert.equal(oneLine(path, 1000), path)
  assert.equal(oneLine(path).length, 160)
})

test('wrappedRows counts the rows a line wraps to at spaces, a word longer than a row cut', () => {
  assert.equal(wrappedRows('', 10), 1)
  assert.equal(wrappedRows('abc def', 10), 1)
  assert.equal(wrappedRows('abcde fghij', 10), 2)
  assert.equal(wrappedRows('abcd efghi', 10), 1)
  assert.equal(wrappedRows('a'.repeat(25), 10), 3)
  assert.equal(wrappedRows(`ab ${'c'.repeat(12)} d`, 10), 3)
})
