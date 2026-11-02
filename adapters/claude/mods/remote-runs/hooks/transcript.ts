// A run's log: its transcript.jsonl (`claude -p --output-format stream-json --verbose`, one event per line) turned
// into one-row log lines. Plain logic, no claude-code import at run time, so `node --test` runs it
// (transcript.unit.ts). The reader never throws: a partial last line, a line that is not JSON and an event of an
// odd shape are skipped.
import { printable, type Run } from './runs.ts'

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
 * dropped (`printable`), secrets masked, then cut, so a cut never leaves part of a secret unmasked.
 */
export function oneLine(line: string, max = LINE_MAX): string {
  const flat = printable(line.replace(ANSI, '').replace(/[\r\n\t]+/g, ' ')).replace(/ {2,}/g, ' ').trim()
  const masked = mask(flat)
  return masked.length > max ? `${masked.slice(0, max - 1)}…` : masked
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
