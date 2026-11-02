import assert from 'node:assert/strict'
import { test } from 'node:test'

import { commonDir, gitDirs } from './runs.ts'

test('commonDir takes the absolute path git printed', () => {
  assert.equal(commonDir(0, '/repo/.git\n'), '/repo/.git')
  assert.equal(commonDir(0, '/repo/.git/\n'), '/repo/.git')
})

test('commonDir is null when git fails or prints no absolute path', () => {
  assert.equal(commonDir(128, 'fatal: not a git repository\n'), null)
  assert.equal(commonDir(1, '/repo/.git\n'), null)
  assert.equal(commonDir(0, ''), null)
  assert.equal(commonDir(0, '.git\n'), null)
})

test('gitDirs lists the common dir, then each worktree git dir in name order', () => {
  assert.deepEqual(gitDirs('/repo/.git', ['b', 'a']), ['/repo/.git', '/repo/.git/worktrees/a', '/repo/.git/worktrees/b'])
  assert.deepEqual(gitDirs('/repo/.git', []), ['/repo/.git'])
})
