// A run's log: its transcript.jsonl (`claude -p --output-format stream-json --verbose`, one event per line) turned
// into one-row log lines. Plain logic, no claude-code import at run time, so `node --test` runs it
// (transcript.unit.ts). The reader never throws: a partial last line, a line that is not JSON and an event of an
// odd shape are skipped.
import { printable, type Run } from './runs.ts'
import type { Log } from './state.d.ts'

export type { Log }

/** What the log says while the transcript holds no event yet. */
export const WAITING = 'waiting for the first event'

/** How many characters a log line keeps, after masking. */
export const LINE_MAX = 160

/** One transcript event: a parsed line that is a JSON object with a string `type`. */
export type TranscriptEvent = { type: string; [field: string]: unknown }

/**
 * The events in `text`, read after `carry` (the unterminated end of the text read before), and the new carry: the
 * last segment when `text` does not end with a newline, as a writer may be half way through it.
 */
export function parseLines(text: string, carry = ''): { events: TranscriptEvent[]; carry: string } {
  const segments = (carry + text).split('\n')
  const rest = segments.pop() ?? ''
  const events: TranscriptEvent[] = []
  for (const s of segments) {
    if (!s.trimStart().startsWith('{')) continue
    try {
      const v: unknown = JSON.parse(s)
      if (isObject(v) && typeof v.type === 'string') events.push(v as TranscriptEvent)
    } catch {
      // not an event: skipped
    }
  }
  return { events, carry: rest }
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

const str = (v: unknown): string | null => (typeof v === 'string' ? v : null)
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

/**
 * Secret-shaped strings and what replaces them. Best effort: remote-agent already redacts the Claude token's exact
 * value at the source, and this second, cheap pass catches other tokens a tool result may print. A false positive
 * hides some text; a token of a shape not listed here still shows.
 */
const SECRETS: readonly [RegExp, string][] = [
  // A private key: its header and all after it on the (flattened) line.
  [/-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*/g, '[masked private key]'],
  [/\bsk-ant-[A-Za-z0-9_-]+/g, '[masked]'],
  [/\bgithub_pat_[A-Za-z0-9_]+/g, '[masked]'],
  [/\bgh[pousr]_[A-Za-z0-9]{20,}/g, '[masked]'],
  [/\bxox[abprs]-[A-Za-z0-9-]+/g, '[masked]'],
  [/\bAKIA[0-9A-Z]{16}\b/g, '[masked]'],
  // A JWT: three base64url parts, the first a JSON header.
  [/\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, '[masked]'],
  [/\b(Bearer|Basic)\s+[^\s"',]+/gi, '$1 [masked]'],
  // user:password@ in a URL.
  [/(:\/\/)[^\s/:@]+:[^\s/@]+@/g, '$1[masked]@'],
  // NAME=value and "name": "value" where the name holds KEY, TOKEN, SECRET or PASSWORD.
  [/\b(\w*(?:KEY|TOKEN|SECRET|PASSWORD)\w*["']?\s*[=:]\s*["']?)[^\s"',;]+/gi, '$1[masked]'],
]

/** `line` with every secret-shaped string replaced by `[masked]` (best effort, see SECRETS). */
export function mask(line: string): string {
  let out = line
  for (const [re, by] of SECRETS) out = out.replace(re, by)
  return out
}

// ANSI escapes: CSI (`ESC [ ... final`, or its one-byte form), OSC (`ESC ] ... BEL` or `ESC \`, or its one-byte
// form) and any other two-byte `ESC x`.
const ANSI = /(?:\u001b\[|\u009b)[0-?]*[ -/]*[@-~]|(?:\u001b\]|\u009d)[^\u0007\u001b\u009c]*(?:\u0007|\u001b\\|\u009c)?|\u001b[@-Z\\-_]?/g

/**
 * `line` as one row of at most `max` characters: newlines and tabs to spaces, ANSI escapes and control characters
 * dropped (`printable`), secrets masked, then cut, so a cut never leaves part of a secret unmasked. Masking runs on
 * the first `4 * max` characters only: some patterns backtrack, and the sandbox writes the line.
 */
export function oneLine(line: string, max = LINE_MAX): string {
  const flat = printable(line.replace(ANSI, '').replace(/[\r\n\t]+/g, ' ')).replace(/ {2,}/g, ' ').trim()
  const masked = mask(flat.slice(0, 4 * max))
  if (masked.length > max) return `${masked.slice(0, max - 1)}…`
  return flat.length > 4 * max ? `${masked}…` : masked
}

/** The sandbox's one-word summary of a tool call's input, as its progress line prints it. */
function summary(input: unknown): string {
  if (!isObject(input)) return ''
  for (const k of ['command', 'file_path', 'pattern', 'description', 'url', 'path']) {
    const v = input[k]
    if (v !== undefined && v !== null && v !== '') return typeof v === 'string' ? v : JSON.stringify(v)
  }
  return ''
}

/** The first non-empty line of a tool result's content: a string, or blocks of text and tool references. */
function firstLine(content: unknown): string {
  let text = ''
  if (typeof content === 'string') text = content
  else if (Array.isArray(content)) {
    text = content
      .map(b => (isObject(b) ? (str(b.text) ?? str(b.tool_name) ?? '') : ''))
      .filter(Boolean)
      .join('\n')
  }
  return text.split('\n').find(l => l.trim() !== '') ?? ''
}

function toolResult(block: Record<string, unknown>, toolNames: ReadonlyMap<string, string>): string {
  const id = str(block.tool_use_id)
  const tool = id === null ? undefined : toolNames.get(id)
  const parts = ['<-', block.is_error === true ? 'ERROR' : '', tool ? `${tool}:` : '', firstLine(block.content)]
  return parts.filter(Boolean).join(' ')
}

function contentBlocks(ev: Record<string, unknown>): unknown[] {
  const message = ev.message
  return isObject(message) && Array.isArray(message.content) ? message.content : []
}

/** The final line of a run's stream: `done in <s>s, $<cost>`, or `FAILED <subtype>` when Claude ended in error. */
function resultLine(ev: Record<string, unknown>): string {
  const subtype = str(ev.subtype)
  if (ev.is_error === true || (subtype !== null && subtype !== 'success')) return ['FAILED', subtype].filter(Boolean).join(' ')
  const ms = num(ev.duration_ms)
  const cost = num(ev.total_cost_usd)
  const parts = [ms === null ? '' : `in ${Math.round(ms / 1000)}s`, cost === null ? '' : `$${cost.toFixed(2)}`].filter(Boolean)
  return parts.length ? `done ${parts.join(', ')}` : 'done'
}

/**
 * The log lines of one event, each through `oneLine`: the agent's text, `-> <tool> <summary>` per tool call,
 * `<- [ERROR] [<tool>:] <first line>` per tool result, `session started (<model>)` for the init event, and the
 * `result` line. Thinking, other system events and unknown types give none. Records each tool call's id in
 * `toolNames`, so its result names the tool.
 */
export function renderEvent(ev: TranscriptEvent, toolNames: Map<string, string>): string[] {
  const raw: string[] = []
  if (ev.type === 'system') {
    if (ev.subtype === 'init') raw.push(['session started', str(ev.model) ? `(${ev.model})` : ''].filter(Boolean).join(' '))
  } else if (ev.type === 'assistant') {
    for (const b of contentBlocks(ev)) {
      if (!isObject(b)) continue
      if (b.type === 'text' && str(b.text)?.trim()) raw.push(b.text as string)
      if (b.type === 'tool_use') {
        const name = str(b.name) ?? 'tool'
        if (str(b.id)) toolNames.set(b.id as string, name)
        raw.push(['->', name, summary(b.input)].filter(Boolean).join(' '))
      }
    }
  } else if (ev.type === 'user') {
    for (const b of contentBlocks(ev)) if (isObject(b) && b.type === 'tool_result') raw.push(toolResult(b, toolNames))
  } else if (ev.type === 'tool_result') {
    raw.push(toolResult(ev, toolNames))
  } else if (ev.type === 'result') {
    raw.push(resultLine(ev))
  }
  return raw.map(l => oneLine(l)).filter(Boolean)
}

/** Every log line of a whole transcript's text, in order; a last line with no newline yet is left out. */
export function renderTranscript(text: string): string[] {
  const toolNames = new Map<string, string>()
  return parseLines(text).events.flatMap(ev => renderEvent(ev, toolNames))
}

// The open log, read a chunk at a time as the run writes. Byte offsets count the file's UTF-8 bytes and stop at the
// last newline read, so a half-written last line is read again whole on the next tick.

/** The most bytes one read takes: the tail shown on first opening a longer transcript, and each read after. */
export const CHUNK = 1 << 20

/** The most log lines kept; past it the oldest go. */
export const LINES_MAX = 2000

/** The note over a log that does not start at the transcript's first event. */
export const EARLIER = '(earlier events not shown: open the file)'

/** No log: what Back and a switch to another run start from. */
export const NO_LOG: Log = { runId: null, offset: 0, lines: [], follow: true, top: 0, truncated: false }

const encoder = new TextEncoder()

/** `offset` moved past the whole lines of `text`, the bytes read from it: up to and including its last newline. */
export function nextOffset(text: string, offset: number): number {
  return offset + encoder.encode(text.slice(0, text.lastIndexOf('\n') + 1)).length
}

const utf8Length = (cp: number) => (cp < 0x80 ? 1 : cp < 0x800 ? 2 : cp < 0x10000 ? 3 : 4)

/**
 * The characters of `text` from UTF-8 byte `start`, at most `length` bytes of them; a character the cut would
 * split at the end is left out, one split at the start too.
 */
export function byteSlice(text: string, start: number, length = Infinity): string {
  let at = 0
  let from = -1
  let i = 0
  for (; i < text.length; ) {
    if (from < 0 && at >= start) from = i
    const cp = text.codePointAt(i) as number
    const size = utf8Length(cp)
    if (from >= 0 && at + size > start + length) break
    at += size
    i += cp > 0xffff ? 2 : 1
  }
  if (from < 0) return ''
  return text.slice(from, i)
}

/** Where a read starts in the transcript, how many bytes it takes, and what readChunk is to make of them. */
export type ReadAt = { from: number; length: number; isTail: boolean; isReset: boolean }

/**
 * Where to read next in a transcript of `size` bytes: a chunk at the log's offset; on a first read of a file longer
 * than a chunk, its last chunk and the byte before it (`isTail`: up to the first newline is dropped, so a line cut
 * there goes and one starting there stays). A file now shorter than the offset was written anew: it is read as
 * a first read again (`isReset`).
 */
export function readFrom(log: Log, size: number): ReadAt {
  const isReset = size < log.offset
  if (!isReset && (log.offset > 0 || log.lines.length > 0)) return { from: log.offset, length: CHUNK, isTail: false, isReset }
  if (size > CHUNK) return { from: size - CHUNK - 1, length: CHUNK + 1, isTail: true, isReset }
  return { from: 0, length: CHUNK, isTail: false, isReset }
}

/**
 * `log` with the lines of `chunk`, the text read from byte `from`, and its offset moved past them. A tail read
 * drops the text up to its first newline. A full chunk with no newline (`isFull`: the file had all `length` bytes)
 * is part of one huge line: it is skipped whole, and the line's end fails to parse and is skipped too.
 * `toolNames` keeps the run's tool call ids across reads. The same `log` when nothing changed.
 */
export function readChunk(
  log: Log,
  chunk: string,
  at: { from: number; length: number; isTail: boolean; isFull: boolean },
  toolNames: Map<string, string>,
): Log {
  let text = chunk
  let from = at.from
  let truncated = log.truncated
  if (at.isTail) {
    const cut = text.indexOf('\n')
    if (cut < 0) return at.isFull ? { ...log, offset: from + at.length, truncated: true } : log
    text = text.slice(cut + 1)
    // Counted from the end the file had whole: the dropped part may start inside a character, whose stray bytes
    // read as U+FFFD and re-encode longer than they were.
    from = at.isFull ? at.from + at.length - encoder.encode(text).length : from + encoder.encode(chunk.slice(0, cut + 1)).length
    truncated = true
  }
  const offset = nextOffset(text, from)
  if (offset === from) {
    if (at.isFull) return { ...log, offset: at.from + at.length, truncated }
    return offset === log.offset && truncated === log.truncated ? log : { ...log, offset, truncated }
  }
  const { events } = parseLines(text.slice(0, text.lastIndexOf('\n') + 1))
  const added = events.flatMap(ev => renderEvent(ev, toolNames))
  return appendLines({ ...log, offset, truncated }, added)
}

/**
 * `log` with `added` at its end, keeping the last `max` lines. A window scrolled up stays on the same lines: its
 * top moves back by the lines dropped.
 */
export function appendLines(log: Log, added: readonly string[], max = LINES_MAX): Log {
  if (added.length === 0) return log
  const all = [...log.lines, ...added]
  const dropped = Math.max(0, all.length - max)
  return {
    ...log,
    lines: dropped ? all.slice(dropped) : all,
    top: Math.max(0, log.top - dropped),
    truncated: log.truncated || dropped > 0,
  }
}

/** The last line a window of `rows` may start at. */
const lastTop = (lines: readonly string[], rows: number) => Math.max(0, lines.length - rows)

/**
 * `log` with its window of `rows` moved `by` lines (negative: up). It follows the end again once it reaches it.
 */
export function scrollLog(log: Log, by: number, rows: number): Log {
  const last = lastTop(log.lines, rows)
  const now = log.follow ? last : Math.min(Math.max(log.top, 0), last)
  const top = Math.min(Math.max(now + by, 0), last)
  return { ...log, top, follow: top >= last }
}

/** The `rows` lines a window shows: the last ones while following, else those from `top`. */
export function windowLines(lines: readonly string[], follow: boolean, top: number, rows: number): string[] {
  const last = lastTop(lines, rows)
  const start = follow ? last : Math.min(Math.max(top, 0), last)
  return lines.slice(start, start + rows)
}

/**
 * The toast after copying the transcript's path: `copied`, or why not from `$.ui.copy`'s reason (null: copied).
 * The path stays drawn as text, so it can be selected by hand.
 */
export function copiedText(reason: string | null): string {
  if (reason === null) return 'copied'
  const why: Record<string, string> = {
    'no-surface': 'this surface has no clipboard',
    'no-clipboard': 'no clipboard tool',
  }
  return `not copied (${why[reason] ?? reason}): select the path shown instead`
}

/** `s` as one shell word: as is when it holds only safe characters, else in single quotes. */
function shellQuote(s: string): string {
  return /^[A-Za-z0-9_@%+=:,./-]+$/.test(s) ? s : `'${s.replace(/'/g, `'\\''`)}'`
}

/**
 * How to pick a finished (done, failed or stopped) run up: `remote-agent resume <run> <worktree>` (the worktree
 * from run.json, shell-quoted) and the warning that it places a session the sandbox wrote; with no session.jsonl
 * (`hasSession` false), that it cannot be resumed. None for a run still going or one with no run.json. Mirrors the
 * session line remote-agent prints at the end of a run.
 */
export function resumeHint(row: Run, hasSession: boolean): string[] {
  if (row.state !== 'done' && row.state !== 'failed' && row.state !== 'stopped') return []
  if (!hasSession) return ['no session came back: this run cannot be resumed']
  const command = ['remote-agent', 'resume', shellQuote(printable(row.id)), ...(row.worktree === null ? [] : [shellQuote(row.worktree)])]
  return [
    `to resume: ${command.join(' ')}`,
    `warning: it places a session the sandbox wrote, which may hold forged turns that read as yours: read ${printable(row.folder)}/session.jsonl first`,
  ]
}

/** How many rows `line` takes wrapped at `columns`, as a terminal wraps text: at spaces, a longer word cut. */
export function wrappedRows(line: string, columns: number): number {
  const width = Math.max(1, Math.floor(columns))
  let rows = 1
  let used = 0
  for (const word of line.split(' ')) {
    const need = used === 0 ? word.length : used + 1 + word.length
    if (need <= width) {
      used = need
      continue
    }
    if (used > 0) rows++
    rows += Math.max(0, Math.ceil(word.length / width) - 1)
    used = word.length % width || (word.length === 0 ? 0 : width)
  }
  return rows
}

/** How the pane draws a log line: a theme color, or dim; `{}` for the agent's own text. */
export type LineStyle = { color?: 'suggestion' | 'success' | 'error'; dimColor?: true }

/**
 * A log line's style from what renderEvent made of it: a tool call (`->`) in the suggestion color, a tool result
 * (`<-`) dim and an errored one red, the session line dim, the end line green (`done`) or red (`FAILED`).
 */
export function lineStyle(line: string): LineStyle {
  if (line.startsWith('-> ')) return { color: 'suggestion' }
  if (line.startsWith('<- ERROR')) return { color: 'error' }
  if (line.startsWith('<- ')) return { dimColor: true }
  if (line.startsWith('session started')) return { dimColor: true }
  if (/^done( in \d+s)?(,? ?\$\d+\.\d{2})?$/.test(line)) return { color: 'success' }
  if (/^FAILED( \S+)?$/.test(line)) return { color: 'error' }
  return {}
}
