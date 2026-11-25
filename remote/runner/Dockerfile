# syntax=docker/dockerfile:1
# The runner image for remote subagents (remote/README.md): the harnesses (Claude Code, the Codex CLI and OpenCode), git, gh and
# python3 for the `node` user. A run picks its harness (remote.harness); all three are always here (OpenCode's npm
# package picks its native binary in a postinstall script, which npm runs by default).
# It holds no credentials and no one's setup: a run sends its tokens through stdin and installs its own setup when it
# execs in. Built and published by .github/workflows/runner-image.yml; Renovate (renovate.json) bumps the pins below.
FROM node:26-trixie-slim@sha256:e6bedef217677b276c591795e169840edd9096dbf13f29df8ef8d1856b8820bc

# renovate: datasource=npm depName=@anthropic-ai/claude-code
ARG CLAUDE_CODE_VERSION=2.1.329
# renovate: datasource=npm depName=@openai/codex
ARG CODEX_VERSION=0.172.0
# renovate: datasource=npm depName=@opencode/cli
ARG OPENCODE_VERSION=2.3.2

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git jq python3 python3-pip python3-venv tini \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/*

RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" "@openai/codex@${CODEX_VERSION}" "@opencode/cli@${OPENCODE_VERSION}" \
 && npm cache clean --force

# The image pins the versions; the global install is root-owned, so updating would fail anyway.
ENV DISABLE_AUTOUPDATER=1

USER node
WORKDIR /home/node

# git authenticates to GitHub through gh, which reads $GH_TOKEN at run time; nothing is stored.
RUN git config --global credential.https://github.com.helper '!gh auth git-credential'

# tini forwards SIGTERM, so a removed sandbox stops at once; the container idles until a run execs into it.
ENTRYPOINT ["tini", "--"]
CMD ["sleep", "infinity"]
