// The remote-runs mod's plain logic. No claude-code import at run time, so `node --test` runs it beside the
// engine (runs.unit.ts); main.tsx keeps only the calls on `$` and the drawing.

export type { Run } from './state.d.ts'

/** What the pane says when it has no run to show, whatever the reason (no repo, no git, no folders). */
export const EMPTY = 'no remote runs in this repo'

/** The repo's common git dir from `git rev-parse --path-format=absolute --git-common-dir`, or null on failure. */
export function commonDir(exitCode: number, stdout: string): string | null {
  const dir = stdout.trim()
  return exitCode === 0 && dir.startsWith('/') ? dir.replace(/\/+$/, '') : null
}

/** The git dirs whose remote-agent/ folders hold this repo's runs: the common one, then each linked worktree's. */
export function gitDirs(common: string, worktrees: readonly string[]): string[] {
  return [common, ...[...worktrees].sort().map(w => `${common}/worktrees/${w}`)]
}
