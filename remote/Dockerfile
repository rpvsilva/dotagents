# syntax=docker/dockerfile:1
# Runner image for remote agents: Claude Code, git, gh and this repo installed for the `node` user.
# Built from the repo root (context `.`) by .github/workflows/runner-image.yml; see remote/README.md.
FROM node:26-trixie-slim@sha256:e4db4c914bf8ad241bd24804fc25d272d89bad97e68cdc8a1414862790a02c39

ARG CLAUDE_CODE_VERSION=2.1.312

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git jq python3 tini \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/*

RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && npm cache clean --force

# The image pins the version; the global install is root-owned, so updating would fail anyway.
ENV DISABLE_AUTOUPDATER=1

USER node
WORKDIR /home/node

COPY --chown=node:node . dotagents

# git authenticates to GitHub through gh, which reads $GH_TOKEN at run time; nothing is stored.
# origin is HTTPS so a run can refresh the snapshot with that token.
RUN git config --global credential.https://github.com.helper '!gh auth git-credential' \
 && git -C dotagents remote set-url origin https://github.com/rpvsilva/dotagents.git \
 && git -C dotagents config --unset-all http.https://github.com/.extraheader || true

RUN dotagents/install.sh claude

# tini forwards SIGTERM, so a deleted sandbox stops at once; the pod idles until a run execs into it.
ENTRYPOINT ["tini", "--"]
CMD ["sleep", "infinity"]
