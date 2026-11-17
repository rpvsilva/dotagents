// Moved from the remote-runs mod's transcript.ts, which this mod replaces: the reader and renderer, without that
// mod's chunked reads, resume hints and copy messages.
//
// A run's log: its transcript.jsonl (`claude -p --output-format stream-json --verbose`, one event per line) turned
// into one-row log lines. Plain logic, no claude-code import at run time, so `node --test` runs it
// (transcript.unit.ts). The reader never throws: a partial last line, a line that is not JSON and an event of an
// odd shape are skipped.
import { printable } from './flow.ts'

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
  return text.split('\n').find(l => tidy(l).trim() !== '') ?? ''
}

/** What opens each kind of log line; lineStyle colors a line by it. */
export const CALL = '▸ '
export const RESULT = '  ⎿ '
export const RESULT_ERROR = '  ⎿ ✗ '
export const SESSION = '── session'
export const DONE = '✓ done'
export const FAILED = '✗ failed'

// The sandbox's working copy, `/home/<user>/.remote-agent/runs/<run>/work`: a path under it is shown relative to it,
// the folder itself as `.`.
const SANDBOX_WORK = /(?:\/[^\s/'"`]+)*\/\.remote-agent\/runs\/[^\s/'"`]+\/work(?:(\/)|(?![\w.-]))/g

/** `text` without the markup Claude Code wraps around some text (`<br>`, `<tool_use_error>`) and sandbox paths. */
export function tidy(text: string): string {
  return text
    .replace(/<br\s*\/?>/gi, ' ')
    .replace(/<\/?tool_use_error>/g, '')
    .replace(SANDBOX_WORK, (_m, slash) => (slash ? '' : '.'))
}

function toolResult(block: Record<string, unknown>): [string, string] {
  return [block.is_error === true ? RESULT_ERROR : RESULT, firstLine(block.content) || '(no output)']
}

function contentBlocks(ev: Record<string, unknown>): unknown[] {
  const message = ev.message
  return isObject(message) && Array.isArray(message.content) ? message.content : []
}

/** The final line of a run's stream: `✓ done in <s>s · $<cost>`, or `✗ failed (<subtype>)` when Claude ended in error. */
function resultLine(ev: Record<string, unknown>): string {
  const subtype = str(ev.subtype)
  if (ev.is_error === true || (subtype !== null && subtype !== 'success')) return subtype ? `${FAILED} (${subtype})` : FAILED
  const ms = num(ev.duration_ms)
  const cost = num(ev.total_cost_usd)
  const parts = [ms === null ? '' : `in ${Math.round(ms / 1000)}s`, cost === null ? '' : `$${cost.toFixed(2)}`].filter(Boolean)
  return parts.length ? `${DONE} ${parts.join(' · ')}` : DONE
}

/**
 * The log lines of one event, each one row (`oneLine`) after `tidy`: the agent's text, `▸ <tool> <summary>` per tool
 * call, `  ⎿ <first line>` per tool result (`  ⎿ ✗ ` when it errored), `── session · <model> ──` for the init event,
 * and the `result` line. Thinking, other system events and unknown types give none. Records each tool call's id in
 * `toolNames`.
 */
export function renderEvent(ev: TranscriptEvent, toolNames: Map<string, string>): string[] {
  const raw: [string, string][] = []
  if (ev.type === 'system') {
    if (ev.subtype === 'init') raw.push(['', str(ev.model) ? `${SESSION} · ${ev.model} ──` : `${SESSION} ──`])
  } else if (ev.type === 'assistant') {
    for (const b of contentBlocks(ev)) {
      if (!isObject(b)) continue
      if (b.type === 'text' && str(b.text)?.trim()) raw.push(['', b.text as string])
      if (b.type === 'tool_use') {
        const name = str(b.name) ?? 'tool'
        if (str(b.id)) toolNames.set(b.id as string, name)
        raw.push([CALL, [name, summary(b.input)].filter(Boolean).join(' ')])
      }
    }
  } else if (ev.type === 'user') {
    for (const b of contentBlocks(ev)) if (isObject(b) && b.type === 'tool_result') raw.push(toolResult(b))
  } else if (ev.type === 'tool_result') {
    raw.push(toolResult(ev))
  } else if (ev.type === 'result') {
    raw.push(['', resultLine(ev)])
  }
  return raw
    .map(([lead, text]): [string, string] => [lead, oneLine(tidy(text), LINE_MAX - lead.length)])
    .filter(([, text]) => text !== '')
    .map(([lead, text]) => lead + text)
}

/** Every log line of a whole transcript's text, in order; a last line with no newline yet is left out. */
export function renderTranscript(text: string): string[] {
  const toolNames = new Map<string, string>()
  return parseLines(text).events.flatMap(ev => renderEvent(ev, toolNames))
}

/** How the pane draws a log line: a theme color, or dim; `{}` for the agent's own text. */
export type LineStyle = { color?: 'suggestion' | 'success' | 'error' | 'warning'; dimColor?: true }

/**
 * A log line's style from what renderEvent made of it: a tool call in the suggestion color, a tool result dim and an
 * errored one red, the session rule dim, the end line green (done) or red (failed).
 */
export function lineStyle(line: string): LineStyle {
  if (line.startsWith(CALL)) return { color: 'suggestion' }
  if (line.startsWith(RESULT_ERROR)) return { color: 'error' }
  if (line.startsWith(RESULT)) return { dimColor: true }
  if (line.startsWith(SESSION)) return { dimColor: true }
  if (/^✓ done(?: in \d+s)?(?:(?: ·)? \$\d+\.\d{2})?$/.test(line)) return { color: 'success' }
  if (/^✗ failed( \(\S+\))?$/.test(line)) return { color: 'error' }
  return {}
}

/** What kind of log line a renderEvent line is, and its parts: a call's tool name, the text after the lead. */
export type LinePart =
  | { kind: 'call'; tool: string; rest: string }
  | { kind: 'result' | 'error' | 'done' | 'failed' | 'text'; rest: string }
  | { kind: 'session'; rest: string }

/** A log line split for drawing: a tool call's name apart from its summary, a result without its lead. */
export function lineParts(line: string): LinePart {
  if (line.startsWith(CALL)) {
    const body = line.slice(CALL.length)
    const space = body.indexOf(' ')
    return space < 0 ? { kind: 'call', tool: body, rest: '' } : { kind: 'call', tool: body.slice(0, space), rest: body.slice(space + 1) }
  }
  if (line.startsWith(RESULT_ERROR)) return { kind: 'error', rest: line.slice(RESULT_ERROR.length) }
  if (line.startsWith(RESULT)) return { kind: 'result', rest: line.slice(RESULT.length) }
  if (line.startsWith(SESSION)) return { kind: 'session', rest: line.replace(/ ──$/, '') }
  const style = lineStyle(line)
  if (line.startsWith(DONE) && style.color === 'success') return { kind: 'done', rest: line.slice(DONE.length).trim() }
  if (line.startsWith(FAILED) && style.color === 'error') return { kind: 'failed', rest: line.slice(FAILED.length).trim() }
  return { kind: 'text', rest: line }
}

/** A session line as a rule across `width` columns: `── session · <model> ─────…`. */
export function sessionRule(line: string, width: number): string {
  const head = `${line.replace(/ ──$/, '')} `
  return head + '─'.repeat(Math.max(2, Math.floor(width) - head.length))
}
