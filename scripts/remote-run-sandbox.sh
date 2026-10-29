#!/usr/bin/env bash
# The part of scripts/remote-run.sh that runs inside the sandbox; remote-run.sh passes it to `bash -c`.
# Usage: remote-run-sandbox.sh <owner/repo> <setup owner/repo> <app slug> <branch>
# Stdin: the Claude token, a GitHub token for the target repo and one for the setup repo, one line each,
# then the prompt. Prints Claude's summary and, when it committed something, the PR URL.
# Exit 3 when Claude committed nothing, so there is no PR.
set -euo pipefail

target="$1" setup="$2" app="$3" branch="$4"
read -r CLAUDE_CODE_OAUTH_TOKEN
read -r target_token
read -r setup_token
prompt="$(cat)"
export CLAUDE_CODE_OAUTH_TOKEN
cd "$HOME"

echo "== installing $setup" >&2
GH_TOKEN="$setup_token" git clone -q --depth 1 "https://github.com/$setup.git" setup
setup/install.sh claude >/dev/null

# From here on, git and gh only reach the target repo.
export GH_TOKEN="$target_token"
echo "== cloning $target" >&2
git clone -q --filter=blob:none "https://github.com/$target.git" work
cd work
bot="${app}[bot]"
git config user.name "$bot"
git config user.email "$(gh api "users/$app%5Bbot%5D" --jq .id)+$bot@users.noreply.github.com"
git switch -q -c "$branch"
base="$(git rev-parse HEAD)"

echo "== claude" >&2
rules="You run unattended in a remote sandbox, in a clone of $target on branch $branch. \
Do the task, then commit your work on this branch with Conventional Commit messages. \
Do not push, open a PR or switch branches: the runner does that after you finish. \
End with a short summary of what you changed and how you checked it; it becomes the PR description."
claude -p "$prompt" --dangerously-skip-permissions --append-system-prompt "$rules" | tee "$HOME/summary.md"

[ -z "$(git status --porcelain)" ] || echo "== warning: uncommitted changes left out of the PR" >&2
if [ "$(git rev-parse HEAD)" = "$base" ]; then
  echo "== no commits, so no PR" >&2
  exit 3
fi

echo "== opening the PR" >&2
# Drop GitHub's "Create a pull request" hint; the PR URL follows anyway.
git push -q -u origin "$branch" 2> >(grep -v '^remote:' >&2)
title="$(git log --reverse --format=%s "$base..HEAD" | head -1)"
# shellcheck disable=SC2016 # the backticks are Markdown
printf '\n---\nOpened by a remote agent run (`scripts/remote-run.sh`, branch `%s`).\n' "$branch" >>"$HOME/summary.md"
gh pr create --head "$branch" --title "$title" --body-file "$HOME/summary.md"
