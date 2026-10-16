FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm git curl ca-certificates \
    poppler-utils tesseract-ocr openssh-client tini unzip \
    && OPENCODE_INSTALL_DIR=/usr/local/bin sh -c 'curl -fsSL https://opencode.ai/install | bash' \
    && ln -sf /root/.opencode/bin/opencode /usr/local/bin/opencode \
    && opencode --version \
    && npm install -g @anthropic-ai/claude-code \
    && claude --version \
    # Codex CLI pinned: the app-server protocol is not stability-guaranteed,
    # so version bumps must go through CI (see agent/codex_runtime.py)
    && npm install -g @openai/codex@0.159.0 \
    && codex --version \
    && pip install --no-cache-dir uv \
    && uv --version \
    && npm install -g \
        mongodb-mcp-server \
        @notionhq/notion-mcp-server \
        @hubspot/mcp-server \
        @sentry/mcp-server \
        @lishenxydlgzs/aws-athena-mcp \
    && rm -rf /var/lib/apt/lists/* /root/.npm

# Go toolchain so agents can build and test Go repos they review or change.
ARG GO_VERSION=1.24.9
RUN curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-$(dpkg --print-architecture).tar.gz" | tar -C /usr/local -xz \
    && /usr/local/go/bin/go version
ENV PATH="/usr/local/go/bin:${PATH}" \
    GOFLAGS=-modcacherw

# Shared caches and toolchains live on the workspace volume, next to the clones.
# Same filesystem, so uv hardlinks a venv from its cache (a few MB) instead of
# copying ~700MB per clone. Install toolchains with scripts/toolchains.sh, never
# per conversation. Dot-dirs are never touched by isolation/workspace_gc.py.
ENV LOMA_TOOLCHAINS_DIR=/opt/loma-workspace/.toolchains \
    LOMA_CACHE_DIR=/opt/loma-workspace/.cache \
    UV_CACHE_DIR=/opt/loma-workspace/.cache/uv \
    UV_LINK_MODE=hardlink \
    PIP_CACHE_DIR=/opt/loma-workspace/.cache/pip \
    npm_config_cache=/opt/loma-workspace/.cache/npm \
    GOPATH=/opt/loma-workspace/.cache/go \
    GOMODCACHE=/opt/loma-workspace/.cache/go/pkg/mod \
    GOCACHE=/opt/loma-workspace/.cache/go-build \
    PLAYWRIGHT_BROWSERS_PATH=/opt/loma-workspace/.toolchains/ms-playwright \
    JAVA_HOME=/opt/loma-workspace/.toolchains/jdk \
    ANDROID_HOME=/opt/loma-workspace/.toolchains/android-sdk \
    ANDROID_SDK_ROOT=/opt/loma-workspace/.toolchains/android-sdk \
    GRADLE_USER_HOME=/opt/loma-workspace/.cache/gradle \
    PUB_CACHE=/opt/loma-workspace/.cache/pub
ENV PATH="/opt/loma-workspace/.toolchains/bin:/opt/loma-workspace/.toolchains/jdk/bin:/opt/loma-workspace/.toolchains/flutter/bin:/opt/loma-workspace/.toolchains/android-sdk/cmdline-tools/latest/bin:/opt/loma-workspace/.toolchains/android-sdk/platform-tools:${PATH}"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
ENV PYTHONPATH=/app
ENV WEBHOOK_PORT=3000
# Persistent in-container workspace for cloning & running repos (named volume
# loma-workspace -> /opt/loma-workspace in docker-compose.yml). Lets long-running
# agent tasks keep their clone across container recreation, unlike /tmp's
# ephemeral writable overlay. Exposed as $LOMA_WORKSPACE_DIR to the agent process.
ENV LOMA_WORKSPACE_DIR=/opt/loma-workspace
RUN mkdir -p /opt/loma-workspace
# SSH config for reaching the ops host. The host's ~/.ssh is bind-mounted
# read-only at /root/.ssh-host (see docker-compose.yml); /root/.ssh itself
# stays writable so known_hosts can persist.
RUN mkdir -p /root/.ssh \
    && printf 'Host 98.83.133.237\n  User ubuntu\n  IdentityFile /root/.ssh-host/id_rsa\n  StrictHostKeyChecking accept-new\n' > /root/.ssh/config \
    && chmod 700 /root/.ssh && chmod 600 /root/.ssh/config
EXPOSE 3000
# tini as PID 1: reaps the orphaned children agent runs leave behind (without it
# they pile up as zombies under `python app.py`) and forwards SIGTERM so the
# backend still drains in-flight runs on shutdown.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "app.py"]
